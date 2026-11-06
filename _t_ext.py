# -*- coding: utf-8 -*-
import statistics as st

import mine_v10 as V
import mine_v10b as C

V.prep_all()
_ORIG = V.feats

LAB = {"ext25": "距MA34≥2.5ATR", "ext30": "距MA34≥3.0ATR", "ext35": "距MA34≥3.5ATR",
       "age20": "段龄≥20根", "age30": "段龄≥30根", "age40": "段龄≥40根",
       "amp8": "段幅≥8ATR", "amp12": "段幅≥12ATR", "amp16": "段幅≥16ATR"}


def feats2(P, i, sd):
    f = _ORIG(P, i, sd)
    bars, S = P["bars"], P["S"]
    atr = S["atr"][i] or 1e-9
    c = bars[i]["c"]
    m = S["ma"][i]
    ext = (c - m) * sd / atr
    f["ext25"] = ext >= 2.5
    f["ext30"] = ext >= 3.0
    f["ext35"] = ext >= 3.5
    f["age20"] = S["age"][i] >= 20
    f["age30"] = S["age"][i] >= 30
    f["age40"] = S["age"][i] >= 40
    s = i - S["age"][i] + 1
    amp = (max(b["h"] for b in bars[s:i + 1]) - min(b["l"] for b in bars[s:i + 1])) / atr
    f["amp8"] = amp >= 8
    f["amp12"] = amp >= 12
    f["amp16"] = amp >= 16
    return f


V.feats = feats2
pos, neg = C.build_samples()
print("正 %d 负 %d" % (len(pos), len(neg)))
names = list(feats2(V.DATA["FG"], 100, 1).keys())
print("%-18s %8s %8s %7s" % ("条件", "顶区", "中段", "lift"))
rows = []
for nm in names:
    a = sum(1 for r in pos if r["f"][nm]) / len(pos)
    b = sum(1 for r in neg if r["f"][nm]) / len(neg)
    rows.append((nm, a, b, a / b if b else 99))
for nm, a, b, lf in sorted(rows, key=lambda x: -x[3]):
    print("%-18s %7.1f%% %7.1f%% %7.2f" % (LAB.get(nm, C.LABEL.get(nm, nm)), a * 100, b * 100, lf))

print("\n组合 precision（在顶区=命中，在中段=误报）：")
combos = [
    ("ext30", "pull"), ("ext30", "ma18"), ("ext30", "ma5t"), ("ext30", "adx"),
    ("ext25", "pull"), ("ext25", "ma18"), ("ext30", "pull", "ma18"),
    ("ext30", "pull", "adx"), ("ext25", "pull", "ma5t"), ("ext30", "pull", "ma5t", "adx"),
    ("age30", "pull"), ("amp12", "pull"), ("ext30", "amp12", "pull"),
    ("ext30", "pull", "shadow"), ("ext30", "shadow"), ("ext30", "oi"),
]
for cs in combos:
    tp = sum(1 for r in pos if all(r["f"][x] for x in cs))
    fp = sum(1 for r in neg if all(r["f"][x] for x in cs))
    prec = tp / (tp + fp) if tp + fp else 0
    print("  %-44s 顶区%5.1f%% 中段%5.1f%% → precision %5.1f%% (n=%4d)"
          % ("+".join(LAB.get(x, C.LABEL.get(x, x)) for x in cs),
             tp / len(pos) * 100, fp / len(neg) * 100, prec * 100, tp + fp))
