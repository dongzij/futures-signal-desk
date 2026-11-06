# -*- coding: utf-8 -*-
"""逐品种配置 vs 通用配置 —— 第二步：样本外检验与判定

判定规则（先定规则再看结果，避免事后挑参数）：
  逐品种配置必须同时满足以下三条才启用：
    ① 样本外准确率提升 ≥ +3.0 个百分点
    ② 样本外盈利率（平均单笔收益）提升 > 0
    ③ 两者的 bootstrap 95% 置信区间下界 > 0（统计显著）
  否则：全部使用通用配置。

三层检验：
  L1 样本内（全样本选参 + 全样本评估）—— 展示「看起来能提升多少」，这是过拟合上限
  L2 固定切分：训练段 <2024 选参 → 盲测段 >=2024 评估
  L3 逐年滚动样本外（walk-forward）：每年只用此前数据选参，在当年评估，汇总全部样本外记录
     —— 这是主判定依据，样本外样本量最大、最贴近实盘
进场雷达与独立出场雷达分别检验（两套参数互相独立）。
"""
import json
import os
import random

CACHE = os.path.join("cache", "per_grid.json")
OUT = os.path.join("cache", "tune_verdict.json")
SPLIT = "2024-01-01"
ORIGINAL5 = {"FG", "M", "RB", "MA", "UR"}       # v4 原始五品种
MIN_N = {"entry": 4, "radar": 8}                 # 选参最小样本，防 1 笔 100% 假象
TH_ACC = 3.0                                     # 判定阈值：准确率提升百分点
B = 2000
random.seed(20261106)

D = json.load(open(CACHE, encoding="utf-8"))
TR, SG, META, GRID, GEN = (D["trades"], D["sigs"], D["meta"],
                           D["grid"], D["generic"])
UNIV = list(META.keys())
G_ENTRY = ("34", "26_3.0")
G_RADAR = "2.6_1.1"

# 候选池
ENTRY_KEYS = [(str(m), "%d_%.1f" % (n, k))
              for m in GRID["ma"] for n in GRID["xma"] for k in GRID["ch"]]
RADAR_KEYS = ["%.1f_%.1f" % (e, p) for e in GRID["ext"] for p in GRID["pull"]]
# 低自由度「邻近网格」：不给过拟合太多自由度，给逐品种方案一个公平机会
NEAR_ENTRY = [(str(m), "%d_%.1f" % (n, k))
              for m in (30, 34, 40) for n in (24, 26, 28) for k in (2.5, 3.0, 3.5)]
NEAR_RADAR = ["%.1f_%.1f" % (e, p)
              for e in (2.4, 2.5, 2.6, 2.7, 2.8) for p in (1.0, 1.1, 1.2)]


# ---------------------------------------------------------------- 基础
def rows_of(kind, c, key, lo=None, hi=None):
    if kind == "entry":
        r = TR[c][key[0]][key[1]]
    else:
        r = SG[c][key]
    if lo is not None:
        r = [x for x in r if x[0] >= lo]
    if hi is not None:
        r = [x for x in r if x[0] < hi]
    return r


def met(kind, rows):
    """返回 {n, win(%), ret(净), avg(单笔平均收益)}"""
    n = len(rows)
    if not n:
        return {"n": 0, "win": 0.0, "ret": 0.0, "avg": 0.0}
    if kind == "entry":
        vals = [x[1] for x in rows]
        oks = [1 if v > 0 else 0 for v in vals]
    else:
        vals = [x[2] for x in rows]
        oks = [x[1] for x in rows]
        # 雷达：只统计建议级及以上（watch 在 scan 里已排除）
    return {"n": n, "win": sum(oks) / n * 100, "ret": sum(vals),
            "avg": sum(vals) / n}


def fmt(m):
    return "n%3d 准确率%5.1f%% 净%+7.1f 单笔%+6.2f" % (m["n"], m["win"], m["ret"], m["avg"])


# ---------------------------------------------------------------- 选参
def choose(kind, c, lo, hi, objective, space):
    """在 [lo,hi) 训练区间为品种 c 选一套参数；objective: mix / acc / ret"""
    pool = (ENTRY_KEYS if space == "full" else NEAR_ENTRY) if kind == "entry" \
        else (RADAR_KEYS if space == "full" else NEAR_RADAR)
    cand = []
    for key in pool:
        m = met(kind, rows_of(kind, c, key, lo, hi))
        if m["n"] < MIN_N[kind]:
            continue
        cand.append((key, m))
    if not cand:
        return None, None
    wins = [x[1]["win"] for x in cand]
    avgs = [x[1]["avg"] for x in cand]
    wmin, wmax, amin, amax = min(wins), max(wins), min(avgs), max(avgs)

    def nz(v, lo_, hi_):
        return (v - lo_) / (hi_ - lo_) if hi_ - lo_ > 1e-12 else 0.5

    def score(m):
        nw, na = nz(m["win"], wmin, wmax), nz(m["avg"], amin, amax)
        return {"mix": 0.5 * nw + 0.5 * na, "acc": nw, "ret": na}[objective]

    key, m = max(cand, key=lambda x: (score(x[1]), x[1]["n"]))
    return key, m


def keystr(kind, key):
    if kind == "entry":
        m = key[0]
        n, k = key[1].split("_")
        return "MA%s / 离场MA%s+吊灯%s" % (m, n, k)
    e, p = key.split("_")
    return "EXT%s / PULL%s" % (e, p)


# ---------------------------------------------------------------- 评估
def eval_fixed(kind, c, key, lo, hi):
    return met(kind, rows_of(kind, c, key, lo, hi))


def collect(kind, c, key, lo, hi):
    """返回该参数在区间内的原始记录（用于 bootstrap）"""
    rows = rows_of(kind, c, key, lo, hi)
    if kind == "entry":
        return [[1 if x[1] > 0 else 0, x[1]] for x in rows]
    return [[x[1], x[2]] for x in rows]


def agg(records):
    """records: [[ok, gain]] -> met-like"""
    n = len(records)
    if not n:
        return {"n": 0, "win": 0.0, "ret": 0.0, "avg": 0.0}
    return {"n": n, "win": sum(x[0] for x in records) / n * 100,
            "ret": sum(x[1] for x in records),
            "avg": sum(x[1] for x in records) / n}


def boot_diff(a, b):
    """非配对 bootstrap：a=逐品种方案, b=通用方案；返回 (Δacc, Δacc CI, Δret, Δret CI)"""
    if not a or not b:
        return (0, (0, 0), 0, (0, 0))
    da = []
    dr = []
    for _ in range(B):
        sa = [a[random.randrange(len(a))] for _ in a]
        sb = [b[random.randrange(len(b))] for _ in b]
        da.append(sum(x[0] for x in sa) / len(sa) * 100 -
                  sum(x[0] for x in sb) / len(sb) * 100)
        dr.append(sum(x[1] for x in sa) / len(sa) -
                  sum(x[1] for x in sb) / len(sb))
    da.sort()
    dr.sort()

    def ci(v):
        return (v[int(B * 0.025)], v[int(B * 0.975)])

    return (sum(x[0] for x in a) / len(a) * 100 -
            sum(x[0] for x in b) / len(b) * 100, ci(da),
            sum(x[1] for x in a) / len(a) - sum(x[1] for x in b) / len(b), ci(dr))


# ---------------------------------------------------------------- L1 / L2
def l1_l2():
    print("=" * 116)
    print("L1 样本内（全样本选参 / 全样本评估）—— 逐品种拟合的上限，不能作为启用依据")
    print("=" * 116)
    for kind in ("entry", "radar"):
        gkey = G_ENTRY if kind == "entry" else G_RADAR
        print("\n  【%s】" % ("进场雷达" if kind == "entry" else "独立出场雷达"))
        print("  %-6s %-7s %-46s %-38s %-38s" %
              ("品种", "", "训练段选出的最优参数", "样本内·逐品种", "样本内·通用"))
        for c in UNIV:
            key, m = choose(kind, c, None, None, "mix", "full")
            g = eval_fixed(kind, c, gkey, None, None)
            print("  %-6s %-7s %-46s %-38s %-38s" %
                  (c, META[c]["name"], keystr(kind, key) if key else "—",
                   fmt(m) if m else "样本不足", fmt(g)))

    print("\n" + "=" * 116)
    print("L2 固定切分：训练段 <2024 选参 → 盲测段 >=2024 评估（真正的前瞻检验）")
    print("=" * 116)
    for kind in ("entry", "radar"):
        gkey = G_ENTRY if kind == "entry" else G_RADAR
        print("\n  【%s】" % ("进场雷达" if kind == "entry" else "独立出场雷达"))
        print("  %-6s %-44s %-36s %-36s" %
              ("品种", "训练段选出的参数", "盲测·逐品种", "盲测·通用"))
        ra, rb = [], []
        for c in UNIV:
            key, m = choose(kind, c, None, SPLIT, "mix", "full")
            if key:
                ra += collect(kind, c, key, SPLIT, None)
            rb += collect(kind, c, gkey, SPLIT, None)
            print("  %-6s %-44s %-36s %-36s" %
                  (c, keystr(kind, key) if key else "样本不足→沿用通用",
                   fmt(eval_fixed(kind, c, key, SPLIT, None)) if key else "—",
                   fmt(eval_fixed(kind, c, gkey, SPLIT, None))))
        A, Bg = agg(ra), agg(rb)
        dacc, cacc, dret, cret = boot_diff(ra, rb)
        print("  %-6s %-44s %-36s %-36s" % ("合计", "", fmt(A), fmt(Bg)))
        print("  差异：准确率 %+.1fpp (95%%CI %.1f~%.1f)  单笔收益 %+.2f (95%%CI %+.2f~%+.2f)" %
              (dacc, cacc[0], cacc[1], dret, cret[0], cret[1]))


# ---------------------------------------------------------------- L3 walk-forward
def wf_years(c):
    y0 = int(META[c]["first"][:4]) + 3
    return list(range(max(2019, y0), 2027))


def l3(space, objective):
    print("\n" + "=" * 116)
    print("L3 逐年滚动样本外（walk-forward）  搜索空间=%s  选参目标=%s  ← 主判定依据" %
          (space, objective))
    print("=" * 116)
    verdict = {}
    for kind in ("entry", "radar"):
        gkey = G_ENTRY if kind == "entry" else G_RADAR
        print("\n  【%s】" % ("进场雷达" if kind == "entry" else "独立出场雷达"))
        ra, rb = [], []
        per = {}
        chosen_cnt = {}
        for c in UNIV:
            A, Bg = [], []
            picks = []
            for y in wf_years(c):
                lo, hi = "%d-01-01" % y, "%d-01-01" % (y + 1)
                key, _ = choose(kind, c, None, lo, objective, space)
                key = key or gkey            # 训练样本不足 → 回退通用
                A += collect(kind, c, key, lo, hi)
                Bg += collect(kind, c, gkey, lo, hi)
                picks.append(keystr(kind, key))
            ra += A
            rb += Bg
            per[c] = (agg(A), agg(Bg))
            chosen_cnt[c] = (len(set(picks)), len(picks))
        A, Bg = agg(ra), agg(rb)
        dacc, cacc, dret, cret = boot_diff(ra, rb)
        print("  %-6s %-38s %-38s %-9s" %
              ("品种", "样本外·逐品种", "样本外·通用", "参数变动"))
        for c in UNIV:
            a, b = per[c]
            print("  %-6s %-38s %-38s %d种/%d年" %
                  (c, fmt(a), fmt(b), chosen_cnt[c][0], chosen_cnt[c][1]))
        print("  %-6s %-38s %-38s" % ("合计", fmt(A), fmt(Bg)))
        print("  差异：准确率 %+.1fpp (95%%CI %.1f~%.1f)  单笔收益 %+.2f (95%%CI %+.2f~%+.2f)" %
              (dacc, cacc[0], cacc[1], dret, cret[0], cret[1]))
        passed = (dacc >= TH_ACC and dret > 0 and cacc[0] > 0 and cret[0] > 0)
        print("  判定：%s（阈值 准确率≥+%.1fpp、收益>0、两者CI下界>0）" %
              ("★ 启用逐品种" if passed else "保持通用", TH_ACC))
        verdict[kind] = {"space": space, "objective": objective,
                         "perSymbol": {c: {"tuned": per[c][0], "generic": per[c][1],
                                           "distinctParams": chosen_cnt[c][0]}
                                       for c in UNIV},
                         "tuned": A, "generic": Bg,
                         "dAcc": round(dacc, 2), "accCI": [round(x, 2) for x in cacc],
                         "dRet": round(dret, 4), "retCI": [round(x, 4) for x in cret],
                         "passed": passed}
    return verdict


def main():
    print("关注池：%s" % "  ".join("%s(%s%s)" %
          (c, META[c]["name"], "新增" if c not in ORIGINAL5 else "原有") for c in UNIV))
    print("已从原五品种移除：%s" % "、".join(sorted(ORIGINAL5 - set(UNIV))))
    print("已新增：%s" % "、".join(c for c in UNIV if c not in ORIGINAL5))
    print("通用配置：进场MA34 / 离场MA26+吊灯3.0ATR / 独立雷达EXT2.6-PULL1.1\n")
    l1_l2()
    res = {"full_mix": l3("full", "mix"),
           "near_mix": l3("near", "mix"),
           "full_acc": l3("full", "acc"),
           "full_ret": l3("full", "ret")}
    print("\n" + "=" * 116)
    print("总判定")
    print("=" * 116)
    for tag, v in res.items():
        print("  %-10s 进场 %-10s 雷达 %-10s | 进场Δacc%+6.1fpp Δret%+7.2f | 雷达Δacc%+6.1fpp Δret%+7.2f" %
              (tag, "逐品种" if v["entry"]["passed"] else "通用",
               "逐品种" if v["radar"]["passed"] else "通用",
               v["entry"]["dAcc"], v["entry"]["dRet"],
               v["radar"]["dAcc"], v["radar"]["dRet"]))
    any_pass = any(v[k]["passed"] for v in res.values() for k in ("entry", "radar"))
    print("\n  最终：%s" % ("存在达标的逐品种方案，需进一步确认" if any_pass
                       else "所有搜索空间/目标下均未达标 → 全部品种使用通用配置"))
    res["finalUsePerSymbol"] = any_pass
    res["universe"] = UNIV
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("  结果已存", OUT)


if __name__ == "__main__":
    main()
