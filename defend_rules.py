# -*- coding: utf-8 -*-
"""v15 失效打叉 · 全量判定规则（用户 2026-11-08 拍板）
v17：按模块二对比表把「顶底雷达 & 打叉日 ADX≥30」降级为【弱叉】。

只做展示与提示，绝不改进场（MA34）与出场（MA26 + 吊灯 3ATR）机制。
弱叉不删数据、不改判定，只打 weak 标记：图上空心圈、列表标「弱」、不单独推微信。

满足其一即算失效：
  R1 反向幅度：信号后收盘相对信号日收盘逆行 > 1.5 ATR
  R2 均线破位：做多收盘下穿 MA26 / MA34，做空收盘上穿（与出场雷达 armed 口径一致）
  R3 时间衰竭：信号后 5 根内未创新高/新低，反而收盘跌破信号日低点 / 涨破高点
  R4 原版止损失效：按 MA26 反向穿越 + 吊灯 3ATR 跟踪，触发离场即算失效

窗口：R1/R2 = 20 根，R3 = 5 根，R4 = 进场后一直跟踪到离场（原机制，不设窗口）。
每笔信号只取【最早】一次失效，同日多条标准同时成立时合并展示。
覆盖信号源：4/4 建仓(pred)、3/4 预警(warn)、顶底雷达(exit_radar suggest/strong)。
"""
import engine

REV_ATR = 1.5
LOOKBACK = 20
DECAY_DAYS = 5
MA_KEYS = ("ma%d" % engine.EXIT_MA, "ma%d" % engine.MA)   # ma26 / ma34

# v17 弱叉降级：与 reports/失效误报_特征对比表.md 完全同口径。
# 只用【打叉当天及之前】可得的数据（来源 + ADX），不含任何未来信息。
# 该组 155 条 / 33.3%，误报率 43.9%（保留组 32.9%），盲测段同向。
WEAK_ADX = 30
WEAK_RULE = "顶底雷达 & 打叉当日 ADX(14) ≥ 30"

REASON_TXT = {
    "rev": "反向 >1.5 ATR",
    "ma": "均线破位 MA26/MA34",
    "decay": "5 日时间衰竭",
    "stop": "原版止损触发",
}
SIG_LABEL = {"pred": "4/4 建仓", "warn": "3/4 预警", "radar": "顶底雷达"}


def first_failure(bars, S, i, exp, atr=None, entry=None):
    """返回某条信号最早一次失效：{date, day, reason, label, reasons, detail}。

    exp = +1 预期上涨 / -1 预期下跌。i = 信号日在 bars 中的下标。
    """
    n = len(bars)
    if i + 1 >= n:
        return None
    atr0 = float(atr or S["atr"][i] or 1e-9)
    c0, H0, L0 = bars[i]["c"], bars[i]["h"], bars[i]["l"]
    hits = {}

    def put(j, key, detail):
        d = bars[j]["d"]
        hits.setdefault(d, []).append((key, REASON_TXT[key], detail, j - i))

    # R1 反向幅度：收盘逆行超过 1.5 ATR
    for j in range(i + 1, min(n, i + 1 + LOOKBACK)):
        adv = -exp * (bars[j]["c"] - c0) / atr0
        if adv > REV_ATR:
            put(j, "rev", "自信号日收盘逆行 %.2f ATR（阈值 1.50）" % adv)
            break

    # R2 均线破位：收盘反向穿越 MA26 / MA34（先在线的另一侧，再收破）
    for key in MA_KEYS:
        arr = S.get(key) or []
        for j in range(i + 1, min(n, i + 1 + LOOKBACK)):
            m0, m1 = arr[j - 1], arr[j]
            if m0 is None or m1 is None:
                continue
            pc, cc = bars[j - 1]["c"], bars[j]["c"]
            cross = ((exp == 1 and pc >= m0 and cc < m1) or
                     (exp == -1 and pc <= m0 and cc > m1))
            if cross:
                put(j, "ma", "收盘%s %s" % (("跌破" if exp == 1 else "涨破"),
                                            key.upper()))
                break

    # R3 时间衰竭：5 根内未创新高/新低，反而收破信号日极值
    new_ext = False
    for j in range(i + 1, min(n, i + 1 + DECAY_DAYS)):
        b = bars[j]
        if (exp == 1 and b["h"] > H0) or (exp == -1 and b["l"] < L0):
            new_ext = True
        broke = ((exp == 1 and b["c"] < L0) or (exp == -1 and b["c"] > H0))
        if (not new_ext) and broke:
            put(j, "decay", "5 根内未创新%s，收盘%s信号日%s"
                % ("高" if exp == 1 else "低", "跌破" if exp == 1 else "涨破",
                   "低点" if exp == 1 else "高点"))
            break

    # R4 原版止损失效：直接调用线上同一套 MA26 + 吊灯 3ATR 跟踪
    ei = i + 1
    ent = entry if entry is not None else bars[ei]["o"]
    xt = engine.exit_track(bars, S, ei, exp, atr0, ent)
    if xt.get("status") == "closed" and xt.get("exitIdx") is not None:
        j = xt["exitIdx"]
        put(j, "stop", "触发原版离场：%s（R %+.2f）"
            % (xt.get("why") or "止损", xt.get("R") or 0.0))

    if not hits:
        return None
    dt = min(hits.keys())
    items = hits[dt]
    return {
        "date": dt,
        "day": items[0][3],
        "reason": items[0][0],
        "label": items[0][1],
        "reasons": [{"key": a, "label": b, "detail": c} for a, b, c, _ in items],
        "detail": "；".join("%s：%s" % (b, c) for a, b, c, _ in items),
    }


def build_defend(code, name, bars, S, signals, radar_hist):
    """全量生成某品种的失效事件（一条信号最多一次）。"""
    idx = {b["d"]: k for k, b in enumerate(bars)}
    out = []

    def add(sig_date, sig_kind, exp, idx_i, entry=None, level=None):
        if idx_i is None or idx_i >= len(bars) - 1:
            return
        f = first_failure(bars, S, idx_i, exp,
                          atr=S["atr"][idx_i], entry=entry)
        if not f:
            return
        # v17：弱叉只做标记，事件本身一条不少（随时可切回全量）
        di = idx.get(f["date"])
        adx_arr = S.get("adx") or []
        adx = float(adx_arr[di] or 0) if (di is not None and di < len(adx_arr)) else 0.0
        weak = bool(sig_kind == "radar" and adx >= WEAK_ADX)
        out.append({
            "weak": weak, "adx": round(adx, 1),
            "kind": "def", "date": f["date"], "sym": code, "name": name,
            "side": "long" if exp == 1 else "short",
            "sigDate": sig_date, "sigKind": sig_kind,
            "sigLabel": SIG_LABEL.get(sig_kind, sig_kind),
            "reason": f["reason"], "label": f["label"],
            "reasons": f["reasons"], "day": f["day"],
            "detail": f["detail"],
            "price": round(bars[idx_i]["c"], 1),
            "atr": round(float(S["atr"][idx_i] or 0), 2),
            "entry": entry, "level": level,
        })

    for s in signals:
        if s.get("kind") not in ("pred", "warn"):
            continue
        exp = 1 if s.get("side") == "long" else -1
        add(s.get("date"), s["kind"], exp, idx.get(s.get("date")),
            entry=s.get("entry") or s.get("en"))

    for h in radar_hist:
        # 顶部雷达 = 看跌（exp=-1 / 反转做空）；底部雷达 = 看涨（exp=+1 / 反转做多）
        exp = -1 if h.get("dir") == "顶部" else 1
        add(h.get("d"), "radar", exp, idx.get(h.get("d")), level=h.get("level"))

    out.sort(key=lambda x: (x["date"], x["sigDate"]))
    return out


def summarize(events):
    by_reason, by_kind = {}, {}
    weak = 0
    for e in events:
        by_reason[e["reason"]] = by_reason.get(e["reason"], 0) + 1
        by_kind[e["sigKind"]] = by_kind.get(e["sigKind"], 0) + 1
        if e.get("weak"):
            weak += 1
    return {"n": len(events),
            "dates": len({e["date"] for e in events}),
            "last": max((e["date"] for e in events), default=None),
            "byReason": by_reason, "byKind": by_kind,
            "weak": weak, "strong": len(events) - weak,
            "weakPct": round(weak / len(events) * 100, 1) if events else 0.0}
