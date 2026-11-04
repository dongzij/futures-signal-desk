# -*- coding: utf-8 -*-
"""v9：慢进快出 —— 出场逻辑重构
=====================================================================
进场完全沿用线上口径（不变）：
  大段(点数≥200 且 长度≥15) 内、段龄≥2、4 条特征(A1/A9/A15/A17)全中且 ADX≥20，
  每段只取第一次，次日开盘逆势进。

第一部分：顶/底盘整期共性特征
  盘整期 = 段极值日 p+1 .. 段结束 e（收盘反向穿越 MA34 之前）
  推进期 = 段起点 s .. p
  找 P(特征|盘整) 显著高于 P(特征|推进) 且提前量大的指标。

第二部分：出场方案网格
  A 基准：收盘反向穿越 MA34
  B 短均线：收盘反向穿越 MA5/15/18/22/26/30
  D 吊灯：极值 ∓ 3ATR，收盘触及即走
  E 组合：短均线 or 吊灯
  C 盘整预警：特征触发 + 吊灯保护（第一部分的结论落地）
"""
import statistics as st
import sys

import build
import cyc
import mine_v4b as M

UNIV = M.UNIV
MA_SET = (5, 10, 15, 18, 20, 22, 24, 26, 28, 30, 34)
MIN_PTS = 200
MIN_LEN = 15
ADX_MIN = 20
WARN_PULL = 1.2
FEE_ATR = {}

DATA = {}


def prep_all():
    for c in UNIV:
        adj, _ = build.panama_adjust(c)
        bars = adj[-2440:] if len(adj) > 2440 else adj
        P = M.prep(bars, 34)
        S = P["S"]
        closes = [b["c"] for b in bars]
        for n in MA_SET:
            S["ma%d" % n] = cyc.sma(closes, n)
        DATA[c] = P
    return DATA


def big_segs(P, min_pts=MIN_PTS):
    """够格大趋势段：长度≥15 且 幅度≥min_pts 点"""
    bars = P["bars"]
    out = []
    for s, e, sd in P["segs"]:
        if e - s + 1 < MIN_LEN:
            continue
        hi = max(bars[k]["h"] for k in range(s, e + 1))
        lo = min(bars[k]["l"] for k in range(s, e + 1))
        if hi - lo < min_pts:
            continue
        out.append((s, e, sd))
    return out


# ---------------------------------------------------------------- 第一部分
def pivot_of(P, s, e, sd):
    """段极值日：多段取最高价，空段取最低价"""
    bars = P["bars"]
    if sd == 1:
        return max(range(s, e + 1), key=lambda k: bars[k]["h"])
    return min(range(s, e + 1), key=lambda k: bars[k]["l"])


def feat_at(P, i, sd):
    """盘整期候选特征（全部方向归一：True = 朝"趋势停滞"方向）"""
    bars, S = P["bars"], P["S"]
    b = bars[i]
    atr = S["atr"][i] or 1e-9
    c = b["c"]
    f = {}

    def ma(n):
        return S["ma%d" % n][i] if n in MA_SET else None

    def dist(n):
        m = ma(n)
        return (c - m) / atr if m else None

    # 均线：价格与短均线的关系（正数=价格仍在均线上方）
    for n in (5, 10, 15, 18, 22, 26, 30):
        d = dist(n)
        if d is not None:
            f["below_ma%d" % n] = (d < 0) if sd == 1 else (d > 0)
    # 短均线拐头
    for n in (5, 10, 15):
        m0 = ma(n)
        m1 = S["ma%d" % n][i - 3] if i >= 3 else None
        if m0 and m1:
            sl = (m0 - m1) / atr
            f["ma%d_turn" % n] = (sl <= 0) if sd == 1 else (sl >= 0)
    # 均线纠缠：价格夹在 MA10 与 MA30 之间且距离都小
    d10, d30 = dist(10), dist(30)
    if d10 is not None and d30 is not None:
        f["ma_tangle"] = abs(d10) <= 0.6 and abs(d30) <= 1.2
    # 不再创新高/新低：距段极值回撤
    pull = ((c - P["hi"][i]) / atr) if sd == 1 else ((P["lo"][i] - c) / atr)
    f["pull_gt1"] = pull <= -1.0
    f["pull_gt2"] = pull <= -2.0
    # 波动率收缩
    a20 = [S["atr"][j] for j in range(max(0, i - 19), i + 1) if S["atr"][j]]
    if a20 and S["atr"][i]:
        f["atr_shrink"] = S["atr"][i] / (sum(a20) / len(a20)) <= 0.85
    # 量仓
    oi, oi5 = b.get("oi") or 0, (bars[i - 5].get("oi") or 0) if i >= 5 else 0
    if oi and oi5:
        doi5 = (oi - oi5) / oi5 * 100
        f["doi5_down"] = doi5 <= -1.0
        f["doi5_down3"] = doi5 <= -3.0
    vma = S["vma20"][i]
    if vma:
        vr1 = b["v"] / vma
        ret1 = (c - bars[i - 1]["c"]) / atr
        f["vol_stall"] = vr1 >= 1.3 and ((ret1 <= 0) if sd == 1 else (ret1 >= 0))
        f["vol_dry"] = vr1 <= 0.8
    # 动量
    if S["macd"][i] is not None:
        f["macd_back"] = (S["macd"][i] <= 0) if sd == 1 else (S["macd"][i] >= 0)
    r = S["rsi"][i]
    if r is not None:
        f["rsi_back"] = (r <= 55) if sd == 1 else (r >= 45)
    if S["adx"][i] is not None:
        f["adx_fade"] = S["adx"][i] <= 25
        if i >= 5 and S["adx"][i - 5] is not None:
            f["adx_down"] = S["adx"][i] - S["adx"][i - 5] <= -2
    return f


def part1():
    print("=" * 96)
    print("第一部分：顶/底盘整期共性特征（盘整期 vs 推进期命中率）")
    print("=" * 96)
    stat = {}
    lead = {}
    n_seg = 0
    len_pl = []
    for c in UNIV:
        P = DATA[c]
        for (s, e, sd) in big_segs(P):
            p = pivot_of(P, s, e, sd)
            if p + 1 >= e:
                continue
            n_seg += 1
            len_pl.append(e - p)
            for i in range(p + 1, e + 1):
                for k, v in feat_at(P, i, sd).items():
                    stat.setdefault(k, [0, 0, 0, 0])       # 盘整中/盘整总/推进中/推进总
                    stat[k][1] += 1
                    stat[k][0] += 1 if v else 0
                # 首次命中提前量
                for k, v in feat_at(P, i, sd).items():
                    if v and k not in lead.setdefault(c + str(s), {}):
                        lead[c + str(s)][k] = e - i
            for i in range(s, p + 1):
                for k, v in feat_at(P, i, sd).items():
                    stat.setdefault(k, [0, 0, 0, 0])
                    stat[k][3] += 1
                    stat[k][2] += 1 if v else 0

    leads = {}
    for seg_key, d in lead.items():
        for k, v in d.items():
            leads.setdefault(k, []).append(v)

    print("样本：%d 个大趋势段（≥%d 点），盘整期长度 中位 %d 根 / 均值 %.1f 根" %
          (n_seg, MIN_PTS, st.median(len_pl), st.mean(len_pl)))
    print("\n%-16s %8s %8s %7s %8s %8s" % ("特征", "盘整期", "推进期", "lift", "提前中位", "提前P25"))
    rows = []
    for k, (a, b, c2, d) in stat.items():
        if b < 50 or d < 50:
            continue
        pr_pl, pr_tr = a / b, c2 / d
        lift = pr_pl / pr_tr if pr_tr > 0 else 99
        lm = leads.get(k) or [0]
        rows.append((lift, k, pr_pl, pr_tr, st.median(lm), sorted(lm)[len(lm) // 4] if lm else 0))
    rows.sort(reverse=True)
    for lift, k, pl, tr, lm, lq in rows:
        print("%-16s %7.1f%% %7.1f%% %7.2f %8.0f %8.0f" %
              (k, pl * 100, tr * 100, lift, lm, lq))
    return rows


# ---------------------------------------------------------------- 第二部分
def entries(P, c):
    """线上口径进场点：每段首次 4/4 + ADX≥20"""
    out = []
    for (s, e, sd) in big_segs(P):
        for i in range(max(60, s + 1), e + 1):
            if DATA[c]["side"][i] != sd:
                continue
            f = feat_at(P, i, sd)
            S = P["S"]
            atr = S["atr"][i] or 1e-9
            bars = P["bars"]
            b = bars[i]
            dma = (b["c"] - S["ma"][i]) / atr if S["ma"][i] else 9
            ret10 = (b["c"] - bars[i - 10]["c"]) / atr if i >= 10 else 9
            m10, m10p = S["ma10"][i], (S["ma10"][i - 5] if i >= 5 else None)
            ma10_sl = (m10 - m10p) / atr if (m10 and m10p) else 9
            macd = S["macd"][i] / atr if S["macd"][i] is not None else 9
            if sd == -1:
                dma, ret10, ma10_sl, macd = -dma, -ret10, -ma10_sl, -macd
            if not (dma <= 0.3 and ret10 <= -1.5 and ma10_sl <= 0 and macd <= 0):
                continue
            adx = S["adx"][i]
            if adx is None or adx < ADX_MIN:
                continue
            out.append({"sym": c, "i": i, "seg": (s, e, sd), "side": -sd,
                        "atr": atr, "entry": bars[i + 1]["o"] if i + 1 < len(bars) else b["c"],
                        "date": bars[i]["d"]})
            break
    return out


def sim(P, ent, rule, k_ch=3.0, ma_n=None, maxhold=250, warn_pull=1.2):
    """rule: 'ma34' | 'ma' | 'chandelier' | 'ma+ch' | 'warn+ch'

    均线离场沿用线上 armed 语义：先站到仓位一侧（新趋势成立），
    之后收盘反向穿越才离场。吊灯止损独立生效、无条件。
    """
    bars, S = P["bars"], P["S"]
    i, side = ent["i"], ent["side"]
    atr = ent["atr"]
    n = len(bars)
    entry = ent["entry"]
    mam = S["ma%d" % (ma_n or 34)]
    peak = bars[i + 1]["h"] if side == 1 else bars[i + 1]["l"]
    armed = False
    xi = None
    reason = "data"
    for j in range(i + 1, min(i + 1 + maxhold, n)):
        c = bars[j]["c"]
        mj = mam[j]
        # 吊灯：进场后极值 ∓ k*ATR，收盘触及无条件离场
        if side == 1:
            peak = max(peak, bars[j]["h"])
            ch_hit = c <= peak - k_ch * atr
        else:
            peak = min(peak, bars[j]["l"])
            ch_hit = c >= peak + k_ch * atr
        # 均线：armed 之后反向穿越
        ma_hit = False
        if mj is not None:
            if not armed:
                if (side == 1 and c > mj) or (side == -1 and c < mj):
                    armed = True
            else:
                if (side == 1 and c < mj) or (side == -1 and c > mj):
                    ma_hit = True
        if rule == "chandelier":
            hit = ch_hit
        elif rule in ("ma34", "ma"):
            hit = ma_hit
        elif rule == "ma+ch":
            hit = ma_hit or ch_hit
        elif rule == "warn+ch":
            hit = ch_hit
            if not hit and armed:
                # 盘整预警：自极值回撤≥X ATR 且（ATR 收缩 或 持仓撤退 或 量能萎缩）
                pull = ((c - peak) / atr) if side == 1 else ((peak - c) / atr)
                f = feat_at(P, j, -side)
                soft = f.get("atr_shrink") or f.get("doi5_down") or f.get("vol_dry")
                hit = pull <= -warn_pull and bool(soft)
        elif rule == "warn+ma+ch":
            hit = ch_hit or ma_hit
            if not hit and armed:
                pull = ((c - peak) / atr) if side == 1 else ((peak - c) / atr)
                f = feat_at(P, j, -side)
                soft = f.get("atr_shrink") or f.get("doi5_down") or f.get("vol_dry")
                hit = pull <= -warn_pull and bool(soft)
        if hit:
            xi, reason = j, rule
            break
    if xi is None:
        xi = min(i + maxhold, n - 1)
    fee_pts = M.FEE[ent["sym"]] / M.MULT[ent["sym"]] + M.TICK[ent["sym"]]
    pts = (bars[xi]["c"] - entry) * side - fee_pts
    mae = 0.0
    mfe = 0.0
    for j in range(i + 1, xi + 1):
        adv = (bars[j]["l"] - entry) if side == 1 else (entry - bars[j]["h"])
        mae = min(mae, adv)
        fav = (bars[j]["h"] - entry) if side == 1 else (entry - bars[j]["l"])
        mfe = max(mfe, fav)
    return {"sym": ent["sym"], "in": bars[i + 1]["d"], "out": bars[xi]["d"],
            "side": side, "R": pts / atr, "pts": pts, "bars": xi - i - 1,
            "mae": mae / atr, "mfe": mfe / atr, "reason": reason, "ai": i, "xi": xi}


def stat(trs):
    if not trs:
        return None
    n = len(trs)
    w = [t for t in trs if t["R"] > 0]
    l = [t for t in trs if t["R"] <= 0]
    eq = peak = dd = 0
    for t in sorted(trs, key=lambda x: x["in"]):
        eq += t["R"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    aw = st.mean([t["R"] for t in w]) if w else 0
    al = st.mean([t["R"] for t in l]) if l else 0
    mfe = st.mean([t["mfe"] for t in trs])
    fin = st.mean([t["R"] for t in trs])
    return {"n": n, "win": len(w) / n, "R": sum(t["R"] for t in trs), "dd": dd,
            "avg": st.mean([t["R"] for t in trs]),
            "payoff": (abs(aw / al) if al else 99),
            "hold": st.median([t["bars"] for t in trs]),
            "mae": st.median([t["mae"] for t in trs]),
            "mfe": mfe, "worst": min(t["R"] for t in trs),
            "giveback": (1 - fin / mfe) if mfe > 0 else 0,
            "per_year": sum(t["R"] for t in trs) / 10.0}


def part2(ents):
    print("\n" + "=" * 110)
    print("第二部分：出场方案网格（进场完全一致，n=%d 笔）" % len(ents))
    print("=" * 110)
    print("%-24s %4s %7s %8s %8s %8s %7s %7s %7s %7s %7s %6s" %
          ("出场规则", "n", "胜率", "净R", "回撤R", "年均R", "盈亏比", "持仓", "浮亏", "最差单笔", "均MFE", "回吐"))
    plans = [("A 反向穿 MA34（基准）", "ma34", None, 3.0, 1.2)]
    for n in (10, 15, 18, 20, 22, 24, 26, 28, 30):
        plans.append(("B 跌破 MA%d" % n, "ma", n, 3.0, 1.2))
    for k in (2.0, 2.5, 3.0, 3.5, 4.0):
        plans.append(("D 吊灯 %.1fATR" % k, "chandelier", None, k, 1.2))
    for n in (18, 20, 22, 24, 26):
        plans.append(("E MA%d + 吊灯3" % n, "ma+ch", n, 3.0, 1.2))
    plans.append(("F MA34 + 吊灯3（只加保护）", "ma+ch", 34, 3.0, 1.2))
    for wp in (1.0, 1.2, 1.5):
        plans.append(("C 预警%.1fATR + 吊灯3" % wp, "warn+ch", None, 3.0, wp))
        for n in (22, 26):
            plans.append(("C 预警%.1f + MA%d + 吊灯3" % (wp, n), "warn+ma+ch", n, 3.0, wp))

    res = []
    detail = {}
    for name, rule, mn, k, wp in plans:
        trs = [sim(DATA[e["sym"]], e, rule, k_ch=k, ma_n=mn, warn_pull=wp) for e in ents]
        s = stat(trs)
        res.append((name, s, rule, mn, k, wp))
        detail[name] = trs
        print("%-24s %4d %6.1f%% %+8.1f %+8.1f %+8.1f %7.2f %7.0f %7.1f %+9.1f %7.1f %5.0f%%" %
              (name, s["n"], s["win"] * 100, s["R"], s["dd"], s["per_year"],
               s["payoff"], s["hold"], s["mae"], s["worst"], s["mfe"], s["giveback"] * 100))
    return res, detail


def robust(ents, plans, detail):
    """训练(<2024)/盲测(>=2024) + 去最大 3 笔"""
    print("\n" + "=" * 110)
    print("稳健性：训练(<2024) / 盲测(>=2024) / 去掉最大 3 笔")
    print("=" * 110)
    print("%-24s %10s %10s %10s %10s" % ("出场规则", "训练R", "盲测R", "去极值R", "去极值回撤"))
    keep = []
    for name, rule, mn, k, wp in plans:
        trs = detail[name]
        tr = [t for t in trs if t["in"] < "2024-01-01"]
        te = [t for t in trs if t["in"] >= "2024-01-01"]
        s1, s2 = stat(tr), stat(te)
        top = sorted(trs, key=lambda t: -t["R"])[:3]
        rest = [t for t in trs if t not in top]
        s3 = stat(rest)
        print("%-24s %+10.1f %+10.1f %+10.1f %+10.1f" %
              (name, (s1 or {}).get("R", 0), (s2 or {}).get("R", 0),
               (s3 or {}).get("R", 0), (s3 or {}).get("dd", 0)))
        keep.append((name, s1, s2, s3))
    return keep


def by_symbol(detail, name):
    print("\n分品种（%s）：" % name)
    for c in UNIV:
        trs = [t for t in detail[name] if t["sym"] == c]
        s = stat(trs)
        if s:
            print("  %-4s n=%2d 胜率%5.1f%% 净%+6.1fR 回撤%+6.1fR 均%+.2fR 持%2.0f根" %
                  (c, s["n"], s["win"] * 100, s["R"], s["dd"], s["avg"], s["hold"]))


def yearly(detail, names):
    print("\n分年度净 R：")
    print("%-22s %s" % ("方案", " ".join("%6d" % y for y in range(2017, 2027))))
    for nm in names:
        trs = detail[nm]
        line = []
        for y in range(2017, 2027):
            line.append(sum(t["R"] for t in trs if t["in"][:4] == str(y)))
        print("%-22s %s" % (nm, " ".join("%+6.1f" % v for v in line)))


if __name__ == "__main__":
    prep_all()
    rows = part1()
    ents = []
    for c in UNIV:
        ents += entries(DATA[c], c)
    ents.sort(key=lambda x: x["date"])
    print("\n进场点合计 %d 笔（每段一次，线上口径）" % len(ents))
    res, detail = part2(ents)
    robust(ents, [(n, r, m, k, w) for (n, s, r, m, k, w) in res], detail)
    best = sorted([r for r in res if r[1]], key=lambda r: -(r[1]["R"] / max(1e-9, abs(r[1]["dd"]))))[:4]
    print("\n收益/回撤比 前四：")
    for nm, s, _, _, _, _ in best:
        print("  %-24s 净%+.1fR 回撤%+.1fR 比%.2f 回吐%.0f%%" %
              (nm, s["R"], s["dd"], s["R"] / abs(s["dd"] or 1), s["giveback"] * 100))
    by_symbol(detail, "B 跌破 MA18")
    by_symbol(detail, "A 反向穿 MA34（基准）")
    by_symbol(detail, "D 吊灯 3.0ATR")
    by_symbol(detail, "E MA18 + 吊灯3")
    yearly(detail, ["A 反向穿 MA34（基准）", "B 跌破 MA10", "B 跌破 MA18",
                    "D 吊灯 3.0ATR", "E MA18 + 吊灯3", "C 盘整预警 + 吊灯3"])
