# -*- coding: utf-8 -*-
"""大趋势反转点规律挖掘
==================
方法论（用户定义）：
  - 大趋势 = 收盘价在 MA34 上方（多头段）/ 下方（空头段）
  - 反转点 = 收盘价穿越 MA34 的那一根（上穿=底部反转，下穿=顶部反转）
  - 提取每个反转点之前 10 根 K 线的特征表现，找多空反转各自的共性
  - 目标：新数据命中共性时，在反转点出现【之前】给出预测信号

无前视：特征只用当根及更早数据；"反转点"只是标签。
"""
import datetime as dt
import json
import statistics as st

import build
import cyc

CODES = ["FG", "SC", "M", "C", "RB", "I", "TA", "CU", "SA", "SR"]
MA = 34
PRE = 10          # 反转点前取多少根做画像


def sma(xs, n):
    return cyc.sma(xs, n)


def atr_series(bars, n=14):
    return cyc.atr_series(bars, n)


def segments(bars, ma):
    """按收盘 vs MA34 划分趋势段。返回每段的 (start, end, side)
    side: +1 多头段（收盘在 MA34 上方），-1 空头段。"""
    segs = []
    cur_side = 0
    start = 0
    for i, b in enumerate(bars):
        if ma[i] is None:
            continue
        side = 1 if b["c"] > ma[i] else -1
        if side != cur_side:
            if cur_side != 0 and i - 1 >= start:
                segs.append((start, i - 1, cur_side))
            start = i
            cur_side = side
    segs.append((start, len(bars) - 1, cur_side))
    return segs


def feats(bars, i, ctx):
    """当根 i 的全部因果特征（只用 <= i 的数据）。ctx 预计算好的指标数组。"""
    c = ctx
    b = bars[i]
    atr = c["atr"][i] or 1e-9
    ma34 = c["ma34"][i]
    side = 1 if b["c"] > ma34 else -1
    # 趋势年龄：连续在 MA34 同侧的根数
    age = 0
    j = i
    while j >= 0 and c["ma34"][j] is not None:
        s2 = 1 if bars[j]["c"] > c["ma34"][j] else -1
        if s2 != side:
            break
        age += 1
        j -= 1
    # 本段内的极值（用段起点以来的最高/最低）
    seg_start = i - age + 1
    hi_seg = max(bars[k]["h"] for k in range(max(0, seg_start), i + 1))
    lo_seg = min(bars[k]["l"] for k in range(max(0, seg_start), i + 1))
    f = {}
    f["age"] = age                                  # 趋势已运行根数
    f["dist_ma"] = (b["c"] - ma34) / atr            # 收盘偏离 MA34（ATR）
    f["dist_hi"] = (b["c"] - hi_seg) / atr          # 距段内高点（多头回落深度）
    f["dist_lo"] = (b["c"] - lo_seg) / atr          # 距段内低点
    f["mom5"] = (b["c"] - bars[max(0, i - 5)]["c"]) / atr
    f["mom10"] = (b["c"] - bars[max(0, i - 10)]["c"]) / atr
    f["mom3"] = (b["c"] - bars[max(0, i - 3)]["c"]) / atr
    # 量
    vma = c["vma20"][i]
    f["vr"] = b["v"] / vma if vma else None
    f["vr5"] = max((bars[k]["v"] / c["vma20"][k]) for k in range(max(0, i - 4), i + 1)
                   if c["vma20"][k]) if vma else None
    # 持仓
    oi_now, oi5, oi10 = b.get("oi"), bars[max(0, i - 5)].get("oi"), bars[max(0, i - 10)].get("oi")
    f["doi5"] = (oi_now - oi5) / oi5 * 100 if oi_now and oi5 else None
    f["doi10"] = (oi_now - oi10) / oi10 * 100 if oi_now and oi10 else None
    # 均线结构
    ma10 = c["ma10"][i]
    f["d_ma10"] = (b["c"] - ma10) / atr if ma10 else None
    f["ma10_ma34"] = (ma10 - ma34) / atr if ma10 else None
    # 波动扩张
    a_prev = c["atr"][max(0, i - 20)]
    f["atr_x"] = atr / a_prev if a_prev else None
    # 连跌/连涨根数
    dn = up = 0
    j = i
    while j > 0 and bars[j]["c"] < bars[j - 1]["c"]:
        dn += 1; j -= 1
    j = i
    while j > 0 and bars[j]["c"] > bars[j - 1]["c"]:
        up += 1; j -= 1
    f["dn"] = dn
    f["up"] = up
    # 新高/新低距本根距离（段内极值出现后已过几根）
    k_hi = max(range(max(0, seg_start), i + 1), key=lambda k: bars[k]["h"])
    k_lo = min(range(max(0, seg_start), i + 1), key=lambda k: bars[k]["l"])
    f["since_hi"] = i - k_hi
    f["since_lo"] = i - k_lo
    f["side"] = side
    return f


def load(code):
    adj, _ = build.panama_adjust(code)
    keep = int(8 * 244)
    return adj[-keep:] if len(adj) > keep else adj


def mine():
    """收集所有反转点前10根窗口的特征 + 基线（趋势中段普通K线）特征。"""
    tops, bots, base_up, base_dn = [], [], [], []
    for code in CODES:
        bars = load(code)
        closes = [b["c"] for b in bars]
        ctx = {"ma34": sma(closes, MA), "ma10": sma(closes, 10),
               "atr": atr_series(bars),
               "vma20": sma([float(b["v"]) for b in bars], 20)}
        segs = segments(bars, ctx["ma34"])
        for si in range(1, len(segs)):
            s0, e0, side0 = segs[si - 1]
            s1, e1, side1 = segs[si]
            r = s1  # 反转点=新段第一根
            if r < 60:
                continue
            win = [feats(bars, k, ctx) for k in range(max(0, r - PRE), r)]
            win.append(feats(bars, r, ctx))
            if side1 == -1:      # 上穿→下穿 = 顶部反转
                tops.append({"code": code, "date": bars[r]["d"], "win": win})
            else:
                bots.append({"code": code, "date": bars[r]["d"], "win": win})
            # 基线：趋势段中部（非反转前10根内）的普通K线
            mid = (s1 + e1) // 2
            if mid > r + PRE + 10:
                for k in range(mid - 5, min(mid + 5, e1)):
                    if abs(k - r) > PRE:
                        f = feats(bars, k, ctx)
                        (base_up if side1 == 1 else base_dn).append(f)
    return tops, bots, base_up, base_dn


def pct_of(windows, key, cond):
    """窗口集合里，某条件在至少一个窗口根上成立的样本占比。"""
    n = sum(1 for w in windows if any(cond(f) for f in w["win"]))
    return n / len(windows) * 100 if windows else 0


def base_frac(base, cond):
    n = sum(1 for f in base if cond(f))
    return n / len(base) * 100 if base else 0


FEATS = [
    ("回落距段高点≥1.5ATR", lambda f: f["dist_hi"] <= -1.5),
    ("回落距段高点≥2.5ATR", lambda f: f["dist_hi"] <= -2.5),
    ("距段高点≥3根前", lambda f: f["since_hi"] >= 3),
    ("5日动量为负", lambda f: f["mom5"] < 0),
    ("10日动量为负", lambda f: f["mom10"] < 0),
    ("3日动量为负", lambda f: f["mom3"] < 0),
    ("跌破MA10", lambda f: f["d_ma10"] is not None and f["d_ma10"] < 0),
    ("跌破MA10≥1ATR", lambda f: f["d_ma10"] is not None and f["d_ma10"] < -1),
    ("单日量比≥1.4", lambda f: (f["vr"] or 0) >= 1.4),
    ("5日内量比≥1.4", lambda f: (f["vr5"] or 0) >= 1.4),
    ("持仓5日降≥2%", lambda f: f["doi5"] is not None and f["doi5"] <= -2),
    ("持仓10日降≥5%", lambda f: f["doi10"] is not None and f["doi10"] <= -5),
    ("持仓5日升≥2%", lambda f: f["doi5"] is not None and f["doi5"] >= 2),
    ("持仓10日升≥5%", lambda f: f["doi10"] is not None and f["doi10"] >= 5),
    ("ATR扩张≥1.2", lambda f: (f["atr_x"] or 1) >= 1.2),
    ("ATR收缩≤0.9", lambda f: (f["atr_x"] or 1) <= 0.9),
    ("连续3根以上下跌", lambda f: f["dn"] >= 3),
    ("连续2根以上下跌", lambda f: f["dn"] >= 2),
    ("趋势年龄≥40根", lambda f: f["age"] >= 40),
    ("趋势年龄≥60根", lambda f: f["age"] >= 60),
    ("MA10在MA34下方", lambda f: f["ma10_ma34"] is not None and f["ma10_ma34"] < 0),
]

FEATS_B = [
    ("反弹距段低点≥1.5ATR", lambda f: f["dist_lo"] >= 1.5),
    ("反弹距段低点≥2.5ATR", lambda f: f["dist_lo"] >= 2.5),
    ("距段低点≥3根前", lambda f: f["since_lo"] >= 3),
    ("5日动量为正", lambda f: f["mom5"] > 0),
    ("10日动量为正", lambda f: f["mom10"] > 0),
    ("3日动量为正", lambda f: f["mom3"] > 0),
    ("站上MA10", lambda f: f["d_ma10"] is not None and f["d_ma10"] > 0),
    ("站上MA10≥1ATR", lambda f: f["d_ma10"] is not None and f["d_ma10"] > 1),
    ("单日量比≥1.4", lambda f: (f["vr"] or 0) >= 1.4),
    ("5日内量比≥1.4", lambda f: (f["vr5"] or 0) >= 1.4),
    ("持仓5日升≥2%", lambda f: f["doi5"] is not None and f["doi5"] >= 2),
    ("持仓10日升≥5%", lambda f: f["doi10"] is not None and f["doi10"] >= 5),
    ("持仓5日降≥2%", lambda f: f["doi5"] is not None and f["doi5"] <= -2),
    ("持仓10日降≥5%", lambda f: f["doi10"] is not None and f["doi10"] <= -5),
    ("ATR扩张≥1.2", lambda f: (f["atr_x"] or 1) >= 1.2),
    ("ATR收缩≤0.9", lambda f: (f["atr_x"] or 1) <= 0.9),
    ("连续3根以上上涨", lambda f: f["up"] >= 3),
    ("连续2根以上上涨", lambda f: f["up"] >= 2),
    ("趋势年龄≥40根", lambda f: f["age"] >= 40),
    ("趋势年龄≥60根", lambda f: f["age"] >= 60),
    ("MA10在MA34上方", lambda f: f["ma10_ma34"] is not None and f["ma10_ma34"] > 0),
]


def report():
    tops, bots, base_up, base_dn = mine()
    print("样本：顶反转 %d 个，底反转 %d 个；基线 多头段 %d 根 / 空头段 %d 根"
          % (len(tops), len(bots), len(base_up), len(base_dn)))
    print("\n=== 顶部反转前10根：特征在「至少一根上出现」的窗口占比 vs 多头段普通K线占比 ===")
    print("%-24s %10s %10s" % ("特征", "顶窗口%", "多头基线%"))
    rows = []
    for name, cond in FEATS:
        a = pct_of(tops, None, cond)
        b = base_frac(base_up, cond)
        rows.append((a - b, name, a, b))
    rows.sort(reverse=True)
    for d, name, a, b in rows:
        print("%-24s %9.1f%% %9.1f%%   差%+6.1f" % (name, a, b, d))
    print("\n=== 底部反转前10根 ===")
    rows = []
    for name, cond in FEATS_B:
        a = pct_of(bots, None, cond)
        b = base_frac(base_dn, cond)
        rows.append((a - b, name, a, b))
    rows.sort(reverse=True)
    for d, name, a, b in rows:
        print("%-24s %9.1f%% %9.1f%%   差%+6.1f" % (name, a, b, d))
    return tops, bots


if __name__ == "__main__":
    report()
