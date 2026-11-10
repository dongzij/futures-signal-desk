# -*- coding: utf-8 -*-
"""路况标注库（v21）

严格按用户给出的量化标准实现，输出两套标注：

A) 事后切片版 label_regime()  —— 用于【路况分析表】的统计
   用 zigzag 摆动腿识别趋势段，允许使用腿的终点（事后视角），用于回答
   "过去 10 年有多少次大趋势、多少时间在震荡"。

B) 因果版 causal_regime()     —— 用于【策略回测】
   只使用截至当日 t 的数据，绝不引用未来 K 线，用于动态策略切换。

------------------------------------------------------------
用户标准（原样落地）
大趋势（三条同时成立）：
  1. 从阶段高/低点算起：涨跌幅 ≥ 12%  或  绝对空间 ≥ 3×日线ATR
  2. 持续时间 ≥ 15 个交易日
  3. MA34 斜率明显倾斜
震荡市（三条同时成立）：
  1. 窗口 15~30 交易日（取 20）
  2. 窗口内最高最低振幅 < 10%
  3. 价格在 MA34 上下穿梭 ≥ 3 次，且 MA34 走平
------------------------------------------------------------
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

# ---- 可调常量（全部先定后跑，不在结果上回调） ----
W_RANGE = 20          # 震荡判定窗口（用户给 15~30，取中值 20）
AMP_RANGE = 0.10      # 震荡振幅上限 10%
CROSS_MIN = 3         # 震荡穿梭次数下限
REL_FLAT = 0.50       # MA34 走平：窗口内 MA34 位移 < 窗口振幅的 50%（相对尺度，自适应波动率）
AMP_TREND = 0.12      # 趋势幅度 12%
ATR_TREND = 3.0       # 趋势空间 3 ATR
DAYS_TREND = 15       # 趋势最短持续 15 个交易日
SLOPE_TILT_LEG = 2.0  # 腿内 MA34 净位移 ≥ 2.0 ATR 视为"明显倾斜"（全样本 42.5% 天数达标）
MIN_THR_PCT = 0.025   # zigzag 确认阈值下限 2.5%（防 ATR 极小时噪声）


def _atr(S, i):
    a = S["atr"][i]
    return a if a else 1e-9


def swing_pivots(bars, S):
    """zigzag 摆动点：[(idx, price, kind)]，kind=1 顶 / -1 底。
    确认阈值 = min(12%, 3ATR)，即幅度满足"12% 或 3ATR"其一时即视为一次有效摆动。"""
    n = len(bars)
    if n < 10:
        return []
    piv = [(0, bars[0]["c"], 0)]
    dirn = 0
    hi = lo = bars[0]["c"]
    hi_i = lo_i = 0
    for i in range(1, n):
        c = bars[i]["c"]
        a = _atr(S, i)
        thr = max(min(AMP_TREND * c, ATR_TREND * a), MIN_THR_PCT * c)
        if dirn >= 0:
            if c > hi:
                hi, hi_i = c, i
            elif c <= hi - thr:
                piv.append((hi_i, hi, 1))
                dirn = -1
                lo, lo_i = c, i
        elif dirn <= 0:
            if c < lo:
                lo, lo_i = c, i
            elif c >= lo + thr:
                piv.append((lo_i, lo, -1))
                dirn = 1
                hi, hi_i = c, i
    piv.append((n - 1, bars[-1]["c"], 0))
    return piv


def legs_of(bars, S):
    """由摆动点生成腿，并判定每条腿是否为【大趋势腿】。"""
    piv = swing_pivots(bars, S)
    out = []
    for k in range(len(piv) - 1):
        i0, p0, _ = piv[k]
        i1, p1, _ = piv[k + 1]
        if i1 <= i0:
            continue
        days = i1 - i0 + 1
        amp_pct = abs(p1 / p0 - 1.0) if p0 else 0.0
        a0, a1 = _atr(S, i0), _atr(S, i1)
        a = (a0 + a1) / 2.0
        amp_atr = abs(p1 - p0) / a if a else 0.0
        m0, m1 = S["ma34"][i0], S["ma34"][i1]
        slope = ((m1 - m0) / a) if (m0 and m1 and a) else 0.0
        side = 1 if p1 > p0 else -1
        # 三条同时成立才算大趋势
        c1 = (amp_pct >= AMP_TREND) or (amp_atr >= ATR_TREND)
        c2 = days >= DAYS_TREND
        c3 = (slope * side >= SLOPE_TILT_LEG)      # 同向且倾斜足够
        out.append({
            "i0": i0, "i1": i1, "d0": bars[i0]["d"], "d1": bars[i1]["d"],
            "p0": p0, "p1": p1, "side": side, "days": days,
            "amp_pct": round(amp_pct * 100, 1), "amp_atr": round(amp_atr, 2),
            "slope_atr": round(slope, 2),
            "c_amp": c1, "c_days": c2, "c_slope": c3,
            "is_trend": bool(c1 and c2 and c3),
        })
    return out


def _crosses(bars, S, t, w):
    """窗口内收盘价穿越 MA34 的次数"""
    cnt = 0
    prev = None
    for j in range(max(0, t - w + 1), t + 1):
        m = S["ma34"][j]
        if not m:
            continue
        s = 1 if bars[j]["c"] > m else -1
        if prev is not None and s != prev:
            cnt += 1
        prev = s
    return cnt


def _window_amp(bars, t, w):
    seg = bars[max(0, t - w + 1): t + 1]
    if not seg:
        return 0.0
    hi = max(x["h"] for x in seg)
    lo = min(x["l"] for x in seg)
    mid = sum(x["c"] for x in seg) / len(seg)
    return (hi - lo) / mid if mid else 0.0


def _is_range(bars, S, t):
    """震荡判定（三条同时成立）：
       ① 20 日窗口振幅 < 10%
       ② 收盘在 MA34 上下穿梭 ≥ 3 次
       ③ MA34 走平：窗口内 MA34 位移 < 窗口振幅的 50%
    返回 (bool, 诊断字典)"""
    if t < W_RANGE + 34:
        return False, {}
    w = W_RANGE
    seg = bars[t - w + 1: t + 1]
    hi = max(x["h"] for x in seg)
    lo = min(x["l"] for x in seg)
    mid = sum(x["c"] for x in seg) / len(seg)
    amp = (hi - lo) / mid if mid else 99.0
    cr = _crosses(bars, S, t, w)
    mt, mp = S["ma34"][t], S["ma34"][t - w]
    if not mt or not mp:
        return False, {}
    span = hi - lo
    rel = abs(mt - mp) / span if span > 0 else 99.0
    ok = (amp < AMP_RANGE) and (cr >= CROSS_MIN) and (rel < REL_FLAT)
    return ok, {"amp": round(amp * 100, 2), "cross": cr, "rel_ma": round(rel, 2)}


def causal_regime(bars, S, t):
    """因果路况：只看到 t 日收盘。返回 trend_up/trend_dn/range/transition/na。"""
    if t < 60:
        return "na"
    w = W_RANGE
    m_t, m_p = S["ma34"][t], S["ma34"][t - w]
    a = _atr(S, t)
    if not m_t or not m_p:
        return "na"
    slope = (m_t - m_p) / a                  # 20 日 MA34 位移（ATR 计）
    c = bars[t]["c"]

    seg = bars[t - w + 1: t + 1]
    ext_hi = max(x["h"] for x in seg)
    ext_lo = min(x["l"] for x in seg)
    up_amp = c / ext_lo - 1.0 if ext_lo else 0.0
    dn_amp = 1.0 - c / ext_hi if ext_hi else 0.0

    # 趋势：斜率倾斜 + 顺斜率方向的空间 ≥12% 或 ≥3ATR
    if abs(slope) >= 0.8:                     # 20 日 MA34 位移 ≥0.8ATR ≈ 腿内 2ATR 的同尺
        room = up_amp if slope > 0 else dn_amp
        if room >= AMP_TREND or room * c >= ATR_TREND * a:
            return "trend_up" if slope > 0 else "trend_dn"
    ok, _ = _is_range(bars, S, t)
    if ok:
        return "range"
    return "transition"


def label_regime(bars, S):
    """事后切片版逐日标签：trend_up / trend_dn / range / transition。
    先铺趋势腿（事后），未覆盖的日子再用 20 日窗口判震荡，其余为过渡。"""
    n = len(bars)
    lab = ["transition"] * n
    legs = legs_of(bars, S)
    for lg in legs:
        if lg["is_trend"]:
            tag = "trend_up" if lg["side"] > 0 else "trend_dn"
            for j in range(lg["i0"], lg["i1"] + 1):
                lab[j] = tag
    for t in range(n):
        if lab[t] != "transition":
            continue
        ok, _ = _is_range(bars, S, t)
        if ok:
            lab[t] = "range"
    return lab, legs


def summarize(bars, S, lab, legs):
    """每品种路况汇总"""
    n = len(bars)
    warm = min(60, n)
    cnt = {"trend_up": 0, "trend_dn": 0, "range": 0, "transition": 0}
    for t in range(warm, n):
        cnt[lab[t]] = cnt.get(lab[t], 0) + 1
    tot = max(1, n - warm)
    tr = [lg for lg in legs if lg["is_trend"]]
    up = [lg for lg in tr if lg["side"] > 0]
    dn = [lg for lg in tr if lg["side"] < 0]
    all_legs = [lg for lg in legs if lg["days"] >= 5]
    return {
        "bars": n,
        "start": bars[warm]["d"] if n > warm else bars[0]["d"],
        "end": bars[-1]["d"],
        "years": round((n - warm) / 244.0, 1),
        "n_legs": len(all_legs),
        "n_trend": len(tr), "n_up": len(up), "n_dn": len(dn),
        "trend_days": cnt["trend_up"] + cnt["trend_dn"],
        "up_days": cnt["trend_up"], "dn_days": cnt["trend_dn"],
        "range_days": cnt["range"], "trans_days": cnt["transition"],
        "pct_trend": round((cnt["trend_up"] + cnt["trend_dn"]) / tot * 100, 1),
        "pct_range": round(cnt["range"] / tot * 100, 1),
        "pct_trans": round(cnt["transition"] / tot * 100, 1),
        "avg_trend_days": round(sum(x["days"] for x in tr) / len(tr), 1) if tr else 0,
        "max_trend_days": max((x["days"] for x in tr), default=0),
        "avg_trend_amp": round(sum(x["amp_pct"] for x in tr) / len(tr), 1) if tr else 0,
        "max_trend_amp": max((x["amp_pct"] for x in tr), default=0),
        "avg_leg_amp": round(sum(x["amp_pct"] for x in all_legs) / len(all_legs), 1) if all_legs else 0,
        "legs": legs,
    }
