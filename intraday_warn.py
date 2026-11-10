# -*- coding: utf-8 -*-
"""盘中预警（intraday warn）——只读，绝不写 state.json / data/

干的事
    把「截至此刻的价格」拼成一根**临时日线**，用同一套信号生成 + 同一套引擎复跑一遍，
    回答一句话：如果此刻就收盘，今天会不会多出一笔进出、路况会不会翻？
    ——命中才推，不命中一个字都不发。

与正式口径的关系
    15:20 的正式计算与信号记录是唯一权威，口径一个字不改。
    这里只是把同一套代码提前跑一遍，只用来「提前知道」，不产生任何新的交易记录。

纪律（不可破）
    1. 不写 state.json：盘中 close 不是收盘价，写进去会永久污染历史。
    2. 不写 data/ 与 dist/：页面正式数据只能由 data_build.py 产生。
    3. 临时 bar 只活在内存里，跑完即弃。
    4. 震荡静默：空仓 + 路况为震荡 → 不推（与正式口径同一条规则）。

上线前的口径校验
    不带临时 bar 复跑，必须与页面 plan 完全一致（持仓 / 路况 / 笔数）。
    实测 7/7 一致 —— 说明复跑链路与原管线同一口径，差异只可能来自「今天这根 bar」。

用法
    python intraday_warn.py                       # 推微信
    python intraday_warn.py --dry                 # 只打印，不推
    python intraday_warn.py --demo MA 2026-10-22  # 用历史某天回放，看真实文案
"""
import datetime as _dt
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import build
import defend_rules
import engine
import engine_v21 as E21
import mw_events
import regime_lib
import sig_pack as SP

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
REPORTS = os.path.join(ROOT, "reports")

REGIME_NM = {"trend_up": "上涨趋势", "trend_dn": "下跌趋势",
             "range": "震荡", "transition": "过渡态"}
SIG_NM = {"pred": "顶底雷达", "warn": "进场预警", "mw": "WM结构",
          "confirm": "反转兑现", "tf": "转折确认", "xsig": "强烈离场",
          "radar": "顶底雷达"}
WHY_NM = {"defend": "信号失效（白圈）", "xsig_strong": "强烈离场雷达",
          "ma_exit": "破 MA26", "chandelier": "吊灯 3ATR", "stop30": "30% 熔断",
          "max_hold": "持有到期", "regime": "路况切换"}
TREND = ("trend_up", "trend_dn")


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ---------------------------------------------------------------- 临时日线

def live_bar(sym, now=None):
    """用分钟线拼「今日到此刻」的临时日线。期货交易日含前夜盘：
    今日 bar = 昨夜 21:00-23:00 + 今日 09:00-11:30（此刻之前）。"""
    now = now or _dt.datetime.now()
    try:
        mm = build.fetch_minute(sym + "0", 15)
    except Exception as e:                                   # noqa: BLE001
        print("   %s 分钟线抓取失败: %s" % (sym, e))
        return None
    if not mm:
        return None
    today = now.strftime("%Y-%m-%d")
    yday = (now - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    seq = [b for b in mm
           if (b["d"][:10] == today and b["d"][11:16] >= "09:00")
           or (b["d"][:10] == yday and b["d"][11:16] >= "21:00")]
    return _agg(seq, today)


def hist_bar(m15, D):
    """回放 / 演示用：取历史某交易日到 11:30 的临时日线。"""
    D0 = _dt.date.fromisoformat(D)
    y = (D0 - _dt.timedelta(days=1)).isoformat()
    seq = [b for b in m15
           if (b[0][:10] == y and b[0][11:16] >= "21:00")
           or (b[0][:10] == D and "09:00" <= b[0][11:16] <= "11:30")]
    return _agg(seq, D)


def _agg(seq, D):
    if not seq:
        return None
    if isinstance(seq[0], dict):
        seq.sort(key=lambda x: x["d"])
        return {"d": D, "o": round(_f(seq[0]["o"]), 1),
                "h": round(max(_f(b["h"]) for b in seq), 1),
                "l": round(min(_f(b["l"]) for b in seq), 1),
                "c": round(_f(seq[-1]["c"]), 1),
                "v": round(sum(_f(b["v"]) for b in seq), 1),
                "oi": round(_f(seq[-1].get("oi")), 1),
                "asof": seq[-1]["d"][11:16]}
    seq.sort(key=lambda x: x[0])
    return {"d": D, "o": round(_f(seq[0][1]), 1),
            "h": round(max(_f(b[2]) for b in seq), 1),
            "l": round(min(_f(b[3]) for b in seq), 1),
            "c": round(_f(seq[-1][4]), 1),
            "v": round(sum(_f(b[5]) for b in seq), 1),
            "oi": round(_f(seq[-1][6]), 1) if len(seq[-1]) > 6 else 0.0,
            "asof": seq[-1][0][11:16]}


# ---------------------------------------------------------------- 复跑

def spec_cfg(sym):
    f = json.load(open(os.path.join(REPORTS, "v22_final.json"), encoding="utf-8"))
    v = f["symbols"][sym]
    cfg = E21.default_cfg()
    cfg["entry"] = {k: tuple(x) for k, x in v["spec"]["entry"].items()}
    for k in ("align", "reverse", "xsig_trend", "chand", "ma_exit", "stop30"):
        if k in v["spec"]:
            cfg[k] = v["spec"][k]
    return v, cfg


def rerun(sym, bars, name, page):
    """同一套信号生成 + 同一套引擎，输入换成「含临时 bar」的日线。"""
    S = engine.make_S(bars)
    sigs, _ = engine.scan_symbol(sym, bars=bars, unit="D")
    radar_all = engine.exit_history(sym, bars=bars, years=10, maxn=10 ** 6, dedupe=8)
    defend = defend_rules.build_defend(sym, name, bars, S, sigs, radar_all)
    mw = mw_events.build_mw(sym, name, bars, S)
    doc = {"sym": sym, "signals": sigs, "defend": defend, "mw": mw, "xsig": {}}
    pack, meta = SP.build(doc, bars, S)
    v, cfg = spec_cfg(sym)
    meta = dict(meta, N=len(bars))
    r = E21.run(page, doc, cfg, pack, meta, bars, S)
    reg = regime_lib.causal_regime(bars, S, len(bars) - 1)
    return r, reg


def judge(sym, nm, page, tb, base_reg=None, base_pos=None):
    """核心判定：拿「含临时 bar」的日线跑一遍，与昨日收盘口径比差异。
    base_reg / base_pos 为空时取页面 plan（= 昨日收盘的正式结论）。"""
    main = page["main"]
    if not main:
        return None
    n = len(main)
    if tb["d"] and main[-1][0] >= tb["d"]:
        n = [i for i, b in enumerate(main) if b[0] >= tb["d"]][0]
    bars = [{"d": b[0], "o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5], "oi": b[6]}
            for b in main[:n]] + [tb]
    r, reg = rerun(sym, bars, nm, page)
    base = page.get("plan") or {}
    prev_reg = base_reg if base_reg is not None else (base.get("regime") or {}).get("now")
    has_pos = base_pos if base_pos is not None else bool(base.get("open"))
    today = tb["d"]

    adv = []
    for t in r["trades"]:
        if t.get("exit_date") == today:
            adv.append("出场 · %s（%s %s 建仓，%+.0f 元）" % (
                WHY_NM.get(t["why"], t["why"]),
                "多" if t["side"] == "long" else "空", t["entry_date"], t["net"]))
    for t in r["trades"]:
        if t.get("entry_date") == today:
            adv.append("进场 · %s 开%s %.1f" % (
                SIG_NM.get(t["kind"], t["kind"]),
                "多" if t["side"] == "long" else "空", t["entry"]))

    reg_txt = ""
    if prev_reg and reg != prev_reg:
        reg_txt = "%s → %s" % (REGIME_NM.get(prev_reg, prev_reg), REGIME_NM.get(reg, reg))
        if has_pos:
            adv.append("路况翻转（持仓中）· %s" % reg_txt)
        elif reg in TREND:
            adv.append("进入可开仓区 · %s" % reg_txt)
        # 空仓 + 转震荡/过渡 → 静默，不推

    print("%-3s %s 现价 %.1f (%+.2f%%) 路况 %s%s" % (
        sym, nm, tb["c"], (tb["c"] / main[-1][4] - 1) * 100,
        REGIME_NM.get(reg, reg), ("  [" + reg_txt + "]") if reg_txt else ""))
    for a in adv:
        print("     → " + a)
    if not adv:
        return None
    return {"sym": sym, "name": nm, "px": tb["c"], "asof": tb["asof"],
            "reg": reg, "regTxt": reg_txt, "adv": adv,
            "pos": (r.get("open_pos") or {}).get("side")}


# ---------------------------------------------------------------- 输出

def html_of(hits, now):
    rows = []
    for h in hits:
        head = "<b>%s %s</b>　现价 %.1f　%s" % (
            h["name"], h["sym"], h["px"], REGIME_NM.get(h["reg"], h["reg"]))
        body = "".join("<div style='color:#b00'>· %s</div>" % a for a in h["adv"])
        rows.append("<div style='margin:7px 0'>%s%s</div>" % (head, body))
    return ("<div style='font-size:13px'><b>盘中预警</b>"
            "<span style='color:#888;font-size:11px'>（截至 %s · 非收盘确认）</span>"
            "<div style='color:#888;font-size:11px'>收盘后 15:20 出正式结论；两者不一致时以正式为准"
            "</div>%s</div>" % (now.strftime("%m-%d %H:%M"), "".join(rows)))


def run_live(dry=True):
    now = _dt.datetime.now()
    idx = json.load(open(os.path.join(DATA, "index.json"), encoding="utf-8"))
    hits = []
    for p in idx["products"]:
        sym, nm = p["sym"], p["name"]
        page = json.load(open(os.path.join(DATA, sym + ".json"), encoding="utf-8"))
        tb = live_bar(sym)
        if tb is None:
            continue
        if page["main"] and page["main"][-1][0] == tb["d"]:
            print("%-3s 正式数据已含今日，跳过" % sym)
            continue
        try:
            h = judge(sym, nm, page, tb)
        except Exception as e:                                # noqa: BLE001
            print("%-3s 复跑失败: %s: %s" % (sym, type(e).__name__, e))
            continue
        if h:
            hits.append(h)
    print("\n命中 %d 条" % len(hits))
    if not hits:
        return
    body = html_of(hits, now)
    if dry:
        print(body)
        return
    sys.path.insert(0, ROOT)
    import daily
    ok = daily.post_push(os.environ.get("PUSHPLUS_TOKEN", ""),
                         "【盘中预警】%s" % now.strftime("%m-%d %H:%M"), body)
    print("推送:", ok)


def demo(sym, D):
    page = json.load(open(os.path.join(DATA, sym + ".json"), encoding="utf-8"))
    tb = hist_bar(page["m15"], D)
    if not tb:
        print("该日无分钟线")
        return
    main = page["main"]
    i = next((k for k, b in enumerate(main) if b[0] >= D), len(main))
    base_bars = [{"d": b[0], "o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5], "oi": b[6]}
                 for b in main[:i]]
    rb, regb = rerun(sym, base_bars, page["name"], page) if i else ({"open_pos": None}, None)
    h = judge(sym, page["name"], page, tb, regb, rb.get("open_pos") is not None)
    if h:
        print()
        print(html_of([h], _dt.datetime.strptime(D + " 11:30", "%Y-%m-%d %H:%M")))
    else:
        print("该日不触发（静默）")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        i = sys.argv.index("--demo")
        demo(sys.argv[i + 1], sys.argv[i + 2])
    else:
        run_live(dry="--dry" in sys.argv)
