# -*- coding: utf-8 -*-
"""v4-D：最终口径标定（MA34 + 4 条件 + ADX 门槛）→ 概率表 / 提前量 / 分品种 / 逐年
并输出 model_v4.json 供线上引擎读取。
"""
import json
import statistics as st

import mine_v4b as M

MA = 34
P0 = ["A1", "A9", "A15", "A17"]     # 距均线≤0.3ATR / 10日逆行≥1.5ATR / MA10斜率≤0 / MACD翻向
P1 = ["A1", "A9", "A16", "A17"]     # 同上但斜率≤-0.2ATR
GATES = {"无门槛": None,
         "ADX≥20": lambda P, i: P["S"]["adx"][i] is not None and P["S"]["adx"][i] >= 20,
         "ADX≥25": lambda P, i: P["S"]["adx"][i] is not None and P["S"]["adx"][i] >= 25}


def ktable(rows_all, combo, gate=None, gate_name="", Hs=(2, 3, 5, 10)):
    sql = ["A%d" % i for i in range(1, 29)]
    conds = [c for c in M.CONDS if c[0] in combo]
    tab = {}
    for H in Hs:
        tab[H] = {}
    for r in rows_all:
        if gate is not None and not gate(r):
            continue
        k = sum(1 for c in conds if M.ok(r, c))
        for H in Hs:
            lead = r["lead"]
            hit = lead is not None and lead <= H
            d = tab[H].setdefault(k, {"n": 0, "h": 0, "trn": 0, "trh": 0, "ten": 0, "teh": 0})
            d["n"] += 1
            d["h"] += 1 if hit else 0
            if r["date"] < M.SPLIT:
                d["trn"] += 1
                d["trh"] += 1 if hit else 0
            else:
                d["ten"] += 1
                d["teh"] += 1 if hit else 0
    return tab


def main():
    data = M.load()
    rows_all = []
    PM = {}
    for c in M.UNIV:
        rows, P = M.Pm_fetch(data, c, MA)
        rows_all += rows
        PM[c] = (rows, P)
    print("MA=%d，五品种样本 %d 条（%.1f 年×5）" % (MA, len(rows_all), len(rows_all) / 5 / 244))

    # ---------- 1. 组合 × ADX 门槛 ----------
    print("\n" + "=" * 118)
    print("一、条件组合 × ADX 门槛（不设止损，持有整段新趋势；内≤2023 / 外 2024~）")
    print("=" * 118)
    print("%-10s %-10s %-82s %s" % ("组合", "门槛", "表现", "内/外"))
    grid = {}
    for cname, combo in (("P0 原4条", P0), ("P1 斜率≤-0.2", P1)):
        for gname, g in GATES.items():
            trs = []
            for c in M.UNIV:
                rows, P = PM[c]
                trs += M.trade(P, rows, combo, c, g)
            a = M.stats(trs)
            tr = [t for t in trs if t["in"] < M.SPLIT]
            te = [t for t in trs if t["in"] >= M.SPLIT]
            at, ae = M.stats(tr), M.stats(te)
            grid[(cname, gname)] = (a, at, ae)
            print("%-10s %-10s %-82s 内%+7.0f 外%+7.0f" %
                  (cname, gname, M.fmt(a), at["yuan"] if at else 0, ae["yuan"] if ae else 0))

    # ---------- 2. 概率标定 ----------
    print("\n" + "=" * 118)
    print("二、概率标定：命中 k 条 → 未来 H 根内出现 MA34 反向穿越的概率")
    print("=" * 118)
    for cname, combo in (("P0", P0), ("P1", P1)):
        for gname in ("无门槛", "ADX≥20"):
            g = GATES[gname]
            if g is None:
                rows_g = rows_all
            else:
                rows_g = []
                for c in M.UNIV:
                    rows, P = PM[c]
                    rows_g += [r for r in rows if g(P, r["i"])]
            tab = ktable(rows_g, combo, Hs=(2, 3, 5, 10))
            base = {H: (sum(d["h"] for d in tab[H].values()) / len(rows_g)) for H in tab}
            print("\n【%s / %s】样本 %d，基准 %s" %
                  (cname, gname, len(rows_g),
                   " ".join("H%d=%.1f%%" % (H, base[H] * 100) for H in sorted(base))))
            print("%3s %6s %8s %8s %8s %8s %8s %8s" %
                  ("k", "样本", "H2", "H3", "H5", "H10", "H5内", "H5外"))
            for k in sorted(tab[5]):
                d5 = tab[5][k]
                if d5["n"] < 15:
                    continue
                print("%3d %6d %7.1f%% %7.1f%% %7.1f%% %7.1f%% %7s %7s" %
                      (k, d5["n"], tab[2][k]["h"] / tab[2][k]["n"] * 100,
                       tab[3][k]["h"] / tab[3][k]["n"] * 100,
                       d5["h"] / d5["n"] * 100, tab[10][k]["h"] / tab[10][k]["n"] * 100,
                       ("%.1f%%" % (d5["trh"] / d5["trn"] * 100)) if d5["trn"] >= 10 else "—",
                       ("%.1f%%" % (d5["teh"] / d5["ten"] * 100)) if d5["ten"] >= 10 else "—"))

    # ---------- 3. 提前量 ----------
    print("\n" + "=" * 118)
    print("三、提前量（k=4 且最终在 H=5 内反转的样本，距反转还有几根）")
    print("=" * 118)
    conds = [c for c in M.CONDS if c[0] in P0]
    for gname, g in GATES.items():
        ld = []
        for r in rows_all:
            if all(M.ok(r, c) for c in conds) and r["lead"] is not None and r["lead"] <= 5:
                if g is not None and not g(PM[r["sym"]][1], r["i"]):
                    continue
                ld.append(r["lead"])
        if ld:
            ld.sort()
            print("  %-8s n=%3d 中位%d根  ≤1根占%.0f%%  ≤2根占%.0f%%  均值%.2f" %
                  (gname, len(ld), st.median(ld), sum(1 for x in ld if x <= 1) / len(ld) * 100,
                   sum(1 for x in ld if x <= 2) / len(ld) * 100, st.mean(ld)))

    # ---------- 4. 最终口径分品种 / 逐年 ----------
    FIN_G = GATES["ADX≥20"]
    trs = []
    for c in M.UNIV:
        rows, P = PM[c]
        trs += M.trade(P, rows, P0, c, FIN_G)
    print("\n" + "=" * 118)
    print("四、最终口径（MA34 + 4 条件 + ADX≥20）：分品种 / 逐年")
    print("=" * 118)
    for c in M.UNIV:
        rows, P = PM[c]
        s = M.stats(M.trade(P, rows, P0, c, FIN_G))
        if s:
            print("  %-3s %-4s %s 浮亏中位%.2fR" % (c, M.NAME[c], M.fmt(s), s["mae_med"]))
    yrs = sorted({t["in"][:4] for t in trs})
    pos = 0
    for y in yrs:
        s = M.stats([t for t in trs if t["in"][:4] == y])
        pos += 1 if s["yuan"] > 0 else 0
        print("  %s n=%2d %+8.0f元" % (y, s["n"], s["yuan"]))
    print("  盈利年份 %d/%d" % (pos, len(yrs)))
    a = M.stats(trs)
    print("  合计 %s" % M.fmt(a))
    print("  年均 %+.0f 元/手，10 年 %+.0f 元（1 手规模，无止损）" % (a["per_year"], a["yuan"]))
    maes = sorted(t["mae"] for t in trs)
    print("  持仓期浮亏：中位 %.2f ATR，P10 %.2f ATR，极值 %.2f ATR" %
          (st.median(maes), maes[int(len(maes) * 0.1)], maes[0]))
    worst = sorted(trs, key=lambda t: t["mae"])[:3]
    for t in worst:
        print("    最深浮亏 %-3s %s 进%.1f 出%.1f %.1fR ≈ %.0f元/手 持%d根" %
              (t["code"], t["in"], t["entry"], t["exit"], t["mae"],
               t["mae"] * t["entry"] * 0.01 * M.MULT[t["code"]] * 0 + t["mae"] * (t["pts"] / t["R"]) / t["R"] * 0, t["bars"]))

    # ---------- 5. 写模型 ----------
    rows_g = []
    for c in M.UNIV:
        rows, P = PM[c]
        rows_g += [r for r in rows if FIN_G(P, r["i"])]
    tab = ktable(rows_g, P0, Hs=(2, 3, 5, 10))
    model = {"version": 4, "ma": MA, "univ": M.UNIV,
             "conds": [{"k": c[0], "f": c[1], "op": c[2], "th": c[3], "t": c[4]}
                       for c in M.CONDS if c[0] in P0],
             "gate": {"adx_min": 20},
             "calib": {str(H): {str(k): round(v["h"] / v["n"], 4)
                                for k, v in tab[H].items() if v["n"] >= 15}
                       for H in tab},
             "calib_n": {str(H): {str(k): v["n"] for k, v in tab[H].items() if v["n"] >= 15}
                         for H in tab},
             "base": {str(H): round(sum(v["h"] for v in tab[H].values()) /
                                    max(1, sum(v["n"] for v in tab[H].values())), 4) for H in tab},
             "stat": {"n": a["n"], "win": round(a["win"], 4), "payoff": round(a["payoff"], 2),
                      "yuan": round(a["yuan"]), "dd": round(a["dd"]),
                      "per_year": round(a["per_year"]), "avgR": round(a["avgR"], 3),
                      "hold": a["hold"]},
             "nofstop": True}
    json.dump(model, open("model_v4.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n已写入 model_v4.json")


if __name__ == "__main__":
    main()
