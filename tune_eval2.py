# -*- coding: utf-8 -*-
"""逐品种配置 vs 通用配置 —— 第三步：补两层稳健性检验

L4 带最小训练样本约束的 walk-forward：训练数据不够就回退通用（更贴近实盘，不给小样本硬调参）
L5 自适应混合方案（逐年前瞻，绝无事后挑品种）：
     每个测试年，先只用历史数据判断「该品种是否值得单独配」——
     训练段上逐品种参数的准确率比通用高 ≥3pp 且单笔收益更高，才启用，否则用通用。
   这检验「大部分品种通用 + 少数品种专属」是否比全通用更好。
"""
import json
import os

import tune_eval as E

OUT = E.OUT
MIN_TRAIN = {"entry": 6, "radar": 12}
MARGIN = 3.0


def run(mode, space, objective):
    res = {}
    for kind in ("entry", "radar"):
        gkey = E.G_ENTRY if kind == "entry" else E.G_RADAR
        ra, rb, n_tuned = [], [], 0
        for c in E.UNIV:
            for y in E.wf_years(c):
                lo, hi = "%d-01-01" % y, "%d-01-01" % (y + 1)
                key, _ = E.choose(kind, c, None, lo, objective, space)
                sel = gkey
                if key:
                    mt = E.met(kind, E.rows_of(kind, c, key, None, lo))
                    mg = E.met(kind, E.rows_of(kind, c, gkey, None, lo))
                    enough = mt["n"] >= MIN_TRAIN[kind] and mg["n"] >= MIN_TRAIN[kind]
                    if mode == "mintrain":
                        use = enough
                    else:   # adaptive
                        use = (enough and mt["win"] >= mg["win"] + MARGIN
                               and mt["avg"] > mg["avg"])
                    if use:
                        sel = key
                        n_tuned += 1
                ra += E.collect(kind, c, sel, lo, hi)
                rb += E.collect(kind, c, gkey, lo, hi)
        A, Bg = E.agg(ra), E.agg(rb)
        dacc, cacc, dret, cret = E.boot_diff(ra, rb)
        passed = (dacc >= E.TH_ACC and dret > 0 and cacc[0] > 0 and cret[0] > 0)
        res[kind] = {"tuned": A, "generic": Bg, "nTunedYears": n_tuned,
                     "dAcc": round(dacc, 2), "accCI": [round(x, 2) for x in cacc],
                     "dRet": round(dret, 4), "retCI": [round(x, 4) for x in cret],
                     "passed": passed}
    return res


def main():
    print("=" * 116)
    print("L4/L5 稳健性补充检验（沿用同一判定阈值：准确率≥+3pp、收益>0、CI下界>0）")
    print("=" * 116)
    combos = [("L4 mintrain full mix", "mintrain", "full", "mix"),
              ("L4 mintrain near mix", "mintrain", "near", "mix"),
              ("L5 adaptive full mix", "adaptive", "full", "mix"),
              ("L5 adaptive near mix", "adaptive", "near", "mix"),
              ("L5 adaptive full acc", "adaptive", "full", "acc"),
              ("L5 adaptive full ret", "adaptive", "full", "ret")]
    allres = {}
    for tag, mode, space, obj in combos:
        r = run(mode, space, obj)
        allres[tag] = r
        print("\n  %s" % tag)
        for kind in ("entry", "radar"):
            v = r[kind]
            print("    %-6s 逐品种 %s | 通用 %s | Δacc%+6.1fpp(CI %.1f~%.1f) Δret%+6.2f(CI %+.2f~%+.2f) | %s%s" %
                  (kind, E.fmt(v["tuned"]), E.fmt(v["generic"]),
                   v["dAcc"], v["accCI"][0], v["accCI"][1],
                   v["dRet"], v["retCI"][0], v["retCI"][1],
                   "启用单独参数的品种年=%d  " % v["nTunedYears"] if mode == "adaptive" else "",
                   "★启用" if v["passed"] else "保持通用"))
    D = json.load(open(OUT, encoding="utf-8"))
    D["robust"] = allres
    any_pass = any(v[k]["passed"] for v in allres.values() for k in ("entry", "radar"))
    D["finalUsePerSymbol"] = D["finalUsePerSymbol"] or any_pass
    json.dump(D, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n  补充检验是否存在达标方案：%s" % ("是" if any_pass else "否"))
    print("  最终结论：%s" % ("启用逐品种配置" if D["finalUsePerSymbol"]
                          else "8 个品种全部使用通用配置"))


if __name__ == "__main__":
    main()
