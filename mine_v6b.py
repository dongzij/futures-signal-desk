# -*- coding: utf-8 -*-
"""v6b：趋势启动指纹——300点以上大趋势启动前/初期的量仓特征
=====================================================================
反向提取：过去10年5品种中，凡走出 ≥300点 的 MA34 段（大趋势），
其 启动前(交叉点前10根) 与 启动初期(交叉后10根) 的量仓特征是什么？
重点回答：
  1) 持仓从什么水平开始加速上升（10日增幅阈值扫描）
  2) 成交量放大倍数（相对20日均量）
  3) 价格突破 MA34 时的持仓状态
  4) 「持仓增幅≥X% + 价格站稳MA34」对"本段最终走出≥300点"的预测力
     —— 若显著，它就是趋势延续/启动信号，可与"持仓下降=反转"共存
     （两个条件描述的是不同时点：启动时增仓 vs 末端减仓）
无前视：所有特征只用交叉点当日及以前 / 启动初期（不含未来）。
"""
import statistics as st

import mine_v4b as M

PT_TH = 300.0        # 大趋势点数门槛（用户口径）
W_PRE = 10           # 启动前窗口
W_INI = 10           # 启动初期窗口


def oi_pct(bars, i, look=60):
    """当前持仓量在过去 look 根中的分位（0~1，无前视）"""
    lo = max(0, i - look + 1)
    win = [bars[j].get("oi") or 0 for j in range(lo, i + 1)]
    cur = bars[i].get("oi") or 0
    if not cur or len(win) < 20:
        return None
    return sum(1 for x in win if x <= cur) / len(win)


def doi(bars, i, w):
    if i - w < 0:
        return None
    o0, o1 = bars[i - w].get("oi") or 0, bars[i].get("oi") or 0
    return (o1 - o0) / o0 * 100 if o0 else None


def vrr(bars, i):
    """当日量 / 20日均量"""
    v = bars[i].get("v") or 0
    vs = [bars[j].get("v") or 0 for j in range(max(0, i - 19), i + 1)]
    ma = sum(vs) / len(vs) if vs else 0
    return v / ma if ma else None


def main():
    data = M.load()
    seg_rows = []          # 每个段一行
    for c in M.UNIV:
        rows, P = M.Pm_fetch(data, c, 34)
        bars, S = P["bars"], P["S"]
        for si, (s, e, sd) in enumerate(P["segs"]):
            if s < 60:
                continue
            amp_pt = max(bars[k]["h"] for k in range(s, e + 1)) - \
                min(bars[k]["l"] for k in range(s, e + 1))
            big = amp_pt >= PT_TH
            # 启动前：交叉点前一日的量仓状态
            p0 = s - 1
            # 启动初期：交叉后 W_INI 根内的最大量仓动作（用段内数据，属本段信息）
            ini_doi = max((doi(bars, j, 5) for j in range(s, min(s + W_INI, len(bars)))
                           if doi(bars, j, 5) is not None), default=None)
            ini_vr = max((vrr(bars, j) for j in range(s, min(min(s + W_INI, e + 1),
                                                              len(bars)))), default=None)
            # 启动初期价格是否曾回踩 MA34（站稳检验）
            retest = None
            for j in range(s + 1, min(s + W_INI, e + 1)):
                if S["ma"][j] is None:
                    continue
                if (sd == 1 and bars[j]["c"] < S["ma"][j]) or \
                   (sd == -1 and bars[j]["c"] > S["ma"][j]):
                    retest = True
                    break
            else:
                retest = False
            seg_rows.append({
                "sym": c, "s": s, "e": e, "side": sd, "amp": amp_pt, "big": big,
                "pre_doi10": doi(bars, p0, 10), "pre_doi5": doi(bars, p0, 5),
                "pre_oiq": oi_pct(bars, p0), "pre_vr": vrr(bars, p0),
                "ini_doi5": doi(bars, s, 5), "ini_doi10": doi(bars, s, 10),
                "ini_doi5max": ini_doi, "ini_vr": ini_vr,
                "retest": retest, "n_bars": e - s + 1,
            })

    bigs = [r for r in seg_rows if r["big"]]
    print("=" * 80)
    print("一、大趋势盘点（MA34 段幅度 ≥%d 点）" % int(PT_TH))
    print("  段总数 %d，其中 ≥300点 的大趋势 %d 段（%.0f%%）" %
          (len(seg_rows), len(bigs), len(bigs) / len(seg_rows) * 100))
    for c in M.UNIV:
        sub = [r for r in seg_rows if r["sym"] == c]
        b = [r for r in sub if r["big"]]
        up = [r for r in b if r["side"] == 1]
        dn = [r for r in b if r["side"] == -1]
        print("  %-3s 段 %2d 个，大趋势 %2d 个（多 %d / 空 %d），平均幅度 %d 点"
              % (c, len(sub), len(b), len(up), len(dn),
                 st.mean([r["amp"] for r in b]) if b else 0))

    print()
    print("=" * 80)
    print("二、启动前(交叉点前一日) vs 启动初期 特征中位数：大趋势 vs 普通段")
    for grp, name in ((bigs, "大趋势≥300点"), ([r for r in seg_rows if not r["big"]], "普通段")):
        print("  —— %s（n=%d）" % (name, len(grp)))
        for k in ("pre_doi10", "pre_doi5", "pre_oiq", "pre_vr",
                  "ini_doi5max", "ini_vr"):
            vals = [r[k] for r in grp if r.get(k) is not None]
            if vals:
                print("      %-12s 中位 %7.2f   P75 %7.2f" %
                      (k, st.median(vals), sorted(vals)[int(len(vals) * .75)]))
        rt = sum(1 for r in grp if r["retest"]) / len(grp) * 100
        print("      启动10根内回踩MA34占比 %.0f%%" % rt)

    print()
    print("=" * 80)
    print("三、阈值扫描：「持仓10日增幅≥X% + 站稳MA34」→ 本段最终≥300点的概率")
    base = len(bigs) / len(seg_rows)
    print("  基准：任意段最终≥300点的概率 %.1f%%（%d/%d）" %
          (base * 100, len(bigs), len(seg_rows)))
    print("  %-14s %-8s %-8s %-8s %-8s" % ("持仓阈值", "X=2%", "X=5%", "X=8%", "X=12%"))
    for k_name, kf in (("交叉当日", lambda r: r["pre_doi10"]),
                       ("启动5日内最大", lambda r: r["ini_doi5max"])):
        line = "  %-14s" % k_name
        for th in (2, 5, 8, 12):
            hit = [r for r in seg_rows
                   if (r[kf.__name__ if False else "x"] if False else kf(r)) is not None
                   and kf(r) >= th and not r["retest"]]
            if len(hit) < 12:
                line += "  n=%-4d " % len(hit)
                continue
            p = sum(1 for r in hit if r["big"]) / len(hit)
            line += "  %2.0f%%(%d) " % (p * 100, len(hit))
        print(line + "   [n=样本数]")

    print()
    print("=" * 80)
    print("四、趋势延续检验：段已达 300 点后，持仓继续增 vs 转降 → 后续还能走多远")
    for c in M.UNIV:
        rows, P = M.Pm_fetch(data, c, 34)
        bars, S = P["bars"], P["S"]
        cont_inc, cont_dec = [], []
        for si, (s, e, sd) in enumerate(P["segs"]):
            for j in range(s + 10, e - 5):
                pt = (max(bars[k]["h"] for k in range(s, j + 1)) if sd == 1
                      else min(bars[k]["l"] for k in range(s, j + 1)))
                entry = bars[s]["c"]
                prog = (pt - entry) * sd
                if prog < PT_TH:
                    continue
                d = doi(bars, j, 10)
                if d is None:
                    continue
                ext = (max(bars[k]["h"] for k in range(j, e + 1)) if sd == 1
                       else min(bars[k]["l"] for k in range(j, e + 1)))
                more = (ext - pt) * sd
                (cont_inc if d >= 0 else cont_dec).append(more)
        def sm(v):
            return "n=%3d 中位 %6.0f 点 均值 %7.0f" % (
                len(v), st.median(v) if v else 0, st.mean(v) if v else 0) if v else "n=0"
        print("  %-3s 持仓10日仍增: %s | 已转降: %s" % (c, sm(cont_inc), sm(cont_dec)))


if __name__ == "__main__":
    main()
