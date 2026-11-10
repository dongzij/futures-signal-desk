# -*- coding: utf-8 -*-
"""v22 模块二（补）：遵守「震荡市静默」纪律的候选族

模块四强制要求：震荡市系统必须保持静默。因此所有入选策略的 range 必须为空集。
在 D3 基础上扩展一族同纪律的变体（只改「趋势/过渡用什么信号」，不改风控）：

  G1 = D3          趋势 WM+反转兑现（顺势）｜震荡 空仓｜过渡 顶底雷达
  G2               趋势 转折确认+反转兑现   ｜震荡 空仓｜过渡 顶底雷达
  G3               趋势 WM+反转兑现+转折确认｜震荡 空仓｜过渡 顶底雷达
  G4               趋势 WM+反转兑现         ｜震荡 空仓｜过渡 雷达+预警
  G5               趋势 只做 WM            ｜震荡 空仓｜过渡 顶底雷达
  G6               趋势 只做 反转兑现        ｜震荡 空仓｜过渡 顶底雷达
  G7               趋势 只做 转折确认        ｜震荡 空仓｜过渡 顶底雷达
  G8               趋势 WM+反转兑现         ｜震荡 空仓｜过渡 雷达+WM

门槛：训练 n≥8 净>0 PF≥1.2 ；验证 n≥4 净>0 PF≥1.2 回撤≤40%
排序：min(训练 Calmar, 验证 Calmar)
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import engine_v21 as E
import v22_lib as L
import v22_universe as U

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def mk(trend, trans, **kw):
    c = dict(L.D3)
    c["entry"] = {"trend_up": trend, "trend_dn": trend,
                  "range": (), "transition": trans}
    c.update(kw)
    return c


GATED = [
    ("G1", "趋势WM+兑现｜过渡雷达", mk(("mw", "confirm"), ("pred",))),
    ("G2", "趋势转折确认+兑现｜过渡雷达", mk(("tf", "confirm"), ("pred",))),
    ("G3", "趋势WM+兑现+转折｜过渡雷达", mk(("mw", "confirm", "tf"), ("pred",))),
    ("G4", "趋势WM+兑现｜过渡雷达+预警", mk(("mw", "confirm"), ("pred", "warn"))),
    ("G5", "趋势只做WM｜过渡雷达", mk(("mw",), ("pred",))),
    ("G6", "趋势只做兑现｜过渡雷达", mk(("confirm",), ("pred",))),
    ("G7", "趋势只做转折｜过渡雷达", mk(("tf",), ("pred",))),
    ("G8", "趋势WM+兑现｜过渡雷达+WM", mk(("mw", "confirm"), ("pred", "mw"))),
]

BASE = ["M", "FG", "MA"]


def main():
    syms = BASE + [s for s in U.CANDS]
    res = {}
    print("=" * 140)
    print("v22 纪律族矩阵（震荡市一律空仓）：%d 个品种 × %d 套策略" % (len(syms), len(GATED)))
    print("=" * 140)
    for i, sym in enumerate(syms, 1):
        if sym in BASE:
            d = json.load(open(os.path.join("dist", "data", sym + ".json"), encoding="utf-8"))
            o = L.obj_from_doc(d)
            name = d["name"]
        else:
            o = L.build_one(sym, verbose=False)
            if not o:
                continue
            name = o["d"]["name"]
        bars = o["bars"]
        cut = L.cut_of(bars)
        rows = []
        for code, name_, cfg in GATED:
            a, _ = L.run_seg(o, cfg, 60, cut)
            b, _ = L.run_seg(o, cfg, cut, len(bars))
            ok = (a["n"] >= 8 and a["net"] > 0 and a["pf"] >= 1.2
                  and b["n"] >= 4 and b["net"] > 0 and b["pf"] >= 1.2 and b["mdd"] <= 40)
            rows.append({"code": code, "strat": name_, "train": a, "test": b, "ok": ok,
                         "minCalmar": round(min(a["calmar"], b["calmar"]), 2)})
        passed = [r for r in rows if r["ok"]]
        passed.sort(key=lambda r: -r["minCalmar"])
        best = passed[0] if passed else None
        res[sym] = {"name": name, "rows": rows, "best": best, "cut": bars[cut]["d"],
                    "bars": len(bars), "in_pool": sym in BASE}
        tag = ("★ %-3s 训练%+7d 验证%+7d 胜率%4.1f%%/%4.1f%% PF%.2f 回撤%.1f%% 年化%+5.1f%%"
               % (best["code"], best["train"]["net"], best["test"]["net"],
                  best["train"]["win"], best["test"]["win"], best["test"]["pf"],
                  best["test"]["mdd"], best["test"]["ann"])) if best else "✗ 全不合格"
        print("[%2d/%d] %-3s %-6s %s" % (i, len(syms), sym, name, tag))

    json.dump(res, open(os.path.join(OUT, "v22_gated.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    ok = [(s, v["best"]) for s, v in res.items() if v["best"] and not v["in_pool"]]
    ok.sort(key=lambda x: -x[1]["minCalmar"])
    print("\n" + "=" * 140)
    print("合格新品种 %d 个（在池 3 个仅作基准，不参与选择）" % len(ok))
    for s, b in ok:
        print("  %-3s %-6s %-3s %-22s 训练 %+7d / 验证 %+7d  胜率 %.1f%%/%.1f%%  "
              "盈亏比 %.2f  回撤 %.1f%%  年化 %+.1f%%"
              % (s, res[s]["name"], b["code"], b["strat"], b["train"]["net"], b["test"]["net"],
                 b["train"]["win"], b["test"]["win"], b["test"]["pf"], b["test"]["mdd"],
                 b["test"]["ann"]))
    print("\n写出 reports/v22_gated.json")


if __name__ == "__main__":
    main()
