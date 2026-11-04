# -*- coding: utf-8 -*-
"""反转预测方法研究 第二阶段 —— 窗口扫描 / 阈值收紧 / 分档标定 / 月度与周线适用性

产出 model_v3.json（引擎与页面直接消费）。
"""
import itertools
import json

import mine_v3 as M

H_LIST = (2, 3, 5, 10)


def show_horizon(rows):
    print("\n===== 预测窗口扫描（日线）=====")
    print("%3s %8s" % ("H", "基准率"))
    for H in H_LIST:
        print("%3d %7.1f%%" % (H, M.base_rate(rows, H) * 100))


def strict_variants():
    """在强指标上做阈值收紧，寻找更高纯度档位。"""
    return [
        ("C3", "d34", "<=", 1.2, "距 MA34 ≤1.2 ATR"),
        ("C3s", "d34", "<=", 0.7, "距 MA34 ≤0.7 ATR"),
        ("C3x", "d34", "<=", 0.3, "距 MA34 ≤0.3 ATR"),
        ("C6", "ret5", "<=", 0.0, "5 日累计已反向"),
        ("C6s", "ret5", "<=", -1.0, "5 日累计反向≥1ATR"),
        ("C7", "ret10", "<=", 0.0, "10 日累计已反向"),
        ("C7s", "ret10", "<=", -1.5, "10 日累计反向≥1.5ATR"),
        ("C16", "ma10_sl", "<=", 0.0, "MA10 斜率转平/负"),
        ("C17", "macd", "<=", 0.0, "MACD 柱翻向"),
        ("C2", "d10", "<=", -0.5, "破 MA10 达 0.5 ATR"),
    ]


def evaluate(rows, conds, H, tag=""):
    out = []
    for c in conds:
        e = M.eval_cond(rows, c, H)
        if e:
            e["tag"] = c[0]
            out.append(e)
    return out


def deep_search(rows, cands, H, max_k=5, min_n=80):
    """候选收窄后做 k≤5 的穷举，找高纯度组合。"""
    names = [c[0] for c in cands]
    idx = {c[0]: c for c in cands}
    pos = {n: 1 << i for i, n in enumerate(names)}
    masks, labs, istr = [], [], []
    for r in rows:
        m = 0
        for n in names:
            if M.ok(r, idx[n]):
                m |= pos[n]
        masks.append(m)
        labs.append(1 if M.lab(r, H) else 0)
        istr.append(r["date"] < M.SPLIT)
    n_all = len(rows)
    ntr = sum(istr)
    nte = n_all - ntr
    base = sum(labs) / n_all
    btr = sum(l for l, t in zip(labs, istr) if t) / ntr if ntr else 0
    bte = sum(l for l, t in zip(labs, istr) if not t) / nte if nte else 0

    res = []
    for k in range(1, max_k + 1):
        for combo in itertools.combinations(names, k):
            cm = 0
            for n in combo:
                cm |= pos[n]
            cnt = hit = ctr = htr = cte = hte = 0
            for m, l, t in zip(masks, labs, istr):
                if (m & cm) == cm:
                    cnt += 1
                    hit += l
                    if t:
                        ctr += 1
                        htr += l
                    else:
                        cte += 1
                        hte += l
            if cnt < min_n or ctr < 20 or cte < 20:
                continue
            res.append({"combo": combo, "n": cnt, "p": hit / cnt,
                        "ptr": htr / ctr, "pte": hte / cte,
                        "base": base, "ltr": htr / ctr / btr if btr else 0,
                        "lte": hte / cte / bte if bte else 0,
                        "lift": (hit / cnt) / base if base else 0})
    for r in res:
        r["minlift"] = min(r["ltr"], r["lte"])
    res.sort(key=lambda x: (-x["minlift"], -x["n"]))
    return res


def main():
    rows_d, prep_d = M.load("D")
    rows_w, prep_w = M.load("W")
    print("日线样本 %d，周线样本 %d" % (len(rows_d), len(rows_w)))

    show_horizon(rows_d)

    cands = strict_variants()
    print("\n===== 阈值收紧对照（日线，H=5，基准率 %.1f%%）=====" % (M.base_rate(rows_d, 5) * 100))
    print("%-5s %-24s %6s %8s %6s %6s %6s" % ("条件", "含义", "样本", "反转率", "lift", "训练", "盲测"))
    ev = evaluate(rows_d, cands, 5)
    for e in ev:
        print("%-5s %-24s %5d %7.1f%% %6.2f %6.2f %6.2f" %
              (e["tag"], e["cond"][4], e["n"], e["p"] * 100, e["lift"], e["ltr"] or 0, e["lte"] or 0))

    print("\n===== 深度组合搜索（H=5，k≤5，样本≥80）=====")
    deep = deep_search(rows_d, cands, 5, 5, 80)
    for b in deep[:15]:
        print("  %-24s n=%4d 反转率%5.1f%% lift%5.2f | 训练%5.1f%% 盲测%5.1f%% (lift %.2f/%.2f)" %
              ("+".join(b["combo"]), b["n"], b["p"] * 100, b["lift"],
               b["ptr"] * 100, b["pte"] * 100, b["ltr"], b["lte"]))

    # 高纯度档：优先选 lift 高且样本 ≥100 的组合
    pool = [b for b in deep if b["n"] >= 100]
    top = pool[0] if pool else deep[0]
    print("\n高纯度组合（样本≥100）：%s  n=%d 反转率%.1f%% 训练%.1f%% 盲测%.1f%%" %
          ("+".join(top["combo"]), top["n"], top["p"] * 100, top["ptr"] * 100, top["pte"] * 100))

    # 逐级组合：从少到多，观察概率单调性
    print("\n===== 阶梯（逐级加条件）=====")
    ladder = []
    for k in range(1, len(top["combo"]) + 1):
        sub = "+".join(top["combo"][:k])
        cm = [c for c in cands if c[0] in top["combo"][:k]]
        hit = [r for r in rows_d if all(M.ok(r, c) for c in cm)]
        if not hit:
            continue
        p = sum(M.lab(r, 5) for r in hit) / len(hit)
        tr = [r for r in hit if r["date"] < M.SPLIT]
        te = [r for r in hit if r["date"] >= M.SPLIT]
        pt = sum(M.lab(r, 5) for r in tr) / len(tr) * 100 if tr else 0
        pe = sum(M.lab(r, 5) for r in te) / len(te) * 100 if te else 0
        ladder.append({"k": k, "combo": top["combo"][:k], "n": len(hit),
                       "p": p, "ptr": pt / 100, "pte": pe / 100})
        print("  k=%d %-22s n=%4d 反转率%5.1f%% 训练%5.1f%% 盲测%5.1f%%" %
              (k, sub, len(hit), p * 100, pt, pe))

    # 周线适用性
    print("\n===== 周线适用性（同一组条件，H=3 周）=====")
    for k in range(1, len(top["combo"]) + 1):
        cm = [c for c in cands if c[0] in top["combo"][:k]]
        hit = [r for r in rows_w if all(M.ok(r, c) for c in cm)]
        if len(hit) < 30:
            print("  k=%d 样本不足(%d)" % (k, len(hit)))
            continue
        p = sum(M.lab(r, 3) for r in hit) / len(hit)
        print("  k=%d %-22s n=%4d 3周内反转 %5.1f%%（基准 %.1f%%）" %
              (k, "+".join(top["combo"][:k]), len(hit), p * 100, M.base_rate(rows_w, 3) * 100))

    # 分品种稳定性（用最终组合）
    print("\n===== 分品种稳定性（最终组合，H=5）=====")
    final_c = [c for c in cands if c[0] in top["combo"]]
    for code in M.CODES:
        sub = [r for r in rows_d if r["sym"] == code and all(M.ok(r, c) for c in final_c)]
        pool_s = [r for r in rows_d if r["sym"] == code]
        if len(sub) < 10:
            print("  %-3s 样本不足(%d)" % (code, len(sub)))
            continue
        p = sum(M.lab(r, 5) for r in sub) / len(sub)
        b = M.base_rate(pool_s, 5)
        print("  %-3s n=%3d 反转率%5.1f%% 基准%5.1f%% lift%5.2f" %
              (code, len(sub), p * 100, b * 100, p / b if b else 0))

    # 年度分布
    print("\n===== 年度分布（最终组合）=====")
    byyear = {}
    for r in rows_d:
        if all(M.ok(r, c) for c in final_c):
            y = r["date"][:4]
            d = byyear.setdefault(y, [0, 0])
            d[0] += 1
            d[1] += 1 if M.lab(r, 5) else 0
    for y in sorted(byyear):
        n, h = byyear[y]
        print("  %s  %3d 次  反转 %5.1f%%" % (y, n, h / n * 100))

    # 提前量
    ld = M.leads(rows_d, top["combo"], 5)
    print("\n提前量：样本 %d 中位 %d 根，≤1根 %.0f%%，≤2根 %.0f%%，≤3根 %.0f%%" %
          (len(ld), sorted(ld)[len(ld) // 2],
           sum(1 for x in ld if x <= 1) / len(ld) * 100,
           sum(1 for x in ld if x <= 2) / len(ld) * 100,
           sum(1 for x in ld if x <= 3) / len(ld) * 100))

    model = {
        "H": 5,
        "base": round(M.base_rate(rows_d, 5), 4),
        "final": [{"k": c[0], "f": c[1], "op": c[2], "th": c[3], "t": c[4]} for c in final_c],
        "ladder": [{"k": l["k"], "p": round(l["p"], 4),
                    "ptr": round(l["ptr"], 4), "pte": round(l["pte"], 4),
                    "n": l["n"], "combo": list(l["combo"])} for l in ladder],
        "n_final": int(sum(1 for r in rows_d if all(M.ok(r, c) for c in final_c))),
        "p_final": round(top["p"], 4),
        "weekly": {"H": 3, "base": round(M.base_rate(rows_w, 3), 4)},
    }
    json.dump(model, open("model_v3.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n已保存 model_v3.json")
    print(json.dumps({k: v for k, v in model.items() if k != "final"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
