# -*- coding: utf-8 -*-
"""无前视口径重算：画像窗口必须等于确认窗口。

教训：早前 bc2/bc_final 直接调 pv.profile()，其内部固定用 i+3 根做画像，
而 w=1/2 时确认日在 i+1/i+2，等于看了未来 1~2 根。胜率随「偷看程度」
单调上升（w=3→43.6%，w=2→53.2%，w=1→67.3%），是偏差不是本事。
本脚本统一 c = i + w，重算全部口径。
"""
import json
import statistics as st
from collections import defaultdict

import cyc
import engine

YEARS = 10.0
COST_RT = 0.0008
RISK = 0.01
INIT_EQ = 1_000_000.0


def walk(bars, sigs, hold, stop_atr=3.0, maxpy=None):
    """日线推进的单一仓位状态机，与实盘 daily.py 完全同一条路径。"""
    n = len(bars)
    atr = engine.make_S(bars)["a"]
    cand = sorted(sigs, key=lambda s: s["conf"])
    by_conf = {}
    for s in cand:
        by_conf.setdefault(s["conf"], s)
    trades, per_year = [], defaultdict(int)
    pos = None
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
                ret = (ex / pos["entry"] - 1) * (1 if side == "long" else -1)
                d = pos["stopDist"] / pos["entry"]
                trades.append({"sym": pos["sym"], "id": pos["id"], "tier": pos["tier"],
                               "side": side, "ret": ret - COST_RT,
                               "R": (ret - COST_RT) / d if d else 0,
                               "entryDate": bars[pos["ei"]]["d"], "exitDate": bars[xi]["d"],
                               "why": why, "stopPct": round(d, 4),
                               "bars": xi - pos["ei"], "year": int(bars[pos["ei"]]["d"][:4]),
                               "open": False})
                pos = None
            if pos:
                continue
        s = by_conf.get(j)
        if not s:
            continue
        ei = j + 1
        if maxpy and per_year[int(bars[min(n - 1, ei)]["d"][:4])] >= maxpy:
            continue
        entry = bars[ei]["o"] or b["c"]
        a = atr[j] or entry * 0.01
        stop = entry - stop_atr * a if s["side"] == "long" else entry + stop_atr * a
        pos = {"sym": s["sym"], "id": s["id"], "tier": s["tier"], "side": s["side"],
               "entry": entry, "ei": ei, "stop": stop,
               "stopDist": abs(entry - stop)}
        per_year[int(bars[ei]["d"][:4])] += 1
    return trades


def portfolio(ts):
    eq = INIT_EQ
    peak = eq
    mdd = 0.0
    for t in ts:
        eq += INIT_EQ * RISK * t["R"]
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    ann = (eq / INIT_EQ) ** (1 / YEARS) - 1 if eq > 0 else -1
    return {"ret": round((eq / INIT_EQ - 1) * 100, 1), "ann": round(ann * 100, 2),
            "mdd": round(mdd * 100, 1)}


def bars_of(code, years=10):
    import build
    adj, _ = build.panama_adjust(code)
    keep = int(years * 244)
    return adj[-keep:] if len(adj) > keep else adj


_B = {}


def get_bars(c):
    if c not in _B:
        _B[c] = bars_of(c)
    return _B[c]


def resample(bars, unit):
    return engine.resample(bars, unit)


def signals(code, unit, w, tier_max=0):
    """unit: None=日线, 'W', 'M'。画像窗口 = 确认窗口 = w。"""
    bars = get_bars(code)
    if unit is None:
        pbar, ps = bars, engine.make_S(bars)
        mapidx = lambda k: k
        n = len(bars)
        need = 60
        last_ok = 2
    else:
        pbar = resample(bars, unit)
        ps = engine.make_S(pbar)
        n = len(pbar)
        need = 40 if unit == "W" else 26
        last_ok = 2
        mapidx = lambda k: pbar[k]["last_idx"]
    out = []
    for i, kind in cyc.find_peaks(pbar, w=w):
        if i < need or i + w > n - last_ok:
            continue
        pr = engine.profile(ps, i, kind, w=w)          # ← 关键：w 与确认一致
        ids = engine.hit(pr)
        if not ids:
            continue
        rank = {"狙击": 0, "重点": 1, "观察": 2}
        ids.sort(key=lambda x: rank[engine.SIG_META[x][2]])
        sid = ids[0]
        if rank[engine.SIG_META[sid][2]] > tier_max:
            continue
        conf = mapidx(min(n - 1, i + w))
        if conf >= len(bars) - 1:
            continue
        out.append({"sym": code, "id": sid, "side": engine.SIG_META[sid][1],
                    "tier": engine.SIG_META[sid][2], "conf": conf,
                    "confDate": bars[conf]["d"], "date": pbar[i]["d"],
                    "kind": kind, "conds": engine.cond_detail(pr)})
    return out


def all_sigs(unit, w, tier_max=0):
    out = []
    for c in engine.CODES:
        out += signals(c, unit, w, tier_max)
    return out


def run(sigs, hold, stop=3.0, maxpy=None):
    ts = []
    for c in engine.CODES:
        ts += walk(get_bars(c), [s for s in sigs if s["sym"] == c], hold=hold,
                   stop_atr=stop, maxpy=maxpy)
    return ts


def line(tag, ts):
    if not ts:
        print("%-30s 无样本" % tag)
        return None
    R = [t["R"] for t in ts]
    w = sum(1 for t in ts if t["ret"] > 0) / len(ts) * 100
    p = portfolio(ts)
    print("%-28s %3d笔 %4.1f/年 胜率%5.1f%% R%+.2f 累计R%+6.1f PF%5s 年化%+5.2f%% MDD%6.1f%%"
          % (tag, len(ts), len(ts) / YEARS, w, st.mean(R), sum(R),
             str(round(p["ret"], 0)), p["ann"], p["mdd"]))
    return {"n": len(ts), "win": round(w, 1), "avgR": round(st.mean(R), 3),
            "sumR": round(sum(R), 1), "ann": p["ann"], "mdd": p["mdd"], "ret": p["ret"]}


if __name__ == "__main__":
    print("=" * 120)
    print("无前视重算 · 狙击层 · 持有30")
    print("=" * 120)
    res = {}
    for unit, tag in ((None, "日线"), ("W", "周线"), ("M", "月线")):
        for w in (1, 2, 3):
            if unit == "M" and w > 2:
                continue
            sigs = all_sigs(unit, w, tier_max=0)
            ts = run(sigs, 30)
            k = "%s_w%d" % (tag, w)
            res[k] = line("%s w=%d 持有30" % (tag, w), ts)
    print()
    print("=" * 120)
    print("无前视重算 · 周线 w=1 · 不同持有期")
    print("=" * 120)
    for h in (20, 30, 40, 60):
        sigs = all_sigs("W", 1, tier_max=0)
        res["W1_h%d" % h] = line("周线 w=1 持有%d" % h, run(sigs, h))
    json.dump(res, open("clean_check.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
