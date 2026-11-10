# -*- coding: utf-8 -*-
"""信号包编译器（v21）

把原始数据文件里的信号，编译成"以执行索引为键"的每日信号包，供 v21 引擎和
信号体检模块共用。所有信号统一语义：

    side      = 该信号建议建立/持有的仓位方向（long / short）
    sig_i     = 信号生成根（收盘后可知）
    exec_i    = 执行根 = sig_i + 1（次日开盘成交，无未来函数）

支持的信号类型
    pred    顶底雷达（反转）        4 条齐 + ADX 过门槛
    warn    进场预警（反转）        3 条胜出 / 4 条但 ADX 未过门槛
    mw      W/M 结构双顶底（反转）  双峰/双底 + 颈线突破确认
    confirm 反转兑现（穿越 MA）     雷达预测被价格兑现 → 顺新方向（趋势确认属）
    tf      转折确认（趋势跟踪）    自极值回撤 ≥3.5ATR → 顺新方向
    xsig    强烈离场雷达            独立出场评估，level=strong 才入包；
                                    其 side 表示"该平掉的仓位方向"
    defend  信号失效（白圈）        side 表示"该平掉的仓位方向"，
                                    并携带 sigDate/sigKind 用于严格配对
"""
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
import engine
import extreme_gate

KIND_NM = {
    "pred": "顶底雷达", "warn": "进场预警", "mw": "WM结构",
    "confirm": "反转兑现", "tf": "转折确认", "xsig": "强烈离场",
}


def bars_of(d):
    return [{"d": b[0], "o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5], "oi": b[6]}
            for b in d["main"]]


def _tags(x, weekly, i):
    prob = x.get("prob") or {}
    side = x.get("side")
    wk = weekly[i] if i < len(weekly) else 0
    return {
        "k": x.get("k"),
        "gate": bool(x.get("gate")),
        "strong": bool(x.get("strong")),
        "adx": x.get("adx"),
        "prob3": prob.get("3") if isinstance(prob, dict) else None,
        "wk": wk,
        "wk_align": (wk == 1 and side == "long") or (wk == -1 and side == "short"),
        "segAge": x.get("segAge"),
    }


def build(d, bars=None, S=None, weekly=None, gate=None, blocked_log=None):
    """返回 {sig_i: [信号...]} + 元信息

    v27：gate 为 extreme_gate.gate_for(bars) 时，触犯【顶不做多·底不做空】的
    进场信号在编译期就被剔除，引擎层根本看不到它。出场类信号不受影响。
    """
    bars = bars or bars_of(d)
    S = S or engine.make_S(bars)
    weekly = weekly if weekly is not None else engine.weekly_pos_series(bars)
    idx = {}
    for i, b in enumerate(bars):
        idx.setdefault(b["d"], i)
    N = len(bars)
    pack = defaultdict(list)

    def put(i, **kw):
        if i is None or i < 0 or i + 1 >= N:
            return
        if gate is not None and kw.get("kind") in extreme_gate.ENTRY_KINDS \
                and gate(i, kw.get("side"), kw.get("kind")):
            if blocked_log is not None:
                blocked_log.append({"date": bars[i]["d"], "kind": kw.get("kind"),
                                    "side": kw.get("side"),
                                    "dev": extreme_gate.deviation(bars, i)})
            return
        kw["i"] = i
        kw["exec_i"] = i + 1
        kw["date"] = bars[i]["d"]
        pack[i].append(kw)

    # 1) 雷达 / 预警 / 反转兑现
    for x in d.get("signals", []):
        i = idx.get(x.get("date"))
        if i is None:
            continue
        k = x.get("kind")
        if k not in ("pred", "warn", "confirm"):
            continue
        put(i, kind=k, side=x.get("side"), dirn=x.get("dir"),
            price=x.get("price"), tags=_tags(x, weekly, i),
            sigLabel=KIND_NM.get(k, k))

    # 2) WM 结构（用颈线突破确认日）
    for x in d.get("mw", []):
        dt = x.get("confirmDate") or x.get("date") or x.get("mkDate")
        i = idx.get(dt)
        if i is None:
            continue
        put(i, kind="mw", side=x.get("side"), dirn=("顶部" if x.get("dir") == "顶部" else "底部"),
            price=x.get("neckPx"), tags={"k": None, "gate": False, "strong": bool(x.get("hiConf")),
                                         "adx": None, "prob3": None, "wk": weekly[i] if i < len(weekly) else 0,
                                         "wk_align": False, "segAge": None},
            sigLabel=KIND_NM["mw"], extra={"depth": x.get("depth"), "mainPeak": x.get("mainPeak")})

    # 3) 趋势转折确认（3.5ATR 回撤）
    for (ci, nd, _pi, _pp) in engine.tf_events(bars, S):
        put(ci, kind="tf", side=("long" if nd == 1 else "short"), dirn=None,
            price=bars[ci]["c"],
            tags={"k": None, "gate": False, "strong": False, "adx": S["adx"][ci],
                  "prob3": None, "wk": weekly[ci] if ci < len(weekly) else 0,
                  "wk_align": (weekly[ci] == nd) if ci < len(weekly) else False, "segAge": None},
            sigLabel=KIND_NM["tf"])

    # 4) 强烈离场雷达（全历史重算，level=strong）
    for i in range(60, N):
        r = engine.exit_radar(bars, S, i)
        if not r or r.get("level") != "strong":
            continue
        put(i, kind="xsig", side=r["side"], dirn=r.get("dir"), price=r.get("close"),
            tags={"k": r.get("k"), "gate": True, "strong": True, "adx": S["adx"][i],
                  "prob3": None, "wk": weekly[i] if i < len(weekly) else 0,
                  "wk_align": False, "segAge": r.get("age")},
            sigLabel=KIND_NM["xsig"], extra={"pull": r.get("pull"), "ext": r.get("ext")})

    # 5) 失效（白圈）——只对"本条信号"有效，保留 sigDate/sigKind 做严格配对
    KINDMAP = {"radar": "pred", "pred": "pred", "warn": "warn", "mw": "mw", "confirm": "confirm"}
    for x in d.get("defend", []):
        i = idx.get(x.get("date"))
        if i is None:
            continue
        k = KINDMAP.get(x.get("sigKind"))
        if k is None:
            continue
        put(i, kind="defend", side=x.get("side"), dirn=None, price=x.get("price"),
            tags={"k": None, "gate": False, "strong": False, "adx": x.get("adx"),
                  "prob3": None, "wk": weekly[i] if i < len(weekly) else 0,
                  "wk_align": False, "segAge": None},
            sigLabel="失效", extra={"sigDate": x.get("sigDate"), "sigKind": k,
                                    "reason": x.get("reason"), "weak": bool(x.get("weak")),
                                    "label": x.get("label")})

    return dict(pack), {"idx": idx, "N": N, "weekly": weekly}


def all_entries(pack, kinds):
    """取出指定类型的全部进场信号，按执行索引排序"""
    out = []
    for i in sorted(pack):
        for s in pack[i]:
            if s["kind"] in kinds:
                out.append(s)
    return out
