# -*- coding: utf-8 -*-
"""构建分合约数据包：data/{SYM}.json（品种主档+信号）与 data/{SYM}_{合约}.json（K线）。

主页面与反转预测雷达（v4，与回测同源，8 品种 FG/SA/M/SP/SM/MA/SR/C）：
MA34 定义趋势段 · 4 条入选特征计分 + ADX≥20 门槛 · 命中 k 条 → 标定的反转概率
（k=4 时 5 日内 78.9%，且不设止损、持有到反向穿越）。
信号来自 state.json（engine.scan_symbol 产出）；周/月信号用同一方法
在聚合后的周/月 K 线上独立计算（看哪个周期就用哪个周期的信号）。
"""
import bisect
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone

import build
import defend_rules
import engine
import engine_v21 as E21
import extreme_gate
import mw_events
import regime_lib
import sig_pack as SP
import div_radar          # v30 叠加层：资金背离雷达（只读 OHLC+OI，不改任何信号）
from nightgate import bj_now

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "data")
EXIT_ALL = []      # 跨品种出场成交汇总（算全局胜率/净 R）
XSIG_ALL = []      # 跨品种独立出场信号当前状态
MKT_ALL = []       # 跨品种市场状态（纯信息展示，不干预信号）
DEF_ALL = []       # v15：跨品种全量失效事件（manifest 汇总用）
MW_ALL = []        # v16：跨品种二段 M/W 结构（DIF背离 + 量仓强）
EXT_BLOCKED = []   # v27：被【顶不做多·底不做空】拦下的页面标识

# 品种交易参数（模拟盘用）：乘数 / 手续费 / 保证金率
# fee: ("lot", 元/手) 或 ("rate", 万分之几)
# ===== v22（2026-11）：新核心品种池 =====
# 海选结论（reports/v22_final.json）：UR/RB/JD 双段全不合格已剔除；SM 锰硅双段为正、
# 验证段盈亏比 12.25（极高）但胜率仅 27.6%~33%，按用户定稿以「高盈亏比」定位纳入池内，
# 不作为胜率型品种宣传。RU 单手保证金 18,665 元（1 万本金无法独立交易）被排除。
PRODUCTS = {
    "M":  {"name": "豆粕", "mult": 10, "fee": ("lot", 1.5),   "margin": 0.08},
    "FG": {"name": "玻璃", "mult": 20, "fee": ("lot", 6),     "margin": 0.09},
    "MA": {"name": "甲醇", "mult": 10, "fee": ("lot", 2),     "margin": 0.08},
    "P":  {"name": "棕榈油", "mult": 10, "fee": ("lot", 2.5), "margin": 0.08},
    "Y":  {"name": "豆油", "mult": 10, "fee": ("lot", 2.5),   "margin": 0.08},
    "HC": {"name": "热卷", "mult": 10, "fee": ("rate", 1.0),  "margin": 0.08},
    "SM": {"name": "锰硅", "mult": 5,  "fee": ("lot", 3),     "margin": 0.09},
}
_PLAN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "v22_final.json")
V22 = {}


def _load_plan():
    global V22
    try:
        with open(_PLAN_PATH, encoding="utf-8") as f:
            V22 = json.load(f)
    except Exception:                                   # noqa: BLE001
        V22 = {}


_load_plan()
# 品种栏顺序 = 近 1 年净收益降序（v22_final.py 产出）
ORDER = V22.get("order") or ["MA", "HC", "Y", "SM", "FG", "P", "M"]

REGIME_NM = {"trend_up": "上涨趋势", "trend_dn": "下跌趋势",
             "range": "震荡", "transition": "过渡态"}
SIG_NM = {"pred": "顶底雷达", "warn": "进场预警", "mw": "WM结构",
          "confirm": "反转兑现", "tf": "转折确认"}
DAILY_YEARS = 10         # 主力连续日线回看年数（支撑年/月/周视图）
MAX_CONTRACTS = 8        # 每品种最多打包的合约数
KEEP_MIN = 40            # 合约至少要有多少根日线才收录
MEMBER_MIN = 20          # 主力轮动栏入选的最低日线根数（够画 MA20 即可，不因新挂牌被误杀）
MEMBER_AHEAD = 2         # 当前主力之后保留几个合约 —— 连同当前主力共 3 个，
                         # 加上「主力连续」正好 4 个按钮（用户 2026-11-12 拍板「一共四个」）。
                         # 这个数不能因为远月挂牌而变多：轮动是「滚动窗口」，永远只显示最近 3 个主力月。


def _ct_ym(cid):
    """"FG2701" → 202701，可直接比大小的年月整数。"""
    try:
        return (2000 + int(cid[-4:-2])) * 100 + int(cid[-2:])
    except Exception:                                       # noqa: BLE001
        return 0


def probe_main_contracts(sym):
    """【v29 主力轮动栏】当前主力 + 往后 2 个主力月，连同「主力连续」共 4 个按钮。

    规则（用户 2026-11-12 拍板「主力连续 + 后续三个主力合约，一共四个，定期轮动」）：
      · 只认该品种的主力月份 —— 复用 build.MONTHS 这一既有真源
        （FG/MA/M/Y/P/SM = 1/5/9，HC = 1/5/10），非主力月份（如 FG2612）一律不进栏，
        也不占用打包体积；
      · 当前主力 = 尚未进入交割月、持仓量最大的合约，与 panama_adjust 判主力的口径一致；
      · 之后按同一周期依次取下一批主力月份，只收「已挂牌且有真实持仓量」的，
        未挂牌的自然跳过；
      · 数量固定为 3 个合约（1 + 2），不随远月挂牌而变多 —— 这是滚动窗口，
        不是"把所有已挂远月都摆出来"。旧合约进交割月自动让位、新主力自动补进来，
        因此永远是「当前 + 后两个」。2027-02-01 时序列自动变成 2705/2709/2801。
    返回顺序为「当前主力 → 远月」，供前端按序渲染。
    """
    months = build.MONTHS.get(sym) or list(range(1, 13))
    now = bj_now()
    cur_ym = now.year * 100 + now.month
    cands = []
    for k in range(-8, 31):        # 往前 8 个月兜底（主力尚未切换）、往后 30 个月覆盖 4 个主力月
        mm = now.month + k
        yy = now.year + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        if mm not in months:
            continue
        cands.append("%s%02d%02d" % (sym, yy % 100, mm))

    info = {}
    for cid in cands:
        try:
            bars = build.fetch_daily(cid)
        except Exception:                                   # noqa: BLE001
            bars = []
        if not bars:
            continue
        oi = bars[-1].get("oi") or 0
        if len(bars) < MEMBER_MIN and oi <= 0:              # 刚挂牌、还没量也没持仓 → 等它长起来
            continue
        info[cid] = {"n": len(bars), "last": bars[-1]["d"], "oi": oi}

    # 已进入交割月（或已交割）的合约不能再当主力
    tradable = [c for c in cands if c in info and _ct_ym(c) > cur_ym]
    if not tradable:                                        # 极端兜底：数据全缺时按持仓量排
        return sorted(info, key=lambda c: -info[c]["oi"])[:MEMBER_AHEAD + 1]

    front = max(tradable, key=lambda c: info[c]["oi"])
    seq = sorted(tradable, key=_ct_ym)
    return [front] + seq[seq.index(front) + 1:][:MEMBER_AHEAD]


def probe_contracts(sym, back=2, ahead=8):
    """旧口径：探测全部可交易月（保留给历史脚本调用，线上主流程已改用主力轮动栏）。"""
    now = datetime.now()
    got = []
    for k in range(-back, ahead + 1):
        m = now.month + k
        yy = now.year + (m - 1) // 12
        mm = (m - 1) % 12 + 1
        cid = "%s%02d%02d" % (sym, yy % 100, mm)
        try:
            bars = build.fetch_daily(cid)
        except Exception:                                   # noqa: BLE001
            continue
        if bars and len(bars) >= KEEP_MIN:
            got.append(cid)
    # 已到期但持仓极小的旧合约剔除（保留最近2个月做历史回看）
    return got


def _load_json(path, default=None):
    """容错读取 JSON（文件缺失/损坏时返回 default）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def bars_to_arr(bars):
    return [[b["d"], b["o"], b["h"], b["l"], b["c"], b.get("v") or 0, b.get("oi") or 0]
            for b in bars]

def compute_tc_signals(main, mult, margin):
    """【右侧趋势中继 · 纯视觉预警】非交易类标记，不进账本/不开平仓/不改任何交易逻辑。

    算法口径与前端 tcBuildSeries 逐行一致（Wilder ATR(14) 同源），由 daily 每日
    更新时一并算好写进 data/{sym}.json 的 tcSignals，与现有 signals/radar 走同一条
    「数据更新 → 计算 → 标注」管线。MOM(N=10,K=3) 确认趋势 + TR1(顺势强K且创10日
    新高/低) 触发 + 同侧 40 根冷却。返回 list of {idx,date,side(1多/-1空),price,atr,
    mult,margin,ret10}。
    """
    n = len(main)
    if n < 65:
        return []
    trs = []
    for i in range(n):
        h = main[i]["h"]; l = main[i]["l"]; c = main[i]["c"]
        if i == 0:
            trs.append(h - l)
        else:
            pc = main[i - 1]["c"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = [0.0] * n
    first = sum(trs[0:14]) / 14.0
    for i in range(0, 14):
        atr[i] = first
    for i in range(14, n):
        atr[i] = (atr[i - 1] * 13 + trs[i]) / 14.0
    COOL = 40
    last = {1: -999, -1: -999}
    out = []
    for i in range(65, n):        # v31：扫描到最后一根（原先 n-20 会让新信号晚 20 根才出现）
        a = atr[i] if atr[i] else ((main[i]["c"] - main[i]["l"]) or 1)
        ret10 = (main[i]["c"] - main[i - 10]["c"]) / a
        if abs(ret10) < 3.0:
            continue
        side = 1 if ret10 > 0 else -1
        if i - last[side] < COOL:
            continue
        body = (main[i]["c"] - main[i]["o"]) / a
        if body * side < 1.0:
            continue
        hh = -1e15; ll = 1e15
        for j in range(i - 10, i):
            if main[j]["c"] > hh:
                hh = main[j]["c"]
            if main[j]["c"] < ll:
                ll = main[j]["c"]
        if side > 0 and not (main[i]["c"] > hh):
            continue
        if side < 0 and not (main[i]["c"] < ll):
            continue
        last[side] = i
        out.append({"idx": i, "date": main[i]["d"], "side": side,
                    "price": main[i]["c"], "atr": a, "mult": mult,
                    "margin": margin, "ret10": ret10})
    # v33【一个大方向只标第一个】：同向链首标 first=True，后续同向信号 first=False。
    # 只在出现反向信号时重新起算，避免趋势走到后半段还在追、吃到山顶。
    # 该字段纯展示用，不参与账本、不改算法参数、不改 tc_monitor 的胜率口径。
    _prev = 0
    _head = 0
    for _s in out:
        # 换方向 => 新趋势；同向但距链首已满 250 根（约 1 年）=> 也视为新趋势重开，
        # 否则一条链可能拖 1-2 年全静默（实测中位 154 根、最长 564 根）。
        _new = (_s["side"] != _prev) or ((_s["idx"] - _head) >= 250)
        _s["first"] = _new
        if _new:
            _prev = _s["side"]
            _head = _s["idx"]
    return out


def _regime_since(bars, S, label):
    """当前路况已持续多少根（同日口径，逐日回溯同一判定函数）"""
    n = 0
    for t in range(len(bars) - 1, 60, -1):
        if regime_lib.causal_regime(bars, S, t) != label:
            break
        n += 1
    return bars[max(60, len(bars) - n)]["d"] if n else bars[-1]["d"], n


def build_plan(sym, doc, main, S):
    """v22：按该品种定稿方案跑一遍完整回测，产出「路况 + 方案持仓 + 进出标识 + 下次触发」。

    与回测引擎同一份代码、同一份数据、同一套风控（防守>底线>反手 + 30% 熔断 + 资金追加），
    因此页面上看到的每一笔进出都能在报告里找到对应行。
    """
    v = (V22.get("symbols") or {}).get(sym)
    if not v:
        return {}
    spec = v["spec"]
    cfg = E21.default_cfg()
    cfg["entry"] = {k: tuple(x) for k, x in spec["entry"].items()}
    for k in ("align", "reverse", "xsig_trend", "chand", "ma_exit", "stop30"):
        if k in spec:
            cfg[k] = spec[k]
    # v27【顶不做多·底不做空】：引擎层拦截。违规进场信号在信号包编译期就被剔除，
    # 因此 marks 里不会出现「进多/进空」方块，账本也不会留下这笔成交。
    _blocked = []
    pack, meta = SP.build(doc, main, S,
                          gate=extreme_gate.gate_for(main),
                          blocked_log=_blocked)
    r = E21.run(doc, doc, cfg, pack, meta, main, S)
    st = E21.stats(doc, r)

    marks = []
    for t in r["trades"]:
        marks.append({"d": t["entry_date"], "t": "in", "side": t["side"],
                      "k": SIG_NM.get(t["kind"], t["kind"]), "px": round(t["entry"], 1)})
        marks.append({"d": t["exit_date"], "t": "out", "side": t["side"],
                      "k": t["why"], "px": round(t["exit"], 1),
                      "net": round(t["net"]), "bars": t["bars"]})
    marks.sort(key=lambda x: (x["d"], 0 if x["t"] == "out" else 1))

    N = len(main)
    reg = regime_lib.causal_regime(main, S, N - 1)
    since, days = _regime_since(main, S, reg)
    kinds = cfg["entry"].get(reg, ())
    pos = r["open_pos"]

    # 建议策略只按路况给，口径与用户定义一致：
    #   上涨/下跌趋势 → 该品种趋势方案（G1 即 D3）；震荡 → 观望空仓；过渡态 → 顶底雷达探测
    if reg == "range":
        advice, code = "观望空仓", "wait"
    elif reg == "transition":
        advice, code = "顶底雷达探测", "radar"
    elif kinds:
        advice = "D3策略" if v["method"] == "G1" else "趋势策略"
        code = "plan"
    else:
        advice, code = "观望空仓", "wait"

    allow_txt = "、".join(SIG_NM.get(k, k) for k in kinds) if kinds else "无（本场景不开仓）"
    if kinds and cfg.get("align") and reg in ("trend_up", "trend_dn"):
        allow_txt += "（仅做%s）" % ("多" if reg == "trend_up" else "空")
    trend_kinds = tuple(dict.fromkeys(tuple(spec["entry"].get("trend_up") or ())
                                      + tuple(spec["entry"].get("trend_dn") or ())))
    trend_txt = "/".join(SIG_NM.get(k, k) for k in trend_kinds) or "反向进场"
    if pos:
        nxt = ("持仓中：①「失效（白圈）/ 强烈离场」→ 次日开盘平仓　②跌破 MA26 或吊灯 3ATR → 平仓"
               "　③出现反向「%s」且原仓已平 → 反手" % trend_txt)
    elif reg == "range":
        nxt = "震荡市系统静默：不开仓、不推信号，等路况切出震荡再评估"
    else:
        nxt = "等待「%s」信号出现 → 次日开盘顺势开仓" % allow_txt

    te, tr_, fu, rc = v["test"], v["train"], v["full"], v["recent"]
    return {
        "v": "v22",
        "method": v["method"], "methodLabel": v["methodLabel"],
        "spec": spec,
        "regime": {"now": reg, "label": REGIME_NM.get(reg, reg), "since": since,
                   "days": days, "advice": advice, "adviceCode": code,
                   "allow": allow_txt},
        "verify": {"win": te["win"], "pf": te["pf"], "n": te["n"], "net": te["net"],
                   "mdd": te["mdd"], "ann": te["ann"],
                   "trainWin": tr_["win"], "trainPf": tr_["pf"], "trainNet": tr_["net"],
                   "recentNet": rc["net"], "recentWin": rc["win"], "recentN": rc["n"],
                   "span": "%s ~ %s" % (v["period"]["train_end"], v["period"]["end"])},
        "full": {"n": st["n"], "win": st["win"], "pf": st["pf"], "net": st["net"],
                 "ann": st["ann"], "mdd": st["mdd"], "adds": st["adds"],
                 "avgDays": st["avg_days"], "maxDays": st["max_days"],
                 "final": st["final"], "totalIn": st["total_in"]},
        "open": ({"side": pos["side"], "kind": SIG_CN(pos["kind"]), "bars": pos["bars"],
                  "float": pos["float"]} if pos else None),
        "openDate": (r["trades"][-1]["entry_date"] if (pos and r["trades"]) else None),
        "marks": marks,
        "next": nxt,
        # v27：铁律过滤说明。verify 仍是 v22 双段盲测的【原始报告值】，不篡改历史验证；
        # full / marks / open 则是按 v27 铁律过滤后重算的真实账本，两者口径须分清。
        "extGate": {
            "version": "v27",
            "label": extreme_gate.LABEL,
            "blockedN": len(_blocked),
            "rule": extreme_gate.cfg()["rule"],
            "note": "本页进出标识与 full 统计已按铁律过滤；"
                    "verify 为 v22 定稿方案原始双段盲测报告，未改动。",
        },
    }


def SIG_CN(k):
    return SIG_NM.get(k, k)


def _exit_all_stat():
    if not EXIT_ALL:
        return None
    rs = [t["R"] for t in EXIT_ALL]
    eq = peak = dd = 0.0
    for t in sorted(EXIT_ALL, key=lambda x: x["d"]):
        eq += t["R"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {"n": len(rs), "win": round(sum(1 for r in rs if r > 0) / len(rs) * 100, 1),
            "R": round(sum(rs), 1), "dd": round(dd, 1),
            "avg": round(sum(rs) / len(rs), 2),
            "perYear": round(sum(rs) / 10.0, 1)}


def build_product(sym):
    cfg = PRODUCTS[sym]
    print("== %s %s ==" % (sym, cfg["name"]))

    # ---- 1) 主力连续日线 ----
    adj, _ = build.panama_adjust(sym)
    keep = int(DAILY_YEARS * 244)
    main = adj[-keep:] if len(adj) > keep else adj
    if not main:
        print("   !! 无主力数据，跳过")
        return None

    # ---- 2) 月合约 ----
    # v29：主力轮动栏 = 当前主力 + 往后 3 个会成为主力的合约，非主力月份不收录
    contracts = probe_main_contracts(sym)
    print("   合约(主力轮动 %d):" % len(contracts), ", ".join(contracts))
    cmeta = []
    for cid in contracts:
        bars = build.fetch_daily(cid)
        if not bars:
            continue
        last = bars[-1]
        cmeta.append({
            "id": cid, "n": len(bars),
            "first": bars[0]["d"], "last": last["d"],
            "c": last["c"], "oi": last.get("oi") or 0,
            "front": not cmeta,          # v29：列表首位即当前主力
        })
    if not cmeta:
        return None
    # 分钟线全给这 4 个合约（原来 6 个：持仓前4 + 到期最近2，现在更省）
    min_ids = {c["id"] for c in cmeta}
    if cmeta:
        print("   当前主力:", cmeta[0]["id"], "持仓 %d" % cmeta[0]["oi"])

    # ---- 3) 信号（日线来自 state.json；周/月用同一方法在聚合K线上现算）----
    S_main = engine.make_S(main)
    wpos = engine.weekly_pos_series(main)
    idx_by_date = {b["d"]: i for i, b in enumerate(main)}
    state = json.load(open("state.json", encoding="utf-8"))

    def pack(s):
        # v12：本地/CI 统一重算 meta（不依赖 state.json 里可能是旧格式的 meta）
        m = s.get("meta")
        i = idx_by_date.get(s.get("date"))
        if i is not None and i < len(main):
            exp = 1 if s.get("side") == "long" else -1
            tier = "strong" if s.get("strong") else "normal"
            try:
                # 首日失效/结构已破坏只对实际建仓(pred)有意义；
                # 预警/兑现档只保留 T 日动能真空
                scope = "all" if s.get("kind") == "pred" else "none"
                m = engine.signal_meta(main, S_main, i, exp, tier, wpos,
                                       code=sym, pool="in", review_scope=scope)
            except Exception as e:              # noqa: BLE001
                print("   meta 重算失败 %s %s: %s" % (sym, s.get("date"), e))
        o = {"kind": s["kind"], "side": s["side"], "dir": s.get("dir"),
             "date": s["date"], "price": s.get("price"), "k": s.get("k"),
             "prob": s.get("prob"), "checks": s.get("checks", []),
             "status": s.get("status"), "why": s.get("why"),
             "ret": s.get("ret"), "float": s.get("float"),
             "pts": s.get("pts"), "yuan": s.get("yuan"),
             "held": s.get("held"), "lots": s.get("lots"),
             "segStart": s.get("segStart"), "segAge": s.get("segAge"),
             "lead": s.get("lead"),
             # v11 甲方案 + v12 三层风险标签（confirm 后台数据保留，前端不展示）
             "meta": m}
        if s["kind"] in ("pred", "confirm"):
            o.update({"en": s.get("entry"), "atr": s.get("atr"),
                      "adx": s.get("adx"), "gate": s.get("gate"),
                      "strong": s.get("strong"), "checksS": s.get("checksS"),
                      "doi5": s.get("doi5"),
                      "buf": s.get("bufYuan"), "el": s.get("exitLine"),
                      "holdTo": s.get("holdTo"),
                      "last": s.get("last"), "lastDate": s.get("lastDate"),
                      "predDate": s.get("predDate"),
                      # v9 快出：离场线 / 预警
                      "ch": s.get("chLine"), "ml": s.get("maLine"),
                      "ew": s.get("exitWarn"), "eww": s.get("exitWarnWhy"),
                      "dch": s.get("distCh"), "dma": s.get("distMa"),
                      "arm": s.get("armed"), "R": s.get("R")})
        return o

    sigs = [pack(s) for s in state.get("signals", []) if s["sym"] == sym]
    sigs.sort(key=lambda x: x["date"])
    # —— 模块二：新入选品种（RB/UR/JD）不在 state.json 中，用 engine.scan_symbol 现算 pred/warn ——
    if not sigs:
        try:
            us, _ = engine.scan_symbol(sym, bars=main, unit="D")
            raw = [x for x in us if x.get("kind") in ("pred", "warn")]
            sigs = [pack({"kind": x["kind"], "side": x["side"], "date": x["date"],
                         "price": x.get("price"), "k": x.get("k"), "prob": x.get("prob"),
                         "checks": x.get("checks", []), "strong": x.get("strong"),
                         "adx": x.get("adx"), "gate": x.get("gate"), "segStart": x.get("segStart"),
                         "segAge": x.get("segAge"), "lead": x.get("lead"),
                         "entry": x.get("entry"), "atr": x.get("atr")})
                     for x in raw]
            sigs.sort(key=lambda x: x["date"])
            print("   signals via scan_symbol（无 state 信号）: %d" % len(sigs))
        except Exception as e:                      # noqa: BLE001
            print("   scan_symbol 信号生成失败 %s: %s" % (sym, e))
            sigs = []

    # ---- 3b) v15：失效 = 独立防守事件（全量覆盖 + 四条放宽标准）----
    # 覆盖 4/4 建仓、3/4 预警、顶底雷达；每条信号只取最早一次失效。
    # radar_all 同时供下面的 xsig.hist 使用，保证「雷达信号」与「失效打叉」同源。
    radar_all = engine.exit_history(sym, bars=main, years=10,
                                    maxn=10 ** 6, dedupe=8)
    defend = defend_rules.build_defend(sym, cfg["name"], main, S_main,
                                       sigs, radar_all)
    DEF_ALL.extend(defend)

    # ---- 3c) v18 模块三：结构双顶/底（纯价格结构重写，替代 v16 信号配对）----
    # 与 mw_events.full_report() 同源；只做图上 ★ 三角形标记与列表提示，不推微信。
    try:
        mw = mw_events.build_mw(sym, cfg["name"], main, S_main)
        # v26.2：大结构标记（与 daily.py 推送筛选共用 mw_events.is_big 单一口径）。
        # 只用于「信息推送 / 页面只读归档标注」，不改任何形态判定与回测结果。
        for w in mw:
            w["big"] = mw_events.is_big(w)
        MW_ALL.extend(mw)
    except Exception as e:                              # noqa: BLE001
        print("   mw failed %s: %s" % (sym, e))
        mw = []

    # 周/月信号：在主力连续 K 线上按对应周期独立计算（预测层全因果）
    unit_sigs = {}
    for u in ("W", "M"):
        try:
            us, _ = engine.scan_symbol(sym, bars=main, unit=u)
            unit_sigs[u] = [{"kind": x["kind"], "side": x["side"], "date": x["date"],
                             "price": x.get("price"), "k": x.get("k"),
                             "prob": x.get("prob"), "checks": x.get("checks", []),
                             "en": x.get("entry"), "adx": x.get("adx"),
                             "gate": x.get("gate"), "lead": x.get("lead")} for x in us]
        except Exception as e:                              # noqa: BLE001
            print("   unit %s scan failed: %s" % (u, e))
            unit_sigs[u] = []

    # 实时反转雷达状态（附周线 MA45 方向，用于卡片展示；不干预任何计算）
    radar = None
    try:
        radar = engine.live_status(sym, bars=main)
        if radar:
            radar["weekPos"] = engine.weekly_pos_series(main)[-1]
            radar["weekMA"] = engine.WEEKLY_MA
    except Exception as e:                                  # noqa: BLE001
        print("   live_status failed: %s" % e)

    # v25：趋势跟踪模块（原 v8 tf）已彻底下线 —— tf 通道关停，页面卡片同步删除，
    # 这里不再计算/写入 DOC.tf（每品种省 40 笔历史 + 统计，纯减负，不影响任何信号与账本）。

    # 出场雷达（v9 慢进快出）
    exit_doc = {"cfg": {"ma": engine.EXIT_MA, "chK": engine.CH_K,
                        "warnPull": engine.WARN_PULL},
                "pos": None, "stat": None, "trades": []}
    try:
        devs, _ = engine.scan_symbol(sym, bars=main, unit="D")
        trs = [x["exit"] for x in devs
               if x["kind"] == "pred" and x.get("exit") and x["exit"]["status"] == "closed"]
        if trs:
            rs = [t["R"] for t in trs]
            eq = peak = dd = 0.0
            for x in sorted([x for x in devs if x["kind"] == "pred" and x.get("exit")
                             and x["exit"]["status"] == "closed"], key=lambda y: y["date"]):
                eq += x["exit"]["R"]
                peak = max(peak, eq)
                dd = min(dd, eq - peak)
            exit_doc["stat"] = {
                "n": len(rs),
                "win": round(sum(1 for r in rs if r > 0) / len(rs) * 100, 1),
                "R": round(sum(rs), 1),
                "avg": round(sum(rs) / len(rs), 2),
                "dd": round(dd, 1),
                "hold": sorted(t["hold"] for t in trs)[len(trs) // 2],
                "winR": round(sum(r for r in rs if r > 0) / max(1, sum(1 for r in rs if r > 0)), 2),
                "losR": round(sum(r for r in rs if r <= 0) / max(1, sum(1 for r in rs if r <= 0)), 2),
                "byMa": sum(1 for t in trs if "MA" in (t["why"] or "")),
                "byCh": sum(1 for t in trs if "吊灯" in (t["why"] or "")),
            }
        EXIT_ALL.extend([{"d": x["date"], "R": x["exit"]["R"]}
                         for x in devs if x["kind"] == "pred" and x.get("exit")
                         and x["exit"]["status"] == "closed"])
        exit_doc["trades"] = [{"d": x["date"], "out": x["exit"]["exitDate"],
                               "sd": x["side"], "R": x["exit"]["R"],
                               "hold": x["exit"]["hold"], "why": x["exit"]["why"],
                               "warn": x["exit"].get("warnDate")}
                              for x in devs if x["kind"] == "pred" and x.get("exit")
                              and x["exit"]["status"] == "closed"][-12:]
    except Exception as e:                                  # noqa: BLE001
        print("   exit scan failed: %s" % e)

    for s in state.get("signals", []):
        if s["sym"] == sym and s["kind"] == "pred" and s.get("status") in ("open", "pending"):
            exit_doc["pos"] = {"side": s["side"], "entry": s.get("entry"),
                               "last": s.get("last"), "held": s.get("held"),
                               "R": s.get("R"),
                               # v15 A' 利润保护：只做视觉提示，绝不动吊灯 / MA26
                               "mfe": (s.get("exit") or {}).get("mfe"),
                               "atr": s.get("atr"),
                               "chLine": s.get("chLine"),
                               "maLine": s.get("maLine"), "armed": s.get("armed"),
                               "warn": s.get("exitWarn"), "why": s.get("exitWarnWhy"),
                               "distCh": s.get("distCh"), "distMa": s.get("distMa"),
                               "date": s.get("date"), "status": s.get("status")}
            break

    # ---- 3a) v11 甲方案：周线 MA45 位置序列 + 市场状态 ----
    # 降级只决定微信推送；市场状态【纯信息展示】，绝不拦截/隐藏/降级任何信号。
    market = engine.market_state_now(main, S_main)
    MKT_ALL.append(dict(market, sym=sym, name=cfg["name"]))

    def xs_meta(h, i=None):
        if i is None:
            i = idx_by_date.get(h.get("d"))
        if i is None or i >= len(main):
            return None
        dirn = h.get("dir")
        exp = (-1 if dirn == "顶部" else 1) if dirn else (-1 if h.get("side") == "long" else 1)
        tier = "strong" if h.get("level") == "strong" else "normal"
        return engine.signal_meta(main, S_main, i, exp, tier, wpos,
                                  code=sym, pool="radar", review_scope="struct")

    # ---- 3b) 独立出场雷达（见顶/见底，与持仓无关）----
    xsig = None
    try:
        now = engine.exit_radar(bars=main, code=sym)
        hist = radar_all[-16:]          # 与全量失效判定共用同一套雷达事件
        for h in hist:
            h["meta"] = xs_meta(h)
        if now:
            now["meta"] = xs_meta(now, len(main) - 1)
        xsig = {"cfg": {"ext": engine.XS_EXT, "pull": engine.XS_PULL,
                        "amp": engine.XS_AMP, "age": engine.XS_AGE,
                        "shadow": engine.XS_SHADOW, "adx": engine.XS_ADX_DROP,
                        "tier": list(engine.XS_TIER)},
                "now": now,
                "hist": hist,
                "stat": engine.xs_stat(sym),
                "weekly": {"ma": engine.WEEKLY_MA, "mode": engine.WEEKLY_FILTER,
                           "keepStrongOnOpp": engine.KEEP_STRONG_ON_OPP}}
        XSIG_ALL.append(dict(now or {}, sym=sym))
    except Exception as e:                                  # noqa: BLE001
        print("   xsig failed: %s" % e)

    # ---- 4) 主力连续的分钟数据（默认视图也要能看小时/分钟）----
    m60 = m15 = None
    try:
        mm = build.fetch_minute(sym + "0", 60)
        m60 = [[r["d"], r["o"], r["h"], r["l"], r["c"], r.get("v") or 0] for r in mm]
        mm = build.fetch_minute(sym + "0", 15)
        m15 = [[r["d"], r["o"], r["h"], r["l"], r["c"], r.get("v") or 0] for r in mm]
    except Exception:                                       # noqa: BLE001
        pass

    # ---- 4b) v17.1：失效的「头」——把产生失效的顶底雷达源预警补齐 ----
    # 用户口径：失效必须是对标着原预警一起看的叠加，不能只有尾巴没有头。
    # 这里只输出【已经产生失效】的 radar 源，字段与 xsig.hist 完全同款，
    # 前端沿用既有的黄色菱形画法，不改任何原有标识、不改判定口径。
    radar_map = {h["d"]: h for h in radar_all}
    defend_src, seen_src = [], set()
    for e in defend:
        if e["sigKind"] != "radar":          # pred / warn 前端本来就有标识，保持不动
            continue
        key = (e["sigDate"], e["sigKind"])
        if key in seen_src:
            continue
        seen_src.add(key)
        h = radar_map.get(e["sigDate"], {})
        defend_src.append({
            "d": e["sigDate"],
            "dir": h.get("dir") or ("顶部" if e["side"] == "short" else "底部"),
            "level": h.get("level"), "k": h.get("k"), "sd": h.get("sd"),
            "side": e["side"], "defDate": e["date"], "weak": bool(e.get("weak")),
        })
    defend_src.sort(key=lambda x: x["d"])

    # ---- 4b) v27【顶不做多·底不做空】：页面数组清理 ----
    # 与 build_plan 的引擎闸门共用 extreme_gate.blocked() 单一口径。
    # 只删【进场】标识及其成对的失效白圈；出场类（xsig / exit / 熔断）一条不动。
    _di = extreme_gate.date_index(main)
    _all_d = [b["d"] for b in main]

    def _i_of(dt):
        if not dt:
            return None
        j = _di.get(dt)
        if j is None:
            j = bisect.bisect_right(_all_d, dt) - 1
        return j if j >= 0 else None

    def _ban(dt, side, kind=None):
        i = _i_of(dt)
        return i is not None and extreme_gate.blocked(main, i, side, kind)

    _n0 = len(sigs) + len(mw) + len(defend) + len(defend_src)
    _banned_keys = set()
    _sigs_kept = []
    for s in sigs:
        if _ban(s.get("date"), s.get("side"), s.get("kind")):
            _banned_keys.add((s.get("date"), s.get("kind")))
            EXT_BLOCKED.append({"sym": sym, "date": s.get("date"),
                                "kind": s.get("kind"), "side": s.get("side"),
                                "from": "signals"})
        else:
            _sigs_kept.append(s)
    _mw_kept = []
    for w in mw:
        dt = w.get("confirmDate") or w.get("date") or w.get("mkDate")
        if _ban(dt, w.get("side"), "mw"):
            _banned_keys.add((dt, "mw"))
            EXT_BLOCKED.append({"sym": sym, "date": dt, "kind": "mw",
                                "side": w.get("side"), "from": "mw"})
        else:
            _mw_kept.append(w)
    _km = {"radar": "pred", "pred": "pred", "warn": "warn",
           "mw": "mw", "confirm": "confirm"}
    _def_kept = [e for e in defend
                 if (e.get("sigDate"), _km.get(e.get("sigKind"), e.get("sigKind")))
                 not in _banned_keys]
    _dsrc_kept = [e for e in defend_src
                  if not _ban(e.get("d"), e.get("side"))]
    for u in ("W", "M"):
        unit_sigs[u] = [x for x in unit_sigs[u]
                        if not _ban(x.get("date"), x.get("side"), x.get("kind"))]
    _n1 = len(_sigs_kept) + len(_mw_kept) + len(_def_kept) + len(_dsrc_kept)
    print("   v27 极值禁令：本品种页面标识清理 %d 条" % (_n0 - _n1))
    sigs, mw, defend, defend_src = _sigs_kept, _mw_kept, _def_kept, _dsrc_kept

    # ---- 5) 写品种主档 ----
    # v19.3：bestCombo（模块五 双轨最优 / 模块「最佳机会」推送匹配的依据）由
    # compute_best_combo.py 每月写一次，重建数据时必须原样继承，否则 CI 每日跑完就丢。
    _old = _load_json(os.path.join(OUT, "%s.json" % sym), {}) or {}
    doc = {
        "sym": sym, "name": cfg["name"],
        "mult": cfg["mult"], "margin": cfg["margin"], "fee": cfg["fee"],
        "main": bars_to_arr(main),
        "m60": m60, "m15": m15,
        "contracts": cmeta,
        "signals": sigs,
        "defend": defend,                    # v13：独立防守事件（失效当天即新信号）
        "defendSrc": defend_src,             # v17.1：失效对应的源预警（与白圈成对）
        "mw": mw,                            # v18：★ 结构双顶/底（纯价格结构，按 mkDate 定位第二峰）
        "sigW": unit_sigs.get("W", []),
        "sigM": unit_sigs.get("M", []),
        "radar": radar,
        "exit": exit_doc,
        "xsig": xsig,
        "market": market,                    # v11：纯信息展示，不干预任何信号/推送
        "weekly": {"ma": engine.WEEKLY_MA, "mode": engine.WEEKLY_FILTER,
                   "keepStrongOnOpp": engine.KEEP_STRONG_ON_OPP,
                   "confirm2Bars": engine.CONFIRM2_BARS,
                   "volRatioTag": engine.VOL_RATIO_TAG},
    }
    doc["tcSignals"] = compute_tc_signals(main, cfg["mult"], cfg["margin"])

    if _old.get("bestCombo"):
        doc["bestCombo"] = _old["bestCombo"]
    # v22：路况指示器 + 方案持仓 + 进出标识（替代 v20 的 bestCombo 双轨标签）
    try:
        doc["plan"] = build_plan(sym, doc, main, S_main)
        doc.pop("bestCombo", None)          # 旧结论不再继承，防止“虚假排序”回流
    except Exception as e:                              # noqa: BLE001
        print("   plan 构建失败 %s: %s" % (sym, e))
        doc["plan"] = {}
    # v30 叠加：资金背离雷达（L1 状态灯 + L2 确认事件）。纯只读，失败不影响主档。
    try:
        doc["divradar"] = div_radar.scan(main)
    except Exception as e:                              # noqa: BLE001
        print("   divradar 构建失败 %s: %s" % (sym, e))
    json.dump(doc, open(os.path.join(OUT, "%s.json" % sym), "w",
                        encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))

    # ---- 5) 写合约K线文件 ----
    for cid in contracts:
        bars = build.fetch_daily(cid)
        if not bars:                                     # 兜底路径可能带进空合约，别写空文件
            continue
        doc2 = {"sym": sym, "id": cid, "d": bars_to_arr(bars)}
        for t in (60, 15):
            if cid in min_ids:
                try:
                    mm = build.fetch_minute(cid, t)
                    doc2["m%d" % t] = [[r["d"], r["o"], r["h"], r["l"], r["c"],
                                        r.get("v") or 0] for r in mm]
                except Exception:                           # noqa: BLE001
                    pass
        json.dump(doc2, open(os.path.join(OUT, "%s_%s.json" % (sym, cid)),
                             "w", encoding="utf-8"), ensure_ascii=False,
                  separators=(",", ":"))

    return sym


def main():
    # ---- 夜盘闸门（v26）：这道必须先于 os.makedirs / os.remove ----
    # 本函数第一件事是清空 dist/data，夜盘误跑会直接把当天收盘数据清掉；
    # 而且夜盘 bar 属于下一交易日，写进去就是半截假 K 线。
    import nightgate
    nightgate.enforce("data_build.py")

    os.makedirs(OUT, exist_ok=True)
    # 清掉旧的品种数据文件（保留目录）；KEEP_DATA=1 时保留（本地快速重算用）
    if not os.environ.get("KEEP_DATA"):
        for f in os.listdir(OUT):
            os.remove(os.path.join(OUT, f))

    ok = []
    for sym in ORDER:
        if build_product(sym):
            ok.append(sym)

    # 全局：策略参考持仓（账户页展示用，免加载全部品种）
    state = json.load(open("state.json", encoding="utf-8"))
    strat_open = []
    for s in state.get("signals", []):
        if s["sym"] not in PRODUCTS:        # 模块二：剔除已下架品种（SR/C/SP/SA 不在核心池）
            continue
        if s["kind"] != "pred" or s["status"] not in ("open", "pending"):
            continue
        strat_open.append({
            "sym": s["sym"], "name": PRODUCTS[s["sym"]]["name"],
            "side": s["side"], "cd": s["date"], "en": s.get("entry"),
            "el": s.get("exitLine"), "atr": s.get("atr"),
            "last": s.get("last"), "lastDate": s.get("lastDate"),
            "float": s.get("float"), "held": s.get("held"),
            "lots": s.get("lots"), "k": s.get("k"),
            "prob": s.get("prob"),
            # v15 A'：浮盈峰值 / 当前 R / ATR，供持仓卡黄字提示
            "R": s.get("R"), "mfe": (s.get("exit") or {}).get("mfe"),
            "atr": s.get("atr"),
        })

    # v18：结构双顶/底统计（供 mwCfg 记录；纯结构全集 + 推荐高置信子集）
    _mw_stat = mw_events.summarize(MW_ALL)
    _mw_doi = mw_events.summarize(
        [e for e in MW_ALL if e.get("filterDif") and e.get("filterOi")])

    # 品种年化/可行性字段：v22 起直接取 reports/v22_final.json（双段盲测结论），
    # 不再从上一版 index.json 继承——避免旧结论（v20 的虚假年化）随重建回流。
    _v22s = V22.get("symbols") or {}

    def _prod(s):
        v = _v22s.get(s) or {}
        fu = v.get("full") or {}
        rc = v.get("recent") or {}
        te = v.get("test") or {}
        return dict({"sym": s, **PRODUCTS[s]},
                    ann=fu.get("ann"), viable=bool(v),
                    method=v.get("method"), methodLabel=v.get("methodLabel"),
                    recentNet=rc.get("net"), recentWin=rc.get("win"), recentN=rc.get("n"),
                    testWin=te.get("win"), testPf=te.get("pf"),
                    testNet=te.get("net"), testMdd=te.get("mdd"))

    _ext_cfg = extreme_gate.cfg()
    _ext_cfg["stat"] = {
        "clearedMarks": len(EXT_BLOCKED),
        "bySym": {s: sum(1 for x in EXT_BLOCKED if x["sym"] == s) for s in ORDER},
        "scope": "仅清进场标识；出场雷达 / 白圈失效 / 吊灯 / MA26 / 30% 熔断一律不动",
    }

    manifest = {
        "built": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "extGate": _ext_cfg,
        "products": [_prod(s) for s in ORDER],
        "v22": {"orderBasis": "近 1 年净收益降序",
                "newSyms": ["P", "HC"], "added": ["SM"], "dropped": ["RB", "UR", "JD"],
                "rules": {"startCap": 10000, "addUnit": 10000, "stop30": 3000,
                          "lot": 1, "rangeSilent": True}},
        "strategyOpen": strat_open,
        "method": state.get("method"),
        "configMode": engine.CONFIG_MODE,
        "uniformCfg": {"ma": engine.MA, "exitMa": engine.EXIT_MA,
                       "chK": engine.CH_K, "ext": engine.XS_EXT,
                       "pull": engine.XS_PULL},
        "weeklyCfg": {"mode": engine.WEEKLY_FILTER, "ma": engine.WEEKLY_MA,
                      "keepStrongOnOpp": engine.KEEP_STRONG_ON_OPP,
                      "confirm2Bars": engine.CONFIRM2_BARS,
                      "volRatioTag": engine.VOL_RATIO_TAG},
        "marketAdvisoryOnly": True,        # 状态栏仅信息参考，不拦截/隐藏/降级信号
        "marketBySym": MKT_ALL,
        # v12：三层风险标签（纯展示，不参与任何决策、不新增推送事件）
        "riskTagCfg": {
            "enabled": True,
            "warn": {"layer": "T日", "tag": "结构未成熟 · 动能真空",
                     "rule": "5维动能打分；进场类>=4、出场雷达类>=5",
                     "cuts": engine.RISK_CUTS, "min": engine.RISK_VAC_MIN},
            "review": [
                {"layer": "T+1", "tag": "首日失效",
                 "rule": "A1破信号日极值 + B1失守MA5，窗口1根"},
                {"layer": "T+3~8", "tag": "结构已破坏",
                 "rule": "入场缓冲>=1ATR 且收盘吃掉>=50%"},
            ],
            "demoted": ["2日未跟进", "低波动待观察"],
            "noExtraPush": True,
            "baseExit": {"exitMa": engine.EXIT_MA, "chK": engine.CH_K},
        },
        # v15：失效打叉全量覆盖（四条任一即算；纯展示，不改进场/出场）
        "defendCfg": {
            "version": "v24",
            "scope": ["4/4 建仓", "3/4 预警", "顶底雷达"],
            "rules": [
                {"key": "rev", "label": "反向幅度", "rule": "收盘逆行 >1.5 ATR（20 根内）"},
                {"key": "ma", "label": "均线破位",
                 "rule": "先在内侧、再连续 2 根收破 MA26/MA34 且超出 0.5 ATR；"
                         "单根就暴破 ≥0.8 ATR 则立即失效（保命，不等确认）"},
                {"key": "decay", "label": "时间衰竭",
                 "rule": "5 根内未创新高/低且收破信号日低点/高点"},
                {"key": "stop", "label": "原版止损失效",
                 "rule": "MA26 反向穿越（同上：连 2 根 + 0.5 ATR，暴破 0.8 ATR 例外）"
                         "或吊灯 3ATR 触发离场"},
            ],
            "onePerSignal": True,
            "baseExitUnchanged": True,
            "profitTip": {"atr": 1.5, "text": "利润已达 1.5 ATR，可手动将止损上移至开仓价保本",
                          "advisoryOnly": True},
            # v17：弱叉降级（数据全留，只改展示与推送）
            "weakCfg": {
                "rule": defend_rules.WEAK_RULE,
                "adx": defend_rules.WEAK_ADX,
                "effect": "空心圈 / 列表标「弱」/ 不单独推微信；事件一条不删",
                "backtest": {"n": 155, "pct": 33.3,
                             "dropFalseAlarm": 43.9, "keepFalseAlarm": 32.9,
                             "dropTrueFail": 41.9, "keepTrueFail": 51.3,
                             "trainPP": 3.5, "blindPP": 31.7},
                "advisoryOnly": True, "reversible": True,
            },
            "stat": defend_rules.summarize(DEF_ALL),
        },
        # v18.1 模块三：结构双顶/底（4 条硬约束严格重写，替代 v18 初版）
        # 与 reports/ 同源；图上只画高置信（hiConf）结构，绝不改动进场/出场。
        "mwCfg": {
            "version": "v18.1",
            "label": "★ 结构双顶/底",
            "rule": "硬约束：① ZigZag摆动波幅≥1.0ATR（毛刺平滑）② 峰为过去60根绝对主峰（至少一峰）③ 颈线回撤≥1.5ATR ④ 双峰差≤1.5ATR ⑤ 两峰间无更极端插入极值；间隔5~60根 · 颈线破位收盘确认",
            "engine": "v18.1 重写：类ZigZag摆动 + 绝对主峰强约束，淘汰小毛刺与半山腰假峰",
            "drawOnlyHiConf": True,  # 图上只画高置信子集（用户 2026-11-08：不要把所有信号都画上去）
            "backtest": {  # 严格结构全集（数据留存供研究）
                "n": _mw_stat.get("backtestN"), "win": _mw_stat.get("winRate"),
                "avgR": _mw_stat.get("avgR"),
            },
            "filterBest": {  # 推荐高置信子集（DIF背离 + 持仓降≥10%）
                "name": "DIF背离 + 持仓降≥10%",
                "n": _mw_doi.get("backtestN"), "win": _mw_doi.get("winRate"),
                "avgR": _mw_doi.get("avgR"),
            },
            "advisoryOnly": True,
            "baseExitUnchanged": True,
            # v26.2：大级别结构（信息推送门槛）。小结构不推微信，只在信号栏做只读归档。
            "bigRule": {
                "gap": mw_events.BIG_GAP,
                "depth": mw_events.BIG_DEPTH,
                "tag": mw_events.BIG_TAG,
                "label": "🏔 大级别结构 · 信息推送级",
                "rule": "两峰间隔 ≥%s 根 且 颈线回撤 ≥%.1f ATR = 大结构，随第⑤通道做信息推送；"
                        "小结构不推，仅在信号栏做只读归档。" % (mw_events.BIG_GAP, mw_events.BIG_DEPTH),
                "n": sum(1 for w in MW_ALL if w.get("big")),
                "advisoryOnly": True,
            },
            "stat": mw_events.summarize(MW_ALL),
        },
        # v17：静默观察期（用户 2026-11-08 拍板）——只做记录，不加任何新指标/规则
        "observeCfg": {
            "mode": "静默观察期", "since": "2026-11-09",
            "mwFreezeMonths": [3, 6], "mwNoBoost": True,
            "noNewIndicators": True, "noNewFilters": True,
            "sampleGate": {"now": 60, "target": [120, 150]},
            "goal": "实盘验证回撤是否下降，不再优化逻辑",
            "dailyLog": {"path": "logs/YYYY-MM-DD.md", "cumulative": "LIVE_LOG.md",
                         "source": "dist/data/*.json + state.json", "advisoryOnly": True},
            "baseEntryUnchanged": True, "baseExitUnchanged": True,
        },
        # v22.1：微信推送白名单 = 当前核心池（与 engine.CODES / ORDER 同源，不再读 state 里的旧名单）
        "pushCfg": {"symbols": list(ORDER), "all": True, "by": "daily",
                    "rangeSilent": True},
        "model": {
            "version": engine.MODEL.get("version", 4),
            "ma": engine.MA,
            "conds": engine.MODEL.get("conds"),
            "calib": engine.MODEL.get("calib"),
            "base": engine.MODEL.get("base"),
            "gate": engine.MODEL.get("gate"),
            "stat": engine.MODEL.get("stat"),
            "lead": engine.MODEL.get("lead", {}),
            "univ": ORDER,
            "nofstop": True,
        },
        "xsig": {
            "cfg": {"ext": engine.XS_EXT, "pull": engine.XS_PULL, "amp": engine.XS_AMP,
                    "age": engine.XS_AGE, "shadow": engine.XS_SHADOW,
                    "adx": engine.XS_ADX_DROP, "tier": list(engine.XS_TIER)},
            "live": [x for x in XSIG_ALL],
        },
        "exit": {
            "ma": engine.EXIT_MA,
            "chK": engine.CH_K,
            "warnPull": engine.WARN_PULL,
            "stat": _exit_all_stat(),
        },
    }
    # ---- v32 强顺势中继 · 多空衰减监控（纯统计层，只写 tcHealth；按笔数取样）----
    # 7 品种合并统计「最近 12 笔已完成信号」的实际胜率；空头 <45% / 多头 <40% 且满 12 笔即标记暂停。
    # 不参与任何交易、账本、平仓、推送决策，只影响页面上的灰化与横幅。
    try:
        import tc_monitor as _tcm
        _h = _tcm.apply_health_to_dir(OUT)
        if _h:
            print("   tcHealth：空头%s / 多头%s（样本 %d 笔）" % (
                "暂停" if _h["status"]["short"]["paused"] else "正常",
                "暂停" if _h["status"]["long"]["paused"] else "正常",
                _h.get("totalSignals", 0)))
    except Exception as _e:
        print("   tcHealth 生成跳过：%s" % _e)

    json.dump(manifest, open(os.path.join(OUT, "index.json"), "w",
                             encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))

    total = 0
    for f in sorted(os.listdir(OUT)):
        sz = os.path.getsize(os.path.join(OUT, f)) / 1024
        total += sz
    print("\n完成 %d 品种，data/ 共 %.1f MB" % (len(ok), total / 1024))


if __name__ == "__main__":
    main()
