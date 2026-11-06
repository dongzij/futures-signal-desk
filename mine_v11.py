# -*- coding: utf-8 -*-
"""v11：独立出场信号叠加到真实交易上 —— 决定性测试
进场完全不变（线上口径 35~37 笔）。
对比：
  BASE    v9 现行出场（MA26 armed 反向穿越 + 吊灯 3ATR）
  SIG(k)  上述规则 + 「独立出场信号 ≥k 条 → 提前离场」
          信号只看出场侧证据：趋势伸展度 / 段幅 / 段龄 / 自极值回撤 / 影线 / ADX 回落
成交统计：胜率、净 R、最大回撤、最差单笔、持仓中位、准确率（走掉后 10 根确实更低/更高）
"""
import statistics as st

import mine_v9 as V9

UNIV = V9.UNIV
HSIG = 10


def prep():
    V9.prep_all()


def sig_feats(P, i, side):
    """side=1 多单离场（找顶）；-1 空单离场（找底）"""
    bars, S = P["bars"], P["S"]
    b = bars[i]
    atr = S["atr"][i] or 1e-9
    m = S["ma"][i]
    c = b["c"]
    f = {}

    def ma(n):
        v = S.get("ma%d" % n)
        return v[i] if v else None

    ext = (c - m) * side / atr if m else 0.0
    f["ext30"] = ext >= 3.0
    f["ext25"] = ext >= 2.5

    age = 0
    for s, e, sd in P["segs"]:
        if s <= i <= e:
            age = i - s + 1
            break
    f["age30"] = age >= 30
    f["age20"] = age >= 20

    s0 = i - age + 1
    amp = (max(x["h"] for x in bars[s0:i + 1]) - min(x["l"] for x in bars[s0:i + 1])) / atr
    f["amp12"] = amp >= 12
    f["amp8"] = amp >= 8

    ext_px = P["hi"][i] if side == 1 else P["lo"][i]
    pull = ((c - ext_px) / atr) if side == 1 else ((ext_px - c) / atr)
    f["pull"] = pull <= -1.2
    f["pull2"] = pull <= -2.0

    rng_up = (b["h"] - max(b["o"], c)) / atr
    rng_dn = (min(b["o"], c) - b["l"]) / atr
    f["shadow"] = (rng_up >= 0.6) if side == 1 else (rng_dn >= 0.6)

    m18 = ma(18)
    f["ma18"] = ((c < m18) if side == 1 else (c > m18)) if m18 else False

    ax = S["adx"][i]
    hi20 = max([x for x in S["adx"][max(0, i - 19):i + 1] if x is not None] or [0])
    f["adx"] = ax is not None and hi20 - ax >= 3

    oi = b.get("oi") or 0
    oi5 = bars[i - 5].get("oi") or 0 if i >= 5 else 0
    doi = (oi / oi5 - 1) if oi5 else 0
    f["oi"] = (doi <= 0) if side == 1 else (doi >= 0)
    return f


def count(f, keys):
    return sum(1 for k in keys if f.get(k))


def sim(P, ent, keys, kth, k_ch=3.0, ma_n=26, maxhold=250):
    bars, S = P["bars"], P["S"]
    i, side = ent["i"], ent["side"]
    atr = ent["atr"]
    n = len(bars)
    entry = ent["entry"]
    peak = bars[i + 1]["h"] if side == 1 else bars[i + 1]["l"]
    armed = False
    xi, reason = None, "data"
    sig_hit = None
    for j in range(i + 1, min(i + 1 + maxhold, n)):
        b = bars[j]
        c = b["c"]
        if side == 1:
            peak = max(peak, b["h"])
            ch = peak - k_ch * atr
            if not armed and c > S["ma%d" % ma_n][j]:
                armed = True
            ma_brk = armed and c < S["ma%d" % ma_n][j]
            ch_hit = c <= ch
        else:
            peak = min(peak, b["l"])
            ch = peak + k_ch * atr
            if not armed and c < S["ma%d" % ma_n][j]:
                armed = True
            ma_brk = armed and c > S["ma%d" % ma_n][j]
            ch_hit = c >= ch
        if ch_hit:
            xi, reason = j, "吊灯"
            break
        if ma_brk:
            xi, reason = j, "MA%d" % ma_n
            break
        if kth:
            f = sig_feats(P, j, side) if side == 1 else sig_feats(P, j, side)
            if count(f, keys) >= kth:
                xi, reason, sig_hit = j, "出场信号", f
                break
    if xi is None:
        xi = min(i + maxhold, n - 1)
    fee = V9.M.FEE[ent["sym"]] / V9.M.MULT[ent["sym"]] + V9.M.TICK[ent["sym"]]
    pts = (bars[xi]["c"] - entry) * side - fee
    # 出场后的正确性：之后 10 根是否真的更低（多）/ 更高（空）
    ok = None
    if xi + HSIG < n:
        ok = (bars[xi + HSIG]["c"] - bars[xi]["c"]) * side < 0
    return {"sym": ent["sym"], "in": bars[i + 1]["d"], "out": bars[xi]["d"], "side": side,
            "R": pts / atr, "bars": xi - i - 1, "why": reason, "ok": ok,
            "sig": bool(sig_hit)}


def stat(trs):
    if not trs:
        return None
    n = len(trs)
    w = [t for t in trs if t["R"] > 0]
    eq = peak = dd = 0
    for t in sorted(trs, key=lambda x: x["in"]):
        eq += t["R"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {"n": n, "win": len(w) / n * 100, "R": sum(t["R"] for t in trs), "dd": dd,
            "avg": st.mean(t["R"] for t in trs), "hold": st.median(t["bars"] for t in trs),
            "worst": min(t["R"] for t in trs)}


def main():
    prep()
    ents = []
    for c in UNIV:
        ents += V9.entries(V9.DATA[c], c)
    ents.sort(key=lambda x: x["date"])
    print("进场（线上口径）：%d 笔\n" % len(ents))

    SETS = {
        "伸展+回撤": ["ext30", "pull"],
        "伸展+回撤+影线": ["ext30", "pull", "shadow"],
        "伸展+回撤+段幅": ["ext30", "pull", "amp12"],
        "伸展+回撤+ADX": ["ext30", "pull", "adx"],
        "伸展+回撤+影线+段龄": ["ext30", "pull", "shadow", "age30"],
        "伸展(2.5)+回撤+影线": ["ext25", "pull", "shadow"],
        "回撤+影线+ADX": ["pull", "shadow", "adx"],
    }
    base = [sim(V9.DATA[e["sym"]], e, [], 0) for e in ents]
    b = stat(base)
    print("%-26s %3s %7s %8s %7s %8s %6s %6s" %
          ("方案", "n", "胜率", "净R", "回撤", "最差", "持仓", "信号"))
    print("%-26s %3d %6.1f%% %+8.1f %+7.1f %+8.1f %6.0f %6d" %
          ("BASE v9（MA26+吊灯3）", b["n"], b["win"], b["R"], b["dd"], b["worst"],
           b["hold"], 0))
    res = []
    for nm, keys in SETS.items():
        for kth in (2, 3, len(keys)):
            trs = [sim(V9.DATA[e["sym"]], e, keys, kth) for e in ents]
            s = stat(trs)
            nsig = sum(1 for t in trs if t["sig"])
            ok = [t for t in trs if t["sig"] and t["ok"] is not None]
            acc = (sum(1 for t in ok if t["ok"]) / len(ok) * 100) if ok else 0
            res.append((nm, kth, s, nsig, acc))
            print("%-26s %3d %6.1f%% %+8.1f %+7.1f %+8.1f %6.0f %5d笔 准确率%.0f%%" %
                  ("%s ≥%d" % (nm, kth), s["n"], s["win"], s["R"], s["dd"], s["worst"],
                   s["hold"], nsig, acc))
    print("\n按 净R/|回撤| 排序：")
    for nm, kth, s, nsig, acc in sorted(res, key=lambda x: -(x[2]["R"] / abs(x[2]["dd"] or 1)))[:6]:
        print("  %-24s ≥%d  净%+.1fR 回撤%+.1fR 比%.2f 信号%d笔 准确率%.0f%%" %
              (nm, kth, s["R"], s["dd"], s["R"] / abs(s["dd"] or 1), nsig, acc))


if __name__ == "__main__":
    main()
