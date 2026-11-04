# -*- coding: utf-8 -*-
"""v6a：多空方向对称性检验
=====================================================================
问题：顶部做空与底部做多两侧，量价持仓数据的变化是否真的"完全相反"，
     现有方法（方向归一/镜像翻转 + 同一套阈值）对两侧是否同样有效？

检验三层：
 1) bar 级事件研究：按 side=1(多头段→将来顶部做空) / side=-1(空头段→将来底部做多)
    分组的基准反转率、4 条入选特征 + 强确认两条的单侧 lift
 2) 首轮触发样本（线上口径 36 笔）按方向分组的成功率/收益/特征分布
 3) 段级别：多头段与空头段的 长度/幅度/反转概率 分布是否对称
"""
import statistics as st

import mine_v4b as M
import mine_v5 as V

REF = V.REF
S1C = ["A13(持仓5日降≤0%)" ]


def p_of(xs):
    xs = [x for x in xs if x is not None]
    return (sum(1 for x in xs if x) / len(xs)) if xs else None


def med(rows, key):
    v = [r[key] for r in rows if r.get(key) is not None]
    return st.median(v) if v else None


def main():
    data = M.load()
    rows_all = []
    Pcache = {}
    for c in M.UNIV:
        rows, P = M.Pm_fetch(data, c, 34)
        Pcache[c] = P
        rows_all += rows

    print("=" * 78)
    print("一、bar 级事件研究：多空两侧分开看（lead<=5 反转，H=20 内有效样本）")
    for sd, name in ((1, "多头段(顶部/做空侧)"), (-1, "空头段(底部/做多侧)")):
        sub = [r for r in rows_all if r["side"] == sd and r["lead"] is not None]
        base = sum(1 for r in sub if r["lead"] <= M.H) / len(sub)
        print("  %-14s n=%5d  基准反转率(≤5根) %.1f%%" % (name, len(sub), base * 100))
        for key, cond in (("A1", ("dma", "<=", 0.3)), ("A9", ("ret10", "<=", -1.5)),
                          ("A15", ("ma10_sl", "<=", 0.0)), ("A17", ("macd", "<=", 0.0)),
                          ("A13", ("doi5", "<=", 0.0)), ("A10", ("pull", "<=", -1.5)),
                          ("A22", ("vr5", ">=", 1.4))):
            hit = [r for r in sub if r.get(cond[0]) is not None
                   and (r[cond[0]] <= cond[2] if cond[1] == "<=" else r[cond[0]] >= cond[2])]
            if len(hit) < 30:
                print("      %s %-22s 样本不足(%d)" % (key, cond, len(hit)))
                continue
            p = sum(1 for r in hit if r["lead"] <= M.H) / len(hit)
            print("      %s %-26s n=%4d  命中后反转率 %.1f%%  lift %.2f"
                  % (key, cond[0] + cond[1] + str(cond[2]), len(hit), p * 100, p / base))

    print()
    print("=" * 78)
    print("二、首轮触发样本（线上口径）按方向分组")
    hits = V.collect(data)
    for sd, name in ((1, "顶部做空"), (-1, "底部做多")):
        sub = [h for h in hits if h["side"] == sd]
        if not sub:
            print("  %s：无样本" % name)
            continue
        win = sum(1 for h in sub if h["lead"] is not None and h["lead"] <= 5)
        val = [h for h in sub if h["lead"] is not None]
        print("  %s：n=%d  5日内反转 %d/%d=%.0f%%  交易R合计 %+.1f  均R %+.2f  持仓中位 %d 根"
              % (name, len(sub), win, len(val), win / len(val) * 100 if val else 0,
                 sum(h["trade_R"] for h in sub),
                 st.mean([h["trade_R"] for h in sub]),
                 st.median([h["hold"] for h in sub])))
        r = [h["rows_feat"] for h in sub]
        for k in ("dma", "ret10", "ma10_sl", "macd", "doi5", "adx", "segAmp", "age"):
            vals = []
            for h in sub:
                f = h["rows_feat"]
                v = f.get(k)
                if v is None and k in ("segAmp", "age"):
                    v = h[k]
                if v is not None:
                    vals.append(v)
            if vals:
                print("      %-8s 中位 %s   [%s]" % (k, "%.2f" % st.median(vals),
                      ", ".join("%.2f" % x for x in vals[:6])))

    print()
    print("=" * 78)
    print("三、段级别对称性：多头段 vs 空头段的长度/幅度/后续反转")
    for sd, name in ((1, "多头段"), (-1, "空头段")):
        lens, amps, nxt5 = [], [], []
        for c, P in Pcache.items():
            bars, S = P["bars"], P["S"]
            segs = P["segs"]
            for si, (s, e, sdd) in enumerate(segs):
                if sdd != sd or e - s + 1 < 10:
                    continue
                lens.append(e - s + 1)
                amps.append((max(bars[k]["h"] for k in range(s, e + 1)) -
                             min(bars[k]["l"] for k in range(s, e + 1))) /
                            (S["atr"][s] or 1e-9))
                if si + 2 < len(segs):
                    nxt5.append(segs[si + 1][0] - s <= 5)
        print("  %-6s n=%3d  段长中位 %.0f 根  幅度中位 %.1f ATR  最长 %d  幅度P90 %.1f"
              % (name, len(lens), st.median(lens), st.median(amps), max(lens),
                 sorted(amps)[int(len(amps) * 0.9)]))

    print()
    print("=" * 78)
    print("四、强确认条件（持仓5日降 + 深回撤≥2ATR）分方向表现")
    for sd, name in ((1, "顶部做空"), (-1, "底部做多")):
        sub = [h for h in hits if h["side"] == sd]
        stg = [h for h in sub
               if (h["rows_feat"].get("doi5") or 0) <= 0
               and ((h["bars_c"] if "bars_c" in h else None) is not None)]
        # 直接按对称 pull 重算
        stg = []
        for h in sub:
            P = Pcache[h["sym"]]
            b = P["bars"][h["i"]]
            pull = ((b["c"] - P["hi"][h["i"]]) / h["atr"] if h["side"] == 1
                    else (P["lo"][h["i"]] - b["c"]) / h["atr"])
            if (h["rows_feat"].get("doi5") or 0) <= 0 and pull <= -2:
                stg.append(h)
        if not stg:
            print("  %s：强确认样本 0" % name)
            continue
        val = [h for h in stg if h["lead"] is not None]
        win = sum(1 for h in val if h["lead"] <= 5)
        print("  %-6s 强确认 %d 笔  5日内反转 %d/%d=%.0f%%  交易R %+.1f"
              % (name, len(stg), win, len(val), win / len(val) * 100 if val else 0,
                 sum(h["trade_R"] for h in stg)))


if __name__ == "__main__":
    main()
