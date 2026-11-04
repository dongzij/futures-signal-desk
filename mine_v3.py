# -*- coding: utf-8 -*-
"""反转预测方法研究 v3
=====================================================================
目标（严格按需求）：
  Q1 反转点之前多少根 K 线开始出现共性变化（窗口由数据决定）
  Q2 哪些指标在「日线+周线」都有效、触发次数够（弱指标淘汰）
  Q3 区间累计指标（5/10 日合计）是否强于单日指标
  Q4 命中 k 条 → 未来 H 根内反转的概率（标定表）
  Q5 命中阈值时的提前量分布（能否提前 1~2 天提醒）
  Q6 组合搜索：在候选指标里找准确率最高的 1~N 条组合
所有特征只用当根及以前数据；标签用未来 MA34 穿越（无前视）。
"""
import itertools
import json
import statistics as st

import build
import engine

CODES = engine.CODES
SPLIT = "2024-01-01"
YEARS = 8


# ---------------------------------------------------------------- 指标底层
def rsi(closes, n=14):
    out = [None] * len(closes)
    ag = al = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0.0), max(-d, 0.0)
        if i <= n:
            ag += g / n
            al += l / n
            if i == n:
                out[i] = 100 - 100 / (1 + (ag / al if al else 99))
        else:
            ag = (ag * (n - 1) + g) / n
            al = (al * (n - 1) + l) / n
            out[i] = 100 - 100 / (1 + (ag / al if al else 99))
    return out


def ema(vals, n):
    out, k, prev = [], 2.0 / (n + 1), None
    for v in vals:
        prev = v if prev is None else prev + k * (v - prev)
        out.append(prev)
    return out


def macd_hist(closes):
    e12, e26 = ema(closes, 12), ema(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    return [(a - b) * 2 for a, b in zip(dif, dea)]


def prep(bars):
    S = engine.make_S(bars)
    closes = [b["c"] for b in bars]
    n = len(bars)
    segs = engine.segments(S)
    side = [None] * n
    lead = [None] * n
    for si, (s, e, sd) in enumerate(segs):
        nxt = segs[si + 1][0] if si + 1 < len(segs) else None
        for i in range(s, e + 1):
            side[i] = sd
            lead[i] = (nxt - i) if nxt is not None else None
    hi = [None] * n
    lo = [None] * n
    for s, e, sd in segs:
        h, l = -1e18, 1e18
        for i in range(s, e + 1):
            h, l = max(h, bars[i]["h"]), min(l, bars[i]["l"])
            hi[i], lo[i] = h, l
    return {"bars": bars, "S": S, "segs": segs, "hist": macd_hist(closes),
            "rsi": rsi(closes), "side": side, "lead": lead, "hi": hi, "lo": lo, "n": n}


def feats(P, i):
    b, S, bars = P["bars"][i], P["S"], P["bars"]
    atr = S["atr"][i] or 1e-9
    c, sd = b["c"], P["side"][i]
    f = {}
    f["d34"] = (c - S["ma34"][i]) / atr if S["ma34"][i] else None
    f["d10"] = (c - S["ma10"][i]) / atr if S["ma10"][i] else None
    f["ret1"] = (c - bars[i - 1]["c"]) / atr
    f["rng1"] = (b["h"] - b["l"]) / atr
    f["cpos"] = (c - b["l"]) / (b["h"] - b["l"]) if b["h"] > b["l"] else 0.5
    for w in (3, 5, 10):
        if i - w >= 0:
            f["ret%d" % w] = (c - bars[i - w]["c"]) / atr
    f["pull"] = (c - P["hi"][i]) / atr if sd == 1 else (P["lo"][i] - c) / atr
    k, j = 0, i
    while j > 0 and ((sd == 1 and bars[j]["c"] < bars[j - 1]["c"]) or
                     (sd == -1 and bars[j]["c"] > bars[j - 1]["c"])):
        k += 1
        j -= 1
    f["streak"] = k
    vma = S["vma20"][i]
    if vma:
        f["vr1"] = b["v"] / vma
        cand5 = [(bars[j2]["v"] / S["vma20"][j2]) for j2 in range(max(0, i - 4), i + 1)
                 if S["vma20"][j2]]
        cand10 = [(bars[j2]["v"] / S["vma20"][j2]) for j2 in range(max(0, i - 9), i + 1)
                  if S["vma20"][j2]]
        f["vr5"] = max(cand5) if cand5 else None
        f["vr10"] = max(cand10) if cand10 else None
        if i - 4 >= 0:
            f["vsum5"] = sum(bars[j2]["v"] for j2 in range(i - 4, i + 1)) / (vma * 5)
        if i - 9 >= 0:
            f["vsum10"] = sum(bars[j2]["v"] for j2 in range(i - 9, i + 1)) / (vma * 10)
    oi = b.get("oi") or 0
    for w in (1, 5, 10):
        if i - w >= 0:
            o0 = bars[i - w].get("oi") or 0
            f["doi%d" % w] = (oi - o0) / o0 * 100 if oi and o0 else None
    a20 = [S["atr"][j2] for j2 in range(max(0, i - 19), i + 1) if S["atr"][j2]]
    if a20 and S["atr"][i]:
        f["atr_exp"] = S["atr"][i] / (sum(a20) / len(a20))
    if S["ma10"][i] and i - 5 >= 0 and S["ma10"][i - 5]:
        f["ma10_sl"] = (S["ma10"][i] - S["ma10"][i - 5]) / atr
    if P["rsi"][i] is not None:
        f["rsi"] = P["rsi"][i]
    if P["hist"][i] is not None:
        f["macd"] = P["hist"][i] / atr
    return f


DIR = {"d34", "d10", "ret1", "ret3", "ret5", "ret10", "pull", "streak",
       "ma10_sl", "macd", "cpos"}


def adj(name, v, side):
    if v is None:
        return None
    if name == "rsi":
        v = v - 50.0
    if side == -1 and name in DIR:
        v = -v
    return v


CONDS = [
    ("C1",  "d10",      "<=", 0.0,  "收盘已破/站上 MA10"),
    ("C2",  "d10",      "<=", -0.5, "破/站上 MA10 达 0.5 ATR"),
    ("C3",  "d34",      "<=", 1.2,  "距 MA34 仅 1.2 ATR"),
    ("C4",  "pull",     "<=", -1.5, "自段极值回撤/反弹 ≥1.5 ATR"),
    ("C5",  "ret3",     "<=", 0.0,  "3 日累计已反向"),
    ("C6",  "ret5",     "<=", 0.0,  "5 日累计已反向"),
    ("C7",  "ret10",    "<=", 0.0,  "10 日累计已反向"),
    ("C8",  "streak",   ">=", 2,    "连续 ≥2 根反向"),
    ("C9",  "doi5",     "<=", -2.0, "持仓 5 日降 ≥2%"),
    ("C10", "doi5",     "<=", -3.0, "持仓 5 日降 ≥3%"),
    ("C11", "doi10",    "<=", -3.0, "持仓 10 日降 ≥3%"),
    ("C12", "vr5",      ">=", 1.4,  "近 5 日放量 ≥1.4×"),
    ("C13", "vsum5",    ">=", 1.2,  "5 日累计量 ≥1.2×"),
    ("C14", "vr10",     ">=", 1.6,  "近 10 日爆量 ≥1.6×"),
    ("C15", "atr_exp",  ">=", 1.15, "ATR 放大 ≥15%"),
    ("C16", "ma10_sl",  "<=", 0.0,  "MA10 斜率转平/负"),
    ("C17", "macd",     "<=", 0.0,  "MACD 柱翻向"),
    ("C18", "rsi",      "<=", -5,   "RSI 回落/回升过 50"),
    ("C19", "cpos",     "<=", 0.35, "收盘在当日区间下/上 1/3"),
    ("C20", "rng1",     ">=", 1.5,  "单日振幅 ≥1.5 ATR"),
]


def ok(row, cond):
    nm, op, th = cond[1], cond[2], cond[3]
    v = row.get(nm)
    if v is None:
        return False
    return v <= th if op == "<=" else v >= th


def rows_of(bars, sym, min_i=60):
    P = prep(bars)
    S = P["S"]
    out = []
    for i in range(min_i, P["n"]):
        if S["ma34"][i] is None or P["side"][i] is None:
            continue
        f = feats(P, i)
        r = {"sym": sym, "date": bars[i]["d"][:10], "i": i,
             "side": P["side"][i], "lead": P["lead"][i]}
        for nm, v in f.items():
            r[nm] = adj(nm, v, P["side"][i])
        out.append(r)
    return out, P


def load(unit="D"):
    all_rows, prep_map = [], {}
    for code in CODES:
        adjb, _ = build.panama_adjust(code)
        keep = int(YEARS * 244)
        bars = adjb[-keep:] if len(adjb) > keep else adjb
        if len(bars) < 300:
            continue
        use = engine.resample(bars, unit) if unit in ("W", "M") else bars
        rows, P = rows_of(use, code)
        prep_map[code] = P
        all_rows.extend(rows)
    return all_rows, prep_map


def lab(r, H):
    return r["lead"] is not None and r["lead"] <= H


def base_rate(rows, H):
    v = [r for r in rows if r["lead"] is not None]
    return sum(lab(r, H) for r in v) / len(v) if v else 0


def eval_cond(rows, cond, H):
    hit = [r for r in rows if ok(r, cond)]
    if len(hit) < 60:
        return None
    base = base_rate(rows, H)
    p = sum(lab(r, H) for r in hit) / len(hit)
    tr = [r for r in hit if r["date"] < SPLIT]
    te = [r for r in hit if r["date"] >= SPLIT]
    btr = base_rate([r for r in rows if r["date"] < SPLIT], H)
    bte = base_rate([r for r in rows if r["date"] >= SPLIT], H)
    ltr = (sum(lab(r, H) for r in tr) / len(tr) / btr) if len(tr) >= 25 and btr else None
    lte = (sum(lab(r, H) for r in te) / len(te) / bte) if len(te) >= 25 and bte else None
    # 分品种稳定性
    good = 0
    for code in CODES:
        sub = [r for r in hit if r["sym"] == code]
        pool = [r for r in rows if r["sym"] == code]
        if len(sub) >= 8:
            bp = base_rate(pool, H)
            pp = sum(lab(r, H) for r in sub) / len(sub)
            if bp and pp / bp >= 1.25:
                good += 1
    return {"cond": cond, "n": len(hit), "cov": len(hit) / len(rows),
            "p": p, "base": base, "lift": p / base if base else 0,
            "ltr": ltr, "lte": lte, "good": good,
            "pts": len({r["sym"] for r in hit})}


def event_study(prep_map):
    """事件研究：对每个穿越点回看 offset=-20..-1，统计候选条件命中率。"""
    OFF = [-20, -15, -10, -7, -5, -3, -2, -1]
    acc = {c[0]: {o: [0, 0] for o in OFF} for c in CONDS}
    base = {c[0]: [0, 0] for c in CONDS}
    events = 0
    for code, P in prep_map.items():
        bars = P["bars"]
        for si in range(1, len(P["segs"])):
            c = P["segs"][si][0]          # 穿越（反转）所在根
            pre_side = P["segs"][si - 1][2]
            pre_side = P["segs"][si - 1][2]
            events += 1
            for o in OFF:
                i = c + o
                if i < 60 or i >= P["n"]:
                    continue
                if P["side"][i] != pre_side:
                    continue
                f = feats(P, i)
                r = {nm: adj(nm, v, pre_side) for nm, v in f.items()}
                for cond in CONDS:
                    acc[cond[0]][o][1] += 1
                    if ok(r, cond):
                        acc[cond[0]][o][0] += 1
    return events, acc


def combo_search(rows, pool, H, max_k=4, min_n=40):
    """在候选池里搜索准确率最高的组合（以 min(训练lift,盲测lift) 为主排序）。

    用位掩码预计算，避免逐组合逐行重复判断条件。
    """
    idx = {c[0]: c for c in pool}
    names = [c[0] for c in pool]
    pos = {n: 1 << i for i, n in enumerate(names)}
    # 每行的条件命中掩码 + 标签
    masks, labs, istr = [], [], []
    for r in rows:
        m = 0
        for n in names:
            if ok(r, idx[n]):
                m |= pos[n]
        masks.append(m)
        labs.append(1 if lab(r, H) else 0)
        istr.append(r["date"] < SPLIT)
    n_all = len(rows)
    n_tr = sum(istr)
    n_te = n_all - n_tr
    base = sum(labs) / n_all if n_all else 0
    btr = (sum(l for l, t in zip(labs, istr) if t) / n_tr) if n_tr else 0
    bte = (sum(l for l, t in zip(labs, istr) if not t) / n_te) if n_te else 0

    best = []
    for k in range(1, max_k + 1):
        for combo in itertools.combinations(names, k):
            cm = 0
            for n in combo:
                cm |= pos[n]
            cnt = hit = ctr = htr = cte = hte = 0
            for m, l, t in zip(masks, labs, istr):
                if (m & cm) == cm:
                    cnt += 1
                    hit += l
                    if t:
                        ctr += 1
                        htr += l
                    else:
                        cte += 1
                        hte += l
            if cnt < min_n or ctr < 15 or cte < 15:
                continue
            p = hit / cnt
            ltr = (htr / ctr / btr) if btr else 0
            lte = (hte / cte / bte) if bte else 0
            best.append({"combo": combo, "n": cnt, "p": p, "base": base,
                         "lift": p / base if base else 0, "ltr": ltr, "lte": lte,
                         "minlift": min(ltr, lte), "sym": 0,
                         "ptr": htr / ctr, "pte": hte / cte})
    best.sort(key=lambda x: (-x["minlift"], -x["n"]))
    return best


def calibration(rows, combo, H):
    """命中 k 条 → 概率表（训练/盲测分开）。"""
    idx = {c[0]: c for c in CONDS}
    cs = [idx[n] for n in combo]
    tab = {}
    for r in rows:
        k = sum(1 for c in cs if ok(r, c))
        d = tab.setdefault(k, {"n": 0, "hit": 0, "trn": 0, "trh": 0, "ten": 0, "teh": 0})
        d["n"] += 1
        d["hit"] += 1 if lab(r, H) else 0
        if r["date"] < SPLIT:
            d["trn"] += 1
            d["trh"] += 1 if lab(r, H) else 0
        else:
            d["ten"] += 1
            d["teh"] += 1 if lab(r, H) else 0
    return tab


def leads(rows, combo, H, conds=None):
    """命中组合且最终发生反转的样本，统计「提前几根」的分布。"""
    src = conds if conds is not None else CONDS
    idx = {c[0]: c for c in src}
    cs = [idx[n] for n in combo]
    out = []
    for r in rows:
        if all(ok(r, c) for c in cs) and r["lead"] is not None and r["lead"] <= H:
            out.append(r["lead"])
    return sorted(out)


def main():
    print("加载日线数据...")
    rows_d, prep_d = load("D")
    rows_w, prep_w = load("W")
    print("日线样本 %d 条，周线样本 %d 条" % (len(rows_d), len(rows_w)))

    for H in (3, 5, 10):
        print("\n日线基准：未来 %d 根内反转概率 = %.1f%%" % (H, base_rate(rows_d, H) * 100))

    # ---------------- Q1 事件研究 ----------------
    events, acc = event_study(prep_d)
    print("\n===== Q1 事件研究：%d 个反转点，各条件在反转前的命中率 =====" % events)
    OFF = [-20, -15, -10, -7, -5, -3, -2, -1]
    allm = {c[0]: [0, 0] for c in CONDS}
    for code, P in prep_d.items():
        for i in range(60, P["n"]):
            if P["side"][i] is None:
                continue
            f = feats(P, i)
            r = {nm: adj(nm, v, P["side"][i]) for nm, v in f.items()}
            for c in CONDS:
                allm[c[0]][1] += 1
                allm[c[0]][0] += 1 if ok(r, c) else 0
    print("%-4s %-26s %6s %s" % ("条件", "含义", "基准", " ".join("%5d" % o for o in OFF)))
    for c in CONDS:
        basep = allm[c[0]][0] / allm[c[0]][1] * 100 if allm[c[0]][1] else 0
        cells = []
        for o in OFF:
            h, t = acc[c[0]][o]
            cells.append("%5.0f" % (h / t * 100) if t >= 20 else "    .")
        print("%-4s %-26s %5.0f%% %s" % (c[0], c[3], basep, " ".join(cells)))

    # ---------------- Q2 单指标筛选（日线） ----------------
    H = 5
    print("\n===== Q2 单指标筛选（日线，H=%d）=====" % H)
    print("%-4s %-26s %5s %6s %6s %6s %6s %5s" %
          ("条件", "含义", "覆盖", "命中率", "lift", "训练", "盲测", "品种"))
    ev = []
    for c in CONDS:
        e = eval_cond(rows_d, c, H)
        if not e:
            print("%-4s %-26s 样本不足(<60)" % (c[0], c[3]))
            continue
        ev.append(e)
        print("%-4s %-26s %4.1f%% %5.1f%% %6.2f %6s %6s %3d/10" %
              (c[0], c[3], e["cov"] * 100, e["p"] * 100, e["lift"],
               ("%.2f" % e["ltr"]) if e["ltr"] else "—",
               ("%.2f" % e["lte"]) if e["lte"] else "—", e["good"]))

    # 淘汰规则：覆盖率 3%~60%、两段 lift≥1.3、品种≥5
    keep = []
    for e in ev:
        if not (0.03 <= e["cov"] <= 0.60):
            continue
        if (e["ltr"] or 0) < 1.3 or (e["lte"] or 0) < 1.3:
            continue
        if e["good"] < 5:
            continue
        keep.append(e["cond"])
    print("\n通过筛选的指标（%d 个）：%s" % (len(keep), "、".join(c[0] for c in keep)))

    # 周线稳健性
    print("\n周线稳健性（H=3 周）：")
    for c in keep:
        ew = eval_cond(rows_w, c, 3)
        if ew:
            print("  %-4s %-24s 覆盖%4.1f%% lift%5.2f 训练%5s 盲测%5s" %
                  (c[0], c[3], ew["cov"] * 100, ew["lift"],
                   ("%.2f" % ew["ltr"]) if ew["ltr"] else "—",
                   ("%.2f" % ew["lte"]) if ew["lte"] else "—"))
        else:
            print("  %-4s %-24s 周线样本不足" % (c[0], c[3]))

    # ---------------- Q6 组合搜索 ----------------
    print("\n===== Q6 组合搜索（日线 H=%d，最多 4 条）=====" % H)
    combos = combo_search(rows_d, keep, H, 4)
    for b in combos[:12]:
        print("  %-28s n=%4d 覆盖%4.1f%% 反转率%5.1f%% lift%5.2f | 训练%5.2f 盲测%5.2f" %
              ("+".join(b["combo"]), b["n"], b["n"] / len(rows_d) * 100, b["p"] * 100,
               b["lift"], b["ltr"], b["lte"]))
    if not combos:
        print("  无满足样本量的组合")
        return
    best = combos[0]["combo"]
    print("\n最佳组合：%s" % "+".join(best))

    # ---------------- Q4 概率标定 ----------------
    print("\n===== Q4 概率标定（组合命中 k 条 → 未来 %d 根内反转概率）=====" % H)
    tab = calibration(rows_d, best, H)
    print("%3s %6s %8s %7s %8s" % ("k", "样本", "反转率", "训练", "盲测"))
    for k in sorted(tab):
        d = tab[k]
        if d["n"] < 20:
            continue
        print("%3d %6d %7.1f%% %6s %7s" %
              (k, d["n"], d["hit"] / d["n"] * 100,
               ("%.1f%%" % (d["trh"] / d["trn"] * 100)) if d["trn"] >= 10 else "—",
               ("%.1f%%" % (d["teh"] / d["ten"] * 100)) if d["ten"] >= 10 else "—"))

    # ---------------- Q5 提前量 ----------------
    ld = leads(rows_d, best, H)
    if ld:
        print("\n===== Q5 提前量（全组合命中且最终反转）=====")
        print("  样本 %d，中位 %d 根，25%%分位 %d，75%%分位 %d，≤3 根占 %.0f%%，≤5 根占 %.0f%%" %
              (len(ld), st.median(ld), ld[len(ld) // 4], ld[len(ld) * 3 // 4],
               sum(1 for x in ld if x <= 3) / len(ld) * 100,
               sum(1 for x in ld if x <= 5) / len(ld) * 100))

    model = {
        "H": H,
        "conds": [{"k": c[0], "f": c[1], "op": c[2], "th": c[3], "t": c[4]} for c in CONDS
                  if c[0] in best],
        "calib": {str(k): round(v["hit"] / v["n"], 4) for k, v in tab.items() if v["n"] >= 20},
        "base": round(base_rate(rows_d, H), 4),
        "combo_n": len(best),
        "combo": list(best),
    }
    json.dump(model, open("model_v3.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n已保存 model_v3.json")


if __name__ == "__main__":
    main()
