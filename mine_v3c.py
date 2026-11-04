# -*- coding: utf-8 -*-
"""第三阶段：提前量 / k=4 建仓收益验证 / 生成最终 model_v3.json"""
import json
import statistics as st

import build
import mine_v3 as M

FIN = [("C3x", "d34", "<=", 0.3, "距 MA34 ≤0.3 ATR"),
       ("C7s", "ret10", "<=", -1.5, "10日累计反向≥1.5ATR"),
       ("C16", "ma10_sl", "<=", 0.0, "MA10 斜率转平或转负"),
       ("C17", "macd", "<=", 0.0, "MACD 柱翻向")]
MULT = {"FG": 20, "SC": 1000, "M": 10, "C": 10, "RB": 10,
        "I": 100, "TA": 5, "CU": 5, "SA": 20, "SR": 10}


def rowf(P, i):
    S, bars, sd = P["S"], P["bars"], P["side"][i]
    atr = S["atr"][i] or 1e-9
    f = {"d34": (bars[i]["c"] - S["ma34"][i]) / atr if S["ma34"][i] else None,
         "macd": P["hist"][i] / atr if P["hist"][i] is not None else None}
    f["ret10"] = (bars[i]["c"] - bars[i - 10]["c"]) / atr if i >= 10 else None
    f["ma10_sl"] = ((S["ma10"][i] - S["ma10"][i - 5]) / atr
                    if i >= 5 and S["ma10"][i] and S["ma10"][i - 5] else None)
    return {k: M.adj(k, v, sd) for k, v in f.items()}


def hit_of(P, i):
    r = rowf(P, i)
    return sum(1 for c in FIN if M.ok(r, c)), r


def main():
    rows_d, prep_d = M.load("D")
    base5 = M.base_rate(rows_d, 5)

    # ---------- 提前量 ----------
    ld = M.leads(rows_d, [c[0] for c in FIN], 5, FIN)
    print("提前量：样本 %d，中位 %d，均值 %.1f，≤1根 %.0f%%，≤2根 %.0f%%，≤3根 %.0f%%"
          % (len(ld), st.median(ld), sum(ld) / len(ld),
             sum(1 for x in ld if x <= 1) / len(ld) * 100,
             sum(1 for x in ld if x <= 2) / len(ld) * 100,
             sum(1 for x in ld if x <= 3) / len(ld) * 100))

    # ---------- 分窗口概率 ----------
    hit4 = [r for r in rows_d if all(M.ok(r, c) for c in FIN)]
    prob = {}
    for H in (2, 3, 5, 10):
        prob[H] = sum(M.lab(r, H) for r in hit4) / len(hit4)

    # ---------- k=4 提前建仓的收益验证 ----------
    print("\n===== k=4 触发后「不等穿越」直接建仓的收益 =====")
    trades = []
    for code in M.CODES:
        adjb, _ = build.panama_adjust(code)
        keep = int(8 * 244)
        bars = adjb[-keep:] if len(adjb) > keep else adjb
        P = prep_d[code]
        if len(P["bars"]) != len(bars):
            # prep 用的是同一份切片，保持一致
            P = M.prep(bars)
        S = P["S"]
        atr_ref = None
        sig = []
        for si, (s, e, sd) in enumerate(P["segs"]):
            for i in range(max(s, 60), e + 1):
                k, _ = hit_of(P, i)
                if k >= 4:
                    sig.append((i, sd))
                    break
        for n, (i, sd) in enumerate(sig):
            ei = i + 1
            if ei >= len(bars) - 2:
                continue
            entry = bars[ei]["o"]
            d = -1 if sd == 1 else 1          # 逆当前段方向
            atr = S["atr"][i] or entry * 0.01
            # 出场：吊灯 3ATR（收盘价基准）/ 反向穿越 / 最长 60 根
            ext = entry
            ex = None
            for j in range(ei, min(ei + 60, len(bars))):
                b = bars[j]
                if d == 1:
                    ext = max(ext, b["h"])
                    if b["c"] <= ext - 3 * atr:
                        ex, why = b["c"], "移动止损"
                        break
                else:
                    ext = min(ext, b["l"])
                    if b["c"] >= ext + 3 * atr:
                        ex, why = b["c"], "移动止损"
                        break
                if j - ei >= 40:
                    ex, why = b["c"], "时间"
                    break
            if ex is None:
                j = min(ei + 59, len(bars) - 1)
                ex, why = bars[j]["c"], "时间"
            pnl_atr = (ex - entry) * d / atr
            trades.append({"sym": code, "date": bars[i]["d"], "d": d,
                           "entry": entry, "exit": ex, "atr": atr,
                           "pnl_atr": pnl_atr, "why": why,
                           "pts": (ex - entry) * d,
                           "yuan": (ex - entry) * d * MULT[code]})
    win = sum(1 for t in trades if t["pnl_atr"] > 0) / len(trades) * 100
    avg = sum(t["pnl_atr"] for t in trades) / len(trades)
    tr = [t for t in trades if t["date"] < M.SPLIT]
    te = [t for t in trades if t["date"] >= M.SPLIT]
    def stat(sub):
        if not sub:
            return "—"
        w = sum(1 for t in sub if t["pnl_atr"] > 0) / len(sub) * 100
        a = sum(t["pnl_atr"] for t in sub) / len(sub)
        return "%d笔 胜率%.1f%% 均%+.2fATR" % (len(sub), w, a)
    print("  合计 %d 笔 | 胜率 %.1f%% | 单笔均 %+.2f ATR" % (len(trades), win, avg))
    print("  训练段 %s" % stat(tr))
    print("  盲测段 %s" % stat(te))
    print("  出场原因 %s" % {w: sum(1 for t in trades if t["why"] == w)
                             for w in ("移动止损", "时间")})

    # ---------- k 分档的交易表现（看 k=3 vs k=4 是否单调） ----------
    print("\n===== 不同 k 阈值的建仓表现（对照）=====")
    for K in (2, 3, 4):
        sub = []
        for code in M.CODES:
            adjb, _ = build.panama_adjust(code)
            keep = int(8 * 244)
            bars = adjb[-keep:] if len(adjb) > keep else adjb
            P = M.prep(bars)
            S = P["S"]
            for si, (s, e, sd) in enumerate(P["segs"]):
                for i in range(max(s, 60), e + 1):
                    k, _ = hit_of(P, i)
                    if k >= K:
                        ei = i + 1
                        if ei >= len(bars) - 2:
                            break
                        entry = bars[ei]["o"]
                        d = -1 if sd == 1 else 1
                        atr = S["atr"][i] or entry * 0.01
                        ext = entry
                        ex = None
                        for j in range(ei, min(ei + 60, len(bars))):
                            b = bars[j]
                            if d == 1:
                                ext = max(ext, b["h"])
                                if b["c"] <= ext - 3 * atr:
                                    ex = b["c"]; break
                            else:
                                ext = min(ext, b["l"])
                                if b["c"] >= ext + 3 * atr:
                                    ex = b["c"]; break
                            if j - ei >= 40:
                                ex = b["c"]; break
                        if ex is None:
                            ex = bars[min(ei + 59, len(bars) - 1)]["c"]
                        sub.append((ex - entry) * d / atr)
                        break
        if sub:
            w = sum(1 for x in sub if x > 0) / len(sub) * 100
            print("  k≥%d: %d 笔 胜率%.1f%% 均%+.2f ATR" % (K, len(sub), w, sum(sub) / len(sub)))

    # ---------- 写模型 ----------
    model = {
        "version": "v3",
        "H": 5,
        "base": round(base5, 4),
        "conds": [{"k": c[0], "f": c[1], "op": c[2], "th": c[3], "t": c[4]} for c in FIN],
        "calib": {},
        "prob": {str(h): round(v, 4) for h, v in prob.items()},
        "lead": {"median": int(st.median(ld)), "mean": round(sum(ld) / len(ld), 2),
                 "p_le1": round(sum(1 for x in ld if x <= 1) / len(ld), 3),
                 "p_le2": round(sum(1 for x in ld if x <= 2) / len(ld), 3),
                 "p_le3": round(sum(1 for x in ld if x <= 3) / len(ld), 3),
                 "n": len(ld)},
        "trade": {"n": len(trades), "win": round(win / 100, 4),
                  "avg_atr": round(avg, 3),
                  "train": stat(tr), "test": stat(te)},
        "thr": {"watch": 2, "warn": 3, "build": 4},
    }
    tab = {}
    for r in rows_d:
        k = sum(1 for c in FIN if M.ok(r, c))
        d = tab.setdefault(k, [0, 0])
        d[0] += 1
        d[1] += 1 if M.lab(r, 5) else 0
    for k, (n, h) in tab.items():
        model["calib"][str(k)] = round(h / n, 4)
    json.dump(model, open("model_v3.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n已写 model_v3.json")
    print(json.dumps({k: v for k, v in model.items() if k != "conds"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
