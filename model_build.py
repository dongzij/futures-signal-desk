# -*- coding: utf-8 -*-
"""生成最终模型 model_v3.json —— 引擎与页面唯一的数据源。

内容：
  conds   4 条入选条件（回测筛选后保留的最高准确率组合）
  matrix  k 命中条数 × 预测窗口(2/3/5/10 根) 的反转概率（训练/盲测分列）
  lead    提前量分布
  trade   按该模型建仓的口径与成绩（含收益集中度风险提示）
  weekly  周线适用性
"""
import json
import statistics as st

import build
import mine_v3 as M

FIN = [("C3x", "d34", "<=", 0.3, "价格逼近 MA34 ≤0.3 ATR（仍在原趋势一侧）"),
       ("C7s", "ret10", "<=", -1.5, "10 日累计逆行 ≥1.5 ATR"),
       ("C16", "ma10_sl", "<=", 0.0, "MA10 斜率转平或转向"),
       ("C17", "macd", "<=", 0.0, "MACD 柱翻向")]
MULT = {"FG": 20, "SC": 1000, "M": 10, "C": 10, "RB": 10,
        "I": 100, "TA": 5, "CU": 5, "SA": 20, "SR": 10}
FEE = {"FG": 6.0, "SC": 0.0, "M": 1.5, "C": 1.5, "RB": 6.0,
       "I": 6.0, "TA": 3.0, "CU": 17.0, "SA": 6.0, "SR": 3.0}
HS = (2, 3, 5, 10)


def rowf(P, i):
    S, bars, sd = P["S"], P["bars"], P["side"][i]
    atr = S["atr"][i] or 1e-9
    f = {"d34": (bars[i]["c"] - S["ma34"][i]) / atr if S["ma34"][i] else None,
         "macd": P["hist"][i] / atr if P["hist"][i] is not None else None}
    f["ret10"] = (bars[i]["c"] - bars[i - 10]["c"]) / atr if i >= 10 else None
    f["ma10_sl"] = ((S["ma10"][i] - S["ma10"][i - 5]) / atr
                    if i >= 5 and S["ma10"][i] and S["ma10"][i - 5] else None)
    return {k: M.adj(k, v, sd) for k, v in f.items()}


def kof(P, i):
    r = rowf(P, i)
    return sum(1 for c in FIN if M.ok(r, c))


def main():
    rows_d, prep_d = M.load("D")
    rows_w, prep_w = M.load("W")

    # ---------------- k × H 概率矩阵 ----------------
    matrix = {}
    for H in HS:
        row = {}
        for k in range(5):
            sub = [r for r in rows_d
                   if sum(1 for c in FIN if M.ok(r, c)) == k]
            if len(sub) < 30:
                continue
            tr = [r for r in sub if r["date"] < M.SPLIT]
            te = [r for r in sub if r["date"] >= M.SPLIT]
            row[str(k)] = {
                "n": len(sub),
                "p": round(sum(M.lab(r, H) for r in sub) / len(sub), 4),
                "ptr": round(sum(M.lab(r, H) for r in tr) / len(tr), 4) if len(tr) >= 20 else None,
                "pte": round(sum(M.lab(r, H) for r in te) / len(te), 4) if len(te) >= 20 else None,
            }
        matrix[str(H)] = {"base": round(M.base_rate(rows_d, H), 4), "rows": row}

    print("k × H 概率矩阵（日线）")
    for H in HS:
        m = matrix[str(H)]
        line = "  H=%-2d 基准%.1f%% | " % (H, m["base"] * 100)
        for k in range(5):
            r = m["rows"].get(str(k))
            line += "k%d:%s " % (k, ("%.1f%%" % (r["p"] * 100)) if r else "—")
        print(line)

    # ---------------- 提前量 ----------------
    ld = M.leads(rows_d, [c[0] for c in FIN], 5, FIN)
    lead = {"n": len(ld), "median": int(st.median(ld)),
            "mean": round(sum(ld) / len(ld), 2),
            "p_le1": round(sum(1 for x in ld if x <= 1) / len(ld), 3),
            "p_le2": round(sum(1 for x in ld if x <= 2) / len(ld), 3),
            "p_le3": round(sum(1 for x in ld if x <= 3) / len(ld), 3)}
    print("\n提前量：中位 %d 根，≤1根 %.0f%%，≤2根 %.0f%%，≤3根 %.0f%%"
          % (lead["median"], lead["p_le1"] * 100, lead["p_le2"] * 100, lead["p_le3"] * 100))

    # ---------------- 建仓口径（结构止损） ----------------
    trades = []
    for code in M.CODES:
        adjb, _ = build.panama_adjust(code)
        keep = int(8 * 244)
        bars = adjb[-keep:] if len(adjb) > keep else adjb
        P = M.prep(bars)
        S = P["S"]
        for s, e, sd in P["segs"]:
            for i in range(max(s, 60), e + 1):
                if kof(P, i) < 4:
                    continue
                ei = i + 1
                if ei >= len(bars) - 2:
                    break
                entry = bars[ei]["o"]
                d = -1 if sd == 1 else 1
                atr = S["atr"][i] or entry * 0.01
                ex, why = None, "时间"
                for j in range(ei, min(ei + 60, len(bars))):
                    b, ma = bars[j], S["ma34"][j]
                    if d == -1 and ma and b["c"] > ma + 0.5 * atr:
                        ex, why = b["c"], "结构失效"
                        break
                    if d == 1 and ma and b["c"] < ma - 0.5 * atr:
                        ex, why = b["c"], "结构失效"
                        break
                if ex is None:
                    ex = bars[min(ei + 59, len(bars) - 1)]["c"]
                trades.append({"sym": code, "date": bars[i]["d"], "d": d,
                               "entry": entry, "exit": ex, "atr": atr,
                               "pts": (ex - entry) * d,
                               "pnl_atr": (ex - entry) * d / atr,
                               "why": why,
                               "yuan": (ex - entry) * d * MULT[code] - FEE[code] * 2})
                break
    trades.sort(key=lambda x: x["date"])
    n = len(trades)
    wins = [t for t in trades if t["pnl_atr"] > 0]
    losses = [t for t in trades if t["pnl_atr"] <= 0]
    aw = sum(t["pnl_atr"] for t in wins) / len(wins)
    al = sum(t["pnl_atr"] for t in losses) / len(losses)
    total_y = sum(t["yuan"] for t in trades)
    top5 = sum(t["yuan"] for t in sorted(trades, key=lambda x: -x["yuan"])[:5])
    top3 = sum(t["yuan"] for t in sorted(trades, key=lambda x: -x["yuan"])[:3])
    print("\n建仓口径：%d 笔 胜率 %.1f%% 均盈 %+.2f 均亏 %+.2f 盈亏比 %.2f 单笔均 %+.2f ATR"
          % (n, len(wins) / n * 100, aw, al, abs(aw / al),
             sum(t["pnl_atr"] for t in trades) / n))
    print("按 1 手：8 年合计 %+.0f 元；前 3 笔 %+.0f 元；剔除前 3 笔后 %+.0f 元"
          % (total_y, top3, total_y - top3))

    trade = {"n": n, "win": round(len(wins) / n, 4),
             "avg_win_atr": round(aw, 2), "avg_loss_atr": round(al, 2),
             "rr": round(abs(aw / al), 2),
             "avg_atr": round(sum(t["pnl_atr"] for t in trades) / n, 3),
             "yuan_1lot": round(total_y), "yuan_top3": round(top3),
             "yuan_ex_top3": round(total_y - top3),
             "yuan_top5": round(top5),
             "stop": "收盘反向穿越 MA34 超 0.5 ATR 即认错离场",
             "maxhold": 60,
             "risk": "收益高度集中于极少数超级行情，不能作为稳定盈利依据"}

    # ---------------- 周线 ----------------
    wrow = {}
    for k in range(5):
        sub = [r for r in rows_w if sum(1 for c in FIN if M.ok(r, c)) == k]
        if len(sub) >= 30:
            wrow[str(k)] = {"n": len(sub), "p": round(sum(M.lab(r, 3) for r in sub) / len(sub), 4)}
    weekly = {"H": 3, "base": round(M.base_rate(rows_w, 3), 4), "rows": wrow}
    print("\n周线（3 周内反转）基准 %.1f%%：%s"
          % (weekly["base"] * 100,
             {k: "%.1f%%(%d)" % (v["p"] * 100, v["n"]) for k, v in wrow.items()}))

    model = {
        "version": "v3",
        "unit": "D",
        "metric": "MA34",
        "conds": [{"k": c[0], "f": c[1], "op": c[2], "th": c[3], "t": c[4]} for c in FIN],
        "matrix": matrix,
        "lead": lead,
        "trade": trade,
        "weekly": weekly,
        "thr": {"watch": 2, "warn": 3, "build": 4},
        "stage": {"0": "平静", "1": "起变化", "2": "关注", "3": "预警", "4": "准备反转"},
        "method": {
            "segment": "收盘价相对 MA34 上方/下方 = 一段大趋势，穿越点 = 反转点",
            "window": "反转前 10 根内开始出现共性变化（C7s 用 10 日累计反向）",
            "screen": "候选 20 条 → 按覆盖率/训练/盲测 lift/分品种稳定性淘汰，保留 4 条",
            "dropped": "放量类(lift 0.6~0.9，实为反向指标)、持仓减仓类(lift 1.14 且仅 1/10 品种)",
        },
    }
    json.dump(model, open("model_v3.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n已写 model_v3.json")


if __name__ == "__main__":
    main()
