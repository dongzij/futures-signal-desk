# -*- coding: utf-8 -*-
"""v27「顶不做多 / 底不做空」极值偏离度回测（可复现）。

口径全部复用线上同一套代码，不另造回测：
  · K 线       data/{sym}.json 的 main 主力连续日线（10 年，7 品种）
  · ATR        cyc.atr_series(bars, 14)，Wilder，无未来函数
  · 进场候选   sig_pack.build() 的 pred / warn / mw / confirm / tf
  · 单笔出场   engine.exit_track()（MA26 + 吊灯 3ATR + v24 的 0.5ATR/连2根/0.8ATR 容错）
               叠加 30% 熔断（-3000 元），与 engine_v21.run 同口径
  · 执行       信号日 i 收盘生成，次日开盘 i+1 成交；偏离度只用 i 及之前数据

偏离度定义（信号日 i）：
  UP(N) = (close[i] - min(low[i-N+1..i])) / ATR[i]     距 N 日最低点涨了几个 ATR
  DN(N) = (max(high[i-N+1..i]) - close[i]) / ATR[i]    距 N 日最高点跌了几个 ATR
  UP%(N) / DN%(N) 为对应的百分比口径

铁律候选：做多信号若 UP(N) >= T 则拦截；做空信号若 DN(N) >= T 则拦截。
"""
import csv
import json
import os
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import engine
import engine_v21 as E21
import sig_pack as SP
import regime_lib

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(ROOT, "reports")
SYMS = ["MA", "HC", "Y", "SM", "FG", "P", "M"]
KINDS = ("pred", "warn", "mw", "confirm", "tf")
N_GRID = [30, 60, 90, 120]
T_GRID = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]
PCT_GRID = [5, 8, 10, 12, 15, 20]
TRAIN_END = "2023-11-30"     # 与 v22 定稿方案的训练/验证切点对齐
MAX_HOLD = 60
STOP_YUAN = 3000


def ext_series(bars, S, N):
    """滚动 N 根极值偏离度，全部只用 i 及之前的数据。"""
    n = len(bars)
    up_a = [None] * n
    dn_a = [None] * n
    up_p = [None] * n
    dn_p = [None] * n
    pos = [None] * n          # 价格在 N 日高低区间中的位置：0=最低 1=最高
    lows = [b["l"] for b in bars]
    highs = [b["h"] for b in bars]
    for i in range(N - 1, n):
        lo = min(lows[i - N + 1:i + 1])
        hi = max(highs[i - N + 1:i + 1])
        c = bars[i]["c"]
        a = S["atr"][i]
        if not a:
            continue
        up_a[i] = (c - lo) / a
        dn_a[i] = (hi - c) / a
        up_p[i] = (c / lo - 1) * 100 if lo else None
        dn_p[i] = (1 - c / hi) * 100 if hi else None
        pos[i] = (c - lo) / (hi - lo) if hi > lo else None
    return {"up": up_a, "dn": dn_a, "upPct": up_p, "dnPct": dn_p, "pos": pos}


def trade_R(d, bars, S, e):
    """与 engine_v21 同口径的单笔结果（返回以 ATR 为单位的净收益 R）。"""
    sd = 1 if e["side"] == "long" else -1
    ei = e["exec_i"]
    atr = S["atr"][e["i"]] or (bars[ei]["o"] * 0.01)
    if not atr or ei >= len(bars) - 1:
        return None
    end = min(len(bars), ei + MAX_HOLD + 1)
    r = engine.exit_track(bars[:end], S, ei, sd, atr,
                          ma_buf=0.5, ma_confirm=2, ma_fast=0.8)
    entry = bars[ei]["o"]
    exit_idx = r["exitIdx"] if r["exitIdx"] is not None else end - 1
    # 30% 熔断：盘中触及即成交，优先于收盘裁决
    stop_px = entry - STOP_YUAN / d["mult"] if sd == 1 else entry + STOP_YUAN / d["mult"]
    hit = None
    for j in range(ei, min(exit_idx, end - 1) + 1):
        if (bars[j]["l"] <= stop_px) if sd == 1 else (bars[j]["h"] >= stop_px):
            hit = j
            break
    if hit is not None and (r["exitIdx"] is None or hit <= r["exitIdx"]):
        px, why, ex = stop_px, "stop30", hit
    else:
        px = r["exitPx"] if r["exitPx"] is not None else bars[end - 1]["c"]
        why, ex = (r["why"] or "maxhold"), exit_idx
    return {"R": round((px - entry) * sd / atr, 4),
            "yuan": round((px - entry) * d["mult"] * sd),
            "why": why, "hold": ex - ei}


def build_rows():
    rows = []
    for sym in SYMS:
        d = json.load(open(os.path.join(ROOT, "data", sym + ".json"), encoding="utf-8"))
        bars = SP.bars_of(d)
        S = engine.make_S(bars)
        pack, meta = SP.build(d, bars, S)
        ext = {N: ext_series(bars, S, N) for N in N_GRID}
        for e in SP.all_entries(pack, KINDS):
            t = trade_R(d, bars, S, e)
            if t is None:
                continue
            i = e["i"]
            row = {"sym": sym, "date": e["date"], "kind": e["kind"],
                   "side": e["side"], "i": i, "seg": "train" if e["date"] <= TRAIN_END else "test"}
            row.update(t)
            for N in N_GRID:
                row["up%d" % N] = ext[N]["up"][i]
                row["dn%d" % N] = ext[N]["dn"][i]
                row["upPct%d" % N] = ext[N]["upPct"][i]
                row["dnPct%d" % N] = ext[N]["dnPct"][i]
                row["pos%d" % N] = ext[N]["pos"][i]
            rows.append(row)
        print("  %s: bars=%d  进场候选=%d" % (sym, len(bars), sum(1 for r in rows if r["sym"] == sym)))
    return rows


def stat(rs):
    if not rs:
        return {"n": 0, "win": 0.0, "pf": 0.0, "avgR": 0.0, "netR": 0.0}
    w = [r for r in rs if r > 0]
    l = [r for r in rs if r <= 0]
    gl = -sum(l)
    return {"n": len(rs),
            "win": round(len(w) / len(rs) * 100, 1),
            "pf": round(sum(w) / gl, 2) if gl > 0 else 99.0,
            "avgR": round(sum(rs) / len(rs), 3),
            "netR": round(sum(rs), 1)}


def deviation(row, N, T, mode="atr"):
    """该信号是否触犯铁律（做多看距低点涨幅；做空看距高点跌幅）。"""
    if mode == "atr":
        v = row["up%d" % N] if row["side"] == "long" else row["dn%d" % N]
    else:
        v = row["upPct%d" % N] if row["side"] == "long" else row["dnPct%d" % N]
    return v is not None and v >= T


def split_stat(rows, N, T, mode="atr", seg=None):
    rr = [r for r in rows if seg is None or r["seg"] == seg]
    blocked = [r["R"] for r in rr if deviation(r, N, T, mode)]
    kept = [r["R"] for r in rr if not deviation(r, N, T, mode)]
    return {"blocked": stat(blocked), "kept": stat(kept),
            "blockPct": round(len(blocked) / max(1, len(rr)) * 100, 1),
            "longBlocked": sum(1 for r in rr if r["side"] == "long" and deviation(r, N, T, mode)),
            "shortBlocked": sum(1 for r in rr if r["side"] == "short" and deviation(r, N, T, mode))}


def fmt(s):
    return "%3d / %5.1f%% / %5.2f / %+6.3f / %+7.1f" % (
        s["n"], s["win"], s["pf"], s["avgR"], s["netR"])


def main():
    print("=== 构建 7 品种 10 年进场事件样本 ===")
    rows = build_rows()
    print("总样本：%d 笔（train %d / test %d）" %
          (len(rows), sum(1 for r in rows if r["seg"] == "train"),
           sum(1 for r in rows if r["seg"] == "test")))
    print("基准：" + fmt(stat([r["R"] for r in rows])))

    # ---------- 表 A：N × T 全网格（ATR 口径） ----------
    print("\n=== 表 A：回看天数 N × 阈值 T（ATR）===")
    print("列：被拦组 n/胜率/盈亏比/均R/净R | 保留组 同 | 拦截率 | 多/空被拦数")
    grid = []
    for N in N_GRID:
        for T in T_GRID:
            s = split_stat(rows, N, T)
            grid.append((N, T, s))
            print("N=%3d T=%.1f  拦 %s | 留 %s | %4.1f%% | 多%3d 空%3d" %
                  (N, T, fmt(s["blocked"]), fmt(s["kept"]), s["blockPct"],
                   s["longBlocked"], s["shortBlocked"]))

    # ---------- 表 A2：百分比口径 ----------
    print("\n=== 表 A2：回看天数 N × 百分比阈值 ===")
    for N in N_GRID:
        for T in PCT_GRID:
            s = split_stat(rows, N, T, "pct")
            print("N=%3d T=%2d%%  拦 %s | 留 %s | %4.1f%%" %
                  (N, T, fmt(s["blocked"]), fmt(s["kept"]), s["blockPct"]))

    # ---------- 表 B：选定 N 的分箱单调性 ----------
    print("\n=== 表 B：偏离度分箱（N=60，看单调性）===")
    bins = [(-1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 99)]
    for lo, hi in bins:
        sub = [r["R"] for r in rows
               if (r["up60"] if r["side"] == "long" else r["dn60"]) is not None
               and lo <= (r["up60"] if r["side"] == "long" else r["dn60"]) < hi]
        print("  [%s,%s) ATR : %s" % (lo, hi, fmt(stat(sub))))

    # ---------- 表 C：ATR 口径样本外 ----------
    print("\n=== 表 C：ATR 口径样本外验证（train <= %s / test 之后）===" % TRAIN_END)
    print("N  T    train拦组                        test拦组                       test保留组")
    for N in N_GRID:
        for T in (3.0, 4.0, 5.0):
            a = split_stat(rows, N, T, seg="train")
            b = split_stat(rows, N, T, seg="test")
            print("N=%3d T=%.1f  %s | %s | %s" %
                  (N, T, fmt(a["blocked"]), fmt(b["blocked"]), fmt(b["kept"])))

    # ---------- 表 C2：百分比口径样本外 ----------
    print("\n=== 表 C2：百分比口径样本外验证 ===")
    print("N  T     train拦组                       test拦组                       test保留组")
    for N in N_GRID:
        for T in (10, 12, 15, 20):
            a = split_stat(rows, N, T, "pct", "train")
            b = split_stat(rows, N, T, "pct", "test")
            print("N=%3d T=%2d%%  %s | %s | %s" %
                  (N, T, fmt(a["blocked"]), fmt(b["blocked"]), fmt(b["kept"])))

    # ---------- 表 E：区间位置口径（最贴合「顶 / 底」字面含义）----------
    POS_T = [0.80, 0.85, 0.90, 0.95]

    def pos_dev(row, N, T):
        p = row.get("pos%d" % N)
        if p is None:
            return False
        return (p >= T) if row["side"] == "long" else (p <= 1 - T)

    print("\n=== 表 E：区间位置口径（做多 pos>=T / 做空 pos<=1-T）===")
    print("N  T     被拦组                          保留组                      拦截率")
    for N in N_GRID:
        for T in POS_T:
            bl = [r["R"] for r in rows if pos_dev(r, N, T)]
            kp = [r["R"] for r in rows if not pos_dev(r, N, T)]
            print("N=%3d T=%.2f  %s | %s | %4.1f%%" %
                  (N, T, fmt(stat(bl)), fmt(stat(kp)), len(bl) / len(rows) * 100))

    print("\n=== 表 E2：区间位置口径样本外 ===")
    for N in N_GRID:
        for T in (0.85, 0.90, 0.95):
            te = [r for r in rows if r["seg"] == "test"]
            bl = [r["R"] for r in te if pos_dev(r, N, T)]
            kp = [r["R"] for r in te if not pos_dev(r, N, T)]
            print("N=%3d T=%.2f  test拦 %s | test留 %s" %
                  (N, T, fmt(stat(bl)), fmt(stat(kp))))

    # ---------- 表 G：玻璃 2025-07-24 争议信号 ----------
    print("\n=== 表 G：用户指出的玻璃 FG 2025-07-25「进多」对应信号 ===")
    for r in rows:
        if r["sym"] == "FG" and r["date"] in ("2025-07-22", "2025-07-24"):
            print("  %s %s %-8s R=%+.2f 元=%+d 出场=%s 持有=%d" %
                  (r["date"], r["side"], r["kind"], r["R"], r["yuan"], r["why"], r["hold"]))
            for N in N_GRID:
                print("    N=%3d  距低点 +%.2f ATR / +%.1f%%  距高点 -%.2f ATR / -%.1f%%  区间位置 %.3f" %
                      (N, r["up%d" % N], r["upPct%d" % N],
                       r["dn%d" % N], r["dnPct%d" % N], r["pos%d" % N]))

    # ---------- 表 D：方向拆分 ----------
    print("\n=== 表 D：按方向拆开（N=60）===")
    for T in T_GRID:
        lg = [r["R"] for r in rows if r["side"] == "long" and deviation(r, 60, T)]
        sh = [r["R"] for r in rows if r["side"] == "short" and deviation(r, 60, T)]
        print("T=%.1f  高位做多 %s | 低位做空 %s" % (T, fmt(stat(lg)), fmt(stat(sh))))

    # ---------- 落盘 ----------
    os.makedirs(REPORTS, exist_ok=True)
    csv_path = os.path.join(REPORTS, "v27_极值偏离度_事件明细.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        cols = ["sym", "date", "seg", "kind", "side", "R", "yuan", "why", "hold"]
        for N in N_GRID:
            cols += ["up%d" % N, "dn%d" % N, "upPct%d" % N,
                     "dnPct%d" % N, "pos%d" % N]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x["date"], x["sym"])):
            w.writerow(r)
    print("\n事件明细已写：", csv_path)

    gpath = os.path.join(REPORTS, "v27_极值偏离度_网格表.csv")
    with open(gpath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["N", "T_ATR", "拦_n", "拦_胜率", "拦_盈亏比", "拦_均R", "拦_净R",
                    "留_n", "留_胜率", "留_盈亏比", "留_均R", "留_净R",
                    "拦截率", "多被拦", "空被拦"])
        for N, T, s in grid:
            a, b = s["blocked"], s["kept"]
            w.writerow([N, T, a["n"], a["win"], a["pf"], a["avgR"], a["netR"],
                        b["n"], b["win"], b["pf"], b["avgR"], b["netR"],
                        s["blockPct"], s["longBlocked"], s["shortBlocked"]])
    print("网格表已写：", gpath)
    json.dump({"rows": len(rows),
               "grid": [{"N": N, "T": T, **s} for N, T, s in grid]},
              open(os.path.join(REPORTS, "v27_极值偏离度_结果.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
