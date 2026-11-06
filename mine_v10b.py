# -*- coding: utf-8 -*-
"""v10b：独立出场信号 —— 顶/底识别（分类口径）
问题定义改成「是不是顶」，而不是「之后会不会跌」：
  正样本 = 真顶/真底前后 ±3 根的 K 线（由大段极值定义）
  负样本 = 同一段推进期内的其它 K 线（趋势还在走）
  看哪些特征能把「顶区」和「趋势中段」分开 → precision / recall / 每年误报
经济性：信号日离真顶还有多少根、之后还能涨多少（机会成本）、
        能避开多少回撤（收益），两者比 = 值不值得走。
"""
import statistics as st

import build
import cyc
import mine_v4b as M
import mine_v10 as V

UNIV = M.UNIV
NEAR = 3          # 正样本窗口 ±3 根
MIN_PTS = 200

NAMES = ["pull", "ma18", "ma5t", "macd", "oi", "vol", "atr", "adx", "shadow"]
LABEL = {"pull": "自极值回撤≥1.2ATR", "ma18": "MA18 反向失守", "ma5t": "MA5 拐头",
         "macd": "MACD 柱转向", "oi": "持仓5日撤退", "vol": "量能萎缩",
         "atr": "ATR 收缩", "adx": "ADX 回落≥3", "shadow": "长影线"}


def pivots(P, min_pts=MIN_PTS, min_len=12):
    bars = P["bars"]
    out = []
    for s, e, sd in P["segs"]:
        if e - s + 1 < min_len:
            continue
        hi = max(bars[k]["h"] for k in range(s, e + 1))
        lo = min(bars[k]["l"] for k in range(s, e + 1))
        if hi - lo < min_pts:
            continue
        p = (max(range(s, e + 1), key=lambda k: bars[k]["h"]) if sd == 1
             else min(range(s, e + 1), key=lambda k: bars[k]["l"]))
        out.append((s, e, sd, p))
    return out


def build_samples():
    V.prep_all()
    pos, neg = [], []
    for c in UNIV:
        P = V.DATA[c]
        pv = pivots(P)
        near = {}
        for s, e, sd, p in pv:
            for i in range(max(s, p - NEAR), min(e, p + NEAR) + 1):
                near[i] = p
        for s, e, sd, p in pv:
            # 只取本段推进期（起点+8 .. p-6）作为负样本，避免段尾横盘污染
            for i in range(s + 8, p - 5):
                if i in near:
                    continue
                sd2 = V.ctx(P, i)
                if sd2 is None:
                    continue
                f = V.feats(P, i, sd2)
                neg.append({"sym": c, "i": i, "d": P["bars"][i]["d"], "sd": sd2,
                            "f": f, "k": sum(f.values())})
        for s, e, sd, p in pv:
            for i in range(max(s, p - NEAR), min(e, p + NEAR) + 1):
                sd2 = V.ctx(P, i)
                if sd2 is None:
                    continue          # 顶区可能已跌破 MA34 失去语境，用段方向兜底
                f = V.feats(P, i, sd2)
                pos.append({"sym": c, "i": i, "d": P["bars"][i]["d"], "sd": sd2,
                            "f": f, "k": sum(f.values()), "p": p, "lag": i - p})
    return pos, neg


def rate(rows, nm):
    return sum(1 for r in rows if r["f"][nm]) / max(len(rows), 1)


def kdist(rows):
    d = {}
    for r in rows:
        d[r["k"]] = d.get(r["k"], 0) + 1
    return d


def main():
    pos, neg = build_samples()
    print("正样本（顶/底区 ±%d 根）：%d   负样本（推进期中段）：%d" % (NEAR, len(pos), len(neg)))
    print("\n单条件：顶区出现率 vs 中段出现率")
    print("%-20s %8s %8s %7s" % ("条件", "顶区", "中段", "lift"))
    rows = []
    for nm in NAMES:
        a, b = rate(pos, nm), rate(neg, nm)
        rows.append((nm, a, b, a / b if b else 99))
    for nm, a, b, lf in sorted(rows, key=lambda x: -x[3]):
        print("%-20s %7.1f%% %7.1f%% %7.2f" % (LABEL[nm], a * 100, b * 100, lf))

    print("\n命中条数分布（占比）：")
    kp, kn = kdist(pos), kdist(neg)
    tot_p, tot_n = sum(kp.values()), sum(kn.values())
    print("%4s %10s %10s %10s" % ("k", "顶区", "中段", "倍率"))
    for k in range(0, 10):
        a = kp.get(k, 0) / tot_p * 100
        b = kn.get(k, 0) / tot_n * 100
        print("%4d %9.1f%% %9.1f%% %9.2f" % (k, a, b, (a / b) if b else 99))

    # 只用有区分度的条件（lift>1.15）
    picked = [nm for nm, a, b, lf in sorted(rows, key=lambda x: -x[3]) if lf > 1.15]
    print("\n入选条件（lift>1.15）：%s" % " / ".join(LABEL[x] for x in picked))

    def score(r):
        return sum(1 for nm in picked if r["f"][nm])

    for r in pos:
        r["kk"] = score(r)
    for r in neg:
        r["kk"] = score(r)

    print("\n入选条件 k 分布：")
    kp2, kn2 = kdist2(pos, "kk"), kdist2(neg, "kk")
    print("%4s %10s %10s %10s" % ("k", "顶区", "中段", "倍率"))
    for k in range(0, len(picked) + 1):
        a = kp2.get(k, 0) / tot_p * 100
        b = kn2.get(k, 0) / tot_n * 100
        print("%4d %9.1f%% %9.1f%% %9.2f" % (k, a, b, (a / b) if b else 99))

    print("\n阈值效果（在顶区＝命中，在中段＝误报）：")
    print("%6s %10s %10s %10s" % ("k≥", "precision", "recall", "每年误报"))
    years = 10.0 * len(UNIV)
    for k in range(2, len(picked) + 1):
        tp = sum(1 for r in pos if r["kk"] >= k)
        fp = sum(1 for r in neg if r["kk"] >= k)
        prec = tp / (tp + fp) if tp + fp else 0
        rec = tp / tot_p
        print("%6d %9.1f%% %9.1f%% %9.1f" % (k, prec * 100, rec * 100, fp / years))

    # 经济性：逐品种告警日，算「还能涨多少」vs「能避开多少」
    print("\n经济性（k≥3 的告警，看未来 10 根）：")
    econ = []
    for c in UNIV:
        P = V.DATA[c]
        bars = P["bars"]
        last = -99
        for i in range(30, len(bars) - 12):
            if i - last < 8:
                continue
            sd = V.ctx(P, i)
            if sd is None:
                continue
            f = V.feats(P, i, sd)
            if sum(1 for nm in picked if f[nm]) < 3:
                continue
            last = i
            atr = P["S"]["atr"][i]
            up = max((bars[j]["h"] - bars[i]["c"]) * sd for j in range(i + 1, i + 11)) / atr
            down = max((bars[i]["c"] - bars[j]["l"]) * sd for j in range(i + 1, i + 11)) / atr
            econ.append({"sym": c, "d": bars[i]["d"], "up": up, "down": down})
    if econ:
        print("  告警 %d 次 / %d 品种 / 10 年（平均每品种每年 %.1f 次）"
              % (len(econ), len(UNIV), len(econ) / years))
        print("  之后 10 根还能顺势走 %.2f ATR（机会成本，中位 %.2f）"
              % (st.mean(x["up"] for x in econ), st.median(x["up"] for x in econ)))
        print("  之后 10 根反向最大 %.2f ATR（能避开的，中位 %.2f）"
              % (st.mean(x["down"] for x in econ), st.median(x["down"] for x in econ)))
        ok = [x for x in econ if x["down"] > x["up"]]
        print("  避开的 > 错过的：%.1f%%（%d/%d）" % (len(ok) / len(econ) * 100, len(ok), len(econ)))


def kdist2(rows, key):
    d = {}
    for r in rows:
        d[r[key]] = d.get(r[key], 0) + 1
    return d


if __name__ == "__main__":
    main()
