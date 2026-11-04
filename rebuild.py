# -*- coding: utf-8 -*-
"""用无前视口径重建方案矩阵，输出 bigcycle_data.json（覆盖旧版）。"""
import json
import random
import statistics as st
from collections import defaultdict

import bc_clean
import engine

YEARS = bc_clean.YEARS
INIT = bc_clean.INIT_EQ
RISK = bc_clean.RISK
_SC = {}


def sigs_of(unit, w, tier=0):
    k = (unit, w, tier)
    if k not in _SC:
        _SC[k] = bc_clean.all_sigs(unit, w, tier)
    return _SC[k]


def boot(vals, B=3000):
    if len(vals) < 3:
        return [None, None]
    random.seed(7)
    n = len(vals)
    m = sorted(sum(random.choice(vals) for _ in range(n)) / n for _ in range(B))
    return [round(m[int(B * 0.025)], 3), round(m[int(B * 0.975)], 3)]


def cap_year(ts, cap=10):
    ts = sorted(ts, key=lambda t: t["entryDate"])
    cnt = defaultdict(int)
    keep = []
    for t in ts:
        y = int(t["entryDate"][:4])
        if cnt[y] >= cap:
            continue
        cnt[y] += 1
        keep.append(t)
    return keep


def pack(tag, ts, note=""):
    ts = sorted(ts, key=lambda t: t["exitDate"])
    eq = INIT
    peak, mdd = eq, 0.0
    curve = []
    for t in ts:
        eq += INIT * RISK * t["R"]
        t["pnl"] = round(INIT * RISK * t["R"], 0)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        curve.append([t["exitDate"], round(eq, 0)])
    R = [t["R"] for t in ts]
    rets = [t["ret"] for t in ts]
    win = sum(1 for x in rets if x > 0) / len(ts) * 100 if ts else 0
    py, pyn, pyw = defaultdict(float), defaultdict(int), defaultdict(list)
    sym = defaultdict(list)
    for t in ts:
        py[t["year"]] += t["pnl"]
        pyn[t["year"]] += 1
        pyw[t["year"]].append(t["ret"])
        sym[t["sym"]].append(t)
    ann = (eq / INIT) ** (1 / YEARS) - 1 if eq > 0 else -1
    return {"tag": tag, "note": note, "n": len(ts), "perYear": round(len(ts) / YEARS, 1),
            "win": round(win, 1), "avgR": round(st.mean(R), 3) if R else 0,
            "sumR": round(sum(R), 1), "pf": round(
                sum(x for x in rets if x > 0) / abs(sum(x for x in rets if x <= 0)), 2)
            if any(x <= 0 for x in rets) else None,
            "bars": round(st.mean([t["bars"] for t in ts]), 1) if ts else 0,
            "ci": boot(R), "ret": round((eq / INIT - 1) * 100, 1),
            "ann": round(ann * 100, 2), "mdd": round(mdd * 100, 1), "finalEq": round(eq, 0),
            "curve": curve, "perYearPnl": {str(y): round(v, 0) for y, v in sorted(py.items())},
            "perYearN": {str(y): pyn[y] for y in sorted(pyn)},
            "yearWin": {str(y): round(sum(1 for x in v if x > 0) / len(v) * 100, 0)
                        for y, v in sorted(pyw.items())},
            "sym": {c: {"n": len(v), "sumR": round(sum(t["R"] for t in v), 1),
                        "win": round(sum(1 for t in v if t["ret"] > 0) / len(v) * 100, 0),
                        "pnl": round(sum(t["pnl"] for t in v), 0)} for c, v in sym.items()},
            "trades": [{"sym": t["sym"], "side": t["side"], "id": t["id"], "tier": t["tier"],
                        "in": t["entryDate"], "out": t["exitDate"],
                        "ret": round(t["ret"] * 100, 2), "R": round(t["R"], 2),
                        "bars": t["bars"], "why": t["why"], "open": t.get("open", False)}
                       for t in ts]}


def main():
    plans = [
        ("日线 w=3 · 持有20", None, 3, 20, "现有终端口径（基线）", {}),
        ("日线 w=3 · 持有30", None, 3, 30, "只拉长持有期", {}),
        ("日线 w=3 · 持有60", None, 3, 60, "继续拉长", {}),
        ("日线 w=1 · 持有30", None, 1, 30, "把确认窗口提前", {}),
        ("周线 w=1 · 持有20", "W", 1, 20, "大周期快确认", {}),
        ("周线 w=1 · 持有30", "W", 1, 30, "周线 + 一个月持有", {}),
        ("周线 w=1 · 持有40", "W", 1, 40, "周线 + 两个月持有", {}),
        ("周线 w=1 · 持有60", "W", 1, 60, "周线 + 三个月持有", {}),
        ("周线 w=2 · 持有30", "W", 2, 30, "等两周再确认", {}),
        ("月线 w=1 · 持有30", "M", 1, 30, "极致大周期", {}),
        ("月线 w=2 · 持有30", "M", 2, 30, "月线稳健确认", {}),
    ]
    rows = []
    for tag, unit, w, hold, note, kw in plans:
        ts = bc_clean.run(sigs_of(unit, w), hold, **kw)
        r = pack(tag, ts, note)
        rows.append(r)
        print("%-22s %3d笔 %4.1f/年 胜率%5.1f%% R%+.2f 年化%+5.2f%% MDD%6.1f%%"
              % (tag, r["n"], r["perYear"], r["win"], r["avgR"], r["ann"], r["mdd"]))

    print()
    for tag, unit, w, hold, cap in (("周线 w=1 · 每品种年≤1", "W", 1, 30, 1),
                                    ("日线 w=3 · 每品种年≤1", None, 3, 30, 1)):
        ts = bc_clean.run(sigs_of(unit, w), hold, maxpy=cap)
        r = pack(tag, ts, "严格限次")
        rows.append(r)
        print("%-22s %3d笔 %4.1f/年 胜率%5.1f%% R%+.2f 年化%+5.2f%% MDD%6.1f%%"
              % (tag, r["n"], r["perYear"], r["win"], r["avgR"], r["ann"], r["mdd"]))

    for tag, unit, w, hold, cap in (("周线 w=1 · 全局≤10", "W", 1, 30, 10),
                                    ("日线 w=3 · 全局≤10", None, 3, 30, 10)):
        ts = cap_year(bc_clean.run(sigs_of(unit, w), hold), cap)
        r = pack(tag, ts, "全局每年≤10次")
        rows.append(r)
        print("%-22s %3d笔 %4.1f/年 胜率%5.1f%% R%+.2f 年化%+5.2f%% MDD%6.1f%%"
              % (tag, r["n"], r["perYear"], r["win"], r["avgR"], r["ann"], r["mdd"]))

    # 前沿：周线 w=1 开频
    front = []
    for mp in (1, 2, None):
        ts = bc_clean.run(sigs_of("W", 1), 30, maxpy=mp)
        r = pack("front", ts)
        front.append({"label": "每品种年≤%s" % (mp or "不限"), "perYear": r["perYear"],
                      "win": r["win"], "avgR": r["avgR"], "ann": r["ann"],
                      "mdd": r["mdd"], "n": r["n"]})
        print("  前沿 %s: %d 笔 胜率%.1f%% 年化%+.2f%%" % (mp or "不限", r["n"], r["win"], r["ann"]))

    # 风险档位（推荐＝日线 w=3 持有30，复利）
    rec = next(r for r in rows if r["tag"] == "日线 w=3 · 持有20")
    risk = []
    for mult in (1, 2, 3, 5):
        eq = INIT
        peak, mdd = eq, 0.0
        for t in rec["trades"]:
            eq *= (1 + RISK * mult * t["R"])
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1)
        risk.append({"risk": RISK * mult * 100, "ret": round((eq / INIT - 1) * 100, 1),
                     "ann": round(((eq / INIT) ** (1 / YEARS) - 1) * 100, 2),
                     "mdd": round(mdd * 100, 1), "finalEq": round(eq, 0)})

    data = {"rows": rows, "front": front, "risk": risk,
            "names": engine.NAMES, "codes": engine.CODES,
            "genAt": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
            "years": YEARS, "initEq": INIT, "riskUnit": RISK * 100}
    json.dump(data, open("../bigcycle_data.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("\nsaved bigcycle_data.json  %.2f MB"
          % (len(json.dumps(data, ensure_ascii=False)) / 1e6))


if __name__ == "__main__":
    main()
