# -*- coding: utf-8 -*-
"""v5：预测失误专项分析
=====================================================================
口径与线上引擎完全一致：
  触发 = 大周期段(seg_big: 长度>=15 & 幅度>=3ATR) 内、段龄>=2、
         4 条特征(A1/A9/A15/A17)全部命中 且 ADX>=20，每段只取第一次
  成功 = 5 根内出现反向穿越(lead<=5)
  失误 = lead>5 或数据结束仍未穿越（右删失，单独标注）

步骤：
  1) 列出全部失误案例（品种/日期/方向/段龄/关键特征/后续走势）
  2) 成功 vs 失误 的特征分布对比（中位数）
  3) 候选补充条件逐一测试：能剔除多少失误、误杀多少成功、
     对不设止损交易盈亏的影响；训练(<2024)/盲测(>=2024)分段验证
无前视：所有特征只用信号当日及以前。
"""
import statistics as st

import build
import cyc
import mine_v4b as M

SPLIT = "2024-01-01"
MIN_LEN, MIN_AMP = 15, 3.0        # 与 engine.seg_big 一致
MIN_AGE = 2
ADX_MIN = 20
REF = ["A1", "A9", "A15", "A17"]


def seg_big(P, s, e):
    bars, atr = P["bars"], P["S"]["atr"][s] or 1e-9
    hi = max(bars[k]["h"] for k in range(s, e + 1))
    lo = min(bars[k]["l"] for k in range(s, e + 1))
    return e - s + 1 >= MIN_LEN and (hi - lo) / atr >= MIN_AMP


def week_side(P, i):
    """信号日之前最后一个完整周线的方向状态（无前视）：
    返回 (周线方向, (周收盘-周MA34)/周ATR，方向已按日线趋势归一)"""
    bars = P["bars"]
    wk = {}                                    # weekKey -> list of idx
    for j in range(i + 1):
        d = bars[j]["d"]
        key = d[:4] + "-" + "%02d" % ((int(d[5:7]) - 1) // 7)  # 粗粒度周键
        wk.setdefault(key[:4] + "W" + d[5:7], []).append(j)
    # 按更简单的方式重算：ISO 周
    wk = {}
    import datetime as _dt
    for j in range(i + 1):
        d = _dt.date(int(bars[j]["d"][:4]), int(bars[j]["d"][5:7]), int(bars[j]["d"][8:10]))
        key = d.strftime("%G-W%V")
        wk.setdefault(key, []).append(j)
    keys = sorted(wk)
    full = [k for k in keys if k != keys[-1]]          # 最后一周可能不完整
    closes = [bars[wk[k][-1]]["c"] for k in full]
    if len(closes) < 40:
        return None, None
    ma = cyc.sma(closes, 34)
    j = full.index(keys[-2]) if keys[-2] in full else len(full) - 1
    if ma[j] is None:
        return None, None
    c = closes[j]
    l = [bars[wk[k][-1]] for k in full]
    atrs = cyc.atr_series(l)
    atr = atrs[j] or 1e-9
    side_d = P["side"][i]
    v = (c - ma[j]) / atr
    wside = 1 if c > ma[j] else -1
    if side_d == -1:
        v = -v                                          # 归一：>0 = 周线仍顺着日线趋势
    return wside, v


def collect(data):
    """按线上口径收集全部触发点（每段一次）"""
    hits = []
    for c in M.UNIV:
        rows, P = M.Pm_fetch(data, c, 34)
        S = P["S"]
        bars = P["bars"]
        segs = P["segs"]
        cs = [x for x in M.CONDS if x[0] in REF]
        by_i = {r["i"]: r for r in rows}
        for si, (s1, e1, sd) in enumerate(segs):
            if not seg_big(P, s1, e1) or si + 2 >= len(segs):
                continue
            for i in range(max(60, s1 + MIN_AGE - 1), e1 + 1):
                r = by_i.get(i)
                if r is None:
                    continue
                if not all(M.ok(r, x) for x in cs):
                    continue
                adx = S["adx"][i]
                if adx is None or adx < ADX_MIN:
                    continue
                # 交易口径：次日开盘逆势进，持有到 si+2 段结束的反向穿越
                entry = bars[i + 1]["o"]
                xi = min(segs[si + 2][0] + 1, len(bars) - 1)
                exitp = bars[xi]["o"] if xi < len(bars) - 1 else bars[-1]["c"]
                atr = S["atr"][i] or entry * 0.01
                pts = (exitp - entry) * (-sd) - M.FEE[c] / M.MULT[c] - M.TICK[c]
                # 段内此前是否已出现过 k>=3（预警先行）
                warned = any(by_i.get(j) and sum(1 for x in cs if M.ok(by_i[j], x)) >= 3
                             for j in range(max(60, s1), i))
                hits.append({
                    "sym": c, "i": i, "date": bars[i]["d"], "side": sd,
                    "lead": P["lead"][i], "trade_pts": pts, "trade_R": pts / atr,
                    "hold": xi - i - 1, "atr": atr,
                    "age": i - s1 + 1,
                    "segAmp": (max(bars[k]["h"] for k in range(s1, e1 + 1)) -
                               min(bars[k]["l"] for k in range(s1, e1 + 1))) / atr,
                    "warned": warned,
                    "rows_feat": r,
                })
                break
    return hits


def extra_feat(P, h):
    """补充维度（含周线），全部无前视"""
    i, r = h["i"], h["rows_feat"]
    S, bars = P["S"], P["bars"]
    b = bars[i]
    f = {}
    f["rsi_n"] = r.get("rsi")                    # 已方向归一 (rsi-50)
    f["streak"] = r.get("streak")
    f["cpos"] = r.get("cpos")
    f["vr5"] = r.get("vr5")
    f["vsum5"] = r.get("vsum5")
    f["doi5"] = r.get("doi5")
    f["doi10"] = r.get("doi10")
    f["adx_sl"] = r.get("adx_sl")
    f["di"] = r.get("di")
    f["atr_exp"] = r.get("atr_exp")
    f["d10"] = r.get("d10")
    f["ret3"] = r.get("ret3")
    b = bars[i]
    f["pull"] = ((b["c"] - P["hi"][i]) / h["atr"] if h["side"] == 1
                 else (P["lo"][i] - b["c"]) / h["atr"])   # 对称：≤0 = 已回撤
    f["ma34_sl"] = ((S["ma"][i] - S["ma"][i - 10]) / h["atr"]
                    if i >= 10 and S["ma"][i - 10] else None)   # MA34 仍斜向上=趋势仍在走
    ext = (b["c"] - P["hi"][i]) / h["atr"] if h["side"] == 1 else (P["lo"][i] - b["c"]) / h["atr"]
    f["newext"] = ext                            # >0 = 已创段内新高/新低（趋势仍在推进）
    _, wd = week_side(P, i)
    f["wdma"] = wd                               # 周线乖离：>0 = 周线仍顺日线趋势方向
    return f


FILT = [
    ("段龄 ≥10",       lambda f, h: h["age"] >= 10),
    ("段龄 ≥15",       lambda f, h: h["age"] >= 15),
    ("段龄 ≥20",       lambda f, h: h["age"] >= 20),
    ("段幅度 ≥5ATR",   lambda f, h: h["segAmp"] >= 5),
    ("段幅度 ≥8ATR",   lambda f, h: h["segAmp"] >= 8),
    ("自极值回撤 ≥2ATR", lambda f, h: (f["pull"] or -9) <= -2),
    ("RSI 已过 45",    lambda f, h: (f["rsi_n"] or 9) <= -5),
    ("RSI 已过 40",    lambda f, h: (f["rsi_n"] or 9) <= -10),
    ("连续反向 ≥3根",  lambda f, h: (f["streak"] or 0) >= 3),
    ("收在反向 1/3",   lambda f, h: (f["cpos"] or 1) <= 0.35),
    ("5日缩量(≤1.1×)", lambda f, h: (f["vsum5"] or 9) <= 1.1),
    ("5日放量(≥1.2×)", lambda f, h: (f["vsum5"] or 0) >= 1.2),
    ("持仓5日降",      lambda f, h: (f["doi5"] or 0) <= 0),
    ("持仓5日增",      lambda f, h: (f["doi5"] or -9) >= 0),
    ("ADX 见顶回落",   lambda f, h: (f["adx_sl"] or 0) <= 0),
    ("DI 已收敛",      lambda f, h: (f["di"] or 9) <= 0),
    ("ATR 未放大(≤1.1)", lambda f, h: (f["atr_exp"] or 9) <= 1.1),
    ("ATR 放大(≥1.15)", lambda f, h: (f["atr_exp"] or 0) >= 1.15),
    ("深破 MA10(≤-0.5)", lambda f, h: (f["d10"] or 0) <= -0.5),
    ("3日已反向",      lambda f, h: (f["ret3"] or 0) <= 0),
    ("MA34 斜率趋平(≤1)", lambda f, h: (f["ma34_sl"] or 9) <= 1.0),
    ("未创段内新极值", lambda f, h: (f["newext"] or 0) <= 0),
    ("预警先行(先3/4)", lambda f, h: h["warned"]),
    ("周线已转平/反向", lambda f, h: f["wdma"] is not None and f["wdma"] <= 0),
    ("周线乖离 ≤1ATR", lambda f, h: f["wdma"] is not None and f["wdma"] <= 1.0),
    ("周线乖离 ≥1ATR", lambda f, h: f["wdma"] is not None and f["wdma"] >= 1.0),
]


def main():
    data = M.load()
    hits = collect(data)
    print("触发总样本 %d（线上口径：大周期段 + 段龄≥2 + ADX≥20 + 4/4，每段一次）" % len(hits))
    labeled = [h for h in hits if h["lead"] is not None]
    succ = [h for h in labeled if h["lead"] <= 5]
    fail = [h for h in labeled if h["lead"] > 5]
    cens = [h for h in hits if h["lead"] is None]
    print("成功(5日内反转) %d (%.1f%%)  失误 %d (%.1f%%)  数据结束未判定 %d" %
          (len(succ), len(succ) / len(labeled) * 100, len(fail),
           len(fail) / len(labeled) * 100, len(cens)))

    # ---------- 1) 失误案例清单 ----------
    print("\n" + "=" * 118)
    print("一、预测失误案例清单（4/4 + ADX≥20，但 5 日内未反转）")
    print("=" * 118)
    print("%-3s %-11s %-4s %5s %7s %7s %6s %7s %7s %6s %6s  %s" %
          ("品种", "日期", "方向", "段龄", "段幅ATR", "回撤ATR", "RSI±", "ADX", "5日量", "持仓5d%", "周线", "后续(持有根数/交易R)"))
    for h in sorted(fail, key=lambda x: x["date"]):
        P = M.Pm_fetch(data, h["sym"], 34)[1]
        f = extra_feat(P, h)
        _, wd = week_side(P, h["i"])
        print("%-3s %-11s %-4s %5d %7.1f %7.1f %6.0f %7.1f %7.2f %6.1f %6s  lead=%s 持%d根 %+5.1fR" %
              (h["sym"], h["date"], "多" if h["side"] == 1 else "空",
               h["age"], h["segAmp"], -(f["pull"] or 0), (f["rsi_n"] or 0) + 50,
               h["rows_feat"].get("adx") or 0, f["vsum5"] or 0, f["doi5"] or 0,
               ("顺" if (wd or 0) > 0 else "反") if wd is not None else "-",
               h["lead"], h["hold"], h["trade_R"]))

    # ---------- 2) 成功 vs 失误 分布 ----------
    print("\n" + "=" * 118)
    print("二、成功(n=%d) vs 失误(n=%d) 特征中位数对比" % (len(succ), len(fail)))
    print("=" * 118)
    Pcache = {c: M.Pm_fetch(data, c, 34)[1] for c in M.UNIV}
    fs = [extra_feat(Pcache[h["sym"]], h) for h in succ]
    ff = [extra_feat(Pcache[h["sym"]], h) for h in fail]
    keys = ["age", "segAmp"] + sorted(fs[0].keys())
    print("%-16s %12s %12s" % ("指标", "成功中位", "失误中位"))
    for k in keys:
        if k in ("age", "segAmp"):
            a = [h[k] for h in succ]; b = [h[k] for h in fail]
        else:
            a = [x.get(k) for x in fs if x.get(k) is not None]
            b = [x.get(k) for x in ff if x.get(k) is not None]
        if len(a) < 5 or len(b) < 5:
            continue
        print("%-16s %12.2f %12.2f   (n=%d/%d)" % (k, st.median(a), st.median(b), len(a), len(b)))

    # ---------- 3) 候选条件逐一测试 ----------
    print("\n" + "=" * 118)
    print("三、候选补充条件测试（保留=条件通过；目标：多剔失误、少杀成功；交易=不设止损口径）")
    print("=" * 118)
    print("%-22s %5s %5s | %6s %6s | %6s %6s | 剔失误 剔成功 | %s" %
          ("条件", "保留", "胜率", "训练胜", "盲测胜", "盈亏R", "回撤R", "交易笔数/胜率"))
    base = {
        "n": len(labeled), "win": len(succ) / len(labeled),
        "tr": [h for h in labeled if h["date"] < SPLIT],
        "te": [h for h in labeled if h["date"] >= SPLIT],
    }
    base["trw"] = sum(1 for h in base["tr"] if h["lead"] <= 5) / len(base["tr"])
    base["tew"] = sum(1 for h in base["te"] if h["lead"] <= 5) / len(base["te"])
    eq = sum(h["trade_R"] for h in labeled)
    peak = dd = 0.0
    for h in sorted(labeled, key=lambda x: x["date"]):
        eq += h["trade_R"]
        peak = max(peak, eq); dd = min(dd, eq - peak)
    print("基准(不加条件)     %5d %5.1f%% | %6.1f%% %6.1f%% | %+6.1f %6.1f |" %
          (base["n"], base["win"] * 100, base["trw"] * 100, base["tew"] * 100,
           sum(h["trade_R"] for h in labeled), dd))
    results = []
    for name, fn in FILT:
        keep = [h for h in labeled if fn(extra_feat(Pcache[h["sym"]], h), h)]
        if len(keep) < 10:
            print("%-22s %5d  样本不足" % (name, len(keep)))
            continue
        w = sum(1 for h in keep if h["lead"] <= 5) / len(keep)
        tr = [h for h in keep if h["date"] < SPLIT]
        te = [h for h in keep if h["date"] >= SPLIT]
        trw = sum(1 for h in tr if h["lead"] <= 5) / len(tr) if tr else 0
        tew = sum(1 for h in te if h["lead"] <= 5) / len(te) if te else 0
        eq = peak = dd = 0.0
        for h in sorted(keep, key=lambda x: x["date"]):
            eq += h["trade_R"]
            peak = max(peak, eq); dd = min(dd, eq - peak)
        tw = sum(1 for h in keep if h["trade_pts"] > 0) / len(keep)
        results.append((name, keep, w, trw, tew, sum(h["trade_R"] for h in keep), dd, tw))
        print("%-22s %5d %5.1f%% | %6.1f%% %6.1f%% | %+6.1f %6.1f |   %d/%.0f%%" %
              (name, len(keep), w * 100, trw * 100, tew * 100,
               sum(h["trade_R"] for h in keep), dd, len(keep), tw * 100))
    print("\n注：交易 R 为不设止损、持有整段新趋势的单笔收益（ATR 计）。")

    # ---------- 4) 组合测试：表现最好的两三个条件叠加 ----------
    print("\n" + "=" * 118)
    print("四、候选条件两两叠加（保留样本 ≥10 且 训练/盲测胜率都提升 才列出）")
    print("=" * 118)
    best = [r for r in results if r[3] > base["trw"] and r[4] > base["tew"]]
    print("单项同时提升训练+盲测的条件：%s" %
          ("、".join(r[0] for r in best) if best else "无"))
    for a in range(len(results)):
        for b in range(a + 1, len(results)):
            na, fa = results[a][0], FILT[[x[0] for x in FILT].index(results[a][0])][1]
            nb, fb = results[b][0], FILT[[x[0] for x in FILT].index(results[b][0])][1]
            keep = [h for h in labeled if fa(extra_feat(Pcache[h["sym"]], h), h)
                    and fb(extra_feat(Pcache[h["sym"]], h), h)]
            if len(keep) < 10:
                continue
            w = sum(1 for h in keep if h["lead"] <= 5) / len(keep)
            tr = [h for h in keep if h["date"] < SPLIT]
            te = [h for h in keep if h["date"] >= SPLIT]
            trw = sum(1 for h in tr if h["lead"] <= 5) / len(tr) if tr else 0
            tew = sum(1 for h in te if h["lead"] <= 5) / len(te) if te else 0
            if trw > base["trw"] and tew > base["tew"]:
                eq = peak = dd = 0.0
                for h in sorted(keep, key=lambda x: x["date"]):
                    eq += h["trade_R"]
                    peak = max(peak, eq); dd = min(dd, eq - peak)
                print("%-20s + %-20s 保留%3d 胜率%.1f%% (内%.1f/外%.1f) 交易R%+.1f 回撤%.1f" %
                      (na, nb, len(keep), w * 100, trw * 100, tew * 100,
                       sum(h["trade_R"] for h in keep), dd))


if __name__ == "__main__":
    main()
