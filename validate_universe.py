# -*- coding: utf-8 -*-
"""换池验证（2026-11）：8 品种关注池，沿用线上方法，检验参数是否仍最优。

关注池：FG 玻璃 / SA 纯碱 / M 豆粕 / SP 纸浆 / SM 锰硅 / MA 甲醇 / SR 白糖 / C 玉米
  重点验证（新增）：SA、SP、SM、SR
  保持不变（原有）：FG、M、MA、C

三块检验，进场/离场都走线上 engine 同源计算：
  A. 进场趋势周期 MA（20/25/30/34/40/45/50）—— MA34 是否仍最优
  B. 持仓离场组合（短均线 × 吊灯 K）—— MA26 + 吊灯 3ATR 是否仍最优
  C. 独立出场雷达门槛（EXT 2.0~3.0 × PULL 0.8~1.5 网格）—— 2.6 / 1.1 是否仍最优
每块给：全池汇总、新增品种分品种、训练段(<2024)/盲测段(>=2024)/去极值。

只评估「全局参数」是否该升级；不做逐品种单独调参（会破坏方法一致性，
且纯碱/纸浆上市时间短，逐品种拟合极易过拟合）。
"""
import json
import os
import statistics as st

import build
import cyc
import engine
import mine_xs

UNIV = ["FG", "SA", "M", "SP", "SM", "MA", "SR", "C"]
NEW = ["SA", "SP", "SM", "SR"]
KEEP = ["FG", "M", "MA", "C"]
SPLIT = "2024-01-01"
FWD = 20
YEAR_BARS = 244.0

RAW = {}
PS = {}


# ---------------------------------------------------------------- 基础
def load():
    print("== 样本 ==")
    for c in UNIV:
        adj, _ = build.panama_adjust(c)
        bars = adj[-2440:] if len(adj) > 2440 else adj
        RAW[c] = bars
        print("  %-3s %-4s %4d 根  %s → %s  (%.1f 年)%s"
              % (c, engine.NAMES[c], len(bars), bars[0]["d"], bars[-1]["d"],
                 len(bars) / YEAR_BARS, "  *新增" if c in NEW else ""))


def stat(trs):
    """trs: [{sym,in,R,hold}]"""
    if not trs:
        return None
    rs = [t["R"] for t in trs]
    eq = peak = dd = 0.0
    for t in sorted(trs, key=lambda x: x["in"]):
        eq += t["R"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {"n": len(rs), "win": sum(1 for r in rs if r > 0) / len(rs) * 100,
            "R": sum(rs), "dd": dd, "avg": st.mean(rs),
            "worst": min(rs), "hold": st.median([t.get("hold", 0) for t in trs])}


def split_stat(trs):
    a = [t for t in trs if t["in"] < SPLIT]
    b = [t for t in trs if t["in"] >= SPLIT]
    top = sorted(trs, key=lambda t: -t["R"])[:3]
    rest = [t for t in trs if t not in top]
    return stat(a), stat(b), stat(rest)


def line(tag, s):
    if not s:
        print("  %-22s  —" % tag)
    else:
        print("  %-22s n=%3d 胜率%5.1f%% 净%+7.1fR 回撤%+7.1fR 均%+.2fR 最差%+.1fR"
              % (tag, s["n"], s["win"], s["R"], s["dd"], s["avg"], s["worst"]))


# ---------------------------------------------------------------- 离场模拟
def sim_exit(bars, ma, k, ei, side, atr, entry):
    """与 engine.exit_track 同口径，只算 R / 持仓根数（省去预警等无关计算）。

    armed：先站到仓位一侧，之后反向穿越短均线才离场；吊灯独立无条件生效。
    """
    n = len(bars)
    peak = bars[ei]["h"] if side == 1 else bars[ei]["l"]
    armed = False
    for j in range(ei, n):
        b = bars[j]
        c = b["c"]
        peak = max(peak, b["h"]) if side == 1 else min(peak, b["l"])
        ch = (peak - k * atr) if side == 1 else (peak + k * atr)
        mj = ma[j]
        ma_hit = False
        if mj is not None:
            if not armed:
                if (side == 1 and c > mj) or (side == -1 and c < mj):
                    armed = True
            elif (side == 1 and c < mj) or (side == -1 and c > mj):
                ma_hit = True
        if ma_hit or (c <= ch if side == 1 else c >= ch):
            return {"R": (c - entry) * side / atr, "hold": j - ei}
    return None


def trades(events, ma_key, k, S_by_code):
    out = []
    for e in events:
        c, ei = e["sym"], e["entryIdx"]
        bars = RAW[c]
        if ei >= len(bars):
            continue
        side = 1 if e["side"] == "long" else -1
        r = sim_exit(bars, S_by_code[c][ma_key], k, ei, side, e["atr"], e["entry"])
        if r:
            r.update({"sym": c, "in": bars[ei]["d"]})
            out.append(r)
    return out


def add_mas(S, bars, mas):
    closes = [b["c"] for b in bars]
    for n in mas:
        key = "ma%d" % n
        if key not in S:
            S[key] = cyc.sma(closes, n)


def gen_events(man):
    """给定趋势周期 MA，重算进场事件（4 条特征 + ADX≥20 口径完全不变）"""
    engine.MA = man
    S_by_code, events = {}, []
    for c in UNIV:
        S = engine.make_S(RAW[c])
        add_mas(S, RAW[c], (18, 20, 22, 24, 26, 28, 30, 34))
        S_by_code[c] = S
        sigs, _ = engine.scan_symbol(c, bars=RAW[c], unit="D")
        events += [x for x in sigs if x["kind"] == "pred"]
    return S_by_code, events


# ---------------------------------------------------------------- A 进场 MA
def part_a():
    print("\n" + "=" * 104)
    print("A. 进场趋势周期 MA 网格（进场 4 条 + ADX≥20 不变；离场用线上 MA26+吊灯3）")
    print("=" * 104)
    rows = []
    for man in (20, 25, 30, 34, 40, 45, 50):
        SB, ev = gen_events(man)
        trs = trades(ev, "ma26", 3.0, SB)
        s = stat(trs)
        a, b, d = split_stat(trs)
        nw = stat([t for t in trs if t["sym"] in NEW])
        rows.append((man, s, a, b, d, nw, trs))
        print("  MA%-2d  n=%3d 胜率%5.1f%% 净%+7.1fR 回撤%+7.1fR | 训练%+6.1f 盲测%+6.1f 去极值%+6.1f | 新增4品种 %+6.1fR(%d笔)"
              % (man, s["n"], s["win"], s["R"], s["dd"],
                 (a or {}).get("R", 0), (b or {}).get("R", 0), (d or {}).get("R", 0),
                 (nw or {}).get("R", 0), (nw or {}).get("n", 0)))
    engine.MA = 34
    base = [r for r in rows if r[0] == 34][0]
    print("\n  当前 MA34 分品种：")
    for c in UNIV:
        line(c + ("（新增）" if c in NEW else "（原有）"),
             stat([t for t in base[6] if t["sym"] == c]))
    return rows


# ---------------------------------------------------------------- B 离场组合
def part_b():
    print("\n" + "=" * 104)
    print("B. 持仓离场组合网格（进场完全不动：MA34 + 4 条 + ADX≥20）")
    print("=" * 104)
    engine.MA = 34
    SB, events = gen_events(34)
    print("  进场事件：%d 笔（%s）"
          % (len(events), " ".join("%s:%d" % (c, sum(1 for e in events if e["sym"] == c))
                                   for c in UNIV)))
    rows = []
    for n in (18, 20, 22, 24, 26, 28, 30, 34):
        for k in (2.0, 2.5, 3.0, 3.5, 4.0):
            trs = trades(events, "ma%d" % n, k, SB)
            s = stat(trs)
            a, b, d = split_stat(trs)
            rows.append((n, k, s, a, b, d, trs))
    print("\n  %-6s %5s %7s %8s %8s %9s %9s %9s %8s"
          % ("MA", "吊灯", "胜率", "净R", "回撤R", "训练R", "盲测R", "去极值R", "最差单笔"))
    for n, k, s, a, b, d, trs in sorted(rows, key=lambda r: -r[2]["R"]):
        flag = "   ← 线上" if (n, k) == (26, 3.0) else ""
        print("  MA%-3d %4.1f %6.1f%% %+8.1f %+8.1f %+9.1f %+9.1f %+9.1f %+8.1f%s"
              % (n, k, s["win"], s["R"], s["dd"], (a or {}).get("R", 0),
                 (b or {}).get("R", 0), (d or {}).get("R", 0), s["worst"], flag))
    cur = [r for r in rows if (r[0], r[1]) == (26, 3.0)][0]
    print("\n  线上 MA26+吊灯3 分品种：")
    for c in UNIV:
        line(c + ("（新增）" if c in NEW else "（原有）"),
             stat([t for t in cur[6] if t["sym"] == c]))
    return rows


# ---------------------------------------------------------------- C 出场雷达
def prep_xs():
    engine.MA = 34
    for c in UNIV:
        arr = [[b["d"], b["o"], b["h"], b["l"], b["c"], b.get("v") or 0,
                b.get("oi") or 0] for b in RAW[c]]
        P = mine_xs.prep(c, arr)
        P["dates"] = [r[0] for r in arr]
        PS[c] = P


def xs_summary(ext, pull):
    rows = []
    for c in UNIV:
        P = PS[c]
        for i, d, a, lv in mine_xs.scan(P, ext, pull):
            if i + FWD >= P["n"]:
                continue
            ok = (P["closes"][i] - P["closes"][i + FWD]) * d > 0
            hit1 = abs(P["closes"][i] - P["closes"][i + FWD]) / (P["atr"][i] or 1e-9) >= 1.0
            rows.append({"sym": c, "date": P["dates"][i], "ok": ok,
                         "hit1": hit1, "lv": lv})
    if not rows:
        return None
    n = len(rows)
    years = sum(PS[c]["n"] / YEAR_BARS for c in UNIV)
    blind = [r for r in rows if r["date"] >= SPLIT]
    strong = [r for r in rows if r["lv"] == "strong"]
    per = {}
    for c in UNIV:
        rr = [r for r in rows if r["sym"] == c]
        per[c] = (len(rr), len(rr) / (PS[c]["n"] / YEAR_BARS),
                  (sum(1 for r in rr if r["ok"]) / len(rr) * 100) if rr else 0)
    return {"ext": ext, "pull": pull, "n": n, "perYear": n / years,
            "minPer": min(v[1] for v in per.values()),
            "ok20": sum(1 for r in rows if r["ok"]) / n * 100,
            "hit1": sum(1 for r in rows if r["hit1"]) / n * 100,
            "blindN": len(blind),
            "blindOk": (sum(1 for r in blind if r["ok"]) / len(blind) * 100) if blind else 0,
            "strongOk": (sum(1 for r in strong if r["ok"]) / len(strong) * 100) if strong else 0,
            "per": per, "rows": rows}


def part_c():
    print("\n" + "=" * 104)
    print("C. 独立出场雷达门槛网格（EXT 2.0~3.0 × PULL 0.8~1.5，0.1 步长 88 组）")
    print("=" * 104)
    grid = []
    for ei in range(20, 31):
        for pi in range(8, 16):
            r = xs_summary(ei / 10.0, pi / 10.0)
            if r:
                grid.append(r)
    cand = [r for r in grid if r["perYear"] >= 3.0 and r["minPer"] >= 1.5]
    if not cand:
        cand = [r for r in grid if r["perYear"] >= 2.5]
    print("  达标候选（每品种每年≥3、单品种≥1.5），按 20 根反转胜率排序 前 12：")
    print("  %5s %5s %6s %8s %9s %9s %9s %9s" %
          ("EXT", "PULL", "次数", "每品种年", "20根胜率", "≥1ATR", "强档胜率", "盲测胜率"))
    for r in sorted(cand, key=lambda x: -x["ok20"])[:12]:
        flag = "  ← 线上" if (abs(r["ext"] - 2.6) < 1e-9 and abs(r["pull"] - 1.1) < 1e-9) else ""
        print("  %5.1f %5.1f %6d %8.2f %8.1f%% %8.1f%% %8.1f%% %8.1f%%%s"
              % (r["ext"], r["pull"], r["n"], r["perYear"], r["ok20"],
                 r["hit1"], r["strongOk"], r["blindOk"], flag))
    cur = [r for r in grid if abs(r["ext"] - 2.6) < 1e-9 and abs(r["pull"] - 1.1) < 1e-9][0]
    print("\n  线上 EXT2.6 / PULL1.1 分品种：")
    print("  %-10s %8s %9s %10s %14s" % ("品种", "次数", "每年", "20根胜率", "盲测胜率"))
    for c in UNIV:
        n, per, ok = cur["per"][c]
        bl = [r for r in cur["rows"] if r["sym"] == c and r["date"] >= SPLIT]
        print("  %-10s %8d %9.2f %9.1f%% %14s"
              % (engine.NAMES[c] + ("*" if c in NEW else ""), n, per, ok,
                 ("%.0f%%(n=%d)" % (sum(1 for r in bl if r["ok"]) / len(bl) * 100, len(bl)))
                 if bl else "—"))
    print("  合计 %d 次 / %.2f 次每品种年 / 20根 %.1f%% / ≥1ATR %.1f%% / 强档 %.1f%% / 盲测 %.1f%%(n=%d)"
          % (cur["n"], cur["perYear"], cur["ok20"], cur["hit1"],
             cur["strongOk"], cur["blindOk"], cur["blindN"]))
    os.makedirs("cache", exist_ok=True)
    json.dump([{k: v for k, v in r.items() if k != "rows"} for r in grid],
              open(os.path.join("cache", "xs_grid_8.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return grid, cur


def main():
    load()
    part_a()
    part_b()
    prep_xs()
    part_c()
    print("\n== 口径说明 ==")
    print("  新增重点验证：纯碱 SA / 纸浆 SP / 锰硅 SM / 白糖 SR；原有：玻璃 FG / 豆粕 M / 甲醇 MA / 玉米 C")
    print("  训练 <2024-01-01，盲测 >=2024-01-01；去极值=剔除全场最大 3 笔")


if __name__ == "__main__":
    main()
