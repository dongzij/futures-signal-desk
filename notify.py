# -*- coding: utf-8 -*-
"""独立补推工具：从 state.json 读本次新信号，发一条纯文本微信。

v11.1 起 GitHub Actions 已改由 daily.py 统一推送（覆盖进场 / 出场雷达 / 持仓离场 /
趋势确认四类，并按 config.pushSymbols 白名单过滤），本文件不再由 workflow 调用，
避免同一条信号重复发两遍。手动补推时口径与 daily.py 保持一致。
"""
import json
import os
import sys
import urllib.parse
import urllib.request

from daily import push_scope          # 与 daily.py 共用同一套白名单口径

cfg = {}
try:
    cfg = json.load(open("config.json", encoding="utf-8"))
except Exception:                                             # noqa: BLE001
    pass
TOKEN = (os.environ.get("PUSHPLUS_TOKEN") or cfg.get("pushplus") or "").strip()
if not TOKEN:
    print("no PUSHPLUS_TOKEN, skip push")
    sys.exit(0)

state = json.load(open("state.json", encoding="utf-8"))
scanned = state.get("scanDate", "")[:10]
min_prob = cfg.get("pushMinProb", 0.70)
allow_push, _, _ = push_scope(cfg)
# 白名单品种全量推送（豁免周线 MA45 降级）；白名单外不发微信。
# 被降级的信号仍写进 state.json、页面照常显示，绝不删除或延迟。
fresh = [s for s in state.get("signals", [])
         if s.get("foundAt", "")[:10] == scanned
         and s.get("kind") == "pred" and s.get("status") in ("open", "pending")
         and (s.get("prob", {}).get("5") or 0) >= min_prob
         and allow_push(s.get("sym"), s.get("meta"))]

if not fresh:
    print("no fresh signals, skip push")
    sys.exit(0)

lines = []
for s in fresh[:8]:
    d = "预空" if s["side"] == "short" else "预多"
    p5 = (s.get("prob", {}).get("5") or 0) * 100
    meta = s.get("meta") or {}
    wk = "顺势" if meta.get("weekAlign") else "逆势·强档"
    tags = []
    if meta.get("lowVol"):
        tags.append("💤 低波动待观察")
    if meta.get("confirm2") == "noFollow":
        tags.append("⚠️ 2日未跟进")
    lines.append("%s %s｜%s 概率%.0f%%  入 %s  建议 %s 手  ｜周线MA45 %s%s"
                 % (s["sym"], s.get("name", ""), d, p5, s["entry"],
                    s.get("lots", "?"), wk,
                    ("  " + "·".join(tags)) if tags else ""))
title = "准备反转 %d 条：%s" % (len(fresh), "、".join(s["sym"] for s in fresh[:4]))
body = "\n".join(lines) + ("\n\n命中 4/4 条反转特征（MA34≤0.3ATR·10日逆行≥1.5ATR·"
                           "MA10转向·MACD翻向），认错=收盘反向穿越MA34超0.5ATR。\n"
                           "历史胜率26.5%、盈亏比3.9，请控制单笔风险。详见仓库 Pages 页面。")

data = urllib.parse.urlencode({
    "token": TOKEN, "title": title, "content": body, "template": "txt",
}).encode()
req = urllib.request.Request("https://www.pushplus.plus/send", data=data,
                             headers={"User-Agent": "curl/8"})
try:
    print(urllib.request.urlopen(req, timeout=15).read().decode()[:200])
except Exception as e:                                        # noqa: BLE001
    print("push failed:", e)
