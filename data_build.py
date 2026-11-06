# -*- coding: utf-8 -*-
"""构建分合约数据包：data/{SYM}.json（品种主档+信号）与 data/{SYM}_{合约}.json（K线）。

主页面与反转预测雷达（v4，与回测同源，8 品种 FG/SA/M/SP/SM/MA/SR/C）：
MA34 定义趋势段 · 4 条入选特征计分 + ADX≥20 门槛 · 命中 k 条 → 标定的反转概率
（k=4 时 5 日内 78.9%，且不设止损、持有到反向穿越）。
信号来自 state.json（engine.scan_symbol 产出）；周/月信号用同一方法
在聚合后的周/月 K 线上独立计算（看哪个周期就用哪个周期的信号）。
"""
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone

import build
import engine

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "data")
EXIT_ALL = []      # 跨品种出场成交汇总（算全局胜率/净 R）
XSIG_ALL = []      # 跨品种独立出场信号当前状态
MKT_ALL = []       # 跨品种市场状态（纯信息展示，不干预信号）

# 品种交易参数（模拟盘用）：乘数 / 手续费 / 保证金率
# fee: ("lot", 元/手) 或 ("rate", 万分之几)
PRODUCTS = {
    "FG": {"name": "玻璃", "mult": 20, "fee": ("lot", 6),     "margin": 0.09},
    "SA": {"name": "纯碱", "mult": 20, "fee": ("lot", 3.5),   "margin": 0.09},
    "M":  {"name": "豆粕", "mult": 10, "fee": ("lot", 1.5),   "margin": 0.08},
    "SP": {"name": "纸浆", "mult": 10, "fee": ("rate", 0.5),  "margin": 0.08},
    "SM": {"name": "锰硅", "mult": 5,  "fee": ("lot", 3),     "margin": 0.09},
    "MA": {"name": "甲醇", "mult": 10, "fee": ("lot", 2),     "margin": 0.08},
    "SR": {"name": "白糖", "mult": 10, "fee": ("lot", 3),     "margin": 0.08},
    "C":  {"name": "玉米", "mult": 10, "fee": ("lot", 1.2),   "margin": 0.08},
}
ORDER = ["FG", "SA", "M", "SP", "SM", "MA", "SR", "C"]
DAILY_YEARS = 10         # 主力连续日线回看年数（支撑年/月/周视图）
MAX_CONTRACTS = 8        # 每品种最多打包的合约数
KEEP_MIN = 40            # 合约至少要有多少根日线才收录


def probe_contracts(sym, back=2, ahead=8):
    """探测可交易月合约（全部12个月都试，玻璃等品种是连续挂牌）。"""
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


def bars_to_arr(bars):
    return [[b["d"], b["o"], b["h"], b["l"], b["c"], b.get("v") or 0, b.get("oi") or 0]
            for b in bars]


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
    contracts = probe_contracts(sym)
    print("   合约:", ", ".join(contracts))
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
        })
    if not cmeta:
        return None
    # 分钟线：只给活跃合约（持仓前4名 + 到期最近的2个），控制体积
    active = sorted(cmeta, key=lambda x: -x["oi"])[:4]
    near = sorted(cmeta, key=lambda x: x["id"])[-2:]
    min_ids = {c["id"] for c in active} | {c["id"] for c in near}

    # ---- 3) 信号（日线来自 state.json；周/月用同一方法在聚合K线上现算）----
    state = json.load(open("state.json", encoding="utf-8"))

    def pack(s):
        o = {"kind": s["kind"], "side": s["side"], "dir": s.get("dir"),
             "date": s["date"], "price": s.get("price"), "k": s.get("k"),
             "prob": s.get("prob"), "checks": s.get("checks", []),
             "status": s.get("status"), "why": s.get("why"),
             "ret": s.get("ret"), "float": s.get("float"),
             "pts": s.get("pts"), "yuan": s.get("yuan"),
             "held": s.get("held"), "lots": s.get("lots"),
             "segStart": s.get("segStart"), "segAge": s.get("segAge"),
             "lead": s.get("lead"),
             # v11 甲方案：周线 MA45 降级档位 + 复盘标签（confirm 后台数据保留，前端不展示）
             "meta": s.get("meta")}
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

    # 趋势跟踪模块（v8）
    tf = None
    try:
        tf = {"live": engine.tf_live(sym, bars=main),
              "trades": engine.tf_trades(sym, main)[-40:],
              "stat": engine.tf_stat(sym)}
    except Exception as e:                                  # noqa: BLE001
        print("   tf failed: %s" % e)

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
                               "R": s.get("R"), "chLine": s.get("chLine"),
                               "maLine": s.get("maLine"), "armed": s.get("armed"),
                               "warn": s.get("exitWarn"), "why": s.get("exitWarnWhy"),
                               "distCh": s.get("distCh"), "distMa": s.get("distMa"),
                               "date": s.get("date"), "status": s.get("status")}
            break

    # ---- 3a) v11 甲方案：周线 MA45 位置序列 + 市场状态 ----
    # 降级只决定微信推送；市场状态【纯信息展示】，绝不拦截/隐藏/降级任何信号。
    S_main = engine.make_S(main)
    wpos = engine.weekly_pos_series(main)
    market = engine.market_state_now(main, S_main)
    MKT_ALL.append(dict(market, sym=sym, name=cfg["name"]))
    idx_by_date = {b["d"]: i for i, b in enumerate(main)}

    def xs_meta(h, i=None):
        if i is None:
            i = idx_by_date.get(h.get("d"))
        if i is None or i >= len(main):
            return None
        dirn = h.get("dir")
        exp = (-1 if dirn == "顶部" else 1) if dirn else (-1 if h.get("side") == "long" else 1)
        tier = "strong" if h.get("level") == "strong" else "normal"
        return engine.signal_meta(main, S_main, i, exp, tier, wpos)

    # ---- 3b) 独立出场雷达（见顶/见底，与持仓无关）----
    xsig = None
    try:
        now = engine.exit_radar(bars=main, code=sym)
        hist = engine.exit_history(sym, bars=main, maxn=16)
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

    # ---- 5) 写品种主档 ----
    doc = {
        "sym": sym, "name": cfg["name"],
        "mult": cfg["mult"], "margin": cfg["margin"], "fee": cfg["fee"],
        "main": bars_to_arr(main),
        "m60": m60, "m15": m15,
        "contracts": cmeta,
        "signals": sigs,
        "sigW": unit_sigs.get("W", []),
        "sigM": unit_sigs.get("M", []),
        "radar": radar,
        "tf": tf,
        "exit": exit_doc,
        "xsig": xsig,
        "market": market,                    # v11：纯信息展示，不干预任何信号/推送
        "weekly": {"ma": engine.WEEKLY_MA, "mode": engine.WEEKLY_FILTER,
                   "keepStrongOnOpp": engine.KEEP_STRONG_ON_OPP,
                   "confirm2Bars": engine.CONFIRM2_BARS,
                   "volRatioTag": engine.VOL_RATIO_TAG},
    }
    json.dump(doc, open(os.path.join(OUT, "%s.json" % sym), "w",
                        encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))

    # ---- 5) 写合约K线文件 ----
    for cid in contracts:
        bars = build.fetch_daily(cid)
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
        })

    manifest = {
        "built": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "products": [{"sym": s, **PRODUCTS[s]} for s in ORDER],
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
        "model": {
            "version": engine.MODEL.get("version", 4),
            "ma": engine.MA,
            "conds": engine.MODEL.get("conds"),
            "calib": engine.MODEL.get("calib"),
            "base": engine.MODEL.get("base"),
            "gate": engine.MODEL.get("gate"),
            "stat": engine.MODEL.get("stat"),
            "lead": engine.MODEL.get("lead", {}),
            "univ": engine.CODES,
            "nofstop": True,
        },
        "tf": {
            "k": engine.TF_K,
            "hold": engine.TF_HOLD,
            "stat": engine.tf_stat(),
            "live": [dict(engine.tf_live(s), sym=s) for s in ORDER],
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
