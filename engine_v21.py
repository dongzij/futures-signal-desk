# -*- coding: utf-8 -*-
"""v21 回测引擎 —— 抛弃预设公式，逐日状态机 + 资金追加 + 平仓优先级

================================================================
模块一（用户定义，逐条落地）
  1. 初始本金 10,000 元；每次固定 1 手；不加仓、不金字塔。
  2. 资金不足 1 手保证金 → 自动追加 10,000 元，累计记录【追加次数】【总投入本金】。
  3. 平仓优先级（严禁死扛）：
       第一 防守：出现【失效（白圈）】或【强烈离场雷达】 → 平仓
       第二 底线：收盘跌破 MA26（多）/ 涨破（空），或吊灯触发 → 平仓
       第三 反手：必须"原持仓已平" 且 "出现反向进场信号" → 才允许反手
================================================================
时序纪律（无未来函数）
  · T 日收盘生成的所有信号 → T+1 开盘价成交。
  · 唯一的盘中成交：30% 本金熔断（浮亏 ≥3,000 元），按触发价成交，
    因为这是本金安全线，等不到收盘。
  · 因此"防守/底线/反手"三类决策都在 T 日收盘确认、T+1 开盘执行，
    优先级在同一个决策点上按 防守 > 底线 > 反手 顺序裁决。
================================================================
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import regime_lib

START_CAP = 10000.0
ADD_UNIT = 10000.0
STOP_YUAN = 3000.0        # 30% 本金熔断
CH_K = 3.0                # 吊灯 ATR 倍数
MA_EXIT = 26
ENTRY_PRIORITY = ("pred", "warn", "mw", "tf", "confirm")


def fee_side(fee, price, mult):
    typ, val = fee[0], fee[1]
    if typ == "lot":
        return float(val)
    if typ == "rate":
        return price * mult * val / 10000.0
    return 0.0


def default_cfg():
    return {
        "entry": {                       # regime -> 允许的进场信号集合
            "trend_up": ("pred", "warn", "mw", "tf", "confirm"),
            "trend_dn": ("pred", "warn", "mw", "tf", "confirm"),
            "range": ("pred", "warn", "mw"),
            "transition": ("pred", "warn"),
        },
        "reverse": True,
        "stop30": True,
        "chand": CH_K,
        "ma_exit": MA_EXIT,
        "defend_on": True,
        "xsig_on": True,
        "xsig_trend": True,      # True=趋势中强离场也照平（用户规则）；False=趋势中交给 MA26/吊灯
        "align": False,          # True=趋势中只允许顺势方向开仓
        "weak_defend": True,
        "max_hold": 0,
    }


def _pick_signal(pack, t, kinds, allow_side=None):
    """当日信号按优先级取一条。方向过滤在挑选之前完成，
    避免"逆势信号占掉名额、把同向有效信号挤掉"。"""
    cands = [s for s in pack.get(t, []) if s["kind"] in kinds]
    if allow_side is not None:
        cands = [s for s in cands if allow_side(s["side"])]
    if not cands:
        return None, []
    cands.sort(key=lambda s: ENTRY_PRIORITY.index(s["kind"]))
    best = cands[0]
    conflicts = [c for c in cands if c["side"] != best["side"]]
    return best, conflicts


def run(p, d, cfg, pack, meta, bars=None, S=None):
    import engine as _eng
    bars = bars or [{"d": b[0], "o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5], "oi": b[6]}
                    for b in d["main"]]
    S = S or _eng.make_S(bars)
    N = meta["N"]
    mult, margin, fee = p["mult"], p["margin"], p["fee"]

    cash = START_CAP
    total_in = START_CAP
    adds = 0
    pos = None
    trades = []
    nav = []          # 剔除追加资本后的净值（以 1 万为基准）
    caps = []        # 每日累计投入资金（时间加权用）
    equity = []
    audit = []
    pending = {}
    skips = []
    stops = []

    def im(px):
        return px * mult * margin

    def fl(px):
        if pos is None:
            return 0.0
        return (px - pos["entry"]) * mult if pos["side"] == "long" else (pos["entry"] - px) * mult

    def do_close(px, why, t, note=""):
        nonlocal cash, pos
        if pos is None:
            return
        gross = (px - pos["entry"]) * mult if pos["side"] == "long" else (pos["entry"] - px) * mult
        f = fee_side(fee, pos["entry"], mult) + fee_side(fee, px, mult)
        net = gross - f
        cash += net
        tr = {
            "side": pos["side"], "entry": pos["entry"], "entry_date": pos["entry_date"],
            "entry_i": pos["entry_i"], "exit": px, "exit_date": bars[t]["d"], "exit_i": t,
            "gross": gross, "fee": f, "net": net, "why": why, "kind": pos["kind"],
            "sig_date": pos["sig_date"], "bars": pos["bars"], "note": note,
            "regime_in": pos["regime_in"],
        }
        trades.append(tr)
        if why == "stop30":
            stops.append(tr)
        audit.append({"t": t, "date": bars[t]["d"], "act": "close", "why": why,
                      "px": round(px, 1), "net": round(net), "note": note,
                      "regime": regime_lib.causal_regime(bars, S, t)})
        pos = None

    def try_open(sig, t):
        nonlocal cash, adds, total_in, pos
        px = bars[t]["o"]
        need = im(px) + fee_side(fee, px, mult)
        if cash < need:
            n = 0
            while cash < need:
                cash += ADD_UNIT
                total_in += ADD_UNIT
                adds += 1
                n += 1
                if n > 50:
                    break
            audit.append({"t": t, "date": bars[t]["d"], "act": "add_cash",
                          "add": int(n * ADD_UNIT), "total_in": int(total_in)})
        if cash < need:
            skips.append((bars[t]["d"], "no_cash"))
            return
        pos = {"side": sig["side"], "entry": px, "entry_i": t, "entry_date": bars[t]["d"],
               "kind": sig["kind"], "sig_date": sig["date"], "bars": 0,
               "hi": bars[t]["h"], "lo": bars[t]["l"], "atr0": S["atr"][sig["i"]] or px * 0.01,
               "regime_in": regime_lib.causal_regime(bars, S, t)}
        audit.append({"t": t, "date": bars[t]["d"], "act": "open", "side": sig["side"],
                      "kind": sig["kind"], "sig_date": sig["date"], "px": round(px, 1),
                      "regime": pos["regime_in"], "why": sig["kind"]})

    def check_stop30(t):
        nonlocal cash, pos
        if pos is None or not cfg["stop30"]:
            return
        if pos["side"] == "long":
            sp = pos["entry"] - STOP_YUAN / mult
            if bars[t]["l"] <= sp:
                do_close(sp, "stop30", t, "浮亏≥3000元，盘中强制平仓")
        else:
            sp = pos["entry"] + STOP_YUAN / mult
            if bars[t]["h"] >= sp:
                do_close(sp, "stop30", t, "浮亏≥3000元，盘中强制平仓")

    def decide_exit(t):
        """收盘裁决。返回 (why, note) 或 None。优先级：防守 > 底线。"""
        if pos is None:
            return None
        # ---- 第一层：防守（失效 / 强烈离场）
        if cfg["defend_on"]:
            for s in pack.get(t, []):
                if s["kind"] != "defend" or s["side"] != pos["side"]:
                    continue
                if s["extra"].get("sigDate") != pos["sig_date"] or s["extra"].get("sigKind") != pos["kind"]:
                    continue
                if not cfg["weak_defend"] and s["extra"].get("weak"):
                    continue
                return ("defend", "白圈失效：%s（%s）" % (s["extra"].get("label") or "-", s["extra"].get("reason") or "-"))
        if cfg["xsig_on"]:
            reg_now = regime_lib.causal_regime(bars, S, t)
            skip_xs = (not cfg["xsig_trend"]) and reg_now in ("trend_up", "trend_dn")
            if not skip_xs:
                for s in pack.get(t, []):
                    if s["kind"] == "xsig" and s["side"] == pos["side"]:
                        return ("xsig_strong", "强烈离场雷达（%s）" % (s.get("dirn") or ""))
        # ---- 第二层：底线
        px = bars[t]["c"]
        if cfg["ma_exit"]:
            ma = S["ma%d" % cfg["ma_exit"]][t] if ("ma%d" % cfg["ma_exit"]) in S else S["ma26"][t]
            if ma:
                if pos["side"] == "long" and px < ma:
                    return ("ma_exit", "收盘跌破 MA%d（%.1f < %.1f）" % (cfg["ma_exit"], px, ma))
                if pos["side"] == "short" and px > ma:
                    return ("ma_exit", "收盘涨破 MA%d（%.1f > %.1f）" % (cfg["ma_exit"], px, ma))
        if cfg["chand"]:
            a = S["atr"][t] or pos["atr0"]
            if pos["side"] == "long" and px <= pos["hi"] - cfg["chand"] * a:
                return ("chandelier", "吊灯触发（自高点回撤 ≥%.1fATR）" % cfg["chand"])
            if pos["side"] == "short" and px >= pos["lo"] + cfg["chand"] * a:
                return ("chandelier", "吊灯触发（自低点反弹 ≥%.1fATR）" % cfg["chand"])
        if cfg["max_hold"] and pos["bars"] >= cfg["max_hold"]:
            return ("max_hold", "持有满 %d 根" % cfg["max_hold"])
        return None

    last = N - 1
    t0 = max(60, int(cfg.get("start_i", 60)))
    t1 = min(N, int(cfg.get("end_i", N)))
    last = t1 - 1
    for t in range(t0, t1):
        # ---------- 1. 开盘执行昨日收盘决策 ----------
        acts = pending.pop(t, [])
        for a in acts:
            if a[0] == "close":
                if pos is not None:
                    do_close(bars[t]["o"], a[1], t, a[2] if len(a) > 2 else "")
            elif a[0] == "open":
                if pos is None:
                    try_open(a[1], t)
        # ---------- 2. 盘中 30% 熔断 ----------
        check_stop30(t)
        # ---------- 3. 收盘更新 ----------
        if pos is not None:
            pos["bars"] += 1
            pos["hi"] = max(pos["hi"], bars[t]["h"])
            pos["lo"] = min(pos["lo"], bars[t]["l"])
        eq = cash + fl(bars[t]["c"])
        equity.append((bars[t]["d"], eq))
        nav.append((bars[t]["d"], eq - (total_in - START_CAP)))
        caps.append(total_in)

        # ---------- 4. 收盘决策 → 次日开盘 ----------
        act = decide_exit(t)
        closed_side = None
        if act:
            pending.setdefault(t + 1, []).append(("close", act[0], act[1]))
            closed_side = pos["side"]
            audit.append({"t": t, "date": bars[t]["d"], "act": "signal_exit", "why": act[0],
                          "note": act[1]})
        reg = regime_lib.causal_regime(bars, S, t)
        kinds = cfg["entry"].get(reg, ())
        if kinds:
            allow = None
            if cfg["align"]:
                if reg == "trend_up":
                    allow = (lambda sd: sd == "long")
                elif reg == "trend_dn":
                    allow = (lambda sd: sd == "short")
            sig, conf = _pick_signal(pack, t, kinds, allow)
            if sig:
                free = (pos is None)
                reversing = (pos is not None and act is not None
                             and cfg["reverse"] and sig["side"] != pos["side"])
                if free or reversing:
                    pending.setdefault(t + 1, []).append(("open", sig))
                elif pos is not None and act is None and sig["side"] != pos["side"]:
                    audit.append({"t": t, "date": bars[t]["d"], "act": "ignore",
                                  "why": "反向信号但原仓未平（反手需先平仓）",
                                  "kind": sig["kind"], "side": sig["side"]})

    # 收尾：最后一根按收盘价平掉（便于结算，视为"未平仓按市价估值"）
    open_pos = None
    if pos is not None:
        open_pos = {"side": pos["side"], "entry": pos["entry"], "kind": pos["kind"],
                    "bars": pos["bars"], "float": round(fl(bars[last]["c"]))}

    final = cash + (fl(bars[last]["c"]) if pos else 0.0)
    nav_vals = [v for _, v in nav]
    peak = START_CAP
    mdd_pct = 0.0
    mdd_yuan = 0.0
    for v in nav_vals:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak * 100
            if dd > mdd_pct:
                mdd_pct = dd
                mdd_yuan = peak - v
    years = max(0.1, (t1 - t0) / 244.0)
    avg_cap = sum(caps) / max(1, len(caps))
    avg_cap = max(avg_cap, START_CAP)

    return {
        "final": final, "cash": cash, "total_in": total_in, "adds": adds,
        "open_pos": open_pos, "trades": trades, "nav": nav, "equity": equity,
        "mdd_pct": round(mdd_pct, 1), "mdd_yuan": round(mdd_yuan),
        "years": round(years, 1), "audit": audit, "skips": skips, "stops": stops,
        "avg_cap": round(avg_cap),
    }


def stats(p, r):
    tr = r["trades"]
    n = len(tr)
    wins = [t for t in tr if t["net"] > 0]
    losses = [t for t in tr if t["net"] <= 0]
    gp = sum(t["net"] for t in wins)
    gl = -sum(t["net"] for t in losses)
    final = r["final"]
    tot_ret = (final - r["total_in"]) / r["total_in"] * 100 if r["total_in"] else 0.0
    ann = ((final / r["avg_cap"]) ** (1.0 / r["years"]) - 1.0) * 100 if final > 0 else -100.0
    return {
        "sym": p["sym"], "name": p["name"], "n": n,
        "win": round(len(wins) / n * 100, 1) if n else 0.0,
        "pf": round(gp / gl, 2) if gl > 0 else (99.0 if gp > 0 else 0.0),
        "net": round(final - r["total_in"]),
        "final": round(final), "total_in": int(r["total_in"]), "adds": r["adds"],
        "tot_ret": round(tot_ret, 1), "ann": round(ann, 1),
        "mdd": r["mdd_pct"], "mdd_yuan": r["mdd_yuan"],
        "mdd_cap": round(r["mdd_yuan"] / r["total_in"] * 100, 1) if r["total_in"] else 0.0,
        "avg_win": round(gp / len(wins)) if wins else 0,
        "avg_loss": round(-gl / len(losses)) if losses else 0,
        "stop30": len(r["stops"]),
        "avg_days": round(sum(t["bars"] for t in tr) / n, 1) if n else 0,
        "max_days": max((t["bars"] for t in tr), default=0),
        "open_pos": (r["open_pos"]["side"] if r["open_pos"] else None),
        "years": r["years"],
        "calmar": round(ann / r["mdd_pct"], 2) if r["mdd_pct"] > 0.5 else round(ann, 2),
    }
