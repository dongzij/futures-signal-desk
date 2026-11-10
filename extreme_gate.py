# -*- coding: utf-8 -*-
"""v27 交易铁律【顶不做多 · 底不做空】—— 单一真源。

定稿依据（reports/v27_极值偏离度回测.md，7 品种 × 10 年 × 990 笔进场事件）：
    N = 60 个交易日，涨幅 >= 20% 且 区间位置 >= 0.90（做空对称：<= 0.10）
    被拦 38 笔：胜率 28.9% / 盈亏比 0.91 / 净 R -3.6（净亏损）
    样本外 2023-12 之后 8 笔：胜率 0.0% / 平均 -1.48R（全亏）
    账本复跑：147 -> 138 笔，净收益 -7.2%，最大回撤 45.8% -> 46.5%（几乎不变）

口径（严格与回测脚本 extreme_gate_backtest.ext_series 一致）：
    LL = min(low[i-N+1..i])，HH = max(high[i-N+1..i])，C = close[i]
    upPct = (C / LL - 1) * 100        距 N 日最低点涨幅
    dnPct = (1 - C / HH) * 100        距 N 日最高点跌幅
    pos   = (C - LL) / (HH - LL)      价格在 N 日区间的位置（0=底 1=顶）

判定（只用 i 及之前的 K 线，无未来函数；在【信号生成日】判定，次日开盘执行）：
    做多：upPct >= 20 且 pos >= 0.90   → 已涨太多且贴在顶上，拦截
    做空：dnPct >= 20 且 pos <= 0.10   → 已跌太多且贴在底上，拦截

拦截对象仅限 5 类【进场】信号：pred / warn / mw / confirm / tf
出场类（xsig / defend / 吊灯 / MA26 / 30% 熔断 / 白圈）一律不动 —— 这是纪律底线。
"""
import bisect

LOOKBACK = 60
PCT_LIMIT = 20.0
POS_LIMIT = 0.90
ENTRY_KINDS = ("pred", "warn", "mw", "confirm", "tf")
LABEL = "顶不做多 · 底不做空"


def deviation(bars, i, n=LOOKBACK):
    """信号生成日 i 的极值偏离度。bars 为 dict 列表（含 h/l/c/d）。"""
    if i is None or i < n - 1 or i >= len(bars):
        return None
    lo = min(b["l"] for b in bars[i - n + 1:i + 1])
    hi = max(b["h"] for b in bars[i - n + 1:i + 1])
    c = bars[i]["c"]
    if not lo or not hi or hi <= lo or c <= 0:
        return None
    return {"upPct": (c / lo - 1) * 100,
            "dnPct": (1 - c / hi) * 100,
            "pos": (c - lo) / (hi - lo),
            "ll": lo, "hh": hi, "close": c}


def blocked(bars, i, side, kind=None, n=LOOKBACK,
            pct=PCT_LIMIT, pos=POS_LIMIT):
    """该进场信号是否触犯铁律。kind 不属于进场类时永远不拦。"""
    if kind is not None and kind not in ENTRY_KINDS:
        return False
    if side not in ("long", "short"):
        return False
    d = deviation(bars, i, n)
    if not d:
        return False
    if side == "long":
        return d["upPct"] >= pct and d["pos"] >= pos
    return d["dnPct"] >= pct and d["pos"] <= 1 - pos


def date_index(bars):
    return {b["d"]: i for i, b in enumerate(bars)}


def blocked_at(bars, date, side, kind=None):
    """按日期判定（daily.py 用）。日期不存在时取不晚于它的最后一根日线。"""
    if not date or side not in ("long", "short"):
        return False
    if kind is not None and kind not in ENTRY_KINDS:
        return False
    ds = [b["d"] for b in bars]
    j = bisect.bisect_right(ds, date) - 1
    if j < LOOKBACK - 1:
        return False
    # 只允许同一天或极近的历史日期，避免把很久以前的旧信号误判
    return blocked(bars, j, side, kind)


def gate_for(bars):
    """返回给 sig_pack.build 用的 gate(i, side, kind)，带缓存。"""
    cache = {}

    def g(i, side, kind=None):
        key = (i, side, kind)
        if key not in cache:
            cache[key] = blocked(bars, i, side, kind)
        return cache[key]

    return g


def cfg():
    """写进 index.json 的公开口径。"""
    return {
        "version": "v27",
        "enabled": True,
        "label": LABEL,
        "lookback": LOOKBACK,
        "pct": PCT_LIMIT,
        "pos": POS_LIMIT,
        "kinds": list(ENTRY_KINDS),
        "rule": "信号生成日回看 %d 根：做多距最低点涨幅 ≥%.0f%% 且区间位置 ≥%.2f → 拦截；"
                "做空距最高点跌幅 ≥%.0f%% 且区间位置 ≤%.2f → 拦截。"
                % (LOOKBACK, PCT_LIMIT, POS_LIMIT, PCT_LIMIT, 1 - POS_LIMIT),
        "formula": "upPct=(C/LL-1)*100, dnPct=(1-C/HH)*100, pos=(C-LL)/(HH-LL)",
        "exitUnchanged": True,
        "advisoryOnly": False,
        "backtest": {
            "sample": 990, "blocked": 38, "blockedWin": 28.9,
            "blockedPf": 0.91, "blockedNetR": -3.6,
            "keptWin": 41.2, "keptPf": 1.59,
            "oosN": 8, "oosWin": 0.0, "oosAvgR": -1.482,
            "ledgerBefore": {"n": 147, "win": 47.6, "pf": 2.48,
                             "net": 153996, "mdd": 45.8},
            "ledgerAfter": {"n": 138, "win": 45.6, "pf": 2.44,
                            "net": 142852, "mdd": 46.5},
        },
        "source": "extreme_gate_backtest.py / extreme_gate_verify.py",
    }
