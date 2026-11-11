# -*- coding: utf-8 -*-
"""夜盘预警（night warn）—— 只读，绝不写 state.json / data/

为什么单独一个脚本
    23:10 的夜盘只走了 21:00-23:00 这一段，而且这段行情按交易所口径属于
    【下一个交易日】。把它拼进日线，等于给 K 线塞一根半截假 bar，
    日线图与账本会永久带错。所以这一班与 15:20 / 15:50 彻底分家：
        · 只读线上 data/ 与新浪分钟线；
        · 夜盘 bar 只在内存里存在，d 标成「下一交易日」，绝不回填当天；
        · 不写 state.json、不写 data/、不写 dist/、不写 logs/；
        · 不 push 任何文件（workflow 里还有一道 git status 断言兜底）。

它回答的问题
    「如果明早开盘就是这个价，会不会出事？」
    命中才推，不命中一个字都不发：
        · 持仓中：夜盘已触发出场（白圈/吊灯/MA26/熔断）→ 必推
        · 持仓中：逆向 ≥0.5 ATR（与白圈同口径）→ 推
        · 任何状态：路况翻成趋势（明早可能开仓）→ 推
        · 空仓 + 仍震荡 → 静默（与「震荡只静音进场」同一条规则）

与正式口径的关系
    明早 15:20 那一班才是权威。夜盘结论只写「请注意明早开盘应对」，
    不产生任何交易记录、不改任何持仓。

用法
    python night_warn.py                    # 推微信
    python night_warn.py --dry              # 只打印，不推
    python night_warn.py --demo M 2026-11-06
        —— 回放「2026-11-05 晚夜盘」，第二个参数填【夜盘归属的交易日】
"""
import datetime as _dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

import build
import engine
import intraday_warn as IW
import nightgate
import regime_lib

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")

REGIME_NM = IW.REGIME_NM
TREND = IW.TREND
NIGHT_HOURS = (21, 3)          # 夜盘时段：21:00 起，至次日 03:00 前


# ---------------------------------------------------------------- 工具

def bj_now():
    return nightgate.bj_now()


def _t(s):
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def next_trade_day(d):
    """夜盘归属的交易日：次一工作日（节假日无法预判，按工作日顺延）。"""
    d = d + _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += _dt.timedelta(days=1)
    return d


def workdays_between(a, b):
    """(a, b] 之间有几个工作日。周五→周一 = 1，周四→周一 = 2（中间漏了周五）。"""
    a = _dt.date.fromisoformat(a) if isinstance(a, str) else a
    b = _dt.date.fromisoformat(b) if isinstance(b, str) else b
    n, d = 0, a
    while d < b:
        d += _dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def push(token, title, html):
    """独立实现，刻意不 import daily —— 那条链路带 state 写入。

    ⚠️ 23:10 夜盘预警**故意不接** daily.post_push() 的 15:00-20:00 静音闸门。
    2026-11-11 用户明确拍板保留：「23:10 那条告诉你明早开盘就是这个价会不会出事，
    是明早的操作依据」。所以它落在闸门窗口外也照发。
    不要为了"统一规则"把它接进闸门 —— 那会让用户失去次日开盘前唯一的主动提示。
    """
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
    except Exception as e:                                    # noqa: BLE001
        print("push failed:", e, file=sys.stderr)
        return False


# ---------------------------------------------------------------- 夜盘 bar

def night_seq(mm, now):
    """从分钟线里取「此刻正在走的这一段夜盘」。

    时段：21:00 之后，或次日 03:00 之前（覆盖 23:00 / 01:00 / 02:30 收盘的品种）。
    只保留靠近此刻的连续段，防止把昨晚的夜盘误当成今晚。
    """
    rows = []
    for b in mm:
        t = _t(b.get("d") or "")
        if t is None or t > now:
            continue
        if now - t > _dt.timedelta(hours=8):
            continue
        if t.hour >= NIGHT_HOURS[0] or t.hour < NIGHT_HOURS[1]:
            rows.append((t, b))
    if not rows:
        return []
    rows.sort(key=lambda x: x[0])
    seg = [rows[-1]]
    for t, b in reversed(rows[:-1]):
        if seg[0][0] - t <= _dt.timedelta(minutes=25):
            seg.insert(0, (t, b))
        else:
            break
    return seg


def agg(seg, D):
    """把夜盘分钟段压成一根日线形态的 bar（只在内存里用）。"""
    bars = [b for _, b in seg]
    return {"d": D,
            "o": round(float(bars[0]["o"]), 1),
            "h": round(max(float(b["h"]) for b in bars), 1),
            "l": round(min(float(b["l"]) for b in bars), 1),
            "c": round(float(bars[-1]["c"]), 1),
            "v": round(sum(float(b["v"]) for b in bars), 1),
            "oi": round(float(bars[-1].get("oi") or 0), 1),
            "n": len(bars),
            "asof": seg[-1][0].strftime("%H:%M")}


def night_bar(sym, now=None):
    """今晚这段夜盘 —— 没有夜盘的品种（FG/SM 等）返回 None。"""
    now = now or bj_now()
    try:
        mm = build.fetch_minute(sym + "0", 15)
    except Exception as e:                                    # noqa: BLE001
        print("   %s 分钟线抓取失败: %s" % (sym, e))
        return None
    seg = night_seq(mm, now)
    if not seg:
        return None
    last = seg[-1][0]
    D = (next_trade_day(last.date()) if last.hour >= NIGHT_HOURS[0]
         else last.date())
    return agg(seg, D.isoformat())


# ---------------------------------------------------------------- 推演

def judge(sym, nm, page, tb, base_reg=None, base_pos=None):
    """含夜盘 bar 复跑一遍，与今日收盘口径比差异。"""
    main = page["main"]
    base = page.get("plan") or {}
    c0 = float(main[-1][4])
    op = base.get("open")
    side = base_pos if base_pos is not None else (
        op.get("side") if isinstance(op, dict) else None)

    bars = [{"d": b[0], "o": b[1], "h": b[2], "l": b[3],
             "c": b[4], "v": b[5], "oi": b[6]} for b in main] + [tb]
    r, reg = IW.rerun(sym, bars, nm, page)

    prev_reg = base_reg if base_reg is not None else (base.get("regime") or {}).get("now")
    px, asof, D = tb["c"], tb["asof"], tb["d"]
    pct = (px / c0 - 1) * 100

    # 逆向幅度（按持仓方向取最不利的一侧），与白圈同口径 0.5 ATR
    atr = None
    try:
        S = engine.make_S(bars)
        atr = float(S["atr"][-1] or 0) or None
    except Exception:                                         # noqa: BLE001
        pass
    if not atr or atr <= 0:
        atr = max(abs(c0) * 0.01, 1e-6)

    if side == "long":
        adverse = (c0 - tb["l"]) / atr
        fav = (tb["h"] - c0) / atr
    elif side == "short":
        adverse = (tb["h"] - c0) / atr
        fav = (c0 - tb["l"]) / atr
    else:
        adverse = max(abs(tb["h"] - c0), abs(c0 - tb["l"])) / atr
        fav = 0.0

    adv = []          # [(级别, 文本)] 级别 1=高 2=中

    # ① 夜盘收盘裁决出的出场（白圈/吊灯/MA26/到期）——引擎把它挂在「明早开盘」执行，
    #    不产生 trade，只在 audit 里留一条 signal_exit。必须按 t=最后一根 取，
    #    否则会漏掉夜盘最主要的那类风险。
    n_last = len(bars) - 1
    for a in (r.get("audit") or []):
        if a.get("act") == "signal_exit" and a.get("t") == n_last:
            adv.append((1, "夜盘收盘裁决出场：<b>%s</b><br>"
                           "<span style='color:#666'>明早开盘执行，别等到 15:20</span>" % (
                               a.get("note") or IW.WHY_NM.get(a.get("why"), a.get("why")))))

    # ② 夜盘直接打穿的 30% 熔断（引擎当场强平，会留下 trade）
    for t in r["trades"]:
        if t.get("exit_date") == D:
            adv.append((1, "夜盘已触发 <b>%s</b>（%+.0f 元），明早开盘无条件处理" % (
                IW.WHY_NM.get(t["why"], t["why"]), t["net"])))

    if prev_reg and reg != prev_reg:
        txt = "%s → <b>%s</b>" % (REGIME_NM.get(prev_reg, prev_reg),
                                  REGIME_NM.get(reg, reg))
        if side:
            adv.append((1, "路况翻转（持仓中）：" + txt))
        elif reg in TREND:
            adv.append((1, "进入可开仓区：" + txt))

    if side and adverse >= 0.5 and not adv:
        adv.append((1 if adverse >= 1.0 else 2,
                    "夜盘逆向 <b>%.1f ATR</b>（%s %.1f / %s %.1f，今收 %.1f）" % (
                        adverse,
                        "最低" if side == "long" else "最高",
                        tb["l"] if side == "long" else tb["h"],
                        "最高" if side == "long" else "最低",
                        tb["h"] if side == "long" else tb["l"], c0)))

    print("%-3s %s 夜盘 %.1f (%+.2f%%) 逆向 %.2fATR 路况 %s%s" % (
        sym, nm, px, pct, adverse, REGIME_NM.get(reg, reg),
        "" if not adv else "  ← 命中 %d 条" % len(adv)))
    for _, a in adv:
        print("     · " + re.sub(r"<[^>]+>", " ", a))
    if not adv:
        return None
    adv.sort(key=lambda x: x[0])
    return {"sym": sym, "name": nm, "px": px, "pct": pct, "asof": asof,
            "reg": reg, "side": side, "atr": round(atr, 2),
            "adv": [a for _, a in adv],
            "has_exit": any(("出场" in a) or ("熔断" in a) for _, a in adv),
            "nbar": tb["n"], "day": D}


# ---------------------------------------------------------------- 输出

def html_of(hits, now):
    rows = []
    for h in hits:
        col = "#d93025" if h["pct"] >= 0 else "#0a7d32"      # 红涨绿跌
        side = {"long": "多", "short": "空"}.get(h["side"] or "", "")
        pos = ("　持%s单" % side) if side else "　空仓"
        head = ("<div style='margin:0 0 4px'><b>%s %s</b>　夜盘 %.1f"
                "<span style='color:%s'>　%+.2f%%</span>"
                "<span style='color:#888;font-size:11.5px'>%s</span></div>"
                % (h["name"], h["sym"], h["px"], col, h["pct"], pos))
        body = "".join("<div style='color:#b91c1c;font-size:12.5px;line-height:1.75'>· %s</div>"
                       % a for a in h["adv"])
        if h["has_exit"]:
            tip = "→ 明早开盘按裁决处理，别等 15:20 收盘"
        else:
            tip = "→ 明早开盘别抢跑，等 09:00 第一根 15 分钟确认"
        rows.append(
            "<div style='margin:9px 0;padding:8px 10px;background:#fffbf5;"
            "border-left:3px solid #e6a23c;border-radius:4px'>%s%s"
            "<div style='color:#8a6d3b;font-size:12px;margin-top:5px'>%s</div></div>"
            % (head, body, tip))
    return ("<div style='font-size:13px'><b>🌙 夜盘预警</b>"
            "<span style='color:#888;font-size:11px'>（截至 %s · 夜盘 21:00-23:00 半截行情）</span>"
            "<div style='color:#888;font-size:11px;line-height:1.7;margin:5px 0 4px'>"
            "只用于决定明早怎么应对；<b>K 线与正式结论仍以今日 15:20 收盘为准</b>，"
            "明早收盘会重新判定一次。</div>%s</div>"
            % (now.strftime("%m-%d %H:%M"), "".join(rows)))


# ---------------------------------------------------------------- 主流程

def load_pages():
    idx = json.load(open(os.path.join(DATA, "index.json"), encoding="utf-8"))
    out = []
    for p in idx["products"]:
        sym = p["sym"]
        try:
            page = json.load(open(os.path.join(DATA, sym + ".json"), encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            continue
        out.append((sym, p.get("name") or sym, page))
    return out


def run_live(dry=True):
    now = bj_now()
    pages = load_pages()
    hits, stale, no_night = [], 0, 0
    for sym, nm, page in pages:
        main = page.get("main") or []
        if not main:
            continue
        tb = night_bar(sym, now)
        if tb is None:
            no_night += 1
            print("%-3s 今晚无夜盘数据，跳过" % sym)
            continue
        # 基线必须是「夜盘交易日之前的那一根」。
        # 不用 now.date() 判断：schedule 延迟到零点之后时 now 已是次日，
        # 那样会把整晚误判成「基线过期」而全程空转。
        # 也不能只看自然日差（周五夜盘→周一是 3 天，属正常）。
        # 用「两个日期之间夹了几个工作日」最准：=1 才说明中间没漏交易日。
        miss = workdays_between(main[-1][0], tb["d"])
        if miss != 1:
            stale += 1
            print("%-3s 正式数据停在 %s，到夜盘交易日 %s 之间夹了 %d 个工作日 —— "
                  "基线有缺口，跳过以免误判" % (sym, main[-1][0], tb["d"], miss))
            continue
        try:
            h = judge(sym, nm, page, tb)
        except Exception as e:                                # noqa: BLE001
            print("%-3s 复跑失败: %s: %s" % (sym, type(e).__name__, e))
            continue
        if h:
            hits.append(h)

    print("\n命中 %d 条（基线过期 %d / 无夜盘 %d）" % (len(hits), stale, no_night))
    # 全线基线过期 = 15:20 那班没跑成，这一班等于空转。
    # 不推微信（避免制造噪音），但让 Actions 标一条 warning 便于事后发现。
    if pages and stale == len(pages):
        print("::warning::全部品种的正式数据都没跟上，夜盘预警空转 —— "
              "请检查 15:20 的 daily-scan 是否失败")
    if not hits:
        return
    body = html_of(hits, now)
    if dry:
        print(body)
        return
    ok = push(os.environ.get("PUSHPLUS_TOKEN", ""),
              "🌙 夜盘预警 %s" % now.strftime("%m-%d %H:%M"), body)
    print("推送:", ok)


def demo(sym, D):
    """回放：把 D 当晚（D 的前一晚？）—— 用 D 这个交易日的夜盘段。
    D 为夜盘所属交易日，即用 D-1 晚 21:00 起的分钟线。"""
    page = json.load(open(os.path.join(DATA, sym + ".json"), encoding="utf-8"))
    mm = build.fetch_minute(sym + "0", 15)
    D0 = _dt.date.fromisoformat(D)
    prev = D0 - _dt.timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= _dt.timedelta(days=1)
    seg = [(t, b) for t, b in ((_t(x["d"]), x) for x in mm)
           if t and t.date() == prev and t.hour >= NIGHT_HOURS[0]]
    if not seg:
        print("%s 无 %s 晚夜盘分钟线" % (sym, prev))
        return
    seg.sort(key=lambda x: x[0])
    tb = agg(seg, D)

    main = page["main"]
    cut = [i for i, b in enumerate(main) if b[0] >= D]
    base_bars = [{"d": b[0], "o": b[1], "h": b[2], "l": b[3],
                  "c": b[4], "v": b[5], "oi": b[6]} for b in main[:cut[0] if cut else len(main)]]
    # 基线也用「截至前一交易日」的复跑结果，避免拿今天的 plan 比昨天
    rb, regb = IW.rerun(sym, base_bars, page["name"], page)
    h = judge(sym, page["name"], page, tb,
              base_reg=regb,
              base_pos=(rb.get("open_pos") or {}).get("side"))
    if h:
        print()
        print(html_of([h], _dt.datetime.strptime(D + " 23:10", "%Y-%m-%d %H:%M")))
    else:
        print("该夜盘不触发（静默）")


def _reg(bars):
    S = engine.make_S(bars)
    return regime_lib.causal_regime(bars, S, len(bars) - 1)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        i = sys.argv.index("--demo")
        demo(sys.argv[i + 1], sys.argv[i + 2])
    else:
        run_live(dry="--dry" in sys.argv)
