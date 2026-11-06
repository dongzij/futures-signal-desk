# -*- coding: utf-8 -*-
"""v10：独立出场信号（见顶/见底探测器）
=====================================================================
与进场完全解耦：不看有没有进场信号，只回答一个问题 ——
「现在这个位置，是不是一段趋势的顶/底，该走了？」

做法：
1. 对每一根 K 线判定「趋势语境」：价格在 MA34 一侧且偏离≥1.0ATR、段龄≥8 根
2. 在该语境下计算 8 个「趋势停滞」候选特征（全部按趋势方向归一）
3. 用未来 5/10/20 根的反向收益衡量每个特征与 k 条组合的「救回幅度」与命中率
   save = (今收 - H 根后收) * 趋势方向 / ATR   （正数＝现在走掉是对的）
4. 按命中率与 lift 挑条件集，标定「建议 / 强烈」两档阈值
"""
import statistics as st

import build
import cyc
import mine_v4b as M

UNIV = M.UNIV
MA_SET = (5, 10, 18, 22, 34)
HS = (5, 10, 20)
DATA = {}


def prep_all():
    for c in UNIV:
        adj, _ = build.panama_adjust(c)
        bars = adj[-2440:] if len(adj) > 2440 else adj
        P = M.prep(bars, 34)
        S = P["S"]
        closes = [b["c"] for b in bars]
        for n in MA_SET:
            if n != 34:
                S["ma%d" % n] = cyc.sma(closes, n)
        # ATR 快慢
        S["atr5"] = cyc.sma([x or 0 for x in S["atr"]], 5)
        S["atr20"] = cyc.sma([x or 0 for x in S["atr"]], 20)
        S["v5"] = cyc.sma([float(b["v"]) for b in bars], 5)
        # 段龄
        age = [0] * len(bars)
        for s, e, sd in P["segs"]:
            for i in range(s, e + 1):
                age[i] = i - s + 1
        S["age"] = age
        DATA[c] = P
    return DATA


def feats(P, i, sd):
    """趋势方向 sd（1=上升趋势，要判断顶部；-1=下跌趋势，判断底部）
    返回 dict：True 表示「朝停滞方向」的特征成立"""
    bars, S = P["bars"], P["S"]
    b = bars[i]
    atr = S["atr"][i] or 1e-9
    c = b["c"]
    f = {}

    def ma(n):
        v = S["ma%d" % n]
        return v[i] if v else None

    # 1 结构：自段内极值回撤 ≥1.2ATR（创新高/新低失败）
    ext = P["hi"][i] if sd == 1 else P["lo"][i]
    pull = ((c - ext) / atr) if sd == 1 else ((ext - c) / atr)
    f["pull"] = pull <= -1.2

    # 2 短均线失守：MA18 被反向穿越
    m18 = ma(18)
    f["ma18"] = (c < m18) if (m18 and sd == 1) else ((c > m18) if m18 else False)

    # 3 MA5 拐头：3 根斜率反向
    m5 = ma(5)
    m5p = S["ma5"][i - 3] if i >= 3 else None
    f["ma5t"] = bool(m5 and m5p and ((m5 - m5p) <= 0 if sd == 1 else (m5 - m5p) >= 0))

    # 4 MACD 柱转向
    h0 = S["macd"][i]
    h3 = S["macd"][i - 3] if i >= 3 else None
    f["macd"] = bool(h0 is not None and h3 is not None
                     and ((h0 - h3) <= 0 if sd == 1 else (h0 - h3) >= 0))

    # 5 持仓撤退：5 日持仓下降
    oi = b.get("oi") or 0
    oi5 = bars[i - 5].get("oi") or 0 if i >= 5 else 0
    doi = (oi / oi5 - 1) if oi5 else 0
    f["oi"] = (doi <= 0) if sd == 1 else (doi >= 0)

    # 6 量能萎缩：5 日均量 < 20 日均量 *0.85
    v5, v20 = S["v5"][i], S["vma20"][i]
    f["vol"] = bool(v5 and v20 and v5 <= v20 * 0.85)

    # 7 波动率收缩：ATR5 < ATR20 *0.85
    a5, a20 = S["atr5"][i], S["atr20"][i]
    f["atr"] = bool(a5 and a20 and a5 <= a20 * 0.85)

    # 8 ADX 回落：自 20 日高点回落 ≥3
    ax = S["adx"][i]
    hi20 = max([x for x in S["adx"][max(0, i - 19):i + 1] if x is not None] or [0])
    f["adx"] = bool(ax is not None and hi20 - ax >= 3)

    # 9 长影线（冲高回落）
    rng = (b["h"] - b["l"]) or 1e-9
    upper = (b["h"] - max(b["o"], c)) / atr
    lower = (min(b["o"], c) - b["l"]) / atr
    f["shadow"] = (upper >= 0.6) if sd == 1 else (lower >= 0.6)
    return f


def ctx(P, i):
    """趋势语境：返回趋势方向或 None"""
    bars, S = P["bars"], P["S"]
    m = S["ma"][i]
    if m is None or i < 30:
        return None
    atr = S["atr"][i] or 1e-9
    sd = 1 if bars[i]["c"] > m else -1
    if abs(bars[i]["c"] - m) / atr < 1.0:
        return None
    if S["age"][i] < 8:
        return None
    # MA34 自身方向须与价格同侧
    m0, m5b = S["ma"][i], S["ma"][i - 5]
    if not m0 or not m5b:
        return None
    if (m0 - m5b) * sd <= 0:
        return None
    return sd


def fwd_save(P, i, sd, h):
    bars = P["bars"]
    if i + h >= len(bars):
        return None
    atr = P["S"]["atr"][i] or 1e-9
    return (bars[i]["c"] - bars[i + h]["c"]) * sd / atr


def collect(P, dedupe=0):
    """遍历所有「趋势语境」bar，返回样本"""
    bars = P["bars"]
    out = []
    for i in range(30, len(bars) - 20):
        sd = ctx(P, i)
        if sd is None:
            continue
        f = feats(P, i, sd)
        k = sum(1 for v in f.values() if v)
        row = {"i": i, "sd": sd, "k": k, "f": f,
               "d": bars[i]["d"], "c": bars[i]["c"]}
        for h in HS:
            row["s%d" % h] = fwd_save(P, i, sd, h)
        out.append(row)
    if dedupe:
        kept, last = [], {}
        for r in out:
            lk = (r["sd"],)
            if lk in last and r["i"] - last[lk] < dedupe:
                continue
            kept.append(r)
            last[lk] = r["i"]
        return kept
    return out


def stat(rows, h):
    vals = [r["s%d" % h] for r in rows if r.get("s%d" % h) is not None]
    if not vals:
        return None
    return {"n": len(vals),
            "hit": sum(1 for v in vals if v > 0.5) / len(vals),
            "save": st.mean(vals),
            "med": st.median(vals)}


def main():
    prep_all()
    allrows = []
    for c in UNIV:
        rows = collect(DATA[c])
        for r in rows:
            r["sym"] = c
        allrows += rows
    print("趋势语境 bar（全样本）：%d" % len(allrows))

    base = {h: stat(allrows, h) for h in HS}
    print("\n基准（任意趋势语境 bar 立刻走）：")
    for h in HS:
        b = base[h]
        print("  H=%-2d  n=%5d  救回≥0.5ATR %5.1f%%  平均救回 %+.2fATR  中位 %+.2f"
              % (h, b["n"], b["hit"] * 100, b["save"], b["med"]))

    names = ["pull", "ma18", "ma5t", "macd", "oi", "vol", "atr", "adx", "shadow"]
    label = {"pull": "自极值回撤≥1.2ATR", "ma18": "MA18 反向失守", "ma5t": "MA5 拐头",
             "macd": "MACD 柱转向", "oi": "持仓 5 日撤退", "vol": "量能萎缩",
             "atr": "ATR 收缩", "adx": "ADX 回落≥3", "shadow": "长影线"}
    print("\n单条件区分度（H=10，对比基准 %.2fATR / %.1f%%）："
          % (base[10]["save"], base[10]["hit"] * 100))
    print("%-20s %6s %8s %10s %8s %8s" % ("条件", "n", "命中率", "平均救回", "lift", "Δ"))
    single = []
    for nm in names:
        sub = [r for r in allrows if r["f"][nm]]
        s = stat(sub, 10)
        if not s:
            continue
        lift = s["save"] / base[10]["save"] if base[10]["save"] else 0
        single.append((nm, s, lift))
        print("%-20s %6d %7.1f%% %+9.2f %8.2f %+8.2f"
              % (label[nm], s["n"], s["hit"] * 100, s["save"], lift, s["save"] - base[10]["save"]))
    single.sort(key=lambda x: -x[2])

    print("\n按命中条数分档（全部 9 条等权，H=5/10/20）：")
    print("%4s %6s %10s %10s %10s %10s" % ("k", "n", "H5命中", "H10命中", "H10救回", "H20救回"))
    for k in range(0, 10):
        sub = [r for r in allrows if r["k"] == k]
        if len(sub) < 20:
            continue
        s5, s10, s20 = stat(sub, 5), stat(sub, 10), stat(sub, 20)
        print("%4d %6d %9.1f%% %9.1f%% %+9.2f %+9.2f"
              % (k, len(sub), s5["hit"] * 100, s10["hit"] * 100, s10["save"], s20["save"]))

    print("\n条件两两组合（H=10，n≥100，按 lift 排序）：")
    combos = []
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            na, nb = names[a], names[b]
            sub = [r for r in allrows if r["f"][na] and r["f"][nb]]
            if len(sub) < 100:
                continue
            s = stat(sub, 10)
            combos.append((na, nb, s, s["save"] / base[10]["save"]))
    combos.sort(key=lambda x: -x[3])
    for na, nb, s, lift in combos[:10]:
        print("  %-14s + %-14s n=%4d 命中%5.1f%% 救回%+.2f lift%.2f"
              % (label[na], label[nb], s["n"], s["hit"] * 100, s["save"], lift))

    return allrows, base


if __name__ == "__main__":
    main()
