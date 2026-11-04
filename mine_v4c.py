# -*- coding: utf-8 -*-
"""v4-C：定标（MA × 条件组合）× ADX 过滤器 —— 决定性对照表
口径：不设止损，命中即次日开盘逆势进，持有到新趋势结束。
所有组合在 7 个 MA 上跑同一套条件，避免「每个 MA 挑不同组合」的过拟合。
"""
import json
import statistics as st

import mine_v4b as M

COMBOS = {
    "P0 原4条":        ["A1", "A9", "A15", "A17"],
    "P1 斜率更严":      ["A1", "A9", "A16", "A17"],
    "P2 +ADX上行":     ["A1", "A9", "A16", "A28"],
    "P3 +ADX≤20":      ["A1", "A9", "A16", "A24"],
    "P4 +ADX上行(替斜率)": ["A1", "A9", "A17", "A28"],
    "P5 逆行1ATR":      ["A1", "A8", "A15", "A17"],
    "P6 3条(去MACD)":   ["A1", "A9", "A16"],
}
MAS = [20, 25, 30, 34, 40, 45, 50]


def cell(data, man, combo, flt=None):
    trs = []
    for c in M.UNIV:
        rows, P = M.Pm_fetch(data, c, man)
        trs += M.trade(P, rows, combo, c, flt)
    a = M.stats(trs)
    tr = [t for t in trs if t["in"] < M.SPLIT]
    te = [t for t in trs if t["in"] >= M.SPLIT]
    return a, M.stats(tr), M.stats(te), trs


def score(a, b):
    if not a or not b or a["yuan"] <= 0 or b["yuan"] <= 0:
        return -1e9
    return min(a["yuan"] / max(1.0, -a["dd"]), b["yuan"] / max(1.0, -b["dd"]))


def main():
    data = M.load()
    print("=" * 132)
    print("一、MA × 组合 全对照（不设止损；1 手/笔；内=2016~2023，外=2024~）")
    print("=" * 132)
    print("%-20s %-4s %-84s %s" % ("组合", "MA", "全样本", "内/外 元"))
    best = []
    for cname, combo in COMBOS.items():
        for man in MAS:
            a, tr, te, _ = cell(data, man, combo)
            if not a:
                continue
            sc = score(tr, te)
            best.append((sc, cname, man, a, tr, te))
            print("%-20s %-4d %-84s 内%+7.0f 外%+7.0f%s" %
                  (cname, man, M.fmt(a), tr["yuan"] if tr else 0,
                   te["yuan"] if te else 0, "  ★" if sc > 0 else ""))
    best.sort(key=lambda x: -x[0])
    print("\n按「内/外都以收益回撤比为正且最小者最优」排序 Top6：")
    for sc, cname, man, a, tr, te in best[:6]:
        print("  %-20s MA%-3d 收益回撤比 内%.2f 外%.2f | n=%d 胜率%.1f%% 盈亏比%.2f 累计%+.0f元 回撤%.0f元" %
              (cname, man, tr["yuan"] / max(1, -tr["dd"]), te["yuan"] / max(1, -te["dd"]),
               a["n"], a["win"] * 100, a["payoff"], a["yuan"], a["dd"]))
    sc, cname, man, a, tr, te = best[0]
    combo = COMBOS[cname]
    print("\n→ 采用：%s  +  MA%d" % (cname, man))

    print("\n" + "=" * 132)
    print("二、ADX 过滤（在 %s / MA%d 上逐档测试）" % (cname, man))
    print("=" * 132)
    FL = [
        ("无 ADX 过滤", None),
        ("ADX≤20", lambda P, i: P["S"]["adx"][i] is not None and P["S"]["adx"][i] <= 20),
        ("ADX≤25", lambda P, i: P["S"]["adx"][i] is not None and P["S"]["adx"][i] <= 25),
        ("ADX≤30", lambda P, i: P["S"]["adx"][i] is not None and P["S"]["adx"][i] <= 30),
        ("ADX≥20", lambda P, i: P["S"]["adx"][i] is not None and P["S"]["adx"][i] >= 20),
        ("ADX≥25", lambda P, i: P["S"]["adx"][i] is not None and P["S"]["adx"][i] >= 25),
        ("ADX≥30", lambda P, i: P["S"]["adx"][i] is not None and P["S"]["adx"][i] >= 30),
        ("ADX 回落(低于5根前)", lambda P, i: P["S"]["adx"][i] is not None and i >= 5
         and P["S"]["adx"][i - 5] is not None and P["S"]["adx"][i] < P["S"]["adx"][i - 5]),
        ("ADX 上升(高于5根前)", lambda P, i: P["S"]["adx"][i] is not None and i >= 5
         and P["S"]["adx"][i - 5] is not None and P["S"]["adx"][i] > P["S"]["adx"][i - 5]),
        ("DI差已收敛(≤0)", lambda P, i: P["S"]["pdi"][i] is not None and
         (P["S"]["pdi"][i] - P["S"]["mdi"][i]) * P["side"][i] <= 0),
    ]
    rows_fl = []
    for name, f in FL:
        a, tr, te, trs = cell(data, man, combo, f)
        if not a:
            print("%-22s 无样本" % name)
            continue
        rows_fl.append((name, a, tr, te))
        print("%-22s %s | 内%+7.0f 外%+7.0f" %
              (name, M.fmt(a), tr["yuan"] if tr else 0, te["yuan"] if te else 0))

    print("\nADX 分档（信号当时的 ADX 水平 → 后续表现）：")
    _, _, _, trs = cell(data, man, combo)
    BUCK = [(0, 15), (15, 20), (20, 25), (25, 30), (30, 40), (40, 100)]
    print("%-10s %5s %8s %8s %10s %10s" % ("ADX", "笔数", "胜率", "盈亏比", "平均R", "累计元"))
    for lo, hi in BUCK:
        sub = [t for t in trs if t["adx"] is not None and lo <= t["adx"] < hi]
        if not sub:
            continue
        s = M.stats(sub)
        print("%-10s %5d %7.1f%% %8.2f %+10.2f %+10.0f" %
              ("%d~%d" % (lo, hi), s["n"], s["win"] * 100, s["payoff"], s["avgR"], s["yuan"]))

    print("\n分品种（%s / MA%d / 无 ADX 过滤）：" % (cname, man))
    for c in M.UNIV:
        rows, P = M.Pm_fetch(data, c, man)
        trs_c = M.trade(P, rows, combo, c)
        s = M.stats(trs_c)
        if s:
            print("  %-4s %s 最大浮亏中位%.2fR" % (c, M.fmt(s), s["mae_med"]))

    print("\n逐年（元）：")
    trs = []
    for c in M.UNIV:
        rows, P = M.Pm_fetch(data, c, man)
        trs += M.trade(P, rows, combo, c)
    yrs = sorted({t["in"][:4] for t in trs})
    for y in yrs:
        sub = [t for t in trs if t["in"][:4] == y]
        s = M.stats(sub)
        print("  %s  n=%2d %+8.0f元" % (y, s["n"], s["yuan"]))

    print("\n不设止损的真实代价（全部 %d 笔）：" % len(trs))
    maes = sorted(t["mae"] for t in trs)
    print("  持仓期最大浮亏：中位 %.2f ATR，最差10%% %.2f ATR，极值 %.2f ATR" %
          (st.median(maes), maes[int(len(maes) * 0.1)], maes[0]))
    print("  折算成 1 手保证金占用（按 12%% 保证金、10万本金 1 手）：最差约 %.1f%% 本金浮亏" %
          (abs(maes[0]) * 0.12 / 1.0))
    worst = sorted(trs, key=lambda t: t["R"])[:5]
    print("  最差 5 笔：")
    for t in worst:
        print("    %-3s %s 进%.1f 出%.1f %+.0f元 (%+.1fR, 浮亏%.1fR, 持%d根)" %
              (t["code"], t["in"], t["entry"], t["exit"], t["pts"] * M.MULT[t["code"]],
               t["R"], t["mae"], t["bars"]))

    json.dump({"best": {"combo_name": cname, "combo": combo, "ma": man,
                        "stat": a, "tr": tr, "te": te},
               "adx_filters": [{"name": n, "n": a2["n"], "win": a2["win"],
                                "yuan": a2["yuan"], "dd": a2["dd"], "payoff": a2["payoff"]}
                               for n, a2, _t, _e in rows_fl]},
              open("v4c_pick.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
