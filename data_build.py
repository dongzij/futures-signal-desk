# -*- coding: utf-8 -*-
"""构建分合约数据包：data/{SYM}.json（品种主档+信号）与 data/{SYM}_{合约}.json（K线）。

主页面与反转预测雷达（v4，与回测同源，仅 FG/M/RB/MA/UR）：
MA34 定义趋势段 · 4 条入选特征计分 + ADX≥20 门槛 · 命中 k 条 → 标定的反转概率
（k=4 时 5 日内 78.9%，且不设止损、持有到反向穿越）。
信号来自 state.json（engine.scan_symbol 产出）；周/月信号用同一方法
在聚合后的周/月 K 线上独立计算（看哪个周期就用哪个周期的信号）。
"""
import json
import os
import re
import shutil
from datetime import datetime

import build
import engine

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "data")

# 品种交易参数（模拟盘用）：乘数 / 手续费 / 保证金率
# fee: ("lot", 元/手) 或 ("rate", 万分之几)
PRODUCTS = {
    "FG": {"name": "玻璃",   "mult": 20,   "fee": ("lot", 6),    "margin": 0.09},
    "M":  {"name": "豆粕",   "mult": 10,   "fee": ("lot", 1.5),  "margin": 0.08},
    "RB": {"name": "螺纹钢", "mult": 10,   "fee": ("rate", 1),   "margin": 0.08},
    "MA": {"name": "甲醇",   "mult": 10,   "fee": ("lot", 2),    "margin": 0.08},
    "UR": {"name": "尿素",   "mult": 20,   "fee": ("lot", 6),    "margin": 0.09},
}
ORDER = ["FG", "M", "RB", "MA", "UR"]
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
             "lead": s.get("lead")}
        if s["kind"] in ("pred", "confirm"):
            o.update({"en": s.get("entry"), "atr": s.get("atr"),
                      "adx": s.get("adx"), "gate": s.get("gate"),
                      "strong": s.get("strong"), "checksS": s.get("checksS"),
                      "doi5": s.get("doi5"),
                      "buf": s.get("bufYuan"), "el": s.get("exitLine"),
                      "holdTo": s.get("holdTo"),
                      "last": s.get("last"), "lastDate": s.get("lastDate"),
                      "predDate": s.get("predDate")})
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

    # 实时反转雷达状态
    radar = None
    try:
        radar = engine.live_status(sym, bars=main)
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
    # 清掉旧的品种数据文件（保留目录）
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
        "built": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "products": [{"sym": s, **PRODUCTS[s]} for s in ORDER],
        "strategyOpen": strat_open,
        "method": state.get("method"),
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
