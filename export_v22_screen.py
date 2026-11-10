# -*- coding: utf-8 -*-
"""v22 表1 重生成：全部 29 个品种的 D3(G1) 双段结果，数据统一取自 v22_gated.json

之前 v22_screen.py 单独跑过一遍 D3，与 v22_gated.py 的 G1 在个别品种上差了 1 笔
（手续费口径不同），两张表数字对不上。这里统一以 232 条矩阵那份为准，口径唯一。
"""
import csv
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "reports")
G = json.load(open(os.path.join(OUT, "v22_gated.json"), encoding="utf-8"))
NAMES = {k: v for k, v in {
    "M": "豆粕", "FG": "玻璃", "MA": "甲醇", "Y": "豆油", "P": "棕榈油",
    "HC": "热卷", "A": "豆一", "C": "玉米", "CS": "淀粉", "RM": "菜粕",
    "OI": "菜油", "SR": "白糖", "CF": "棉花", "TA": "PTA", "SA": "纯碱",
    "UR": "尿素", "RB": "螺纹钢", "JD": "鸡蛋", "I": "铁矿", "J": "焦炭",
    "JM": "焦煤", "RU": "橡胶", "BU": "沥青", "FU": "燃油", "SP": "纸浆",
    "V": "PVC", "PP": "聚丙烯", "PF": "短纤", "L": "塑料", "EG": "乙二醇", "SM": "锰硅",
    "SF": "硅铁", "PK": "花生", "AP": "苹果", "ZN": "锌", "AL": "铝",
    "CU": "铜", "NI": "镍", "AU": "黄金", "AG": "白银", "SC": "原油",
}.items()}

rows = []
for sym, v in G.items():
    g1 = [r for r in v["rows"] if r["code"] == "G1"]
    if not g1:
        continue
    r = g1[0]
    t, x = r["train"], r["test"]
    both_pos = t["net"] > 0 and x["net"] > 0
    strict = both_pos and t["win"] > 55 and x["win"] > 55 and t["n"] >= 6
    if not both_pos:
        verdict = "双段未同时为正"
    elif strict:
        verdict = "双段达标（胜率>55%）"
    elif x["n"] < 3:
        verdict = "验证样本不足"
    elif t["win"] <= 55 or x["win"] <= 55:
        verdict = "双段为正，但胜率未过 55%%（训练 %.0f%% / 验证 %.0f%%）" % (t["win"], x["win"])
    else:
        verdict = "双段为正"
    rows.append([sym, NAMES.get(sym, sym), t["n"], t["win"], t["net"], t["pf"], t["mdd"],
                 x["n"], x["win"], x["net"], x["pf"], x["mdd"],
                 "是" if both_pos else "否", verdict])

rows.sort(key=lambda r: (r[12] != "是", -(r[4] + r[9])))

with open(os.path.join(OUT, "v22_表1_全市场海选.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["品种", "名称", "训练笔数", "训练胜率", "训练净", "训练盈亏比", "训练回撤",
                "验证笔数", "验证胜率", "验证净", "验证盈亏比", "验证回撤",
                "双段净>0", "判定"])
    w.writerows(rows)

print("表1 重生成：%d 个品种；双段为正 %d 个" % (len(rows), sum(1 for r in rows if r[12] == "是")))
for r in rows[:10]:
    print("  %-3s %-5s %s" % (r[0], r[1], r[13]))
