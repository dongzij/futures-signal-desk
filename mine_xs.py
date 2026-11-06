# -*- coding: utf-8 -*-
"""独立出场雷达（顶底探测器）参数寻优 —— XS_EXT × XS_PULL 网格

目标（用户口径）：
  - 每个品种每年「建议离场 / 强烈离场」触发 ≥ 3 次（不达 3 次则取最接近且准确率最高的）
  - 触发后 20 根 K 线内发生反向走势（顶→跌、底→涨）的比例最高
  - 允许完全空仓触发（与持仓、与进场零耦合）

做法：
  1) 一次性把 5 品种 10 年日线的逐根事实算好（趋势方向 sd / 伸展 ext / 自极值回撤 pull / 6 条辅助）
  2) 在 EXT∈[2.0,3.0]、PULL∈[0.8,1.5] 上做 0.1 步长网格
  3) 每格统计：触发次数、每品种每年、20 根反转胜率、10 根反转胜率、20 根平均反向幅度(ATR)、
     以及「强烈离场(辅助≥2)」子集的胜率
输出：完整网格表 + 推荐参数 + 推荐参数下的 5 品种明细表
"""
import json
import os

import engine

CACHE = os.path.join("cache", "xs_bars.json")
SYMS = engine.CODES
DEDUPE = 8          # 同一品种同一波只提示一次
FWD = 20            # 评估窗口（根）
YEAR_BARS = 244.0

# 辅助证据阈值（沿用线上，不参与寻优）
AMP, AGE, SHADOW, ADXDROP = engine.XS_AMP, engine.XS_AGE, engine.XS_SHADOW, engine.XS_ADX_DROP


def load_bars():
    if os.path.exists(CACHE):
        d = json.load(open(CACHE, encoding="utf-8"))
        return {k: v for k, v in d.items()}
    d = {}
    for s in SYMS:
        bars = engine._bars_of(s, years=10)
        d[s] = [[b["d"], b["o"], b["h"], b["l"], b["c"], b.get("v") or 0, b.get("oi") or 0]
                for b in bars]
        print("  load %s %d bars" % (s, len(d[s])))
    os.makedirs("cache", exist_ok=True)
    json.dump(d, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return d


def prep(sym, arr):
    bars = [{"d": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5], "oi": r[6]}
            for r in arr]
    S = engine.make_S(bars)
    segs = engine.segments(S)
    n = len(bars)
    sd = [0] * n
    ext_ratio = [0.0] * n
    pull_ratio = [0.0] * n
    aux = [0] * n
    for s, e, d in segs:
        h, l = -1e18, 1e18
        for i in range(s, e + 1):
            b = bars[i]
            h = max(h, b["h"])
            l = min(l, b["l"])
            sd[i] = d
            atr = S["atr"][i] or 1e-9
            m34 = S["ma34"][i]
            c = b["c"]
            ext_ratio[i] = ((c - m34) * d / atr) if m34 else 0.0
            extp = h if d == 1 else l
            pull_ratio[i] = ((c - extp) / atr) if d == 1 else ((extp - c) / atr)
            k = 0
            if (h - l) / atr >= AMP:
                k += 1
            up = (b["h"] - max(b["o"], c)) / atr
            dn = (min(b["o"], c) - b["l"]) / atr
            if (up >= SHADOW) if d == 1 else (dn >= SHADOW):
                k += 1
            if (i - s + 1) >= AGE:
                k += 1
            ax = S["adx"][i]
            hi20 = max([x for x in S["adx"][max(0, i - 19):i + 1] if x is not None] or [0])
            if ax is not None and hi20 - ax >= ADXDROP:
                k += 1
            m18 = S["ma18"][i]
            if m18 and ((c < m18) if d == 1 else (c > m18)):
                k += 1
            oi = b.get("oi") or 0
            oi5 = bars[i - 5].get("oi") or 0 if i >= 5 else 0
            if oi5 and (((oi / oi5 - 1) <= 0) if d == 1 else ((oi / oi5 - 1) >= 0)):
                k += 1
            aux[i] = k
    closes = [b["c"] for b in bars]
    return {"sym": sym, "n": n, "closes": closes, "atr": S["atr"],
            "sd": sd, "ext": ext_ratio, "pull": pull_ratio, "aux": aux,
            "years": n / YEAR_BARS}


def scan(P, ext_th, pull_th):
    """返回该参数下的信号列表 [(i, sd, aux, level)]，dedupe 后"""
    out, last = [], -999
    for i in range(40, P["n"] - 1):
        if P["ext"][i] < ext_th or P["pull"][i] > -pull_th:
            continue
        lv = "strong" if P["aux"][i] >= 2 else ("suggest" if P["aux"][i] >= 1 else "watch")
        if lv == "watch":
            continue
        if i - last < DEDUPE:
            continue
        last = i
        out.append((i, P["sd"][i], P["aux"][i], lv))
    return out


def eval_sig(P, sigs):
    n = P["n"]
    c, atr = P["closes"], P["atr"]
    ok20 = ok10 = 0
    m20 = m10 = 0
    adv, mfe = [], []
    strong_ok = strong_n = 0
    for i, d, a, lv in sigs:
        if i + 10 < n:
            m10 += 1
            ok10 += 1 if (c[i] - c[i + 10]) * d > 0 else 0
        if i + FWD >= n:
            continue
        m20 += 1
        good = (c[i] - c[i + FWD]) * d > 0
        ok20 += 1 if good else 0
        adv.append((c[i] - c[i + FWD]) * d / (atr[i] or 1e-9))
        best = max((c[i] - c[i + j]) * d / (atr[i] or 1e-9) for j in range(1, FWD + 1))
        mfe.append(best)
        if lv == "strong":
            strong_n += 1
            strong_ok += 1 if good else 0
    if not m20:
        return None
    return {"n": len(sigs), "m20": m20,
            "ok20": ok20 / m20 * 100, "ok10": (ok10 / m10 * 100) if m10 else 0,
            "adv": sum(adv) / len(adv), "mfe": sum(mfe) / len(mfe),
            "s_ok": (strong_ok / strong_n * 100) if strong_n else None,
            "s_n": strong_n}


def main():
    raw = load_bars()
    Ps = [prep(s, raw[s]) for s in SYMS]
    print("样本：%s\n" % "  ".join("%s %.1f年" % (p["sym"], p["years"]) for p in Ps))

    rows = []
    for ei in range(20, 31):
        ext_th = ei / 10.0
        for pi in range(8, 16):
            pull_th = pi / 10.0
            tot = {"n": 0, "ok20": 0.0, "ok10": 0.0, "adv": 0.0, "mfe": 0.0,
                   "s_ok": 0.0, "s_n": 0, "m20": 0, "perSym": {}}
            for P in Ps:
                sg = scan(P, ext_th, pull_th)
                st = eval_sig(P, sg)
                if not st:
                    tot["perSym"][P["sym"]] = (0, 0.0, 0.0)
                    continue
                per = len(sg) / P["years"]
                tot["perSym"][P["sym"]] = (len(sg), per, st["ok20"])
                tot["n"] += len(sg)
                tot["m20"] += st["m20"]
                tot["ok20"] += st["ok20"] * st["m20"]
                tot["ok10"] += st["ok10"] * st["m20"]
                tot["adv"] += st["adv"] * st["m20"]
                tot["mfe"] += st["mfe"] * st["m20"]
                if st["s_ok"] is not None:
                    tot["s_ok"] += st["s_ok"] * st["s_n"]
                    tot["s_n"] += st["s_n"]
            if not tot["m20"]:
                continue
            perYear = tot["n"] / sum(p["years"] for p in Ps)
            rows.append({
                "ext": ext_th, "pull": pull_th, "n": tot["n"],
                "perYear": perYear, "minPer": min(v[1] for v in tot["perSym"].values()),
                "ok20": tot["ok20"] / tot["m20"], "ok10": tot["ok10"] / tot["m20"],
                "adv": tot["adv"] / tot["m20"], "mfe": tot["mfe"] / tot["m20"],
                "s_ok": (tot["s_ok"] / tot["s_n"]) if tot["s_n"] else None,
                "perSym": tot["perSym"], "m20": tot["m20"],
            })

    print("== 全网格（按 每品种每年 降序）==")
    print("%5s %5s %5s %7s %7s %7s %7s %8s %7s" %
          ("EXT", "PULL", "次数", "每品种年", "最低品种", "20根胜率", "10根胜率",
           "20根幅度", "强档胜率"))
    for r in sorted(rows, key=lambda x: -x["perYear"]):
        print("%5.1f %5.1f %5d %7.2f %7.2f %6.1f%% %6.1f%% %+7.2f %s" %
              (r["ext"], r["pull"], r["n"], r["perYear"], r["minPer"], r["ok20"], r["ok10"],
               r["adv"], ("%.0f%%" % r["s_ok"]) if r["s_ok"] is not None else "-"))

    # 目标：每品种每年 >=3 次；在此约束下 20 根胜率最高
    cand = [r for r in rows if r["perYear"] >= 3.0 and r["minPer"] >= 1.5]
    if not cand:
        cand = [r for r in rows if r["perYear"] >= 2.5]
    print("\n== 达标候选（每品种每年≥3、单品种≥1.5）按 20 根胜率排序 ==")
    for r in sorted(cand, key=lambda x: (-x["ok20"], -x["adv"]))[:12]:
        print("EXT %.1f  PULL %.1f  次数%3d  每年%.2f  20根胜率%.1f%%  20根幅度%+.2fATR  强档%s" %
              (r["ext"], r["pull"], r["n"], r["perYear"], r["ok20"], r["adv"],
               ("%.0f%%/%d" % (r["s_ok"], 0)) if r["s_ok"] else "-"))

    best = sorted(cand, key=lambda x: (-x["ok20"], -x["adv"]))[0]
    print("\n== 推荐参数：EXT=%.1f  PULL=%.1f ==" % (best["ext"], best["pull"]))
    print("%6s %8s %10s %10s %10s" % ("品种", "触发次数", "每年", "20根胜率", "20根均幅ATR"))
    for sym in SYMS:
        n, per, ok = best["perSym"][sym]
        P = [p for p in Ps if p["sym"] == sym][0]
        st = eval_sig(P, scan(P, best["ext"], best["pull"]))
        print("%6s %8d %9.2f %9.1f%% %+10.2f" %
              (sym, n, per, ok, (st["adv"] if st else 0)))
    print("%6s %8d %9.2f %9.1f%% %+10.2f" %
          ("合计/均", best["n"], best["perYear"], best["ok20"], best["adv"]))
    print("\n样本外切分（2024-01-01 之后为盲测）：")

    # 训练/盲测拆分
    for tag, lo, hi in (("训练 2016-2023", 0, 0.8), ("盲测 2024-2026", 0.8, 1.1)):
        tot_n = tot_m = tot_ok = 0
        for P in Ps:
            cut = int(P["n"] * lo)
            sigs = [s for s in scan(P, best["ext"], best["pull"]) if s[0] >= cut]
            c = P["closes"]
            for i, d, a, lv in sigs:
                if i + FWD >= P["n"]:
                    continue
                tot_n += 1
                tot_ok += 1 if (c[i] - c[i + FWD]) * d > 0 else 0
                tot_m += 1
        print("  %-14s n=%3d  20根胜率=%.1f%%" %
              (tag, tot_n, (tot_ok / tot_m * 100) if tot_m else 0))

    json.dump([{k: v for k, v in r.items() if k != "perSym"} for r in
               sorted(rows, key=lambda x: -x["perYear"])],
              open("cache/xs_grid.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
