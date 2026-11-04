# -*- coding: utf-8 -*-
"""v6d：漏抓清单——被模型过滤但事后走出 ≥200点 趋势的区间反向归因
=====================================================================
定义：每次 MA34 反转（旧段→新段）都是一次机会。
  「抓住」= 旧段内出现过首轮 pred（4条+ADX≥20）→ 页面给出建仓提示
  「漏抓」= 旧段内从未出现 pred，但新段走了 ≥200 点
对每个漏抓的反转点记录：旧段末端最高命中条数 k、当时 ADX、距 MA34 距离、
持仓 5/10 日变化、是否到过预警(k≥3)、新段幅度。
再回答用户假设：漏抓是否系统性表现为「增仓型真趋势」？
最后测：能否用独立的「趋势启动指纹」补缺口（结合 v6b 的量能结论）。
"""
import statistics as st

import mine_v4b as M
import mine_v5 as V5

PT_TH = 200.0


def main():
    data = M.load()
    caught, missed = [], []
    detail_miss = []
    for c in M.UNIV:
        rows, P = M.Pm_fetch(data, c, 34)
        bars, S, segs = P["bars"], P["S"], P["segs"]
        by_i = {r["i"]: r for r in rows}
        for si, (s1, e1, sd) in enumerate(segs):
            if si + 1 >= len(segs) or s1 < 60:
                continue
            s2, e2, sd2 = segs[si + 1]
            amp = max(bars[k]["h"] for k in range(s2, e2 + 1)) - \
                min(bars[k]["l"] for k in range(s2, e2 + 1))
            if amp < PT_TH:
                continue
            rec = {"sym": c, "cross": bars[s2]["d"], "dir": sd2, "amp": amp,
                   "atr": S["atr"][s2] or 1e-9}
            is_pred = rec_has_pred(c, bars[s1]["d"], bars[s2]["d"])
            (caught if is_pred else missed).append(rec)
            if not is_pred:
                # 旧段末端的模型状态（取 k 最高那根，无前视）
                best = None
                for i in range(max(60, s1 + V5.MIN_AGE - 1), e1 + 1):
                    r = by_i.get(i)
                    if r is None:
                        continue
                    k = sum(1 for x in V5.REF if M.ok(r, next(
                        y for y in M.CONDS if y[0] == x)))
                    adx = S["adx"][i]
                    if adx is None or adx < 10:
                        continue
                    if best is None or k > best["k"]:
                        b = bars[i]
                        pull = ((b["c"] - P["hi"][i]) / (S["atr"][i] or 1e-9)
                                if sd == 1 else (P["lo"][i] - b["c"]) / (S["atr"][i] or 1e-9))
                        best = {"k": k, "date": b["d"], "adx": adx, "dma": r.get("dma"),
                                "doi5": r.get("doi5"), "doi10": r.get("doi10"),
                                "pull": pull, "age": i - s1 + 1,
                                "gate": adx >= V5.ADX_MIN,
                                "vr": r.get("vr5")}
                if best:
                    best.update(rec)
                    detail_miss.append(best)

    print("=" * 88)
    print("一、漏抓盘点（新段 ≥%d 点；口径：旧段无建仓提示）" % int(PT_TH))
    for c in M.UNIV:
        a = [x for x in caught if x["sym"] == c]
        m = [x for x in missed if x["sym"] == c]
        print("  %-3s 反转机会（新段≥200点）%2d 次：抓住 %2d，漏抓 %2d"
              % (c, len(a) + len(m), len(a), len(m)))
    print("  合计机会 %d：抓住 %d（%.0f%%），漏抓 %d（%.0f%%）"
          % (len(caught) + len(missed), len(caught),
             len(caught) / (len(caught) + len(missed)) * 100,
             len(missed), len(missed) / (len(caught) + len(missed)) * 100))

    print()
    print("=" * 88)
    print("二、漏抓清单（旧段末端模型状态，按新段幅度排序）")
    hdr = ("品种", "反转日", "方向", "新段幅度", "末端k", "末端ADX", "距MA34",
           "doi5%", "doi10%", "回撤ATR", "量峰")
    print("  %-4s %-11s %-3s %7s %4s %6s %7s %7s %7s %7s %5s" % hdr)
    for x in sorted(detail_miss, key=lambda z: -z["amp"]):
        print("  %-4s %-11s %-3s %5.0f点 %3d  %6.1f %+6.2f %+7.1f %+7.1f %+6.2f %5s"
              % (x["sym"], x["cross"], "多" if x["dir"] == 1 else "空",
                 x["amp"], x["k"], x["adx"], x["dma"] if x["dma"] is not None else 0,
                 x["doi5"] if x["doi5"] is not None else 0,
                 x["doi10"] if x["doi10"] is not None else 0,
                 x["pull"] if x["pull"] is not None else 0,
                 ("%.1f" % x["vr"]) if x["vr"] is not None else "-"))
    print("  （doi5/doi10 >0 = 旧段末端持仓仍在增加；回撤ATR为正表示距段极值已达该深度）"
          if False else
          "  注：doi5>0=末端持仓在增；pull 为按趋势方向归一的回撤深度")

    print()
    print("=" * 88)
    print("三、用户假设检验：漏抓的反转是否系统性「增仓」？")
    data_c = CAUGHT_FEATS
    for name, pool in (("漏抓", detail_miss), ("抓住", data_c)):
        if not pool:
            print("  %s: n=0" % name)
            continue
        d5 = [x["doi5"] for x in pool if x.get("doi5") is not None]
        d10 = [x["doi10"] for x in pool if x.get("doi10") is not None]
        adx = [x["adx"] for x in pool]
        inc5 = sum(1 for v in d5 if v > 0) / len(d5) * 100 if d5 else 0
        inc10 = sum(1 for v in d10 if v > 0) / len(d10) * 100 if d10 else 0
        print("  %s n=%2d：末端 doi5 中位 %+6.1f%%（增仓占比 %.0f%%）  "
              "doi10 中位 %+6.1f%%（增仓 %.0f%%）  ADX 中位 %.1f  末端k中位 %.0f"
              % (name, len(pool), st.median(d5) if d5 else 0, inc5,
                 st.median(d10) if d10 else 0, inc10,
                 st.median(adx), st.median([x["k"] for x in pool])))

    print()
    print("=" * 88)
    print("四、漏抓原因分解（旧段末端状态）")
    for name, cond in (("k从未到4（特征不齐）", lambda x: x["k"] < 4),
                       ("k到4但ADX<20（门槛拦下）", lambda x: x["k"] >= 4 and not x["gate"]),
                       ("k到4且ADX达标（应抓住却漏——时序/段过滤）",
                        lambda x: x["k"] >= 4 and x["gate"])):
        sub = [x for x in detail_miss if cond(x)]
        print("  %-28s %d 笔（%.0f%%）" % (name, len(sub),
              len(sub) / len(detail_miss) * 100 if detail_miss else 0))


def rec_has_pred(sym, d_start, d_end):
    """旧段（cross 于 d_end 之前结束的段）内是否出现过 pred"""
    for h in PRED_HITS:
        if h["sym"] == sym and d_start <= h["date"] < d_end:
            return True
    return False


if __name__ == "__main__":
    _d = M.load()
    PRED_HITS = V5.collect(_d)
    CAUGHT_FEATS = []
    for c in M.UNIV:
        rows, P = M.Pm_fetch(_d, c, 34)
        bars, S, segs = P["bars"], P["S"], P["segs"]
        by_i = {r["i"]: r for r in rows}
        for si, (s1, e1, sd) in enumerate(segs):
            if si + 1 >= len(segs) or s1 < 60:
                continue
            s2, e2, sd2 = segs[si + 1]
            amp = max(bars[k]["h"] for k in range(s2, e2 + 1)) - \
                min(bars[k]["l"] for k in range(s2, e2 + 1))
            if amp < PT_TH:
                continue
            hit = [h for h in PRED_HITS if h["sym"] == c and s1 <= h["i"] <= e1]
            if not hit:
                continue
            h = hit[0]
            r = h["rows_feat"]
            b = bars[h["i"]]
            pull = ((b["c"] - P["hi"][h["i"]]) / h["atr"] if sd == 1
                    else (P["lo"][h["i"]] - b["c"]) / h["atr"])
            kk = sum(1 for x in V5.REF if M.ok(r, next(y for y in M.CONDS if y[0] == x)))
            CAUGHT_FEATS.append({"sym": c, "cross": bars[s2]["d"], "dir": sd2,
                                 "amp": amp, "k": kk, "adx": r.get("adx"),
                                 "dma": r.get("dma"), "doi5": r.get("doi5"),
                                 "doi10": r.get("doi10"), "pull": pull,
                                 "gate": True, "age": h["age"]})
    main()
