# -*- coding: utf-8 -*-
"""v5-model：把失误专项分析结论写进 model_v4.json
  - firsthit：线上口径（每段只触发一次）下 k=4+ADX≥20 的实测概率（H=2/3/5/10）
  - strong：强确认层（追加「持仓 5 日下降」）的概率与交易统计
样本与 mine_v5.collect 完全一致（大周期段 + 段龄≥2 + ADX≥20 + 4/4）。
"""
import json

import mine_v4b as M
import mine_v5 as V

data = M.load()
hits = V.collect(data)
labeled = [h for h in hits if h["lead"] is not None]
Pcache = {c: M.Pm_fetch(data, c, 34)[1] for c in M.UNIV}


def p_at(keep, H):
    ok = [h for h in keep if h["lead"] is not None]
    if not ok:
        return None
    return sum(1 for h in ok if h["lead"] <= H) / len(ok)


first = {"n": len(labeled)}
for H in (2, 3, 5, 10):
    first["p%d" % H] = round(p_at(labeled, H), 4)
tr = [h for h in labeled if h["date"] < M.SPLIT]
te = [h for h in labeled if h["date"] >= M.SPLIT]
first["tr"] = round(p_at(tr, 5), 4) if tr else None
first["te"] = round(p_at(te, 5), 4) if te else None
first["trN"], first["teN"] = len(tr), len(te)
first["tradeR"] = round(sum(h["trade_R"] for h in labeled), 1)
first["win"] = round(sum(1 for h in labeled if h["trade_pts"] > 0) / len(labeled), 4)

strong = [h for h in labeled if (h["rows_feat"].get("doi5") or 0) <= 0]
st = {"n": len(strong)}
for H in (2, 3, 5, 10):
    st["p%d" % H] = round(p_at(strong, H), 4)
trs = [h for h in strong if h["date"] < M.SPLIT]
tes = [h for h in strong if h["date"] >= M.SPLIT]
st["tr"] = round(p_at(trs, 5), 4) if trs else None
st["te"] = round(p_at(tes, 5), 4) if tes else None
st["trN"], st["teN"] = len(trs), len(tes)
st["tradeR"] = round(sum(h["trade_R"] for h in strong), 1)
st["win"] = round(sum(1 for h in strong if h["trade_pts"] > 0) / len(strong), 4)
st["fails"] = [h["sym"] + " " + h["date"] for h in strong if h["lead"] > 5]

m = json.load(open("model_v4.json", encoding="utf-8"))
m["firsthit"] = first
m["strong"] = {
    "cond": [{"k": "S1", "f": "doi5", "op": "<=", "th": 0, "t": "持仓 5 日下降"},
             {"k": "S2", "f": "pull", "op": "<=", "th": -2, "t": "自极值回撤 ≥2ATR"}],
    "use": "doi5",                       # 实际启用的门槛（pull 仅作展示）
    "stat": st,
    "note": "失误专项分析（v5）：11 笔失误中 9 笔信号时持仓 5 日在增加；"
            "追加「持仓 5 日下降」后 19 笔中 17 笔 5 日内反转（89.5%，内 91.7%/外 85.7%），"
            "不设止损交易 R 由 +18.7 升至 +26.5，回撤 -7.8R 减至 -4.8R",
}
json.dump(m, open("model_v4.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("firsthit", json.dumps(first, ensure_ascii=False))
print("strong", json.dumps(st, ensure_ascii=False))
