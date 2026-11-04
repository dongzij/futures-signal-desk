# -*- coding: utf-8 -*-
"""
周期拐点研究引擎
================
目标：不做次日方向预测，只研究「波段级（周/月）顶部与底部」，
     并回答一个具体问题：真顶/真底 与 假顶/假底 在当时可见的指标上有什么区别。

无前视原则：
  - 候选拐点用「局部极值 + w 根确认」定义，特征只取确认日及之前的数据
  - 真/假标签使用拐点之后的数据（这是标签，不是特征）
"""
import math

# ---------------------------------------------------------------- 基础指标


def sma(xs, n):
    out = [None] * len(xs)
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def atr_series(bars, n=14):
    """Wilder ATR"""
    trs = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b["h"] - b["l"])
        else:
            pc = bars[i - 1]["c"]
            trs.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    out = [None] * len(bars)
    if len(bars) < n:
        return out
    first = sum(trs[:n]) / n
    out[n - 1] = first
    for i in range(n, len(bars)):
        out[i] = (out[i - 1] * (n - 1) + trs[i]) / n
    # 前置填充
    for i in range(n - 1):
        out[i] = first
    return out


def rsi_series(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100 - 100 / (1 + (ag / al if al else 99))
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + (ag / al if al else 99))
    return out


# ---------------------------------------------------------------- 拐点识别


def find_peaks(bars, w=3):
    """局部极值：bar i 的 high 在 [i-w, i+w] 内最高 → 顶候选；low 最低 → 底候选。
    返回 (idx, 'H'|'L')，按 idx 升序。确认日为 idx+w。"""
    n = len(bars)
    out = []
    for i in range(w, n - w):
        h = bars[i]["h"]
        if all(h >= bars[k]["h"] for k in range(i - w, i + w + 1)):
            if not (bars[i]["h"] == bars[i - 1]["h"] and i - 1 >= w):
                out.append((i, "H"))
        l = bars[i]["l"]
        if all(l <= bars[k]["l"] for k in range(i - w, i + w + 1)):
            if not (bars[i]["l"] == bars[i - 1]["l"] and i - 1 >= w):
                out.append((i, "L"))
    # 同一根既是顶又是底 → 取幅度大的
    dedup = {}
    for i, t in out:
        if i in dedup:
            rng = bars[i]["h"] - bars[i]["l"]
            if rng > 0:
                continue
        dedup[i] = t
    return sorted(dedup.items())
