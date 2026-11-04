# -*- coding: utf-8 -*-
"""按「点数 → 元」实算，10 万本金一年到底赚多少。

三条口径：
  A. 统一 20 元/点（用户口径，最直观）
  B. 各品种真实点值（FG 20 / SC 1000 / I 100 / RB 10 ...）
信号：日线 w=3 确认、3ATR 止损、持有 20 日，与回测同一条路径。
"""
import json
import statistics as st
from collections import defaultdict

import cyc
import engine

MULT = engine.MULT
CODES = engine.CODES
NAMES = engine.NAMES
YEAR_NO = 10.0
CAPITAL = 100_000.0
MARGIN_RATE = 0.12
POINT_UNIT = 20.0     # 用户口径：每点 20 元
_BARS, _SIG = {}, {}


def bars_of(c, years=YEAR_NO):
    k = (c, years)
    if k not in _BARS:
        import build
        adj, _ = build.panama_adjust(c)
        keep = int(years * 244)
        _BARS[k] = adj[-keep:] if len(adj) > keep else adj
    return _BARS[k]


def sigs_of(c, years=YEAR_NO):
    k = (c, years)
    if k not in _SIG:
        s, _ = engine.scan_symbol(c, bars=bars_of(c, years))
        _SIG[k] = s
    return _SIG[k]


TIER_RANK = {"狙击": 0, "重点": 1, "观察": 2}


def walk(c, hold=20, stop_atr=3.0, max_tier=0, years=YEAR_NO):
    bars, sigs = bars_of(c, years), sigs_of(c, years)
    n = len(bars)
    atr = engine.make_S(bars)["a"]
    by_conf = {}
    for s in sorted(sigs, key=lambda x: x["confIdx"]):
        if TIER_RANK[s["tier"]] <= max_tier:
            by_conf.setdefault(s["confIdx"], s)
    mult = MULT.get(c, 10)
    out, pos = [], None
    for j in range(131, n - 1):
        b = bars[j]
        if pos:
            side, stop = pos["side"], pos["stop"]
            ex = why = None
            xi = j
            if (b["l"] <= stop) if side == "long" else (b["h"] >= stop):
                ex = min(stop, b["o"]) if side == "long" else max(stop, b["o"])
                why = "止损"
            elif j - pos["ei"] >= hold - 1:
                ex, why = b["c"], "到期"
            if ex is not None:
                d = 1 if side == "long" else -1
                gross = (ex - pos["entry"]) * d
                cost = pos["entry"] * 0.0008
                out.append({
                    "sym": c, "name": NAMES.get(c, c), "side": side, "id": pos["id"],
                    "tier": pos["tier"], "kind": pos["kind"],
                    "entryDate": pos["entryDate"], "exitDate": bars[xi]["d"],
                    "entry": round(pos["entry"], 1), "exit": round(ex, 1),
                    "grossPts": round(gross, 1), "pts": round(gross - cost, 1),
                    "mult": mult, "money": round((gross - cost) * mult, 0),
                    "why": why, "bars": xi - pos["ei"],
                    "year": int(pos["entryDate"][:4]),
                    "margin1": round(pos["entry"] * mult * MARGIN_RATE, 0),
                })
                pos = None
            if pos:
                continue
        s = by_conf.get(j)
        if not s:
            continue
        ei = j + 1
        entry = bars[ei]["o"] or b["c"]
        a = atr[j] or entry * 0.01
        stop = entry - stop_atr * a if s["side"] == "long" else entry + stop_atr * a
        pos = {"sym": c, "side": s["side"], "id": s["id"], "tier": s["tier"],
               "kind": s["kind"], "entry": entry, "ei": ei, "stop": stop,
               "entryDate": bars[ei]["d"]}
    return out


def all_trades(hold=20, max_tier=0, codes=None):
    ts = []
    for c in (codes or CODES):
        ts += walk(c, hold=hold, max_tier=max_tier)
    ts.sort(key=lambda t: t["entryDate"])
    return ts


def yearly(ts, key="pts"):
    d = defaultdict(list)
    for t in ts:
        d[t["year"]].append(t[key])
    return dict(sorted(d.items()))


def show(ts, title, unit=POINT_UNIT, lots=1):
    tot_pts = sum(t["pts"] for t in ts)
    tot_amt = tot_pts * unit * lots
    win = sum(1 for t in ts if t["pts"] > 0) / len(ts) * 100
    print("\n### %s" % title)
    print("  笔数 %d（%.1f 笔/年）  平均每笔 %+.1f 点  胜率 %.1f%%"
          % (len(ts), len(ts) / YEAR_NO, st.mean(t["pts"] for t in ts), win))
    print("  十年净点数 %+.0f 点 → %.0f 元/点 × %d 手 = %+.0f 元"
          % (tot_pts, unit, lots, tot_amt))
    print("  年均 %+.0f 元（本金 10 万，%.1f%%/年）"
          % (tot_amt / YEAR_NO, tot_amt / YEAR_NO / CAPITAL * 100))
    ys = yearly(ts)
    print("  年份  笔数     点数       金额")
    for y, v in ys.items():
        print("   %d  %2d  %+8.0f  %+9.0f" % (y, len(v), sum(v), sum(v) * unit * lots))
    amts = {y: sum(v) * unit * lots for y, v in ys.items()}
    pos_y = sum(1 for v in amts.values() if v > 0)
    print("  赚钱 %d 年 / 亏钱 %d 年  最差年 %+.0f  最好年 %+.0f"
          % (pos_y, len(amts) - pos_y, min(amts.values()), max(amts.values())))
    return tot_amt


if __name__ == "__main__":
    ts = all_trades(hold=20, max_tier=0)
    print("狙击层 %d 笔" % len(ts))
    show(ts, "A. 统一 20 元/点 · 每次 1 手", unit=20, lots=1)
    print("\n--- B. 各品种真实点值 vs 统一口径 ---")
    for c in CODES:
        g = [t for t in ts if t["sym"] == c]
        if not g:
            continue
        real = sum(t["pts"] * t["mult"] for t in g)
        print("  %s %-4s %2d笔  真实 %+9.0f 元 | 20元/点 %+8.0f 元 | 1手保证金 %5.0f"
              % (c, NAMES[c], len(g), real, sum(t["pts"] for t in g) * 20,
                 g[-1]["margin1"]))
    print("  合计真实 %+.0f 元" % sum(t["pts"] * t["mult"] for t in ts))
