# -*- coding: utf-8 -*-
"""每日任务 v4：拉数据 → 反转雷达扫描 → 推进持仓 → 按概率阈值推送 → 落盘

由 cron 在交易日 15:10 触发，也可以手动 python3 daily.py 立刻跑一次。

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

import engine

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


# ---------------------------------------------------------------- 持仓推进

def update_open_positions(state):
    """pred 持仓推进（与回测口径完全一致，不设止损）：
    进场后先等价格站到仓位方向一侧（新趋势成立），之后一旦收盘反向穿越 MA34
    → 新趋势结束，离场/反手。只有数据异常才靠 250 根上限兜底。"""
    for s in state["signals"]:
        if s["kind"] != "pred" or s["status"] not in ("open", "pending"):
            continue
        bars = engine_bars(s["sym"])
        S = engine_S(s["sym"])
        if not bars:
            continue
        ei = s["entryIdx"]
        if ei > len(bars) - 1:                 # 还没到进场日（次日开盘）
            s["status"] = "pending"
            s["held"] = 0
            cur = bars[-1]
            s["last"] = round(cur["c"], 1)
            s["lastDate"] = cur["d"]
            s["exitLine"] = round(S["ma"][-1] or cur["c"], 1)
            s["float"] = 0.0
            continue
        s["status"] = "open"
        long = s["side"] == "long"
        atr = s.get("atr") or s["entry"] * 0.01
        exit_px = exit_date = why = None
        armed = False                      # 是否已站到仓位方向一侧（新趋势成立）
        for j in range(ei, min(ei + engine.MAXHOLD, len(bars))):
            b, ma = bars[j], S["ma"][j]
            if ma is None:
                continue
            if not armed:
                if (long and b["c"] > ma) or ((not long) and b["c"] < ma):
                    armed = True
                continue
            if long and b["c"] < ma:
                exit_px, exit_date, why = b["c"], b["d"], "反向穿越 MA34"
                break
            if (not long) and b["c"] > ma:
                exit_px, exit_date, why = b["c"], b["d"], "反向穿越 MA34"
                break
        if exit_px is None and len(bars) - 1 >= ei + engine.MAXHOLD:
            j = ei + engine.MAXHOLD - 1
            exit_px, exit_date, why = bars[j]["c"], bars[j]["d"], "时间到期"
        if exit_px is not None:
            pts = (exit_px - s["entry"]) * (1 if long else -1)
            mult = engine.MULT.get(s["sym"], 10)
            fee = engine.FEE.get(s["sym"], 5) * 2
            s.update({"status": "closed", "exit": round(exit_px, 1),
                      "exitDate": exit_date, "why": why,
                      "pts": round(pts, 1),
                      "ret": round(pts / s["entry"] * 100, 2),
                      "yuan": round(pts * mult - fee)})
        else:
            cur = bars[-1]
            ma = S["ma"][-1] or cur["c"]
            s["last"] = round(cur["c"], 1)
            s["lastDate"] = cur["d"]
            s["exitLine"] = round(ma, 1)
            s["floatPts"] = round((cur["c"] - s["entry"]) * (1 if long else -1), 1)
            s["float"] = round((cur["c"] / s["entry"] - 1) * (1 if long else -1) * 100, 2)
            s["held"] = len(bars) - 1 - ei


# ---------------------------------------------------------------- 主流程

def main():
    cfg = load_json(CONF, {})
    codes = cfg.get("codes") or engine.CODES
    equity = cfg.get("equity", 100_000)
    risk = cfg.get("riskPct", 5.0) / 100.0      # 无止损：预留给浮亏的本金比例
    min_prob = cfg.get("pushMinProb", 0.70)
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
    update_open_positions(state)

    preds = [s for s in state["signals"] if s["kind"] == "pred"]
    opens = [s for s in preds if s["status"] in ("open", "pending")]
    closed = [s for s in preds if s["status"] == "closed"]
    watches = []
    for c in codes:
        for s in reversed(state["signals"]):
            if s["sym"] == c and s["kind"] == "warn":
                watches.append(s)
                break

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
        "name": "反转预测雷达 v4",
        "desc": "MA34 定义趋势段（7 个周期回测确认 34 最优）；反转前 4 条入选特征计分 + "
                "ADX≥20 趋势门槛 → 命中条数对应反转概率；命中 4 条即「准备反转」，"
                "次日开盘建仓，不设止损，持有到反向穿越 MA34",
        "calib": engine.MODEL.get("calib", {}),
        "base": engine.MODEL.get("base", {}),
        "gate": engine.MODEL.get("gate", {}),
        "trade": mt,
        "lead": engine.MODEL.get("lead", {}),
        "univ": engine.CODES,
        "ma": engine.MA,
    }
    # ---------- 趋势跟踪状态（v8）：方向变化时推送 ----------
    tf_prev = state.get("tf", {})
    tf_now = {}
    tf_flips = []
    for c in engine.CODES:
        L = engine.tf_live(c)
        if not L:
            continue
        tf_now[c] = {"side": L["side"], "since": L["since"], "entry": L["entry"]}
        if not first and tf_prev.get(c, {}).get("since") != L["since"]:
            tf_flips.append((c, L))
    state["tf"] = tf_now
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---------- 推送：概率达阈值的新信号（首次回填不推） ----------
    near = (now - _dt.timedelta(days=12)).strftime("%Y-%m-%d")
    fresh = [] if first else [s for s in found
                              if s["date"] >= near
                              and s["kind"] == "pred"
                              and (s.get("prob", {}).get("5") or 0) >= min_prob]
    if fresh and cfg.get("pushplus"):
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
            body = ('<div style="background:#fff1f0;padding:10px;border-radius:6px;'
                    'font-size:13px;line-height:1.9">'
                    '当前价 <b>%s</b> · 距 MA34 仅 %s ATR<br>'
                    '4 条反转特征 <b>全部命中</b> + ADX <b>%s</b>（门槛 20）%s：'
                    '未来 3 日内反转概率 <b>%.0f%%</b>，5 日内 <b>%.0f%%</b>（首轮触发实测 69.4%%）<br>'
                    '参考入场 <b>%s</b>（次日开盘）· <b>不设止损</b>，持有到反向穿越 MA34<br>'
                    '建议 <b>%s 手</b>（本金 %s，浮亏预算 %s%%，每手预留约 %s 元）'
                    '<div style="color:#a33;font-size:12px;margin-top:8px">'
                    '⚠ 该口径历史 %d 笔（10 年 5 品种）：胜率 %.0f%%、盈亏比 %.1f、'
                    '年均约 %.1f 笔；持仓期浮亏中位 0.95ATR、最深 8.7ATR（约 8,900 元/手）。'
                    '收益集中在少数大行情，务必小仓。</div>'
                    '</div>%s'
                    % (s["price"], "%.2f" % abs(s.get("dist", 0)) if s.get("dist") is not None else "—",
                       s.get("adx"), "★ 强确认" if s.get("strong") else "", p3, p5,
                       s.get("entry"), s.get("lots", "—"),
                       "{:,}".format(int(equity)), cfg.get("riskPct", 5),
                       "{:,}".format(int(s.get("bufYuan") or 0)),
                       int(engine.MODEL.get("stat", {}).get("n", 0)),
                       (engine.MODEL.get("stat", {}).get("win", 0) or 0) * 100,
                       engine.MODEL.get("stat", {}).get("payoff", 0),
                       engine.MODEL.get("stat", {}).get("n", 0) / 10.0, acts))
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
        ok = post_push(cfg["pushplus"], title, "".join(cards))
        print("push:", ok)

    # ---------- 推送：趋势跟踪方向确认 ----------
    if tf_flips and cfg.get("pushplus"):
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
        ok = post_push(cfg["pushplus"], title, "".join(cards))
        print("push tf:", ok)

    print("scan done  found=%d  pushed=%d  pred=%d open=%d closed=%d win=%s%% sumYuan=%s"
          % (len(found), len(fresh), len(preds), len(opens), len(closed),
             state["stats"]["win"], state["stats"]["sumYuan"]))


if __name__ == "__main__":
    main()
