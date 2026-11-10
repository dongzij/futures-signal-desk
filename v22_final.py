# -*- coding: utf-8 -*-
"""v22 模块二收尾：定稿新 6 大核心池的方法、双段数据、近 1 年表现

产出 reports/v22_final.json，供 data_build.py（页面/推送）读取：
    · 每个品种选定的方法（spec：各场景允许的进场信号 + 出场模式）
    · 训练段 / 验证段 / 全样本 / 近 1 年 四组真实数据
    · 品种排序（按近 1 年净收益）

方法族（全部「震荡市空仓」，见 v22_gated.py）：
    G1 = D3   趋势 WM+反转兑现（顺势）｜过渡 顶底雷达
    G2        趋势 转折确认+反转兑现（顺势）｜过渡 顶底雷达
    G4        趋势 WM+反转兑现（顺势）｜过渡 雷达+预警
    G8        趋势 WM+反转兑现（顺势）｜过渡 雷达+WM
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import engine_v21 as E
import v22_gated as G
import v22_lib as L

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# 定稿：在池 3 个沿用 v21 已验收的 D3(G1)；新增 3 个用各自通过双段盲测的变体
FINAL = {
    "M":  ("G1", "豆粕"),
    "FG": ("G1", "玻璃"),
    "MA": ("G1", "甲醇"),
    "P":  ("G2", "棕榈油"),
    "Y":  ("G2", "豆油"),
    "HC": ("G8", "热卷"),
    # SM 锰硅：用户定稿指定 G4（趋势 WM+兑现｜过渡 雷达+预警）。
    # 特点：胜率低（训练 27.6%）但盈亏比极高（验证段 12.25），按「高盈亏比」定位上架。
    "SM": ("G4", "锰硅"),
}
RECENT_BARS = 244          # 近 1 年


def spec_of(code):
    for k, n, c in G.GATED:
        if k == code:
            return {"entry": {kk: list(v) for kk, v in c["entry"].items()},
                    "align": c["align"], "xsig_trend": c["xsig_trend"],
                    "reverse": c["reverse"], "chand": c["chand"],
                    "ma_exit": c["ma_exit"], "stop30": c["stop30"]}
    raise KeyError(code)


def main():
    res = {}
    print("=" * 128)
    print("v22 定稿：新 6 大核心池 · 双段 + 全样本 + 近 1 年")
    print("=" * 128)
    for sym, (code, name) in FINAL.items():
        cfg = [c for k, n, c in G.GATED if k == code][0]
        if sym in ("M", "FG", "MA"):
            d = json.load(open(os.path.join("dist", "data", sym + ".json"), encoding="utf-8"))
            o = L.obj_from_doc(d)
        else:
            o = L.build_one(sym, verbose=False)
        bars = o["bars"]
        cut = L.cut_of(bars)
        N = len(bars)
        tr, _ = L.run_seg(o, cfg, 60, cut)
        te, _ = L.run_seg(o, cfg, cut, N)
        fu, _ = L.run_seg(o, cfg, 60, N)
        rc, _ = L.run_seg(o, cfg, max(60, N - RECENT_BARS), N)
        row = [r for r in json.load(open(os.path.join(OUT, "v22_gated.json"),
                                         encoding="utf-8"))[sym]["rows"] if r["code"] == code]
        strat_nm = row[0]["strat"] if row else ""
        res[sym] = {
            "sym": sym, "name": name, "method": code, "methodLabel": strat_nm,
            "spec": spec_of(code),
            "period": {"start": bars[60]["d"], "end": bars[-1]["d"],
                       "train_end": bars[cut]["d"], "recent_start": bars[max(60, N - RECENT_BARS)]["d"]},
            "train": tr, "test": te, "full": fu, "recent": rc,
        }
        print("%-3s %-6s %s %-22s 训练 %+7d(%.0f%%) 验证 %+7d(%.0f%%) 近1年 %+6d  全样本 %+8d 年化 %+.1f%%"
              % (sym, name, code, strat_nm, tr["net"], tr["win"], te["net"], te["win"],
                 rc["net"], fu["net"], fu["ann"]))

    order = sorted(res, key=lambda s: -res[s]["recent"]["net"])
    print("\n品种栏排序（近 1 年净收益降序）：")
    for i, s in enumerate(order, 1):
        r = res[s]["recent"]
        print("  %d. %-6s（%s）近1年 %+d 元 / %.0f%% 胜率 / %d 笔"
              % (i, res[s]["name"], s, r["net"], r["win"], r["n"]))

    out = {"order": order, "symbols": res,
           "rules": {"start_cap": 10000, "stop30": 3000, "lot": 1,
                     "add_unit": 10000, "range_silent": True}}
    json.dump(out, open(os.path.join(OUT, "v22_final.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n写出 reports/v22_final.json")
    return out


if __name__ == "__main__":
    main()
