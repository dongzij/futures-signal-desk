# -*- coding: utf-8 -*-
"""v27 阈值定稿：把候选铁律接进 v22 定稿方案的真实账本，逐品种复跑对比。

做法：在「信号包编译层」把违规的进场信号从 pack 里剔除，再用同一份
engine_v21.run / stats 复跑。等价于引擎层拦截器，且不改出场规则、不动资金模型。
"""
import json
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import engine
import engine_v21 as E21
import sig_pack as SP
from extreme_gate_backtest import (ROOT, REPORTS, SYMS, KINDS, N_GRID,
                                   ext_series, stat, fmt, TRAIN_END)

V22 = json.load(open(os.path.join(ROOT, "reports", "v22_final.json"), encoding="utf-8"))

# ---- 候选规则：函数 (row-like dict of series values, side) -> 是否拦截 ----
def r_atr6(v, side):
    return (v["up60"] if side == "long" else v["dn60"]) is not None and \
           (v["up60"] if side == "long" else v["dn60"]) >= 6.0


def r_atr5(v, side):
    return (v["up60"] if side == "long" else v["dn60"]) is not None and \
           (v["up60"] if side == "long" else v["dn60"]) >= 5.0


def r_pct15(v, side):
    x = v["upPct60"] if side == "long" else v["dnPct60"]
    return x is not None and x >= 15.0


def r_pct20(v, side):
    x = v["upPct60"] if side == "long" else v["dnPct60"]
    return x is not None and x >= 20.0


def r_pos95(v, side):
    p = v["pos60"]
    if p is None:
        return False
    return p >= 0.95 if side == "long" else p <= 0.05


def r_pct15_or_pos95(v, side):
    return r_pct15(v, side) or r_pos95(v, side)


def r_pct15_or_atr6(v, side):
    return r_pct15(v, side) or r_atr6(v, side)


def r_pct20_120(v, side):
    x = v["upPct120"] if side == "long" else v["dnPct120"]
    return x is not None and x >= 20.0


def r_atr6_30(v, side):
    x = v["up30"] if side == "long" else v["dn30"]
    return x is not None and x >= 6.0


def _g(v, side, key_up, key_dn, t):
    x = v[key_up] if side == "long" else v[key_dn]
    return x is not None and x >= t


def _pos(v, side, N, t):
    p = v["pos%d" % N]
    if p is None:
        return False
    return p >= t if side == "long" else p <= 1 - t


def r_and20_pos90(v, side):
    return r_pct20(v, side) and _pos(v, side, 60, 0.90)


def r_and15_pos90(v, side):
    return r_pct15(v, side) and _pos(v, side, 60, 0.90)


def r_and15_atr6(v, side):
    return r_pct15(v, side) and r_atr6(v, side)


def r_and_atr6_pos90(v, side):
    return r_atr6(v, side) and _pos(v, side, 60, 0.90)


def r_and12_pos95(v, side):
    return _g(v, side, "upPct60", "dnPct60", 12) and _pos(v, side, 60, 0.95)


def r_and20_pos85(v, side):
    return r_pct20(v, side) and _pos(v, side, 60, 0.85)


def r_and20_120pos90(v, side):
    return r_pct20_120(v, side) and _pos(v, side, 120, 0.90)


def r_none(v, side):
    return False


RULES = [
    ("R0 基线·不拦截", r_none),
    ("R1 N60 ATR>=6", r_atr6),
    ("R2 N60 ATR>=5", r_atr5),
    ("R3 N60 涨幅>=15%", r_pct15),
    ("R4 N60 涨幅>=20%", r_pct20),
    ("R5 N60 位置>=0.95", r_pos95),
    ("R6 N60 15% 或 位置0.95", r_pct15_or_pos95),
    ("R7 N60 15% 或 ATR6", r_pct15_or_atr6),
    ("R8 N120 涨幅>=20%", r_pct20_120),
    ("R9 N30 ATR>=6", r_atr6_30),
    ("A1 N60 20% 且 位置>=0.90", r_and20_pos90),
    ("A2 N60 15% 且 位置>=0.90", r_and15_pos90),
    ("A3 N60 15% 且 ATR>=6", r_and15_atr6),
    ("A4 N60 ATR>=6 且 位置>=0.90", r_and_atr6_pos90),
    ("A5 N60 12% 且 位置>=0.95", r_and12_pos95),
    ("A6 N60 20% 且 位置>=0.85", r_and20_pos85),
    ("A7 N120 20% 且 位置>=0.90", r_and20_120pos90),
]


def prep(sym):
    d = json.load(open(os.path.join(ROOT, "data", sym + ".json"), encoding="utf-8"))
    bars = SP.bars_of(d)
    S = engine.make_S(bars)
    pack, meta = SP.build(d, bars, S)
    ext = {N: ext_series(bars, S, N) for N in N_GRID}
    v = V22["symbols"][sym]
    cfg = E21.default_cfg()
    cfg["entry"] = {k: tuple(x) for k, x in v["spec"]["entry"].items()}
    for k in ("align", "reverse", "xsig_trend", "chand", "ma_exit", "stop30"):
        if k in v["spec"]:
            cfg[k] = v["spec"][k]
    return d, bars, S, pack, meta, ext, cfg


def vals_at(ext, i):
    v = {}
    for N in N_GRID:
        for key, name in (("up", "up%d" % N), ("dn", "dn%d" % N),
                          ("upPct", "upPct%d" % N), ("dnPct", "dnPct%d" % N),
                          ("pos", "pos%d" % N)):
            v[name] = ext[N][key][i]
    return v


def run_rule(pre, rule):
    d, bars, S, pack, meta, ext, cfg = pre
    fp = {}
    blocked = []
    for i, arr in pack.items():
        keep = []
        for s in arr:
            if s["kind"] in KINDS and rule(vals_at(ext, i), s["side"]):
                blocked.append((bars[i]["d"], s["kind"], s["side"]))
            else:
                keep.append(s)
        fp[i] = keep
    r = E21.run(d, d, cfg, fp, meta, bars, S)
    return E21.stats(d, r), blocked, r


def main():
    pres = {}
    print("=== 载入 7 品种 ===")
    for sym in SYMS:
        t0 = time.time()
        pres[sym] = prep(sym)
        print("  %s ok  %.1fs" % (sym, time.time() - t0))

    print("\n=== 账本复跑：7 品种合计（加总，win 按笔数加权）===")
    print("规则                         笔数  胜率   盈亏比   净收益   年化   回撤   拦掉信号")
    summary = []
    for name, rule in RULES:
        agg = {"n": 0, "w": 0, "gp": 0.0, "gl": 0.0, "net": 0,
               "ann": 0.0, "mdd": 0.0, "blk": 0}
        detail = {}
        for sym in SYMS:
            st, blk, r = run_rule(pres[sym], rule)
            agg["n"] += st["n"]
            agg["net"] += st["net"]
            agg["ann"] += st["ann"]
            agg["mdd"] = max(agg["mdd"], st["mdd"])
            agg["blk"] += len(blk)
            # 用 pf 与 win 反推盈亏额，做加权
            wins = st["n"] * st["win"] / 100.0
            agg["w"] += wins
            gp = (st["pf"] * abs(st["avg_loss"] * (st["n"] - wins)))
            agg["gp"] += gp
            agg["gl"] += abs(st["avg_loss"] * (st["n"] - wins))
            detail[sym] = {"n": st["n"], "win": st["win"], "pf": st["pf"],
                           "net": st["net"], "blk": len(blk)}
        win = agg["w"] / agg["n"] * 100 if agg["n"] else 0
        pf = agg["gp"] / agg["gl"] if agg["gl"] else 0
        print("%-28s %4d  %5.1f%%  %6.2f  %+8d  %5.1f  %5.1f  %4d" %
              (name, agg["n"], win, pf, agg["net"], agg["ann"] / len(SYMS),
               agg["mdd"], agg["blk"]))
        summary.append({"rule": name, "n": agg["n"], "win": round(win, 1),
                        "pf": round(pf, 2), "net": agg["net"], "blk": agg["blk"],
                        "bySym": detail})

    # ---- 各规则下玻璃 2025-07-24 是否被拦 ----
    print("\n=== 玻璃 FG 2025-07-24「结构双底·进多」在各规则下的命运 ===")
    d, bars, S, pack, meta, ext, cfg = pres["FG"]
    idx = {b["d"]: i for i, b in enumerate(bars)}
    i = idx["2025-07-24"]
    v = vals_at(ext, i)
    print("  信号日偏离度：ATR60=%.2f  涨幅60=%.1f%%  位置60=%.3f" %
          (v["up60"], v["upPct60"], v["pos60"]))
    for name, rule in RULES:
        print("  %-28s -> %s" % (name, "已拦截 ✔" if rule(v, "long") else "保留 ✘"))

    out = os.path.join(REPORTS, "v27_账本复跑对比.json")
    json.dump(summary, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n结果已写：", out)


if __name__ == "__main__":
    main()
