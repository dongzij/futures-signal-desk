# -*- coding: utf-8 -*-
"""v22 海选数据与回测公共库

对任意品种构建与线上完全同源的信号包：
    signals(pred/warn) ← engine.scan_symbol(unit="D")
    mw                 ← mw_events.build_mw
    defend(白圈失效)    ← defend_rules.build_defend（与线上同一套规则）
    xsig(强烈离场)      ← engine.exit_radar 全历史重算（sig_pack 内部完成）
随后交给 engine_v21 按 防守>底线>反手 逐日裁决。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import build
import defend_rules
import engine
import engine_v21 as E
import mw_events
import sig_pack as SP
import v22_universe as U

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "dist", "v22")
KEEP = 2440          # 与在池品种一致：最近 10 年


def to_main(bars):
    return [[b["d"], b["o"], b["h"], b["l"], b["c"], b.get("v") or 0, b.get("oi") or 0]
            for b in bars]


def build_one(code, verbose=True):
    """构建单品种信号包（带磁盘缓存）"""
    cp = os.path.join(CACHE, code + ".json")
    if os.path.exists(cp):
        d = json.load(open(cp, encoding="utf-8"))
    else:
        adj, meta = build.panama_adjust(code)
        if not adj:
            return None
        bars = adj[-KEEP:] if len(adj) > KEEP else adj
        S0 = engine.make_S(bars)
        sigs = [x for x in engine.scan_symbol(code, bars=bars, unit="D")[0]
                if x.get("kind") in ("pred", "warn")]
        radar_all = engine.exit_history(code, bars=bars, years=10, maxn=10 ** 6, dedupe=8)
        name = U.CANDS[code][0]
        defend = defend_rules.build_defend(code, name, bars, S0, sigs, radar_all)
        mw = mw_events.build_mw(code, name, bars, S0)
        d = {"sym": code, "name": name, "mult": U.CANDS[code][1],
             "fee": list(U.CANDS[code][2]), "margin": U.CANDS[code][3],
             "main": to_main(bars), "signals": sigs, "mw": mw, "defend": defend}
        os.makedirs(CACHE, exist_ok=True)
        json.dump(d, open(cp, "w", encoding="utf-8"), ensure_ascii=False)

    bars = SP.bars_of(d)
    S = engine.make_S(bars)
    pack, meta = SP.build(d, bars, S)
    if verbose:
        mg = bars[-1]["c"] * d["mult"] * d["margin"]
        print("  %-3s %-6s %4d 根 %s~%s  单手保证金 %5.0f 元  信号 pred%d warn%d mw%d 失效%d"
              % (code, d["name"], len(bars), bars[0]["d"], bars[-1]["d"], mg,
                 sum(1 for s in d["signals"] if s["kind"] == "pred"),
                 sum(1 for s in d["signals"] if s["kind"] == "warn"),
                 len(d["mw"]), len(d["defend"])))
    return {"d": d, "bars": bars, "S": S, "pack": pack, "meta": meta}


def obj_from_doc(d):
    """把一份现成 doc（dist/data/*.json）转成统一的回测对象"""
    bars = SP.bars_of(d)
    S = engine.make_S(bars)
    pack, meta = SP.build(d, bars, S)
    return {"d": d, "bars": bars, "S": S, "pack": pack, "meta": meta}


def run_seg(obj, cfg, i0, i1):
    d, bars, S, pack, meta = obj["d"], obj["bars"], obj["S"], obj["pack"], obj["meta"]
    c = dict(cfg)
    c["start_i"], c["end_i"] = i0, i1
    r = E.run(d, d, c, pack, meta, bars, S)
    return E.stats(d, r), r


def cut_of(bars, frac=0.7):
    return 60 + int((len(bars) - 60) * frac)


D3 = E.default_cfg()
D3["entry"] = {"trend_up": ("mw", "confirm"), "trend_dn": ("mw", "confirm"),
               "range": (), "transition": ("pred",)}
D3["align"] = True
