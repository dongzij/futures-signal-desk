# -*- coding: utf-8 -*-
"""v22 推送纪律测试：用假数据验证三件事

1. 「方案内信号」通道：路况允许 + 信号在该品种定稿方案的进场集合内 → 最高优先级推送，
   文案必须带【当前路况】【方案依据】【实战建议】【防守底线】。
2. 震荡静默：震荡品种的进场信号一律不推（打印 muted 明细，数得清）。
3. 逆势静默：下跌趋势里做多的信号不推。

不污染真实 state.json（临时副本），不真发微信（patch post_push 捕获）。
产出 _push_preview_v22.html，用于截图验收。
"""
import datetime
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import daily
import engine

TODAY = datetime.date.today().isoformat()

tmp_state = os.path.join(tempfile.gettempdir(), "state_push_v22.json")
shutil.copy(os.path.join(ROOT, "state.json"), tmp_state)
daily.STATE = tmp_state

captured = []
daily.post_push = lambda token, title, html: captured.append((title, html)) or True

CHECKS = [{"ok": True, "t": "MA34 趋势段", "v": "命中"},
          {"ok": True, "t": "反转特征 4/4", "v": "全部命中"},
          {"ok": True, "t": "ADX≥20", "v": "24.0"},
          {"ok": True, "t": "动能收缩", "v": "命中"}]

# 路况（实测）：FG 过渡 / M 下跌 / SM 震荡 / HC 过渡
FAKE = {
    # 玻璃：过渡态 + 顶底雷达做多 = 方案 G1 的允许信号 → 应推 🔥
    "FG": [{"kind": "pred", "side": "long", "price": 1186.0, "adx": 25.2,
            "prob": {"5": 0.79, "3": 0.71}, "checks": CHECKS}],
    # 豆粕：下跌趋势 + WM 结构做空 = 方案 G1 允许（顺势）→ 应推 🔥
    #       同一品种再来一条逆势做多的顶底雷达 → 必须被闸门拦下
    "M": [{"kind": "mw", "side": "short", "price": 2988.0, "adx": 31.0,
           "prob": {"5": 0.74, "3": 0.66}, "checks": CHECKS},
          {"kind": "pred", "side": "long", "price": 3005.0, "adx": 26.0,
           "prob": {"5": 0.83, "3": 0.77}, "checks": CHECKS}],
    # 锰硅：震荡 → 任何进场信号都不推，系统静默
    "SM": [{"kind": "pred", "side": "long", "price": 5712.0, "adx": 21.0,
            "prob": {"5": 0.80, "3": 0.72}, "checks": CHECKS}],
    # 热卷：过渡态 + 顶底雷达做空 = 方案 G8 允许 → 应推 🔥
    "HC": [{"kind": "pred", "side": "short", "price": 3271.0, "adx": 23.4,
            "prob": {"5": 0.76, "3": 0.69}, "checks": CHECKS}],
}

_orig_scan = engine.scan_symbol


def fake_scan(code, bars=None, unit="D"):
    sigs, extra = _orig_scan(code, bars=bars, unit=unit)
    for f in FAKE.get(code, []):
        sigs = sigs + [dict({"sym": code, "date": TODAY, "unit": unit}, **f)]
    return sigs, extra


engine.scan_symbol = fake_scan

os.environ["PUSHPLUS_TOKEN"] = "TEST_TOKEN_LOCAL_ONLY"
daily.main()

print("\n== captured %d pushes ==" % len(captured))
for i, (title, html) in enumerate(captured):
    print("#%d %s" % (i + 1, title))

cards = []
for i, (title, html) in enumerate(captured):
    cards.append('<div style="margin:22px 0;padding:6px 0;border-top:2px dashed #ddd">'
                 '<div style="font-size:13px;color:#666;margin-bottom:8px">'
                 '推送 #%d · 标题：%s</div>%s</div>' % (i + 1, title, html))
page = ('<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        '<body style="font-family:-apple-system,\'PingFang SC\',sans-serif;max-width:720px;'
        'margin:0 auto;background:#fff;padding:18px">'
        '<h2 style="color:#222;font-size:18px">v22 推送纪律测试 · 假数据 %d 条</h2>'
        '<div style="background:#f6f8fa;border-left:3px solid #4c8dff;padding:8px 10px;'
        'font-size:12.5px;line-height:1.8;color:#333">'
        '预期：M/FG/HC 三条「方案内信号」入推；M 的逆势做多、Y 豆油的震荡趋势确认被闸门拦下。</div>'
        '%s</body></html>' % (len(captured), "".join(cards)))
out = os.path.join(ROOT, "_push_preview_v22.html")
open(out, "w", encoding="utf-8").write(page)
print("preview ->", out)
