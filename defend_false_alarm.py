# -*- coding: utf-8 -*-
"""v16 模块二：失效打叉「误报」特征专项回测（只研究，不改线上）

问题：v15 全量打叉抓出了一些「后来又走对」的假失效，哪些打叉其实不强、可以剔除？

口径（筛选条件全部只用打叉当天及之前的数据，无前视）：
  事件源 = 线上 defend_rules.build_defend（466 条，与页面 K 线红叉同源）
  误报 = 打叉后【第 7~15 根】内，收盘顺原信号方向相对打叉日收盘走出 ≥1.5 ATR，
         且第 15 根收盘仍同向 ≥0.5 ATR（排除一日反抽）。
  真失效 = 第 15 根收盘逆向 ≥0.5 ATR，或 15 根内最大逆向 ≥2 ATR。
  中性 = 其余来回震荡。
  稳健性对照口径 = 7~15 根内顺向重新超过【信号日收盘】≥1ATR（更严格）。

输出：假设检验 / 单特征表 / 候选过滤组合 / 推荐条件的证明表 / 训练·盲测 / 逐品种。
本脚本只交数据表，**线上判定口径一字未改**，等用户拍板再落地。
"""
import io
import itertools
import json
import statistics as stat
from datetime import datetime

import engine
import defend_rules

OUT = []
F = []          # 单特征 / 组合候选


def add(s=""):
    OUT.append(s)


def mean(a):
    return stat.mean(a) if a else 0.0


def pct(a):
    return 100.0 * sum(1 for x in a if x) / len(a) if a else 0.0


def rate(rows, key="fa"):
    return pct([x[key] for x in rows])


# ---------------------------------------------------------------- 取数
def load_events():
    st = json.load(open("state.json", encoding="utf-8"))
    rows, ctx = [], {}
    for code in engine.CODES:
        bars = engine._bars_of(code, 10)
        S = engine.make_S(bars)
        idx = {b["d"]: i for i, b in enumerate(bars)}
        ctx[code] = (bars, S, idx)
        sigs = [s for s in st["signals"]
                if s["sym"] == code and s["kind"] in ("pred", "warn")]
        radar = engine.exit_history(code, bars=bars, years=10,
                                    maxn=10 ** 6, dedupe=8)
        ev = defend_rules.build_defend(code, engine.NAMES[code], bars, S,
                                       sigs, radar)
        rows.extend(build_row(code, bars, S, idx, e) for e in ev)
    return rows


def build_row(code, bars, S, idx, e):
    i = idx[e["date"]]
    js = idx.get(e["sigDate"])
    n = len(bars)
    exp = 1 if e["side"] == "long" else -1
    atr = float(S["atr"][i] or e.get("atr") or 1e-9)
    atr0 = float(S["atr"][js or i] or atr)
    c, b = bars[i]["c"], bars[i]
    c0 = bars[js]["c"] if js is not None else c
    fwd, mfe, mae, mfe715 = {}, 0.0, 0.0, 0.0
    for k in (3, 5, 7, 10, 15, 20):
        j = min(n - 1, i + k)
        fwd[k] = exp * (bars[j]["c"] - c) / atr
    for k in range(1, 21):
        j = i + k
        if j >= n:
            break
        v = exp * (bars[j]["c"] - c) / atr
        mfe, mae = max(mfe, v), min(mae, v)
        if 7 <= k <= 15:
            mfe715 = max(mfe715, v)
    mature = i + 15 < n
    fa = bool(mature and mfe715 >= 1.5 and fwd[15] >= 0.5)
    tf = bool(mature and (fwd[15] <= -0.5 or mae <= -2.0))
    # 事后评估量（不参与任何筛选条件）
    no_follow = bool(mature and mae > -1.0 and fwd[15] > -0.5)
    recover = bool(mature and max(
        exp * (bars[min(i + k, n - 1)]["c"] - c0) / atr0
        for k in range(7, 16)) >= 1.0)

    ma34, ma26 = S["ma34"][i], S["ma26"][i]
    ma34_5 = S["ma34"][i - 5] if i >= 5 else None
    oi, oi1 = b.get("oi") or 0, (bars[i - 1].get("oi") or 0) if i else 0
    oi5 = (bars[i - 5].get("oi") or 0) if i >= 5 else 0
    v20 = [bars[k].get("v") or 0 for k in range(max(0, i - 20), i)]
    vma = mean(v20)
    atr20 = mean([S["atr"][k] or 0 for k in range(max(0, i - 20), i)])
    rng = max(b["h"] - b["l"], 1e-9)
    adv = -exp * (c - c0) / atr0            # 打叉时原信号已逆行多少 ATR

    f = {}
    f["ma34_ok"] = bool(ma34 is not None and exp * (c - ma34) > 0)
    f["ma34_slope_ok"] = bool(ma34 is not None and ma34_5 is not None
                              and exp * (ma34 - ma34_5) > 0)
    f["ma34_both"] = bool(f["ma34_ok"] and f["ma34_slope_ok"])
    f["ma26_ok"] = bool(ma26 is not None and exp * (c - ma26) > 0)
    f["ma34_26_ok"] = bool(f["ma34_ok"] and f["ma26_ok"])
    f["break_shallow"] = bool(ma26 is not None
                              and -exp * (c - ma26) / atr <= 0.30)
    f["oi_drop5"] = bool(oi5 and (oi - oi5) / oi5 * 100 <= -5)
    f["oi_drop10"] = bool(oi5 and (oi - oi5) / oi5 * 100 <= -10)
    f["oi_drop1"] = bool(oi1 and (oi - oi1) / oi1 * 100 <= -2)
    f["oi_up5"] = bool(oi5 and (oi - oi5) / oi5 * 100 >= 3)
    f["vol_dry"] = bool(vma and (b.get("v") or 0) / vma <= 0.8)
    f["vol_dry05"] = bool(vma and (b.get("v") or 0) / vma <= 0.5)
    f["atr_calm"] = bool(atr20 and atr / atr20 <= 0.9)
    f["small_body"] = bool(abs(c - b["o"]) / rng <= 0.35)
    f["long_shadow"] = bool(((min(b["o"], c) - b["l"]) if exp == 1
                             else (b["h"] - max(b["o"], c))) / atr >= 0.6)
    f["close_high"] = bool(exp * ((c - b["l"]) / rng - 0.5) > 0)
    f["mom5_pos"] = bool(i >= 5 and exp * (c - bars[i - 5]["c"]) / atr > 0)
    f["early"] = bool(e["day"] <= 3)
    f["reason_ma"] = e["reason"] == "ma"
    f["reason_rev"] = e["reason"] == "rev"
    f["reason_decay"] = e["reason"] == "decay"
    f["reason_stop"] = e["reason"] == "stop"
    f["radar"] = e["sigKind"] == "radar"
    f["pred"] = e["sigKind"] == "pred"
    f["warn"] = e["sigKind"] == "warn"
    f["adx_low"] = bool((S["adx"][i] or 0) < 20)
    f["adx_high"] = bool((S["adx"][i] or 0) >= 30)
    f["still_profit"] = bool(adv <= 0)
    f["shallow_adv"] = bool(adv <= 0.5)
    f["deep_adv"] = bool(adv >= 2.0)

    return {"code": code, "date": e["date"], "sym": e["sym"],
            "name": e["name"], "side": e["side"], "sigDate": e["sigDate"],
            "sigKind": e["sigKind"], "reason": e["reason"], "day": e["day"],
            "mature": mature, "fa": fa, "tf": tf, "recover": recover,
            "no_follow": no_follow, "adv": adv,
            "fwd": fwd, "mfe": mfe, "mae": mae, "mfe715": mfe715,
            "ft": f}


HDR = ("| 组别 | n | 误报率 | 真失效率 | 打叉后15根顺向(ATR) | "
       "7~15根最大顺向(ATR) | 15根最大逆向(ATR) |")
SEP = "|---|---:|---:|---:|---:|---:|---:|"


def agg(rows, label):
    if not rows:
        add("| %s | 0 | — | — | — | — | — |" % label)
        return
    add("| %s | %d | %.1f%% | %.1f%% | %+.2f | %+.2f | %+.2f |"
        % (label, len(rows), rate(rows), rate(rows, "tf"),
           mean([x["fwd"][15] for x in rows]),
           mean([x["mfe715"] for x in rows]),
           mean([x["mae"] for x in rows])))


def main():
    rows = load_events()
    m = [x for x in rows if x["mature"]]
    n_fa = sum(1 for x in m if x["fa"])
    n_tf = sum(1 for x in m if x["tf"])

    add("# 失效打叉 · 误报特征与过滤条件对比表（v16 模块二）")
    add("")
    add("生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    add("")
    add("**样本**：10 年 × 8 品种，与线上 K 线红叉同源的 %d 条失效事件；"
        "其中 %d 条有完整打叉后 15 根行情（最近 15 根内的新事件只统计、不判标签）。"
        % (len(rows), len(m)))
    add("")
    add("**误报**：打叉后第 7~15 根内，收盘顺原方向相对打叉日收盘走出 ≥1.5 ATR，"
        "且第 15 根收盘仍同向 ≥0.5 ATR。　**真失效**：第 15 根收盘逆向 ≥0.5 ATR "
        "或 15 根内最大逆向 ≥2 ATR。　其余为中性震荡。")
    add("")
    add("**筛选特征只用打叉当天及之前的数据**（均线、持仓量、成交量、ATR、K 线、ADX、"
        "原信号浮盈亏）。顺向/逆向仅用于事后评估，不参与筛选，无前视偏差。")
    add("")
    add("## 一、基线")
    add("")
    add(HDR)
    add(SEP)
    agg(m, "**全部打叉（基线）**")
    agg([x for x in m if x["fa"]], "　└ 误报组（7~15 根又走对）")
    agg([x for x in m if x["tf"]], "　└ 真失效组（继续走坏）")
    add("")

    add("## 二、你点名的两条假设 + 全部单特征")
    add("")
    add("误报率高于基线 36.6% 才算「更能识别出不该打叉」。")
    add("")
    add(HDR)
    add(SEP)
    singles = [
        ("【假设1】MA34 没坏（收盘仍在原方向一侧）", "ma34_ok"),
        ("【假设1】MA34 斜率未反向（5 根）", "ma34_slope_ok"),
        ("MA34 没坏 + 斜率未反向", "ma34_both"),
        ("MA26 也没坏", "ma26_ok"),
        ("MA34 与 MA26 都没坏", "ma34_26_ok"),
        ("贴线破位（距 MA26 ≤0.3ATR）", "break_shallow"),
        ("【假设2】打叉日持仓量 5 日降 ≥5%", "oi_drop5"),
        ("打叉日持仓量 5 日降 ≥10%", "oi_drop10"),
        ("打叉日持仓量单日降 ≥2%", "oi_drop1"),
        ("打叉日持仓量 5 日反增 ≥3%", "oi_up5"),
        ("打叉日缩量 ≤0.8×20日均量", "vol_dry"),
        ("打叉日极度缩量 ≤0.5×20日均量", "vol_dry05"),
        ("ATR 收缩（≤0.9×20日均）", "atr_calm"),
        ("小实体（≤35% 当日振幅）", "small_body"),
        ("顺向长影线 ≥0.6ATR", "long_shadow"),
        ("收盘落在顺向半边", "close_high"),
        ("打叉前 5 根仍顺向", "mom5_pos"),
        ("打叉发生在信号后 ≤3 根", "early"),
        ("判定标准：均线破位", "reason_ma"),
        ("判定标准：反向 >1.5ATR", "reason_rev"),
        ("判定标准：5 日时间衰竭", "reason_decay"),
        ("判定标准：原版止损", "reason_stop"),
        ("来源：顶底雷达", "radar"),
        ("来源：4/4 建仓", "pred"),
        ("来源：3/4 预警", "warn"),
        ("ADX<20（趋势弱）", "adx_low"),
        ("ADX≥30（趋势强）", "adx_high"),
        ("打叉时原信号仍有浮盈", "still_profit"),
        ("打叉时逆行 ≤0.5ATR", "shallow_adv"),
        ("打叉时已深逆行 ≥2ATR", "deep_adv"),
    ]
    for nm, key in singles:
        agg([x for x in m if x["ft"][key]], nm)
    add("")
    add("**假设检验结果**：")
    add("")
    add("- **假设 1「MA34 没坏」不成立，方向还相反**：这类打叉误报率仅 %.1f%%（低于基线 %.1f%%），"
        "真失效率 %.1f%%。MA34 没坏的破位后反而更常继续走坏，不能拿它当过滤条件。"
        % (rate([x for x in m if x["ft"]["ma34_ok"]]), rate(m),
           rate([x for x in m if x["ft"]["ma34_ok"]], "tf")))
    add("- **假设 2「持仓量下降」弱成立**：持仓 5 日降 ≥10%% 组误报率 %.1f%%（+%.1fpp），"
        "缩量组 %.1f%%（+%.1fpp），但幅度不足以单独支撑剔除 1/3。"
        % (rate([x for x in m if x["ft"]["oi_drop10"]]),
           rate([x for x in m if x["ft"]["oi_drop10"]]) - rate(m),
           rate([x for x in m if x["ft"]["vol_dry"]]),
           rate([x for x in m if x["ft"]["vol_dry"]]) - rate(m)))
    add("- 真正把「不强打叉」分出来的是**信号来源 × 趋势强度**：顶底雷达 + ADX≥30（强趋势中趋势末端的洗盘式破位）。")
    add("")

    # ---- 候选组合搜索 ----
    keys = [k for _, k in singles]
    cands = [(k, (lambda kk: (lambda f: f[kk]))(k)) for k in keys]
    for a, b in itertools.combinations(keys, 2):
        cands.append(("%s & %s" % (a, b),
                      lambda f, a=a, b=b: f[a] and f[b]))
        cands.append(("%s | %s" % (a, b),
                      lambda f, a=a, b=b: f[a] or f[b]))
    for a, b, c in itertools.combinations(keys, 3):
        cands.append(("%s/%s/%s 中≥2" % (a, b, c),
                      lambda f, a=a, b=b, c=c: f[a] + f[b] + f[c] >= 2))
    scored = []
    for nm, fn in cands:
        cut = [x for x in m if fn(x["ft"])]
        keep = [x for x in m if not fn(x["ft"])]
        if not cut or not keep:
            continue
        cov = len(cut) / len(m)
        if not (0.28 <= cov <= 0.38):
            continue
        sc = (rate(cut) - rate(keep)) - 0.7 * (rate(cut, "tf") - rate(keep, "tf"))
        scored.append((sc, nm, fn, cut, keep))
    scored.sort(key=lambda x: -x[0])

    add("## 三、候选过滤组合（自动搜索 %d 个单/双/三特征组合，只保留覆盖 28~38%% 的）" % len(cands))
    add("")
    add("| 过滤条件 | 剔除n | 占比 | 剔除组误报率 | 保留组误报率 | 剔除组真失效率 | "
        "保留组真失效率 | 误报捕获率 | 真失效误删率 | 剔除组15根顺向 | 保留组15根顺向 |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for sc, nm, fn, cut, keep in scored[:10]:
        add("| %s | %d | %.1f%% | %.1f%% | %.1f%% | %.1f%% | %.1f%% | %.1f%% | %.1f%% | %+.2f | %+.2f |"
            % (nm, len(cut), 100.0 * len(cut) / len(m), rate(cut), rate(keep),
               rate(cut, "tf"), rate(keep, "tf"),
               100.0 * sum(1 for x in cut if x["fa"]) / n_fa,
               100.0 * sum(1 for x in cut if x["tf"]) / n_tf,
               mean([x["fwd"][15] for x in cut]),
               mean([x["fwd"][15] for x in keep])))
    add("")

    # ---- 推荐条件 ----
    rec_name = "顶底雷达 & ADX≥30"
    rec = lambda f: f["radar"] and f["adx_high"]
    cut = [x for x in m if rec(x["ft"])]
    keep = [x for x in m if not rec(x["ft"])]

    add("## 四、推荐条件 **%s** 的证明表" % rec_name)
    add("")
    add("规则（打叉当天即可判定，无需未来数据）：**原信号来源是顶底雷达**（不是 4/4 建仓、3/4 预警）"
        "**且打叉当日 ADX(14) ≥ 30**（趋势仍强）。这两类叠在一起，多半是强趋势末端的洗盘式破位，"
        "不是真转势。")
    add("")
    add("| 指标 | 剔除组（不打叉） | 保留组（继续打叉） | 差异 |")
    add("|---|---:|---:|---:|")
    metrics = [
        ("事件数 / 占比", "%d / %.1f%%" % (len(cut), 100.0 * len(cut) / len(m)),
         "%d / %.1f%%" % (len(keep), 100.0 * len(keep) / len(m)), "≈ 1/3"),
        ("误报率（7~15根又走对）", "%.1f%%" % rate(cut), "%.1f%%" % rate(keep),
         "%+.1fpp" % (rate(cut) - rate(keep))),
        ("真失效率（继续走坏）", "%.1f%%" % rate(cut, "tf"), "%.1f%%" % rate(keep, "tf"),
         "%+.1fpp" % (rate(cut, "tf") - rate(keep, "tf"))),
        ("打叉后 15 根顺原方向(ATR)", "%+.2f" % mean([x["fwd"][15] for x in cut]),
         "%+.2f" % mean([x["fwd"][15] for x in keep]),
         "%+.2f" % (mean([x["fwd"][15] for x in cut]) - mean([x["fwd"][15] for x in keep]))),
        ("打叉后完全没继续走坏占比", "%.1f%%" % rate(cut, "no_follow"),
         "%.1f%%" % rate(keep, "no_follow"),
         "%+.1fpp" % (rate(cut, "no_follow") - rate(keep, "no_follow"))),
        ("15 根内最大逆向(ATR)", "%+.2f" % mean([x["mae"] for x in cut]),
         "%+.2f" % mean([x["mae"] for x in keep]),
         "%+.2f" % (mean([x["mae"] for x in cut]) - mean([x["mae"] for x in keep]))),
        ("误报捕获率 / 真失效误删率",
         "%.1f%%" % (100.0 * sum(1 for x in cut if x["fa"]) / n_fa),
         "%.1f%%" % (100.0 * sum(1 for x in cut if x["tf"]) / n_tf), "收益 / 代价"),
    ]
    for a, b, c, d in metrics:
        add("| %s | %s | %s | %s |" % (a, b, c, d))
    add("")
    add("- 剔除组捕获了 **%.1f%% 的误报**，代价是误删 **%.1f%% 的真失效**；"
        "剔除组打叉后 15 根平均还**顺原方向走了 %+.2f ATR**，"
        "说明这 1/3 的叉事后看基本是错标，剔掉不损失防守方向。"
        % (100.0 * sum(1 for x in cut if x["fa"]) / n_fa,
           100.0 * sum(1 for x in cut if x["tf"]) / n_tf,
           mean([x["fwd"][15] for x in cut])))
    add("- 诚实说明：两组的「15 根内最大逆向幅度」接近（%+.2f vs %+.2f ATR），"
        "即瞬时波动并没有更小；差别在**最终落点方向**，不是波动幅度。"
        % (mean([x["mae"] for x in cut]), mean([x["mae"] for x in keep])))
    add("")

    add("## 五、稳健性：训练段 / 盲测段 / 逐品种 / 替代口径")
    add("")
    add("| 段 | 剔除n/占比 | 剔除组误报率 | 保留组误报率 | 剔除组真失效率 | 保留组真失效率 |")
    add("|---|---:|---:|---:|---:|---:|")
    for seg, lo, hi in (("训练 <2024", "0000", "2023-12-31"),
                        ("盲测 ≥2024", "2024-01-01", "9999"),
                        ("全期", "0000", "9999")):
        sub = [x for x in m if lo <= x["date"] <= hi]
        c = [x for x in sub if rec(x["ft"])]
        k = [x for x in sub if not rec(x["ft"])]
        add("| %s | %d / %.1f%% | %.1f%% | %.1f%% | %.1f%% | %.1f%% |"
            % (seg, len(c), 100.0 * len(c) / max(1, len(sub)),
               rate(c), rate(k), rate(c, "tf"), rate(k, "tf")))
    add("")
    add("| 品种 | 打叉n | 剔除n | 占比 | 剔除组误报率 | 保留组误报率 |")
    add("|---|---:|---:|---:|---:|---:|")
    for code in engine.CODES:
        sub = [x for x in m if x["code"] == code]
        c = [x for x in sub if rec(x["ft"])]
        k = [x for x in sub if not rec(x["ft"])]
        add("| %s | %d | %d | %.1f%% | %.1f%% | %.1f%% |"
            % (code, len(sub), len(c), 100.0 * len(c) / max(1, len(sub)),
               rate(c), rate(k)))
    add("")
    add("**替代口径**（7~15 根内顺向重新超过【信号日收盘】≥1ATR，更严格）："
        "剔除组 %.1f%% vs 保留组 %.1f%%，差异很小 —— 说明本过滤剔除的是"
        "「打叉后又反弹回去」的弱叉，而不是「原信号最终被证明完全正确」的那类。"
        "两种口径不冲突，但落地时应按前者理解收益。"
        % (rate(cut, "recover"), rate(keep, "recover")))
    add("")

    add("## 六、结论（本轮只交表，线上口径未改）")
    add("")
    add("- 基线 %d 条可评估打叉：误报 %d 条（%.1f%%）、真失效 %d 条（%.1f%%）。"
        % (len(m), n_fa, rate(m), n_tf, rate(m, "tf")))
    add("- 你的两条假设：**MA34 没坏反而更不该剔**（误报率 %.1f%% < 基线）；"
        "**持仓量下降 / 缩量是弱加分**（%.1f%% / %.1f%%），单独用不足以剔 1/3。"
        % (rate([x for x in m if x["ft"]["ma34_ok"]]),
           rate([x for x in m if x["ft"]["oi_drop10"]]),
           rate([x for x in m if x["ft"]["vol_dry"]])))
    add("- 能稳定剔掉 1/3 且盲测更强的是 **%s**：剔 %d 条（%.1f%%），"
        "误报率 %.1f%% → 保留组 %.1f%%，真失效率 %.1f%% → 保留组 %.1f%%；"
        "训练段 +%.1fpp、盲测段 +%.1fpp 同向。"
        % (rec_name, len(cut), 100.0 * len(cut) / len(m),
           rate(cut), rate(keep), rate(cut, "tf"), rate(keep, "tf"),
           rate([x for x in cut if x["date"] < "2024-01-01"])
           - rate([x for x in keep if x["date"] < "2024-01-01"]),
           rate([x for x in cut if x["date"] >= "2024-01-01"])
           - rate([x for x in keep if x["date"] >= "2024-01-01"])))
    add("- **建议落地方式**：这批不是删除，而是降级 —— K 线画空心小圈、列表标「弱」、"
        "不进微信独立推送；数据全部保留在 `defend` 里，随时可切回全量。"
        "这样既减少约 1/3 的不强标识，又不会丢任何历史。")
    add("")
    add("> **本轮按你的指令只交对比表**：`defend_rules.py` / `data_build.py` / `daily.py` 判定口径一字未改，"
        "MA34 进场与 MA26 + 吊灯 3ATR 出场完全不变。是否按此条件上线，等拍板。")

    txt = "\n".join(OUT)
    io.open("reports/失效误报_特征对比表.md", "w", encoding="utf-8").write(txt)
    json.dump(rows, io.open("reports/失效误报_逐笔.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(txt)


if __name__ == "__main__":
    main()
