# -*- coding: utf-8 -*-
"""v30 资金背离雷达（纯叠加层 · 用户 2026-11-12 拍板 L1+L2 都上）

【纪律】本模块只做"读"：输入 OHLC+持仓量，输出状态与事件。
不修改任何既有信号、推送通道、进场/出场逻辑。L2 推送并入第⑤通道
（与 v26.2 大结构并入第⑤通道同一先例），不新增第 7 条通道。

【背景】FG 2026-09-11 大顶前 10 天：价格 +9%、持仓量 -30%（价涨仓缩，
无新增资金的上涨）。异常在持仓量里；单日背离被主力换月污染（全局精度
仅 38%，基线 29.6%），"持续性"才能区分噪音——换月降仓几天就恢复，
真撤退连续 7~11 天。

L1 状态（只显示、不推送、零打扰）：
    当日收盘 ≥ 99.5% × 前 19 日最高收盘，且 持仓量 < 90% × 近 20 日持仓高点
    → 记 1 天"背离日"；近 7 天 ≥ 3 天背离日 = 灯亮。
L2 确认（真预警）：
    灯亮（近 7 天 ≥ 3 天背离日）且当日收盘跌破前 10 日最低价 → 确认事件。
    每段行情只发一次：触发后武装解除，直到近 7 天背离清零才重新武装。

实测（7 品种 × 10 年 · 397 个大顶，详见 reports/v29b）：
    L2 触发 33 次 / 10 年，命中率 45.5%（基线 29.6%）；
    FG 2026-09-11 于顶后第 1 天（崩盘当天 09-14）触发。
"""

D, O, H, L, C, V, OI = 0, 1, 2, 3, 4, 5, 6


def _f(b, k):
    """行兼容：list 行按下标（bars_to_arr / data JSON），dict 行按键（engine bars）。"""
    if isinstance(b, dict):
        return {D: b.get("d"), O: b.get("o"), H: b.get("h"), L: b.get("l"),
                C: b.get("c"), V: b.get("v") if b.get("v") is not None else b.get("vol"),
                OI: b.get("oi")}.get(k)
    return b[k] if k < len(b) else None


def _div_day(bars, i):
    """单日背离判定：价在 20 日高位区 + 持仓明显低于近 20 日高点。需要 i≥20。"""
    if i < 20:
        return False
    c = _f(bars[i], C)
    hi = max(_f(bars[k], C) or 0 for k in range(i - 19, i))
    oi20 = max(_f(bars[k], OI) or 0 for k in range(i - 19, i + 1))
    oi_now = _f(bars[i], OI) or 0
    return bool(c and hi and oi20 and c >= hi * 0.995 and oi_now < oi20 * 0.90)


def _lamp(st, bars, n):
    recent = sum(1 for k in range(max(0, n - 7), n) if st[k])
    run, k = 0, n - 1
    while k >= 0 and st[k]:
        run += 1
        k -= 1
    return {
        "on": recent >= 3,
        "days": recent,                                   # 近 7 天背离天数（3~7）
        "run": run,                                       # 当前连续背离天数
        "since": _f(bars[n - run], D) if run else None,   # 连续背离起始日
    }


def scan(bars):
    """返回 {"lamp": {...}, "events": [...]}。events 为全历史 L2 确认日（供图上标记）。"""
    n = len(bars)
    st = [_div_day(bars, i) for i in range(n)]
    events, armed = [], True
    for i in range(20, n):
        recent = sum(1 for k in range(max(0, i - 6), i + 1) if st[k])
        lamp = recent >= 3
        lo10 = min(_f(bars[k], L) or 0 for k in range(max(0, i - 10), i))
        c = _f(bars[i], C)
        if armed and lamp and lo10 and c is not None and c < lo10:
            oi_now = _f(bars[i], OI) or 0
            oi_ref = _f(bars[i - 10], OI) or 0
            events.append({
                "d": _f(bars[i], D), "px": c, "lampDays": recent,
                "oiDrop": round((oi_now / oi_ref - 1) * 100, 1) if oi_ref else None,
            })
            armed = False
        # 同一段行情只确认一次：近 7 天背离清零（且当日也不背离）才重新武装
        if recent == 0 and not st[i]:
            armed = True
    return {"lamp": _lamp(st, bars, n), "events": events}
