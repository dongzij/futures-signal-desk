# -*- coding: utf-8 -*-
"""逐品种配置 vs 通用配置 —— 第一步：全网格预计算（结果缓存）

产出 cache/per_grid.json：
  trades[品种][进场MA][离场"MA_吊灯"] = [[日期, R], ...]
  sigs  [品种]["EXT_PULL"]           = [[日期, 方向正确0/1, 20根收益ATR, 档位], ...]

设计原则：
  - 进场/离场完全走线上 engine 同源计算，不另造简化模型
  - 参数网格只覆盖此前已寻优过的可调维度：进场趋势周期、持仓离场(短均线×吊灯)、
    独立雷达门槛(EXT×PULL)；ADX/段幅/辅助证据等结构性条件保持方法口径不变
  - 离场均线网格不含 34，避免与「进场趋势周期」变量耦合产生歧义
"""
import json
import os

import build
import cyc
import engine
import mine_xs
import validate_universe as V

UNIV = list(engine.CODES)
MA_GRID = [20, 25, 30, 34, 40, 45, 50]
XMA_GRID = [18, 20, 22, 24, 26, 28, 30]
CH_GRID = [2.0, 2.5, 3.0, 3.5, 4.0]
EXT_GRID = [i / 10.0 for i in range(20, 31)]
PULL_GRID = [i / 10.0 for i in range(8, 16)]
FWD = 20
OUT = os.path.join("cache", "per_grid.json")


def main():
    RAW = {}
    print("== 载入复权日线 ==")
    for c in UNIV:
        RAW[c] = engine._bars_of(c, 10)
        print("  %-3s %-4s %4d 根  %s → %s" %
              (c, engine.NAMES[c], len(RAW[c]), RAW[c][0]["d"], RAW[c][-1]["d"]))

    trades, sigs, meta = {}, {}, {}
    for c in UNIV:
        bars = RAW[c]
        closes = [b["c"] for b in bars]
        tc = {}
        print("  进场网格 %s ..." % c, flush=True)
        for m in MA_GRID:
            engine.MA = m
            S = engine.make_S(bars)
            # 补齐离场短均线（这些字段与趋势周期无关）
            for n in XMA_GRID:
                S["ma%d" % n] = cyc.sma(closes, n)
            sig_list, _ = engine.scan_symbol(c, bars=bars, unit="D")
            ev = [x for x in sig_list if x["kind"] == "pred"]
            per_ma = {}
            for n in XMA_GRID:
                for k in CH_GRID:
                    rows = []
                    for e in ev:
                        ei = e["entryIdx"]
                        if ei >= len(bars):
                            continue
                        side = 1 if e["side"] == "long" else -1
                        r = V.sim_exit(bars, S["ma%d" % n], k, ei, side,
                                       e["atr"], e["entry"])
                        if r:
                            rows.append([bars[ei]["d"], round(r["R"], 4)])
                    per_ma["%d_%.1f" % (n, k)] = rows
            tc[str(m)] = per_ma
        engine.MA = 34
        trades[c] = tc

        # ---------- 独立出场雷达（MA 固定 34，与线上一致）----------
        arr = [[b["d"], b["o"], b["h"], b["l"], b["c"], b.get("v") or 0,
                b.get("oi") or 0] for b in bars]
        P = mine_xs.prep(c, arr)
        dates = [r[0] for r in arr]
        sc = {}
        for ext in EXT_GRID:
            for pull in PULL_GRID:
                rows = []
                for i, d, a, lv in mine_xs.scan(P, ext, pull):
                    if i + FWD >= P["n"]:
                        continue
                    gain = (P["closes"][i] - P["closes"][i + FWD]) * d
                    rows.append([dates[i], 1 if gain > 0 else 0,
                                 round(gain / (P["atr"][i] or 1e-9), 4), lv])
                sc["%.1f_%.1f" % (ext, pull)] = rows
        sigs[c] = sc
        meta[c] = {"name": engine.NAMES[c], "bars": len(bars),
                   "first": bars[0]["d"], "last": bars[-1]["d"],
                   "years": round(len(bars) / 244.0, 2),
                   "pred_n_ma34": len(trades[c]["34"]["26_3.0"]),
                   "xs_n_generic": len(sigs[c]["2.6_1.1"])}
    engine.MA = 34

    os.makedirs("cache", exist_ok=True)
    json.dump({"trades": trades, "sigs": sigs, "meta": meta,
               "grid": {"ma": MA_GRID, "xma": XMA_GRID, "ch": CH_GRID,
                        "ext": EXT_GRID, "pull": PULL_GRID},
               "generic": {"ma": 34, "xma": 26, "ch": 3.0,
                           "ext": 2.6, "pull": 1.1}},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("== 完成 ==", OUT, os.path.getsize(OUT), "bytes")
    print("  通用配置：进场MA34 / 离场MA26+吊灯3 / 雷达EXT2.6-PULL1.1")
    for c in UNIV:
        print("  %-3s %-4s pred(MA34)=%2d 笔  radar(2.6/1.1)=%2d 次" %
              (c, meta[c]["name"], meta[c]["pred_n_ma34"], meta[c]["xs_n_generic"]))


if __name__ == "__main__":
    main()
