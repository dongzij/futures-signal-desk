# -*- coding: utf-8 -*-
"""v4-B：在 FG/M/RB/MA/UR 上重建反转预测模型，并对 MA 周期 + ADX 做联合寻优
=====================================================================
与 v3 的差别：
  1) 只保留 5 个品种（用户实际交易）
  2) MA 周期作为可调参数，逐一测算「预测准确率」与「不设止损的实际盈亏」
  3) ADX / +DI / -DI 进入候选特征池，由数据决定是否入选
  4) 交易口径改为用户实际做法：命中信号 → 次日开盘逆势进 → **不设止损** →
     持有到反向穿越 MA（上限 200 根）→ 统计胜率/盈亏比/累计盈亏/最大浮回撤
无前视：特征只用当根及以前；标签用未来 MA 穿越。
"""
import itertools
import json
import statistics as st

import build
import cyc

UNIV = ["FG", "M", "RB", "MA", "UR"]
NAME = {"FG": "玻璃", "M": "豆粕", "RB": "螺纹", "MA": "甲醇", "UR": "尿素"}
MULT = {"FG": 20, "M": 10, "RB": 10, "MA": 10, "UR": 20}
FEE = {"FG": 6.0, "M": 1.5, "RB": 6.0, "MA": 2.0, "UR": 6.0}
TICK = {"FG": 1.0, "M": 1.0, "RB": 1.0, "MA": 1.0, "UR": 1.0}
SPLIT = "2024-01-01"
YEARS = 10
H = 5                 # 反转判定窗口（根）
MAXHOLD = 200         # 最长持有（不设止损，只防永久持有）
MAS = [20, 25, 30, 34, 40, 45, 50]


# ---------------------------------------------------------------- 指标
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


def adx(bars, n=14):
    L = len(bars)
    ax = [None] * L
    pdi = [None] * L
    mdi = [None] * L
    if L < n * 2 + 1:
        return ax, pdi, mdi
    trs, pdm, mdm = [bars[0]["h"] - bars[0]["l"]], [0.0], [0.0]
    for i in range(1, L):
        up = bars[i]["h"] - bars[i - 1]["h"]
        dn = bars[i - 1]["l"] - bars[i]["l"]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
        pc = bars[i - 1]["c"]
        trs.append(max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - pc), abs(bars[i]["l"] - pc)))
    atr = sum(trs[1:n + 1]) / n
    sp = sum(pdm[1:n + 1]) / n
    sm = sum(mdm[1:n + 1]) / n
    dxs = []
    for i in range(n, L):
        if i > n:
            atr = (atr * (n - 1) + trs[i]) / n
            sp = (sp * (n - 1) + pdm[i]) / n
            sm = (sm * (n - 1) + mdm[i]) / n
        p = 100 * sp / atr if atr else 0.0
        m = 100 * sm / atr if atr else 0.0
        pdi[i], mdi[i] = p, m
        dxs.append(100 * abs(p - m) / (p + m) if (p + m) else 0.0)
        if len(dxs) == 1:
            ax[i] = dxs[0]
        elif len(dxs) <= n:
            ax[i] = sum(dxs) / len(dxs)
        else:
            ax[i] = (ax[i - 1] * (n - 1) + dxs[-1]) / n
    return ax, pdi, mdi


# ---------------------------------------------------------------- 结构
def prep(bars, man):
    closes = [b["c"] for b in bars]
    n = len(bars)
    S = {"bars": bars, "n": n, "ma": cyc.sma(closes, man), "ma10": cyc.sma(closes, 10),
         "atr": cyc.atr_series(bars), "vma20": cyc.sma([float(b["v"]) for b in bars], 20),
         "macd": macd_hist(closes), "rsi": rsi(closes)}
    ax, pdi, mdi = adx(bars)
    S["adx"], S["pdi"], S["mdi"] = ax, pdi, mdi
    segs, cur, start = [], 0, 0
    for i in range(n):
        if S["ma"][i] is None:
            continue
        side = 1 if closes[i] > S["ma"][i] else -1
        if side != cur:
            if cur:
                segs.append((start, i - 1, cur))
            start, cur = i, side
    if cur:
        segs.append((start, n - 1, cur))
    side = [None] * n
    lead = [None] * n
    seg_of = [None] * n
    for si, (s, e, sd) in enumerate(segs):
        nxt = segs[si + 1][0] if si + 1 < len(segs) else None
        for i in range(s, e + 1):
            side[i] = sd
            seg_of[i] = si
            lead[i] = (nxt - i) if nxt is not None else None
    hi = [None] * n
    lo = [None] * n
    for s, e, sd in segs:
        h, l = -1e18, 1e18
        for i in range(s, e + 1):
            h, l = max(h, bars[i]["h"]), min(l, bars[i]["l"])
            hi[i], lo[i] = h, l
    P = {"bars": bars, "S": S, "segs": segs, "side": side, "lead": lead, "hi": hi,
         "lo": lo, "n": n, "seg_of": seg_of, "ma_n": man}
    return P


def feats(P, i):
    b, S, bars = P["bars"][i], P["S"], P["bars"]
    atr = S["atr"][i] or 1e-9
    c, sd = b["c"], P["side"][i]
    f = {}
    f["dma"] = (c - S["ma"][i]) / atr if S["ma"][i] else None
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
        c5 = [bars[j2]["v"] / S["vma20"][j2] for j2 in range(max(0, i - 4), i + 1) if S["vma20"][j2]]
        f["vr5"] = max(c5) if c5 else None
        if i - 4 >= 0:
            f["vsum5"] = sum(bars[j2]["v"] for j2 in range(i - 4, i + 1)) / (vma * 5)
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
    if P["S"]["rsi"][i] is not None:
        f["rsi"] = P["S"]["rsi"][i]
    if S["macd"][i] is not None:
        f["macd"] = S["macd"][i] / atr
    if S["adx"][i] is not None:
        f["adx"] = S["adx"][i]
        f["di"] = (S["pdi"][i] - S["mdi"][i]) / 100.0
        f["adx_sl"] = (S["adx"][i] - S["adx"][i - 5]) if i >= 5 and S["adx"][i - 5] else None
    return f


DIR = {"dma", "d10", "ret1", "ret3", "ret5", "ret10", "pull", "streak",
       "ma10_sl", "macd", "cpos", "di"}


def adj(name, v, side):
    if v is None:
        return None
    if name == "rsi":
        v = v - 50.0
    if side == -1 and name in DIR:
        v = -v
    return v


CONDS = [
    ("A1", "dma",     "<=", 0.3,  "距均线 ≤0.3 ATR"),
    ("A2", "dma",     "<=", 0.6,  "距均线 ≤0.6 ATR"),
    ("A3", "dma",     "<=", 1.0,  "距均线 ≤1.0 ATR"),
    ("A4", "d10",     "<=", 0.0,  "已破 MA10"),
    ("A5", "d10",     "<=", -0.5, "破 MA10 达 0.5ATR"),
    ("A6", "ret3",    "<=", 0.0,  "3 日累计已反向"),
    ("A7", "ret5",    "<=", 0.0,  "5 日累计已反向"),
    ("A8", "ret10",   "<=", -1.0, "10 日累计逆行 ≥1ATR"),
    ("A9", "ret10",   "<=", -1.5, "10 日累计逆行 ≥1.5ATR"),
    ("A10", "pull",   "<=", -1.5, "自极值回撤 ≥1.5ATR"),
    ("A11", "streak", ">=", 2,    "连续 ≥2 根反向"),
    ("A12", "streak", ">=", 3,    "连续 ≥3 根反向"),
    ("A13", "doi5",   "<=", -2.0, "持仓 5 日降 ≥2%"),
    ("A14", "doi10",  "<=", -3.0, "持仓 10 日降 ≥3%"),
    ("A15", "ma10_sl", "<=", 0.0, "MA10 斜率转平/转负"),
    ("A16", "ma10_sl", "<=", -0.2, "MA10 斜率明显转负"),
    ("A17", "macd",   "<=", 0.0,  "MACD 柱翻向"),
    ("A18", "rsi",    "<=", -5,   "RSI 越过 50"),
    ("A19", "cpos",   "<=", 0.35, "收在当日反向 1/3"),
    ("A20", "rng1",   ">=", 1.5,  "单日振幅 ≥1.5ATR"),
    ("A21", "atr_exp", ">=", 1.15, "ATR 放大 ≥15%"),
    ("A22", "vr5",    ">=", 1.4,  "5 日放量 ≥1.4×"),
    ("A23", "adx",    "<=", 25,   "ADX ≤25（趋势未强）"),
    ("A24", "adx",    "<=", 20,   "ADX ≤20（趋势弱）"),
    ("A25", "adx",    ">=", 25,   "ADX ≥25（趋势强）"),
    ("A26", "adx_sl", "<=", 0.0,  "ADX 见顶回落"),
    ("A27", "adx_sl", ">=", 0.0,  "ADX 仍在上升"),
    ("A28", "di",     "<=", 0.0,  "DI 差已收敛"),
]


def ok(row, cond, idx=None):
    nm, op, th = cond[1], cond[2], cond[3]
    v = row.get(nm)
    if v is None:
        return False
    return v <= th if op == "<=" else v >= th


def rows_of(bars, code, man):
    P = prep(bars, man)
    S = P["S"]
    out = []
    for i in range(60, P["n"]):
        if P["side"][i] is None or S["ma"][i] is None:
            continue
        f = feats(P, i)
        r = {"sym": code, "date": bars[i]["d"], "i": i, "side": P["side"][i],
             "lead": P["lead"][i], "seg": P["seg_of"][i]}
        for nm, v in f.items():
            r[nm] = adj(nm, v, P["side"][i])
        out.append(r)
    return out, P


def load():
    d = {}
    for c in UNIV:
        adjb, _ = build.panama_adjust(c)
        keep = int(YEARS * 244)
        b = adjb[-keep:] if len(adjb) > keep else adjb
        if len(b) >= 300:
            d[c] = b
    return d


def lab(r):
    return r["lead"] is not None and r["lead"] <= H


def base_rate(rows, lo=None, hi=None):
    v = [r for r in rows if r["lead"] is not None and (lo is None or r["date"] >= lo)
         and (hi is None or r["date"] < hi)]
    return sum(1 for r in v if lab(r)) / len(v) if v else 0.0


def eval_cond(rows, c):
    hit = [r for r in rows if ok(r, c)]
    if len(hit) < 40:
        return None
    b = base_rate(rows)
    p = sum(1 for r in hit if lab(r)) / len(hit)
    tr = [r for r in hit if r["date"] < SPLIT]
    te = [r for r in hit if r["date"] >= SPLIT]
    btr = base_rate(rows, hi=SPLIT)
    bte = base_rate(rows, lo=SPLIT)
    ltr = (sum(1 for r in tr if lab(r)) / len(tr) / btr) if len(tr) >= 15 and btr else None
    lte = (sum(1 for r in te if lab(r)) / len(te) / bte) if len(te) >= 15 and bte else None
    good = 0
    for code in UNIV:
        sub = [r for r in hit if r["sym"] == code]
        pool = [r for r in rows if r["sym"] == code]
        if len(sub) >= 6:
            bp = base_rate(pool)
            pp = sum(1 for r in sub if lab(r)) / len(sub)
            if bp and pp / bp >= 1.2:
                good += 1
    return {"c": c, "n": len(hit), "cov": len(hit) / len(rows), "p": p, "base": b,
            "lift": p / b if b else 0, "ltr": ltr, "lte": lte, "good": good}


def search(rows, pool, max_k=4, min_n=40):
    names = [c[0] for c in pool]
    idx = {c[0]: c for c in pool}
    pos = {n: 1 << i for i, n in enumerate(names)}
    masks, labs, istr = [], [], []
    for r in rows:
        m = 0
        for n in names:
            if ok(r, idx[n]):
                m |= pos[n]
        masks.append(m)
        labs.append(1 if lab(r) else 0)
        istr.append(r["date"] < SPLIT)
    n_tr = sum(istr)
    n_te = len(rows) - n_tr
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
            if cnt < min_n or ctr < 12 or cte < 8:
                continue
            ltr = (htr / ctr / btr) if btr else 0
            lte = (hte / cte / bte) if bte else 0
            if ltr < 1.2 or lte < 1.2:
                continue
            best.append({"combo": combo, "n": cnt, "p": hit / cnt, "ltr": ltr, "lte": lte,
                         "minlift": min(ltr, lte), "ptr": htr / ctr, "pte": hte / cte})
    best.sort(key=lambda x: (-x["minlift"], -x["n"]))
    return best


def calib(rows, combo):
    cs = [c for c in CONDS if c[0] in combo]
    tab = {}
    for r in rows:
        k = sum(1 for c in cs if ok(r, c))
        d = tab.setdefault(k, {"n": 0, "h": 0})
        d["n"] += 1
        d["h"] += 1 if lab(r) else 0
    return tab


def trade(P, rows, combo, code, adx_flt=None):
    """命中组合 → 次日开盘逆势进 → 不设止损 → 持有到「新趋势结束」的反向穿越。
    即：进场后价格先完成预测的反转，接着走完一整段新趋势，再被下一次反向穿越带走。
    若预测失败（价格没穿、继续原趋势），则一路持有到真正的反转并走完那段趋势 —— 这正是
    不设止损的真实代价，用 MAE（最大浮亏）量化。"""
    S = P["S"]
    bars = P["bars"]
    segs = P["segs"]
    cs = [c for c in CONDS if c[0] in combo]
    hit = [r for r in rows if all(ok(r, c) for c in cs)]
    out = []
    used = set()
    for r in sorted(hit, key=lambda x: x["i"]):
        i = r["i"]
        si = P["seg_of"][i]
        if si in used or si + 2 >= len(segs):
            continue
        if i + 1 >= len(bars):
            continue
        if adx_flt is not None and not adx_flt(P, i):
            continue
        used.add(si)
        sd = P["side"][i]
        side = -sd
        entry = bars[i + 1]["o"]
        xi = min(segs[si + 2][0] + 1, len(bars) - 1)
        exitp = bars[xi]["o"] if xi < len(bars) - 1 else bars[-1]["c"]
        atr = S["atr"][i] or entry * 0.01
        cost = FEE[code] / MULT[code] + TICK[code]
        pts = (exitp - entry) * side - cost
        mae = 0.0
        for j in range(i + 1, xi + 1):
            adv = (bars[j]["l"] - entry) * side if side == 1 else (entry - bars[j]["h"])
            mae = min(mae, adv)
        out.append({"code": code, "in": bars[i + 1]["d"], "out": bars[xi]["d"],
                    "side": side, "entry": entry, "exit": exitp, "pts": pts,
                    "R": pts / atr, "bars": xi - i - 1, "mae": mae / atr,
                    "lead": P["lead"][i], "adx": S["adx"][i],
                    "aopen": (xi >= len(bars) - 1)})
    return out


def stats(trs):
    if not trs:
        return None
    n = len(trs)
    win = sum(1 for t in trs if t["pts"] > 0)
    eq = peak = dd = 0.0
    for t in sorted(trs, key=lambda x: x["in"]):
        eq += t["pts"] * MULT[t["code"]]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    gp = sum(t["pts"] * MULT[t["code"]] for t in trs if t["pts"] > 0)
    gl = -sum(t["pts"] * MULT[t["code"]] for t in trs if t["pts"] <= 0)
    yrs = max(1, len({t["in"][:4] for t in trs}))
    return {"n": n, "win": win / n, "yuan": eq, "dd": dd, "per_year": eq / yrs,
            "pf": gp / gl if gl else 99, "payoff": (gp / max(1, win)) / (gl / max(1, n - win)) if gl else 0,
            "avgR": st.mean([t["R"] for t in trs]), "sumR": sum(t["R"] for t in trs),
            "mae_med": st.median([t["mae"] for t in trs]),
            "mae_avg": st.mean([t["mae"] for t in trs]),
            "hold": st.median([t["bars"] for t in trs]),
            "lead": st.median([t["lead"] for t in trs if t["lead"] is not None] or [0])}


def fmt(s):
    if not s:
        return "无样本"
    return ("n=%3d 胜率%5.1f%% 盈亏比%5.2f 平均%+6.2fR 累计%+7.1fR 盈亏%+8.0f元 回撤%8.0f元 持%3.0f根"
            % (s["n"], s["win"] * 100, s["payoff"], s["avgR"], s["sumR"], s["yuan"], s["dd"], s["hold"]))


def main():
    data = load()
    print("品种样本：", "、".join("%s %d根" % (c, len(data[c])) for c in UNIV))

    print("\n" + "=" * 130)
    print("一、MA 周期 × 组合预测：先看「同一套 4 条件」在不同 MA 上的预测力与不设止损收益")
    print("=" * 130)
    REF = ["A1", "A9", "A15", "A17"]      # v3 原 4 条（dma≤0.3 / ret10≤-1.5 / ma10斜率≤0 / macd≤0）
    print("%-4s %-92s %s" % ("MA", "k=4 触发后的实际表现（不设止损）", "k=4 命中率"))
    for man in MAS:
        allt, kk = [], {}
        for c in UNIV:
            rows, P = Pm_fetch(data, c, man)
            cs = [x for x in CONDS if x[0] in REF]
            n4 = sum(1 for r in rows if all(ok(r, x) for x in cs))
            hit4 = sum(1 for r in rows if all(ok(r, x) for x in cs) and lab(r))
            kk.setdefault("n", 0)
            kk["n"] += n4
            kk["h"] = kk.get("h", 0) + hit4
            allt += trade(P, rows, REF, c)
        s = stats(allt)
        print("%-4d %-92s %4.1f%%" % (man, fmt(s), (kk["h"] / kk["n"] * 100) if kk["n"] else 0))

    print("\n" + "=" * 130)
    print("二、每个 MA 重新筛选指标 + 组合搜索（样本内 <2024 / 盲测 2024~，只挑两段都 lift≥1.2）")
    print("=" * 130)
    store = {}
    for man in MAS:
        rows_all, Pm = [], {}
        for c in UNIV:
            rows, P = Pm_fetch(data, c, man)
            rows_all += rows
            Pm[c] = (rows, P)
        base = base_rate(rows_all)
        keep = []
        for c in CONDS:
            e = eval_cond(rows_all, c)
            if not e:
                continue
            if not (0.02 <= e["cov"] <= 0.65):
                continue
            if (e["ltr"] or 0) < 1.2 or (e["lte"] or 0) < 1.2:
                continue
            if e["good"] < 3:
                continue
            keep.append(e)
        print("\nMA=%d：基准反转率 %.1f%%，通过筛选 %d 条 → %s" %
              (man, base * 100, len(keep),
               "  ".join("%s(lift%.2f)" % (e["c"][0], e["lift"]) for e in keep)))
        combos = search(rows_all, [e["c"] for e in keep], 4)
        if not combos:
            print("   无稳健组合")
            continue
        for b in combos[:5]:
            print("   %-22s n=%3d 准确率%5.1f%% lift%4.2f (内%4.2f/外%4.2f)" %
                  ("+".join(b["combo"]), b["n"], b["p"] * 100, b["minlift"], b["ltr"], b["lte"]))
        best = combos[0]
        store[man] = {"combo": list(best["combo"]), "n": best["n"], "p": best["p"],
                      "ltr": best["ltr"], "lte": best["lte"], "base": base,
                      "rows": len(rows_all), "keep": [e["c"][0] for e in keep]}

    print("\n" + "=" * 130)
    print("三、各 MA 的最优组合 → 不设止损实盘化测算（逆势次日开盘进，持有到反向穿越）")
    print("=" * 130)
    print("%-4s %-24s %-88s" % ("MA", "组合", "表现"))
    picks = {}
    for man in MAS:
        if man not in store:
            continue
        combo = store[man]["combo"]
        allt = []
        for c in UNIV:
            rows, P = Pm_fetch(data, c, man)
            allt += trade(P, rows, combo, c)
        s = stats(allt)
        tmp = [t for t in allt if t["in"] < SPLIT]
        te = [t for t in allt if t["in"] >= SPLIT]
        picks[man] = {"combo": combo, "all": s,
                      "tr": stats(tmp), "te": stats(te)}
        print("%-4d %-24s %s" % (man, "+".join(combo), fmt(s)))
        if picks[man]["tr"] and picks[man]["te"]:
            print("%-4s %-24s 内: %s" % ("", "", fmt(picks[man]["tr"])))
            print("%-4s %-24s 外: %s" % ("", "", fmt(picks[man]["te"])))

    json.dump({"H": H, "store": {str(k): {kk: vv for kk, vv in v.items() if kk != "rows"}
                                 for k, v in store.items()},
               "picks": {str(k): {"combo": v["combo"],
                                  "n": v["all"]["n"], "win": v["all"]["win"],
                                  "yuan": v["all"]["yuan"], "dd": v["all"]["dd"],
                                  "payoff": v["all"]["payoff"]} for k, v in picks.items()}},
              open("v4b_ma_model.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


_PMCACHE = {}


def Pm_fetch(data, c, man):
    key = (c, man)
    if key not in _PMCACHE:
        _PMCACHE[key] = rows_of(data[c], c, man)
    return _PMCACHE[key]


if __name__ == "__main__":
    main()
