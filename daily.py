# -*- coding: utf-8 -*-
"""每日任务 v4：拉数据 → 反转雷达扫描 → 推进持仓 → 按概率阈值推送 → 落盘

由 GitHub Actions 在交易日 15:20 / 15:50（北京时间）触发，也可手动跑一次。
微信推送按 config.pushSymbols 白名单过滤；CI 环境用环境变量 PUSHPLUS_TOKEN。

信号三层（同一段趋势内各自最多一次，方向唯一）：
  warn    预警：命中 3/4 条（或 4 条但 ADX<20）→ 只提示，不建仓
  pred    准备反转：命中 4/4 条且 ADX≥20 → 概率 78.9%（5 日内），次日开盘可建仓
  confirm 反转兑现：收盘真正穿越 MA34（只作参考，不追）
持仓管理：**不设止损**。进场后一直持有，直到收盘反向穿越 MA34（新趋势走完）才离场。
          安全上限 250 根（回测最长 83 根），仅防数据异常导致的僵尸持仓。
推送阈值：概率 ≥ config.pushMinProb（默认 0.70，即命中 4 条才会推送）。
"""
import datetime as _dt
import json
import os
import sys
import urllib.parse
import urllib.request

import defend_rules
import engine
import mw_events
import regime_lib

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state.json")
CONF = os.path.join(ROOT, "config.json")

_BARS = {}
_S = {}


# ---------------------------------------------------------------- 工具

def load_json(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def post_push(token, title, html):
    if not token:
        return False
    body = urllib.parse.urlencode({
        "token": token, "title": title, "content": html, "template": "html"}).encode()
    try:
        req = urllib.request.Request(
            "https://www.pushplus.plus/send", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8", "ignore"))
        return j.get("code") == 200
    except Exception as e:                      # noqa: BLE001
        print("push failed:", e, file=sys.stderr)
        return False


def engine_bars(code, years=10):
    if code in _BARS:
        return _BARS[code]
    import build
    adj, _ = build.panama_adjust(code)
    keep = int(years * 244)
    _BARS[code] = adj[-keep:] if len(adj) > keep else adj
    return _BARS[code]


def engine_S(code):
    if code not in _S:
        _S[code] = engine.make_S(engine_bars(code))
    return _S[code]


# ---------------------------------------------------------------- v22 路况 + 方案
# 每个品种的定稿方案（G1=D3 / G2 / G4 / G8）与双段盲测数据由 v22_final.py 写入
# dist/data/{sym}.json 的 plan 字段。路况则【实时计算】——daily 在 data_build 之前跑，
# 读文件会拿到昨天的路况，所以这里用同一份 regime_lib 当场算，口径与页面完全一致。
REGIME_NM = {"trend_up": "上涨趋势", "trend_dn": "下跌趋势",
             "range": "震荡", "transition": "过渡态"}
SIG_NM = {"pred": "顶底雷达", "warn": "进场预警", "mw": "WM结构",
          "confirm": "反转兑现", "tf": "转折确认"}
_PLAN = {}
_REG = {}


def plan_for(sym):
    """读该品种定稿方案（spec / verify）。读不到返回 {}。"""
    if sym in _PLAN:
        return _PLAN[sym]
    p = {}
    for base in (os.path.join(ROOT, "dist", "data"), os.path.join(ROOT, "data")):
        try:
            with open(os.path.join(base, sym + ".json"), encoding="utf-8") as f:
                p = (json.load(f) or {}).get("plan") or {}
            if p:
                break
        except Exception:                            # noqa: BLE001
            continue
    _PLAN[sym] = p
    return p


def regime_now(sym):
    """实时路况：(kind, 中文)。只依赖日线收盘，与页面/回测同一函数。"""
    if sym in _REG:
        return _REG[sym]
    try:
        bars = engine_bars(sym)
        reg = regime_lib.causal_regime(bars, engine_S(sym), len(bars) - 1)
    except Exception:                                # noqa: BLE001
        reg = "na"
    _REG[sym] = (reg, REGIME_NM.get(reg, "未知"))
    return _REG[sym]


def entry_kinds(sym, reg):
    """该品种在当前路况下允许的进场信号集合（来自定稿方案 spec）"""
    sp = (plan_for(sym) or {}).get("spec") or {}
    return tuple((sp.get("entry") or {}).get(reg) or ())


def regime_side_ok(sym, reg, side):
    """趋势中只做顺势（与回测 align=True 一致）"""
    sp = (plan_for(sym) or {}).get("spec") or {}
    if not sp.get("align"):
        return True
    if reg == "trend_up":
        return side == "long"
    if reg == "trend_dn":
        return side == "short"
    return True


def regime_banner(syms):
    """推送顶部的路况一览：哪些在趋势/过渡（可交易）、哪些在震荡（静默）"""
    on, off = [], []
    for s in syms:
        reg, nm = regime_now(s)
        if reg in ("trend_up", "trend_dn", "transition"):
            on.append("%s %s%s" % (s, nm, "（过渡·雷达）" if reg == "transition" else ""))
        elif reg == "range":
            off.append(s)
    h = ""
    if on:
        h += ('<div style="background:#f0f7ff;border-left:3px solid #4c8dff;padding:7px 10px;'
              'border-radius:4px;font-size:12.5px;line-height:1.7;margin-bottom:12px">'
              '<b>🌤 今日路况（可交易）</b><br>%s</div>') % "　|　".join(on)
    if off:
        h += ('<div style="background:#f6f6f6;border-left:3px solid #999;padding:7px 10px;'
              'border-radius:4px;font-size:12px;line-height:1.7;color:#666;margin-bottom:12px">'
              '<b>💤 震荡静默（不推进场信号）</b>：%s</div>') % "、".join(off)
    return h


def regime_line(sym):
    """单条推送里的路况行"""
    reg, nm = regime_now(sym)
    p = plan_for(sym) or {}
    kk = entry_kinds(sym, reg)
    if reg == "range":
        tail = "本场景不开仓（系统静默）"
    elif reg == "transition":
        tail = "允许信号：%s" % ("、".join(SIG_NM.get(k, k) for k in kk) or "无")
    else:
        tail = "允许信号：%s%s" % ("、".join(SIG_NM.get(k, k) for k in kk) or "无",
                                 "（仅顺势）" if (p.get("spec") or {}).get("align") else "")
    return "【当前路况】<b>%s</b>（已持续 %s 个交易日）· %s" % (
        nm, (p.get("regime") or {}).get("days") or "—", tail)


# ---------------------------------------------------------------- 持仓推进

def update_open_positions(state, all_pred=False):
    """pred 持仓推进（v9 慢进快出）：
    进场不变；离场走两条独立规则 ——
      ① 吊灯：自进场后极值回撤 3ATR，收盘触及无条件走；
      ② 短均线：站到仓位一侧后，收盘反向穿越 MA26。
    盘整预警（自极值回撤≥1.2ATR 且 ATR收缩/持仓降/缩量）只提示，不平仓。
    """
    for s in state["signals"]:
        if s["kind"] != "pred":
            continue
        if not all_pred and s["status"] not in ("open", "pending"):
            continue
        if "entryIdx" not in s:
            continue
        bars = engine_bars(s["sym"])
        S = engine_S(s["sym"])
        if not bars:
            continue
        ei = s["entryIdx"]
        long = s["side"] == "long"
        side = 1 if long else -1
        if ei > len(bars) - 1:                 # 还没到进场日（次日开盘）
            s["status"] = "pending"
            s["held"] = 0
            cur = bars[-1]
            s["last"] = round(cur["c"], 1)
            s["lastDate"] = cur["d"]
            s["maLine"] = None
            s["chLine"] = None
            s["float"] = 0.0
            continue
        s["status"] = "open"
        atr = s.get("atr") or s["entry"] * 0.01
        xt = engine.exit_track(bars, S, ei, side, atr, s["entry"])
        s["maLine"] = xt["maLine"]
        s["chLine"] = xt["chLine"]
        s["distMa"] = xt["distMa"]
        s["distCh"] = xt["distCh"]
        s["armed"] = xt["armed"]
        s["exitWarn"] = xt["warn"]
        s["exitWarnWhy"] = xt["warnWhy"]
        if xt["status"] == "closed":
            pts = (xt["exitPx"] - s["entry"]) * side
            mult = engine.MULT.get(s["sym"], 10)
            fee = engine.FEE.get(s["sym"], 5) * 2
            s.update({"status": "closed", "exit": xt["exitPx"],
                      "exitDate": xt["exitDate"], "why": xt["why"],
                      "pts": round(pts, 1),
                      "ret": round(pts / s["entry"] * 100, 2),
                      "yuan": round(pts * mult - fee),
                      "held": xt["hold"], "R": xt["R"],
                      "exitWarn": False})
        else:
            cur = bars[-1]
            s["last"] = round(cur["c"], 1)
            s["lastDate"] = cur["d"]
            s["floatPts"] = round((cur["c"] - s["entry"]) * side, 1)
            s["float"] = round((cur["c"] / s["entry"] - 1) * 100 * side, 2)
            s["held"] = xt["hold"]
            s["R"] = round((cur["c"] - s["entry"]) * side / atr, 2)


# ---------------------------------------------------------------- 推送白名单

def risk_line(meta, with_review=False):
    """v12：风险标签一行，只附在【已有】推送卡片里，绝不单独触发推送。

    with_review=True 时附带事后标签（首日失效 / 结构已破坏）；
    False 只带 T 日「动能真空」。
    """
    if not meta:
        return ""
    r = meta.get("risk") or {}
    parts = []
    v = r.get("vac") or {}
    if v.get("tag"):
        parts.append('<b style="color:#a16207">⚠ 结构未成熟 · 动能真空 %d/5</b>'
                     % int(v.get("score") or 0))
    if with_review:
        d1 = r.get("day1")
        if d1:
            parts.append('<b style="color:#c2410c">⚠ 首日失效 · %s（第%s根）</b>'
                         % (d1.get("why") or "", d1.get("day")))
        st = r.get("struct")
        if st:
            parts.append('<b style="color:#b91c1c">⚠ 结构已破坏 · 第%s根</b>'
                         % st.get("day"))
    if not parts:
        return ""
    return ('<div style="margin-top:8px;padding:6px 8px;background:#fffbeb;'
            'border-left:3px solid #f59e0b;font-size:12px;line-height:1.8;color:#444">'
            '风险标签：%s</div>' % " ｜ ".join(parts))


def push_scope(cfg):
    """返回 (allow_push, symbols, all_flag)：微信推送的品种范围。

    只决定「发不发微信」；信号生成、页面展示、后台盈亏计算完全不受影响。
      pushSymbols     只推这些品种；留空 = 沿用原规则（全部品种 + MA45 降级）
      pushSymbolsAll  白名单品种豁免 MA45 降级，进出场信息全量推送
    """
    syms = [str(x).upper() for x in (cfg.get("pushSymbols") or [])]
    all_flag = bool(cfg.get("pushSymbolsAll", True))

    def allow(sym, meta=None):
        if syms and str(sym).upper() not in syms:
            return False
        if syms and all_flag:
            return True
        return (meta or {}).get("push", True)

    return allow, syms, all_flag


# ---------------------------------------------------------------- v19.1：30% 单笔浮亏强制熔断
# 用户 2026-11-09 拍板：铁律优先级最高，高于反手策略（等下一个信号）。
# 口径：单笔（1 手）反向浮亏 ≥ 30% 本金（1 万/品种 → 约 3,000 元）即无条件手动平仓。
BREAKER_YUAN = 3000
IRON_RULE = (
    '<div style="margin-top:14px;padding:8px 10px;background:#fff0f0;'
    'border-left:3px solid #d93025;border-radius:4px;font-size:12px;'
    'line-height:1.8;color:#d93025">'
    '<b>⚠️ 铁律 · 出场优先级（v21 防守优先，严禁死扛）</b>：'
    '① 出现<b>失效（白圈）</b>或<b>强烈离场</b> → 立刻平仓，先下车再等新信号；'
    '② 跌破 MA26 或吊灯 3ATR → 立刻平仓；'
    '③ 原仓已平之后，才允许反向反手。'
    '任一持仓单笔浮亏达到本金 30%（约 3,000 元）→ <b>无条件平仓保命</b>，'
    '优先级高于以上全部。</div>')


def breaker_scan(opens, allow_push):
    """扫描持仓，返回触发 30% 熔断的信号列表（1 手口径浮亏 ≥ BREAKER_YUAN）。"""
    hits = []
    for s in opens:
        mult = engine.MULT.get(s.get("sym"), 10)
        loss = (s.get("floatPts") or 0) * mult          # 负数 = 亏损
        if loss <= -BREAKER_YUAN and allow_push(s.get("sym")):
            s["breakerLoss"] = round(loss)
            hits.append(s)
    return hits


# ---------------------------------------------------------------- v22：方案内信号（最高优先级）
# v20 的 bestCombo（历史最优/近1年最优标签）已被 v21 双段盲测推翻并删除。
# 现在的判断依据是 data/{sym}.json 的 plan.spec：该品种定稿方案在当前路况下
# 允许哪些进场信号、是否只做顺势。震荡市一律不匹配 → 系统静默。


def theo_pos(sym, signals, exclude=None):
    """与前端 renderFlipBoard 同源：按信号时间推演该品种当前理论持仓方向。"""
    pos = None
    for s in sorted([x for x in signals if x.get("sym") == sym
                     and x.get("kind") in ("pred", "warn") and x is not exclude],
                    key=lambda x: x.get("date", "")):
        if pos is None or pos.get("side") != s.get("side"):
            pos = {"side": s.get("side"), "date": s.get("date")}
    return pos


def combo_action(label, sig_side, cur_side):
    """v22：按「防守优先」口径给出实战建议（不再默认反手）"""
    sd = "多" if sig_side == "long" else "空"
    if cur_side and cur_side != sig_side:
        return "先平%s（防守优先，不等反手），再顺信号做%s" % (
            "多" if cur_side == "long" else "空", sd)
    if cur_side and cur_side == sig_side:
        return "持仓观望（方向与当前持仓一致，不加仓）"
    return "次日开盘顺势开仓做%s" % sd


def fwd_gate(s):
    """v22 纪律闸门：震荡 / 未知路况一律不推进场信号；趋势中只推顺势。

    这是用户明确要求的「震荡市系统必须保持静默（不推虚假信号）」的代码落点。
    注意只管「推不推微信」：页面照常展示全部信号，回测/统计不变。
    """
    sym = s.get("sym")
    reg, _nm = regime_now(sym)
    if reg in ("range", "na"):
        return False
    return regime_side_ok(sym, reg, s.get("side"))


def best_match(found, signals, allow_push):
    """v22：信号命中「该品种定稿方案 + 当前路况允许 + 顺势」→ 最高优先级推送。

    震荡市一律不匹配（系统静默，不推虚假进场信号）。
    这是 v19.2「最佳机会」的 v22 版本：判断依据从 v20 的 bestCombo 标签，
    换成双段盲测定稿的 plan.spec，且叠加实时路况闸门。
    """
    hits = []
    for s in found:
        sym = s.get("sym")
        if s.get("kind") not in ("pred", "warn", "mw", "confirm", "tf"):
            continue
        if not allow_push(sym):
            continue
        reg, nm = regime_now(sym)
        if reg in ("range", "na"):
            continue                      # 震荡/未知 → 静默
        kk = entry_kinds(sym, reg)
        if s.get("kind") not in kk:
            continue
        if not regime_side_ok(sym, reg, s.get("side")):
            continue
        p = plan_for(sym) or {}
        cur = theo_pos(sym, signals, exclude=s)
        if (p.get("open") or {}).get("side"):
            cur = {"side": p["open"]["side"]}
        s["bestTrack"] = "plan"
        s["bestRegime"] = nm
        s["bestInfo"] = p.get("verify") or {}
        s["bestAdvice"] = combo_action("plan", s.get("side"), (cur or {}).get("side"))
        hits.append(s)
    return hits


# ---------------------------------------------------------------- 主流程

def main():
    cfg = load_json(CONF, {})
    codes = cfg.get("codes") or engine.CODES
    equity = cfg.get("equity", 100_000)
    risk = cfg.get("riskPct", 5.0) / 100.0      # 无止损：预留给浮亏的本金比例
    min_prob = cfg.get("pushMinProb", 0.70)
    risk_on = bool(cfg.get("riskTags", True))   # v12：风险标签总开关（只影响附注文字）
    # ---- v11.1：微信推送按品种白名单（只管「发不发」，不碰信号/页面/计算）----
    allow_push, push_syms, push_all = push_scope(cfg)
    push_token = (cfg.get("pushplus") or os.environ.get("PUSHPLUS_TOKEN") or "").strip()
    state = load_json(STATE, {"signals": [], "seen": [], "runs": 0})
    first = not state.get("seen")
    now = _dt.datetime.now()
    state["scanDate"] = now.strftime("%Y-%m-%d")
    state["runs"] = state.get("runs", 0) + 1

    found, newest = [], None
    cut_back = (now - _dt.timedelta(days=365 * 10)).strftime("%Y-%m-%d")
    for c in codes:
        try:
            sigs, _ = engine.scan_symbol(c, bars=engine_bars(c))
        except Exception as e:                  # noqa: BLE001
            print("scan %s failed: %s" % (c, e), file=sys.stderr)
            continue
        for s in sigs:
            if s["date"] < cut_back:            # 回填 10 年，与回测样本一致
                continue
            key = "%s|%s|%s" % (s["sym"], s["kind"], s["date"])
            if key in state["seen"]:
                continue
            state["seen"].append(key)
            if s["kind"] == "pred":
                pos = engine.position_of(s)
                s.update(pos)
                s["lots"] = engine.lots(equity, risk, pos["entry"], None, c, s.get("atr"))
                s["status"] = "open"
            else:
                s["status"] = "watch"
            s["foundAt"] = now.strftime("%Y-%m-%d %H:%M")
            found.append(s)
            if newest is None or s["date"] > newest:
                newest = s["date"]

    state["signals"] = sorted(state["signals"] + found, key=lambda x: x["date"])
    # ---------- v11 甲方案：周线 MA45 降级档位 + 复盘标签 ----------
    # 只决定「要不要推微信」，不删除、不延迟任何信号；后台盈亏计算完全不变。
    # 依据 10 年 A/B（reports/终极融合_AB对比表.txt）：仅周线降级是双达标模块。
    for c in codes:
        bars = engine_bars(c)
        S = engine_S(c)
        wpos = engine.weekly_pos_series(bars)
        for s in state["signals"]:
            if s.get("sym") != c:
                continue
            i = s.get("idx")
            if i is None or i >= len(bars):
                continue
            exp = 1 if s.get("side") == "long" else -1
            tier = "strong" if s.get("strong") else "normal"
            # v12：首日失效/结构已破坏只对实际建仓(pred)有意义
            scope = "all" if s.get("kind") == "pred" else "none"
            s["meta"] = engine.signal_meta(bars, S, i, exp, tier, wpos,
                                           code=c, pool="in", review_scope=scope)
    # 全量重算：出场口径变更后，历史成交也按新规则统一（MA26 + 吊灯 3ATR）
    update_open_positions(state, all_pred=True)

    preds = [s for s in state["signals"] if s["kind"] == "pred"]
    opens = [s for s in preds if s["status"] in ("open", "pending")]
    closed = [s for s in preds if s["status"] == "closed"]
    watches = []
    for c in codes:
        for s in reversed(state["signals"]):
            if s["sym"] == c and s["kind"] == "warn":
                watches.append(s)
                break

    # ---------- 离场预警（v9）：盘整特征点亮，当天推一次 ----------
    today = now.strftime("%Y-%m-%d")
    exit_warns = []
    for s in opens:
        if (s.get("exitWarn") and s.get("warnPushedAt") != today
                and allow_push(s["sym"])):
            s["warnPushedAt"] = today
            exit_warns.append(s)

    # ---------- v19.1：30% 单笔浮亏熔断扫描（有触发必醒目推送） ----------
    breaker_hits = breaker_scan(opens, allow_push)

    # ---------- v22：方案内信号匹配（路况允许 + 方案进场集合 → 最高优先级推送） ----------
    # 只认「近 12 天」的新信号：首次回填新品种时 found 里会有 10 年历史信号，
    # 不过滤会把它们当成今天的交易机会推出去（上一版漏了这个过滤，实测推出 16 条）。
    near = (now - _dt.timedelta(days=12)).strftime("%Y-%m-%d")
    best_hits = best_match([x for x in found if x["date"] >= near],
                           state["signals"], allow_push)

    state["updated"] = now.strftime("%Y-%m-%d %H:%M")
    state["stats"] = {
        "pred": len(preds), "open": len(opens), "closed": len(closed),
        "warn": len(watches),
        "win": round(sum(1 for s in closed if s.get("ret", 0) > 0) / len(closed) * 100, 1)
        if closed else None,
        "avgRet": round(sum(s.get("ret", 0) for s in closed) / len(closed), 2)
        if closed else None,
        "sumYuan": round(sum(s.get("yuan", 0) for s in closed)),
    }
    mt = engine.MODEL.get("stat", {})
    state["method"] = {
        "name": "反转预测雷达 v9（慢进快出）",
        "desc": "进场不变：MA34 定义趋势段，反转前 4 条特征 + ADX≥20，命中 4 条即「准备反转」，"
                "次日开盘建仓。出场改为独立快出：①吊灯止损（自进场后极值回撤 3ATR）；"
                "②收盘反向穿越 MA26（先站到仓位一侧再穿回）。盘整预警只提示不平仓",
        "calib": engine.MODEL.get("calib", {}),
        "base": engine.MODEL.get("base", {}),
        "gate": engine.MODEL.get("gate", {}),
        "trade": mt,
        "lead": engine.MODEL.get("lead", {}),
        "univ": engine.CODES,
        "ma": engine.MA,
        "exit": {"ma": engine.EXIT_MA, "chK": engine.CH_K, "warnPull": engine.WARN_PULL},
        # v11 甲方案：只影响推送，不干预信号
        "filter": {"mode": engine.WEEKLY_FILTER, "weeklyMA": engine.WEEKLY_MA,
                   "keepStrongOnOpp": engine.KEEP_STRONG_ON_OPP,
                   "confirm2Bars": engine.CONFIRM2_BARS,
                   "volRatioTag": engine.VOL_RATIO_TAG,
                   "marketStateAdvisoryOnly": True},
        # v11.1：微信推送范围（页面仍展示全部 8 品种、全部信号）
        "push": {"symbols": push_syms, "all": push_all, "by": "daily",
                 "note": "白名单外品种照常显示，仅不发微信"},
    }
    # ---------- 独立出场信号（见顶/见底，与是否持仓完全无关） ----------
    xs_today, xs_fresh = [], []
    seen_x = state.setdefault("xsigSeen", [])
    for c in codes:
        try:
            r = engine.exit_radar(code=c, bars=engine_bars(c))
        except Exception as e:                  # noqa: BLE001
            print("xsig %s failed: %s" % (c, e), file=sys.stderr)
            continue
        if not r:
            continue
        r["sym"] = c
        bars = engine_bars(c)
        S = engine_S(c)
        dirn = r.get("dir")
        exp = (-1 if dirn == "顶部" else 1) if dirn else (-1 if r.get("side") == "long" else 1)
        tier = "strong" if r.get("level") == "strong" else "normal"
        r["meta"] = engine.signal_meta(bars, S, len(bars) - 1, exp, tier,
                                       code=c, pool="radar")
        xs_today.append(r)
        # 顺势全推；逆势只推强档。观察档页面照常显示，只是不推微信（不删信号）
        if r["level"] in ("suggest", "strong") and allow_push(c, r["meta"]):
            key = "%s|%s|%s" % (c, r["date"], r["level"])
            if key not in seen_x:
                seen_x.append(key)
                xs_fresh.append(r)
    state["xsigSeen"] = seen_x[-400:]
    state["xsigToday"] = xs_today

    # ---------- 趋势跟踪状态（v8）：方向变化时推送 ----------
    tf_prev = state.get("tf", {})
    tf_now = {}
    tf_flips = []
    for c in engine.CODES:
        L = engine.tf_live(c)
        if not L:
            continue
        tf_now[c] = {"side": L["side"], "since": L["since"], "entry": L["entry"]}
        if (not first and tf_prev.get(c, {}).get("since") != L["since"]
                and allow_push(c)):
            # v22 纪律：震荡 / 逆势的趋势确认同样是虚假信号，一并不推
            _reg, _nm = regime_now(c)
            if _reg in ("range", "na") or not regime_side_ok(c, _reg, L["side"]):
                print("muted tf flip: %s %s(%s)" % (c, L["side"], _nm))
            else:
                tf_flips.append((c, L))
    state["tf"] = tf_now
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---------- v15/v17：独立防守事件（全量失效打叉，白名单独立推送）----------
    # 与页面 K 线白圈同源：4/4 建仓 / 3/4 预警 / 顶底雷达，四条标准任一成立即失效。
    # v17：weak=True（顶底雷达 & ADX≥30）只在图上画空心圈、列表标「弱」，不进独立推送。
    # 只加提示，不改 MA34 进场与 MA26 + 吊灯 3ATR 出场。
    defend_fresh = []
    seen_def = state.setdefault("defendSeen", [])
    if not first:
        for code in codes:
            if not allow_push(code):
                continue
            bars = engine_bars(code)
            S = engine_S(code)
            sigs = [s for s in state["signals"]
                    if s["sym"] == code and s.get("kind") in ("pred", "warn")]
            radar = engine.exit_history(code, bars=bars, years=10,
                                        maxn=10 ** 6, dedupe=8)
            events = defend_rules.build_defend(
                code, engine.NAMES.get(code, code), bars, S, sigs, radar)
            for e in events:
                if e["date"] != today:
                    continue
                # v17 弱叉降级：顶底雷达 & ADX≥30 只记录、只上图、不单独推微信。
                # 事件本身一条不删，判定口径不变，随时可切回全量。
                if e.get("weak"):
                    continue
                dedup = "%s|%s|%s|%s" % (code, e["sigDate"], e["sigKind"], e["date"])
                if dedup in seen_def:
                    continue
                defend_fresh.append((e, dedup))

    if defend_fresh and push_token:
        cards = []
        for e, _dd in defend_fresh[:8]:
            sd = "多" if e["side"] == "long" else "空"
            why = "跌破信号日低点 / 均线破位" if e["side"] == "long" \
                else "涨破信号日高点 / 均线破位"
            cards.append(
                '<div style="margin-bottom:16px">'
                '<div style="font-size:16px;font-weight:700;color:#c2185b">'
                '🛑 失效打叉：%s（%s）%s单 · %s</div>'
                '<div style="font-size:13px;line-height:1.85;margin-top:6px">'
                '判定日 <b>%s</b> · 原信号 <b>%s</b>（%s，%s）· 第 <b>%s</b> 根<br>'
                '<span style="color:#555">%s</span></div>'
                '<div style="margin-top:8px;padding:6px 8px;background:#fff1f5;'
                'border-left:3px solid #FF2D95;font-size:12px;color:#7a1f45;line-height:1.7">'
                '这是独立防守提示：走势已经证明原信号错了，可手动止损；'
                '后台吊灯 3ATR 与 MA26 离场线保持不变。'
                '</div></div>'
                % (e["name"], e["sym"], sd, why, today, e["sigDate"],
                   e["sigLabel"], e["label"], e["day"], e["detail"]))
        title = "🛑 失效打叉 %d 条：%s" % (
            len(defend_fresh),
            "、".join("%s%s" % (e["name"], "多" if e["side"] == "long" else "空")
                     for e, _d in defend_fresh[:8]))
        ok = post_push(push_token, title, "".join(cards) + IRON_RULE)
        print("push defend:", ok)
        if ok:
            seen_def.extend(dd for _e, dd in defend_fresh)
    state["defendSeen"] = seen_def[-600:]
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---------- v18 模块三：结构双顶/底（仅页面 ★ 三角形标记 + 列表提示，不再单独推微信） ----------
    # 用户 2026-11-08：避免打扰，M/W 结构不单独发微信；daily 不再扫描/推送（数据由 data_build 写入各品种 JSON）。
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---------- 推送：概率达阈值的新信号（首次回填不推） ----------
    # v22：先过纪律闸门（震荡静默 / 只做顺势），再谈概率门槛。
    # 被闸门拦下的信号会打印出来，数得清、查得到，不是"悄悄消失"。
    raw_fresh = [] if first else [s for s in found
                                  if s["date"] >= near
                                  and s["kind"] == "pred"
                                  and not s.get("bestTrack")
                                  and (s.get("prob", {}).get("5") or 0) >= min_prob
                                  and allow_push(s["sym"], s.get("meta"))]
    fresh = [s for s in raw_fresh if fwd_gate(s)]
    muted = [s for s in raw_fresh if s not in fresh]
    if muted:
        print("muted by regime gate:", ", ".join(
            "%s %s(%s)" % (s["sym"], s.get("kind"), regime_now(s["sym"])[1]) for s in muted))

    # ---------- v22 推送：方案内信号（路况允许 + 方案匹配 → 最高优先级） ----------
    # 与 v19.2 的区别：判断依据从「v20 的 bestCombo 标签」换成「v21 双段盲测定稿的 plan.spec」，
    # 并叠加实时路况闸门（震荡一律静默）。文案必须带当前路况——这是明确的纪律要求。
    if best_hits and push_token and not first:
        site = cfg.get("siteUrl", "")
        cards = []
        for s in best_hits:
            sym = s["sym"]
            v = s.get("bestInfo") or {}
            p = plan_for(sym) or {}
            sig_nm = SIG_NM.get(s["kind"], s["kind"])
            sd_txt = "做多方向" if s.get("side") == "long" else "做空方向"
            ver_txt = ("%s 笔 · 胜率 <b>%.1f%%</b> · 盈亏比 <b>%.2f</b> · 净 %+d 元 · 回撤 %.1f%%"
                       % (v.get("n", 0), v.get("win") or 0, v.get("pf") or 0,
                          v.get("net") or 0, v.get("mdd") or 0)) if v.get("n") \
                      else "验证段样本不足（不足 3 笔），按方案信号执行但降低仓位预期"
            cards.append(
                '<div style="margin-bottom:18px;border:1px solid #f59e0b;border-radius:8px;'
                'padding:12px;background:#fffdf5">'
                '<div style="font-size:17px;font-weight:700;color:#b45309">'
                '🔥 方案内信号：%s %s</div>'
                '<div style="font-size:13px;line-height:2.0;margin-top:8px">'
                '%s<br>'
                '【当前信号】<b>%s</b>（%s）· 触发日 %s<br>'
                '【方案依据】%s『%s』的后 30%% 盲测段：%s<br>'
                '【实战建议】<b style="color:#b45309">%s</b><br>'
                '【防守底线】🚨 浮亏达本金 30%%（约 3000 元）无条件平仓，不等反手、不抗单。</div>'
                '<div style="color:#888;font-size:12px;margin-top:6px">'
                '<a href="%s">查看反转雷达 →</a></div></div>'
                % (sym, engine.NAMES.get(sym, ""), regime_line(sym), sig_nm, sd_txt,
                   s["date"], sym, p.get("methodLabel") or p.get("method") or "—", ver_txt,
                   s.get("bestAdvice", "持仓观望"), site or "#"))
        title = "🔥 方案内信号 %d 条：%s" % (
            len(best_hits), "、".join(
                "%s%s(%s)" % (s["sym"], "多" if s.get("side") == "long" else "空",
                              (s.get("bestRegime") or "")[:2]) for s in best_hits))
        ok = post_push(push_token, title,
                       regime_banner([s["sym"] for s in best_hits]) + "".join(cards) + IRON_RULE)
        print("push plan-signal:", ok)

    if fresh and push_token:
        site = cfg.get("siteUrl", "")
        cards = []
        for s in fresh:
            p3 = (s.get("prob", {}).get("3") or 0) * 100
            p5 = (s.get("prob", {}).get("5") or 0) * 100
            side_txt = "顶部反转 · 做空方向" if s["side"] == "short" else "底部反转 · 做多方向"
            acts = "".join(
                '<div style="padding:5px 0;border-bottom:1px dashed #eee;font-size:13px">'
                '<span style="color:%s;font-weight:700">%s</span> %s'
                '<span style="float:right;color:#555">%s</span></div>'
                % ("#0a0" if c["ok"] else "#c33", "✓" if c["ok"] else "✗", c["t"], c["v"])
                for c in s["checks"])
            body = ('<div style="background:#f7f9ff;padding:8px 10px;border-radius:6px;'
                    'border-left:3px solid #4c8dff;font-size:13px;line-height:1.85">%s</div>'
                    '<div style="background:#fff1f0;padding:10px;border-radius:6px;'
                    'font-size:13px;line-height:1.9;margin-top:8px">'
                    '当前价 <b>%s</b> · 距 MA34 仅 %s ATR · 参考入场 <b>%s</b>（次日开盘）<br>'
                    '4 条反转特征 <b>全部命中</b> + ADX <b>%s</b>（门槛 20）%s：'
                    '未来 3 日内反转概率 <b>%.0f%%</b>，5 日内 <b>%.0f%%</b>（首轮触发实测 69.4%%）<br>'

                    '建议 <b>%s 手</b>（本金 %s，浮亏预算 %s%%，每手预留约 %s 元）'
                    '<div style="color:#a33;font-size:12px;margin-top:8px">'
                    '⚠ 该口径历史 %d 笔（10 年 5 品种）：胜率 %.0f%%、盈亏比 %.1f、'
                    '年均约 %.1f 笔；持仓期浮亏中位 0.95ATR、最深 8.7ATR（约 8,900 元/手）。'
                    '收益集中在少数大行情，务必小仓。</div>'
                    '</div>%s'
                    % (regime_line(s["sym"]),
                       s["price"], "%.2f" % abs(s.get("dist", 0)) if s.get("dist") is not None else "—",
                       s.get("entry"),
                       s.get("adx"), "★ 强确认" if s.get("strong") else "", p3, p5,
                       s.get("lots", "—"),
                       "{:,}".format(int(equity)), cfg.get("riskPct", 5),
                       "{:,}".format(int(s.get("bufYuan") or 0)),
                       int(engine.MODEL.get("stat", {}).get("n", 0)),
                       (engine.MODEL.get("stat", {}).get("win", 0) or 0) * 100,
                       engine.MODEL.get("stat", {}).get("payoff", 0),
                       engine.MODEL.get("stat", {}).get("n", 0) / 10.0, acts))
            # v22：此通道只推「路况允许 + 顺势」但不在该品种方案进场集合内的信号，
            # 明确定性为参考，不写进方案账本。
            _p = plan_for(s["sym"]) or {}
            body += ('<div style="color:#8a6d3b;background:#fffbe6;padding:6px 8px;'
                     'border-left:3px solid #f59e0b;border-radius:4px;font-size:12px;'
                     'margin-top:6px">ℹ 该信号不在 %s 定稿方案（%s）的进场集合内，'
                     '仅作方向参考，不构成进场指令。</div>'
                     % (s["sym"], _p.get("methodLabel") or _p.get("method") or "—"))
            if risk_on:
                body += risk_line(s.get("meta"))
            cards.append(
                '<div style="margin-bottom:18px">'
                '<div style="font-size:16px;font-weight:700">🔔 准备反转：%s %s</div>'
                '<div style="color:#888;font-size:12px;margin:4px 0 8px">%s · %s</div>%s'
                '<div style="color:#888;font-size:12px;margin-top:8px">%s</div></div>'
                % (s["sym"], engine.NAMES.get(s["sym"], ""), side_txt, s["date"], body,
                   ("<a href='%s'>查看反转雷达 →</a>" % site) if site else ""))
        title = "反转预警 %d 条：%s" % (
            len(fresh), "、".join("%s%s(%.0f%%)" % (
                s["sym"], "空" if s["side"] == "short" else "多",
                (s.get("prob", {}).get("5") or 0) * 100) for s in fresh))
        ok = post_push(push_token, title, "".join(cards) + IRON_RULE)
        print("push:", ok)

    # ---------- 推送：出场预警（顶/底盘整特征点亮） ----------
    if exit_warns and push_token:
        site = cfg.get("siteUrl", "")
        cards = []
        for s in exit_warns:
            sd = "多" if s["side"] == "long" else "空"
            cards.append(
                '<div style="margin-bottom:16px">'
                '<div style="font-size:16px;font-weight:700;color:#d97706">⚠ 出场预警：%s %s（持%s）</div>'
                '<div style="background:#fffbeb;padding:10px;border-radius:6px;'
                'font-size:13px;line-height:1.9;margin-top:6px">'
                '当前 <b>%s</b> · 浮盈 <b>%+.2fR</b> · 已持 <b>%s</b> 根<br>'
                '预警信号：%s<br>'
                '离场线：吊灯 <b>%s</b>（触及无条件走）· MA%d <b>%s</b><br>'
                '<span style="color:#666;font-size:12px">盘整特征已出现，趋势可能停滞。'
                '不必立即平仓，但不要再加仓；跌破离场线即走。</span>%s</div></div>'
                % (s["sym"], engine.NAMES.get(s["sym"], ""), sd, s.get("last"),
                   s.get("R", 0), s.get("held"), " · ".join(s.get("exitWarnWhy") or []),
                   s.get("chLine"), engine.EXIT_MA, s.get("maLine"),
                   (risk_line(s.get("meta"), True) if risk_on else "")))
        title = "出场预警 %d 条：%s" % (
            len(exit_warns), "、".join("%s%s" % (s["sym"], "多" if s["side"] == "long" else "空")
                                       for s in exit_warns))
        ok = post_push(push_token, title, "".join(cards) + IRON_RULE)
        print("push exit-warn:", ok)

    # ---------- 推送：独立出场信号（顶/底） ----------
    if xs_fresh and push_token:
        site = cfg.get("siteUrl", "")
        cards = []
        for r in xs_fresh:
            col = "#dc2626" if r["level"] == "strong" else "#d97706"
            lvl = "强烈建议离场" if r["level"] == "strong" else "建议减仓"
            okn = " · ".join(x["n"] for x in r["conds"] if x["ok"]) or "—"
            cards.append(
                '<div style="margin-bottom:16px">'
                '<div style="font-size:16px;font-weight:700;color:%s">'
                '🔔 %s：%s %s</div>'
                '<div style="background:#fff7ed;padding:10px;border-radius:6px;'
                'font-size:13px;line-height:1.9;margin-top:6px">'
                '%s 收盘 <b>%s</b> · 距 MA34 <b>%+.2f ATR</b> · 自极值回撤 <b>%.2f ATR</b><br>'
                '已满足 %d 条：%s<br>'
                '<span style="color:#666;font-size:12px">这是独立于进场信号的出场提示，'
                '不需要持仓也可参考。是否离场由你自己决定。</span>%s</div></div>'
                % (col, lvl, r["sym"], engine.NAMES.get(r["sym"], ""),
                   r["date"], r["close"], r["ext"], r["pull"],
                   len([x for x in r["conds"] if x["ok"]]), okn,
                   (risk_line(r.get("meta")) if risk_on else "")))
        title = "出场信号 %d 条：%s" % (
            len(xs_fresh), "、".join("%s%s" % (r["sym"], "顶" if r["dir"] == "顶部" else "底")
                                     for r in xs_fresh))
        ok = post_push(push_token, title, "".join(cards) + IRON_RULE)
        print("push xsig:", ok)

    # ---------- 推送：趋势跟踪方向确认 ----------
    if tf_flips and push_token:
        site = cfg.get("siteUrl", "")
        cards = []
        for c, L in tf_flips:
            sd = "多" if L["side"] == "long" else "空"
            cards.append(
                '<div style="margin-bottom:14px">'
                '<div style="font-size:15px;font-weight:700">📈 趋势确认：%s %s → 持%s</div>'
                '<div style="font-size:13px;line-height:1.8;margin-top:4px">'
                '确认日 <b>%s</b> · 参考入场 <b>%s</b>（次日开盘顺势）<br>'
                '反向确认价 <b>%s</b>（触及即离场）· 持有上限 %d 根<br>'
                '<span style="color:#888;font-size:12px">该口径 10 年 254 笔、胜率 49%%、'
                '累计 +136R、回撤 -27R；属趋势确认层，比反转信号晚但可靠，'
                '适合与反转仓位分仓管理。</span></div></div>'
                % (c, engine.NAMES.get(c, ""), sd, L["since"], L["entry"],
                   L["flip"], L["hold"]))
        title = "趋势确认 %d 条：%s" % (
            len(tf_flips), "、".join("%s%s" % (c, "多" if L["side"] == "long" else "空")
                                     for c, L in tf_flips))
        ok = post_push(push_token, title, "".join(cards) + IRON_RULE)
        print("push tf:", ok)

    # ---------- v19.1 推送：30% 熔断触发（每个信号每天只推一次，红字置顶） ----------
    if breaker_hits and push_token:
        seen_brk = state.setdefault("breakerPushed", {})
        todo = [s for s in breaker_hits
                if seen_brk.get("%s|%s" % (s["sym"], s["date"])) != today]
        if todo:
            site = cfg.get("siteUrl", "")
            cards = []
            for s in todo:
                sd = "多" if s["side"] == "long" else "空"
                cards.append(
                    '<div style="margin-bottom:16px">'
                    '<div style="font-size:17px;font-weight:700;color:#d93025">'
                    '🚨 30% 熔断触发：%s %s（持%s）</div>'
                    '<div style="background:#fff0f0;padding:10px;border-radius:6px;'
                    'border:1px solid #f3c2c2;font-size:13px;line-height:1.9;margin-top:6px">'
                    '信号日 <b>%s</b> · 入场 <b>%s</b> · 当前 <b>%s</b><br>'
                    '单笔浮亏约 <b style="color:#d93025">%s 元</b>'
                    '（1 手口径，已达本金 30%%）<br>'
                    '<b style="color:#d93025">⚠️ 无条件立即手动平仓！'
                    '不等反手信号、不等反弹、不抗单。</b><br>'
                    '平仓后回到空仓，等待下一个信号再入场。</div></div>'
                    % (s["sym"], engine.NAMES.get(s["sym"], ""), sd,
                       s.get("date"), s.get("entry"), s.get("last"),
                       "{:,}".format(abs(int(s.get("breakerLoss") or 0)))))
            title = "🚨 30% 熔断触发 %d 笔：%s（立即平仓！）" % (
                len(todo), "、".join(s["sym"] for s in todo))
            ok = post_push(push_token, title, "".join(cards) + IRON_RULE)
            print("push breaker:", ok)
            if ok:
                for s in todo:
                    seen_brk["%s|%s" % (s["sym"], s["date"])] = today
            state["breakerPushed"] = dict(list(seen_brk.items())[-200:])
            json.dump(state, open(STATE, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
    # 无持仓触发时：仅在交易日把铁律以摘要形式随每日状态落盘，供页面/日志核对
    state["breaker"] = {
        "rule": "单笔浮亏 ≥ 30% 本金（约 3,000 元）→ 无条件手动平仓",
        "thresholdYuan": BREAKER_YUAN,
        "checkedAt": now.strftime("%Y-%m-%d %H:%M"),
        "hits": [s["sym"] for s in breaker_hits],
    }

    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("scan done  found=%d  pushed=%d  pred=%d open=%d closed=%d win=%s%% sumYuan=%s"
          % (len(found), len(fresh), len(preds), len(opens), len(closed),
             state["stats"]["win"], state["stats"]["sumYuan"]))


if __name__ == "__main__":
    main()
