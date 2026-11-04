# -*- coding: utf-8 -*-
"""导出「点数 → 元」测算数据，供报告页使用。"""
import json
import statistics as st
from collections import defaultdict

import money

CAP = 100_000.0
SMALL = ["FG", "M", "C", "RB", "TA", "SA", "SR"]
ts = money.all_trades(20, 0)
ts2 = [t for t in ts if t["sym"] in SMALL]


def q(a, p):
    a = sorted(a)
    return a[min(len(a) - 1, int(len(a) * p))]


def block(lst, lots=1):
    y = defaultdict(lambda: [0, 0.0, 0.0])
    for t in lst:
        r = y[t["year"]]
        r[0] += 1
        r[1] += t["pts"]
        r[2] += t["pts"] * t["mult"] * lots
    eq = peak = mdd = 0.0
    for t in sorted(lst, key=lambda x: x["entryDate"]):
        eq += t["pts"] * t["mult"] * lots
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    tot = sum(v[2] for v in y.values())
    return {
        "n": len(lst), "lots": lots,
        "pts": round(sum(t["pts"] for t in lst), 0),
        "total": round(tot, 0), "annual": round(tot / 10, 0),
        "yield": round(tot / 10 / CAP * 100, 1),
        "mdd": round(mdd, 0),
        "win": round(sum(1 for t in lst if t["pts"] * t["mult"] > 0) / len(lst) * 100, 1),
        "perTrade": round(tot / len(lst), 0),
        "perTradePts": round(st.mean(t["pts"] for t in lst), 1),
        "years": {str(k): {"n": v[0], "pts": round(v[1], 0), "amt": round(v[2], 0)}
                  for k, v in sorted(y.items())},
    }


rows = [block(ts2, l) for l in (1, 2, 3, 4, 5)]
rows = [r | {"margin": round(max(t["margin1"] for t in ts2) * r["lots"], 0)} for r in rows]

# 品种明细
syms = []
for c in money.CODES:
    g = [t for t in ts if t["sym"] == c]
    if not g:
        continue
    syms.append({
        "code": c, "name": money.NAMES[c], "mult": g[0]["mult"],
        "price": g[-1]["entry"], "contract": round(g[-1]["entry"] * g[-1]["mult"], 0),
        "margin": round(max(t["margin1"] for t in g), 0),
        "n": len(g), "pts": round(sum(t["pts"] for t in g), 0),
        "money": round(sum(t["pts"] * t["mult"] for t in g), 0),
        "avgPts": round(st.mean(t["pts"] for t in g), 1),
        "win": round(sum(1 for t in g if t["pts"] * t["mult"] > 0) / len(g) * 100, 1),
        "worst": round(min(t["pts"] * t["mult"] for t in g), 0),
        "fit": max(t["margin1"] for t in g) <= 10000,
    })

# 每笔点数分布（小合约）
amts = [t["pts"] * t["mult"] for t in ts2]
pts = [t["pts"] for t in ts2]
dist = []
for lo, hi in ((-99999, -3000), (-3000, -1000), (-1000, -300), (-300, 0),
               (0, 300), (300, 1000), (1000, 3000), (3000, 99999)):
    g = [a for a in amts if lo <= a < hi]
    dist.append({"lo": lo, "hi": hi, "n": len(g)})

# 20 元/点 口径 vs 真实口径
wrong = sum(t["pts"] for t in ts) * 20
right = sum(t["pts"] * t["mult"] for t in ts)
wrong_small = sum(t["pts"] for t in ts2) * 20

# 单笔最大亏损 / 连亏
losses = [t for t in ts2 if t["pts"] * t["mult"] < 0]
streak = best = 0
for t in sorted(ts2, key=lambda x: x["entryDate"]):
    streak = streak + 1 if t["pts"] * t["mult"] < 0 else 0
    best = max(best, streak)

out = {
    "cap": CAP, "rows": rows, "syms": syms, "dist": dist,
    "cmp": {"wrongAll": round(wrong, 0), "rightAll": round(right, 0),
            "wrongSmall": round(wrong_small, 0),
            "rightSmall": round(sum(t["pts"] * t["mult"] for t in ts2), 0)},
    "stats": {
        "n": len(ts2), "perYear": round(len(ts2) / 10, 1),
        "ptsMean": round(st.mean(pts), 1), "ptsMedian": round(q(pts, .5), 1),
        "ptsP10": round(q(pts, .1), 0), "ptsP90": round(q(pts, .9), 0),
        "ptsMin": round(min(pts), 0), "ptsMax": round(max(pts), 0),
        "amtMean": round(st.mean(amts), 0), "amtMedian": round(q(amts, .5), 0),
        "amtP10": round(q(amts, .1), 0), "amtP90": round(q(amts, .9), 0),
        "amtMin": round(min(amts), 0), "amtMax": round(max(amts), 0),
        "worstStreak": best,
        "nLoss": len(losses),
        "avgLoss": round(st.mean(t["pts"] * t["mult"] for t in losses), 0),
        "avgWin": round(st.mean(t["pts"] * t["mult"] for t in ts2
                                if t["pts"] * t["mult"] > 0), 0),
    },
}
json.dump(out, open("../points_data.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("rows", [(r["lots"], r["annual"], r["yield"], r["mdd"]) for r in rows])
print("cmp", out["cmp"])
print("stats", out["stats"])
