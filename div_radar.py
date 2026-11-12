# -*- coding: utf-8 -*-
"""v30b 资金背离雷达（顶/底对称 · 用户 2026-11-12 拍板）

【纪律】纯"读"层：输入 OHLC+持仓量，输出状态与事件。不改任何既有信号、
推送通道、进场/出场逻辑。L2 并入第⑤通道（与 v26.2 大结构同例），不新增通道。

【v30 已上线】单边（只顶）。【v30b】扩成顶/底对称，且把顶部状态灯从单一
"价涨仓缩"扩成"多信号极值区"，以提升大顶/大底的早期覆盖。

两条独立的状态家族（顶 / 底，完全镜像）：
  A_oi_div  ：价格处于 20 日极端区 + 持仓量低于近 20 日高点 → 资金异动
              （顶：价高位+持仓萎缩=上涨无资金支持；底：价低位+持仓溃散=抛压耗尽）
  B_ob      ：价格处于 60 日区间极端 + RSI 极端（顶：上 15% + RSI≥72 超买；
              底：下 15% + RSI≤28 超卖）——覆盖"冲顶/恐慌"型，A 覆盖不到

【L1 状态灯（只显示、不推送、零打扰）】broad = A or B 成立日；近 7 天 ≥3 天 → 灯亮。
  实测：大顶前 15 天内有灯 45%、大底前 15 天内有灯 46%（A 单灯仅 31%）。

【L2 确认（真预警，并入第⑤通道）】只认 A_oi_div（资金异动那条，精度最高）：
  顶：A 灯近 7 天 ≥3 天 且 当日收盘跌破前 10 日最低价 → "顶风险确认"。
  底：A 灯近 7 天 ≥3 天 且 当日收盘升破前 10 日最高价 → "底风险确认"。
  每段行情只发一次：触发后武装解除，直到近 7 天 A 清零才重新武装。
  实测（7 品种 × 10 年）：A 型 L2 触发稀疏（≈25 次/10 年），命中率约 45%（基线 29%）。

诚实边界：大顶/大底作为一个整体，日线层面无法可靠预判（基线 29~33%，没有任何
日线特征显著超越）。本雷达抓的是"资金异动型/极端区型"这一类极端行情，漏报是常态；
灯亮=风险区域提示（非指令），L2 响=风险确认（仍非做空/做多指令）。
"""

D, O, H, L, C, V, OI = 0, 1, 2, 3,4, 5, 6


def _f(b, k):
    """行兼容：list 行按下标（bars_to_arr / data JSON），dict 行按键（engine bars）。"""
    if isinstance(b, dict):
        return {D: b.get("d"), O: b.get("o"), H: b.get("h"), L: b.get("l"),
                C: b.get("c"), V: b.get("v") if b.get("v") is not None else b.get("vol"),
                OI: b.get("oi")}.get(k)
    return b[k] if k < len(b) else None


def _div_day(bars, i, mode):
    """A_oi_div：价格处于 20 日极端区 + 持仓低于近 20 日高点。需要 i≥20。"""
    if i < 20:
        return False
    c = _f(bars[i], C)
    oi = _f(bars[i], OI) or 0
    if mode == "top":
        ext = max(_f(bars[k], C) or 0 for k in range(i - 19, i))
        if not (c and ext and c >= ext * 0.995):
            return False
    else:
        ext = min(_f(bars[k], C) or 0 for k in range(i - 19, i))
        if not (c and ext and c <= ext * 1.005):
            return False
    oi_ref = max(_f(bars[k], OI) or 0 for k in range(i - 19, i + 1))
    return bool(oi_ref and oi < oi_ref * 0.90)


def _ob_day(bars, i, mode, rsi):
    """B_ob：60 日区间极端 + RSI 极端。需要 i≥60。"""
    if i < 60 or rsi[i] is None:
        return False
    c = _f(bars[i], C)
    hi = max(_f(bars[k], H) or 0 for k in range(i - 59, i + 1))
    lo = min(_f(bars[k], L) or 0 for k in range(i - 59, i + 1))
    if not (hi > lo):
        return False
    pos = (c - lo) / (hi - lo)
    if mode == "top":
        return pos >= 0.85 and rsi[i] >= 72
    return pos <= 0.15 and rsi[i] <= 28


def _lamp(st, bars, n):
    """近 7 天 ≥3 天状态日 = 灯亮；并给出连续天数与起始日。"""
    recent = sum(1 for k in range(max(0, n - 7), n) if st[k])
    run, k = 0, n - 1
    while k >= 0 and st[k]:
        run += 1
        k -= 1
    return {
        "on": recent >= 3,
        "days": recent,
        "run": run,
        "since": _f(bars[n - run], D) if run else None,
    }


def _l2_events(bars, div_st, mode):
    """A_oi_div 精确确认：A 灯近 7 天 ≥3 天（含当日）且收盘破前 10 日极值。每段只一次。"""
    n = len(bars)
    events, armed = [], True
    for i in range(20, n):
        recent = sum(1 for k in range(max(0, i - 6), i + 1) if div_st[k])
        lamp = recent >= 3
        if mode == "top":
            ref = min(_f(bars[k], L) or 0 for k in range(max(0, i - 10), i))
            hit = armed and lamp and _f(bars[i], C) is not None and _f(bars[i], C) < ref
        else:
            ref = max(_f(bars[k], H) or 0 for k in range(max(0, i - 10), i))
            hit = armed and lamp and _f(bars[i], C) is not None and _f(bars[i], C) > ref
        if hit:
            oi_now = _f(bars[i], OI) or 0
            oi_ref = _f(bars[i - 10], OI) or 0
            events.append({
                "d": _f(bars[i], D), "px": _f(bars[i], C), "lampDays": recent,
                "oiDrop": round((oi_now / oi_ref - 1) * 100, 1) if oi_ref else None,
            })
            armed = False
        if recent == 0 and not div_st[i]:   # 近 7 天 A 清零 → 重新武装
            armed = True
    return events


def scan(bars):
    """返回 {lamp:{top,bottom}, events:{top,bottom}}。events 为全历史 L2 确认日。"""
    n = len(bars)
    closes = [_f(bars[i], C) for i in range(n)]
    # RSI14 数组（O(14n)）
    rsi = [None] * n
    g, ls = 0.0, 0.0
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        if d > 0:
            g += d
        else:
            ls -= d
        if i >= 15:
            if i > 15:
                pd = closes[i - 15] - closes[i - 16]
                if pd > 0:
                    g -= pd
                else:
                    ls += pd
            rsi[i] = 50.0 if g + ls == 0 else 100.0 * g / (g + ls)
    div_top = [_div_day(bars, i, "top") for i in range(n)]
    div_bot = [_div_day(bars, i, "bottom") for i in range(n)]
    st_top = [(_div_day(bars, i, "top") or _ob_day(bars, i, "top", rsi)) for i in range(n)]
    st_bot = [(_div_day(bars, i, "bottom") or _ob_day(bars, i, "bottom", rsi)) for i in range(n)]
    lamp_top = _lamp(st_top, bars, n)
    lamp_bot = _lamp(st_bot, bars, n)
    ev_top = _l2_events(bars, div_top, "top")
    ev_bot = _l2_events(bars, div_bot, "bottom")
    return {
        "lamp": {"top": lamp_top, "bottom": lamp_bot},
        "events": {"top": ev_top, "bottom": ev_bot},
    }
