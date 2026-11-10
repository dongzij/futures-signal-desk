# -*- coding: utf-8 -*-
"""v22 导出：表2 六池定稿、表3 逐笔清单（1 万本金口径，与页面 K 线标识同源）"""
import csv
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "reports")
SYMS = ["M", "FG", "MA", "Y", "P", "HC"]
WHY = {"defend": "失效", "xsig_strong": "强离场", "ma_exit": "破MA26",
       "chandelier": "吊灯", "stop30": "熔断", "max_hold": "到期", "flip": "反手"}

F = json.load(open(os.path.join(OUT, "v22_final.json"), encoding="utf-8"))

# ---------- 表2 ----------
with open(os.path.join(OUT, "v22_表2_六池定稿.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["排序", "品种", "名称", "方案", "方案内容", "训练笔数", "训练胜率", "训练净",
                "训练盈亏比", "训练回撤", "验证笔数", "验证胜率", "验证净", "验证盈亏比",
                "验证回撤", "近1年净", "近1年笔数", "全样本净", "全样本年化", "全样本期末",
                "当前路况", "建议策略"])
    for i, s in enumerate(F["order"], 1):
        r = F["symbols"][s]
        t, v, rc, fu = r["train"], r["test"], r["recent"], r["full"]
        spec = r["spec"]["entry"]
        body = "趋势 %s｜震荡 空仓｜过渡 %s" % (
            "/".join({"mw": "WM", "confirm": "兑现", "tf": "转折"}.get(k, k)
                     for k in spec.get("trend_up", [])) or "—",
            "/".join({"pred": "雷达", "warn": "预警", "mw": "WM"}.get(k, k)
                     for k in spec.get("transition", [])) or "—")
        plan = json.load(open(os.path.join(ROOT, "dist", "data", s + ".json"),
                              encoding="utf-8"))["plan"]
        w.writerow([i, s, r["name"], r["method"], body, t["n"], t["win"], t["net"], t["pf"],
                    t["mdd"], v["n"], v["win"], v["net"], v["pf"], v["mdd"], rc["net"], rc["n"],
                    fu["net"], fu["ann"], fu["final"],
                    plan["regime"]["label"], plan["regime"]["advice"]])
print("表2 ok")

# ---------- 表3 逐笔 ----------
with open(os.path.join(OUT, "v22_表3_六池逐笔清单.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["品种", "名称", "方向", "开仓日", "开仓价", "平仓日", "平仓价",
                "平仓原因", "持仓根数", "净盈亏(元)", "开仓信号"])
    cnt = 0
    for s in SYMS:
        d = json.load(open(os.path.join(ROOT, "dist", "data", s + ".json"), encoding="utf-8"))
        pl = d.get("plan") or {}
        side_nm = {"long": "多", "short": "空"}
        cur = None
        for m in pl.get("marks") or []:
            if m["t"] == "in":
                cur = m
            elif m["t"] == "out" and cur:
                w.writerow([s, d.get("name"), side_nm.get(cur["side"]), cur["d"], cur["px"],
                            m["d"], m["px"], WHY.get(m["k"], m["k"]), m.get("bars"),
                            m.get("net"), cur.get("k")])
                cnt += 1
                cur = None
        if pl.get("open"):
            w.writerow([s, d.get("name"), side_nm.get(pl["open"]["side"]), pl["openDate"],
                        pl["open"].get("px"), "持仓中", pl["open"].get("last") or "—",
                        "未平仓", pl["open"].get("bars"), pl["open"].get("float"),
                        pl["open"].get("kind")])
print("表3 ok：%d 笔已平仓 + 未平仓单列" % cnt)
