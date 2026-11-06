# -*- coding: utf-8 -*-
"""标定独立出场信号的 6 条件组合：分档精确率、触发频率、之后 10 根的反向幅度"""
import statistics as st

import mine_v11 as V11
import mine_v9 as V9
import build
import mine_v4b as M
import cyc

UNIV = V9.UNIV
KEYS = ["ext30", "pull", "amp12", "shadow", "age30", "adx"]
LAB = {"ext30": "距 MA34 ≥3ATR", "pull": "自极值回撤≥1.2ATR", "amp12": "段幅≥12ATR",
       "shadow": "长影线≥0.6ATR", "age30": "段龄≥30根", "adx": "ADX 回落≥3"}


def main():
    V9.prep_all()
    rows = []
    for c in UNIV:
        P = V9.DATA[c]
        bars = P["bars"]
        last = -99
        for i in range(60, len(bars) - 12):
            sd = V9.M.prep  # noqa  (占位，避免误用)
            # 趋势方向：本根所在段的方向
            sdir = None
            for s, e, d in P["segs"]:
                if s <= i <= e:
                    sdir = d
                    break
            if sdir is None:
                continue
            if i - last < 8:
                continue
            f = V11.sig_feats(P, i, sdir)
            k = sum(1 for x in KEYS if f.get(x))
            if k < 2:
                continue
            last = i
            atr = P["S"]["atr"][i]
            up = max((bars[j]["h"] - bars[i]["c"]) * sdir for j in range(i + 1, i + 11)) / atr
            dn = max((bars[i]["c"] - bars[j]["l"]) * sdir for j in range(i + 1, i + 11)) / atr
            fwd10 = (bars[i]["c"] - bars[min(i + 10, len(bars) - 1)]["c"]) * sdir / atr
            rows.append({"sym": c, "i": i, "d": bars[i]["d"], "k": k,
                         "up": up, "dn": dn, "fwd10": fwd10})
    years = 10.0 * len(UNIV)
    print("总触发（≥2 条，8 根内去重）：%d 次，约每品种每年 %.1f 次\n"
          % (len(rows), len(rows) / years))
    print("%4s %5s %8s %10s %10s %10s %10s" %
          ("k≥", "n", "年均/品种", "反向≥1ATR", "反向均值", "剩余顺势", "净优势"))
    for k in (2, 3, 4, 5, 6):
        sub = [r for r in rows if r["k"] >= k]
        if len(sub) < 5:
            print("%4d %5d  样本不足" % (k, len(sub)))
            continue
        rev = sum(1 for r in sub if r["dn"] >= 1.0) / len(sub) * 100
        print("%4d %5d %9.2f %9.1f%% %+10.2f %+10.2f %+10.2f" %
              (k, len(sub), len(sub) / years, rev,
               st.mean(r["dn"] for r in sub),
               st.mean(r["up"] for r in sub),
               st.mean(r["dn"] - r["up"] for r in sub)))
    print("\n各条命中率（触发集内）：")
    for key in KEYS:
        pass
    print("净优势 = 之后10根反向最大 − 顺势最大（正=走掉更好）")
    print("\n最近 12 次触发：")
    for r in sorted(rows, key=lambda x: -x["i"])[:12]:
        print("  %s %-3s k=%d  之后反向%+.1fATR 顺势%+.1fATR"
              % (r["d"], r["sym"], r["k"], r["dn"], r["up"]))


if __name__ == "__main__":
    main()
