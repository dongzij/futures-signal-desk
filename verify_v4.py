# -*- coding: utf-8 -*-
"""v4 上线前自检：用「线上引擎真实发出的信号」重放，核对与研究报告口径是否一致。
（引擎比研究脚本多了两条实盘保护：大周期段过滤 seg_big、段内至少运行 5 根 MIN_AGE）
"""
import json
import statistics as st

import build
import daily
import engine

YEARS = 10
trade = []
for c in engine.CODES:
    adj, _ = build.panama_adjust(c)
    keep = int(YEARS * 244)
    bars = adj[-keep:] if len(adj) > keep else adj
    sigs, _ = engine.scan_symbol(c, bars=bars)
    S = engine.make_S(bars)
    for s in sigs:
        if s["kind"] != "pred":
            continue
        ei = s["entryIdx"]
        if ei >= len(bars):
            continue
        long = s["side"] == "long"
        armed = False
        ex = None
        for j in range(ei, min(ei + engine.MAXHOLD, len(bars))):
            ma = S["ma"][j]
            if ma is None:
                continue
            if not armed:
                if (long and bars[j]["c"] > ma) or ((not long) and bars[j]["c"] < ma):
                    armed = True
                continue
            if (long and bars[j]["c"] < ma) or ((not long) and bars[j]["c"] > ma):
                ex = (bars[j]["c"], bars[j]["d"], j)
                break
        if ex is None:
            ex = (bars[-1]["c"], bars[-1]["d"], len(bars) - 1)
        pts = (ex[0] - s["entry"]) * (1 if long else -1)
        mult = engine.MULT[c]
        trade.append({"code": c, "in": s["date"], "out": ex[1], "pts": pts,
                      "R": pts / s["atr"] if s["atr"] else 0,
                      "yuan": pts * mult - engine.FEE[c] * 2,
                      "bars": ex[2] - ei, "win": pts > 0, "k": s["k"],
                      "adx": s.get("adx")})

n = len(trade)
win = sum(1 for t in trade if t["win"])
eq = peak = dd = 0.0
for t in sorted(trade, key=lambda x: x["in"]):
    eq += t["yuan"]
    peak = max(peak, eq)
    dd = min(dd, eq - peak)
gp = sum(t["yuan"] for t in trade if t["yuan"] > 0)
gl = -sum(t["yuan"] for t in trade if t["yuan"] <= 0)
print("线上引擎口径：n=%d 胜率%.1f%% 盈亏比%.2f 平均%+.2fR 累计%+.0f元 回撤%.0f元 年均%+.0f元 持%d根" %
      (n, win / n * 100, (gp / win) / (gl / max(1, n - win)), st.mean([t["R"] for t in trade]),
       eq, dd, eq / 10, st.median([t["bars"] for t in trade])))
print("盈利年份:", end=" ")
for y in sorted({t["in"][:4] for t in trade}):
    v = sum(t["yuan"] for t in trade if t["in"][:4] == y)
    print("%s %+.0f" % (y, v), end="  ")
print()
for c in engine.CODES:
    sub = [t for t in trade if t["code"] == c]
    if sub:
        w = sum(1 for t in sub if t["win"])
        print("  %-3s n=%2d 胜率%3.0f%% 累计%+7.0f元" %
              (c, len(sub), w / len(sub) * 100, sum(t["yuan"] for t in sub)))
json.dump({"n": n, "win": round(win / n, 4), "yuan": round(eq), "dd": round(dd),
           "per_year": round(eq / 10), "avgR": round(st.mean([t["R"] for t in trade]), 3),
           "hold": st.median([t["bars"] for t in trade]),
           "payoff": round((gp / win) / (gl / max(1, n - win)), 2)},
          open("v4_engine_stat.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
