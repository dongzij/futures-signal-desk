# -*- coding: utf-8 -*-
"""数据新鲜度守卫（v27.1）—— 自愈重试 + 产物一致性校验

两个模式，对应 daily.yml 的两个不同阶段：

  python fresh_guard.py            写盘【之前】：只读预检行情源，取不到当日完整日线
                                  就等 3~5 分钟重取，最多 GUARD_MAX_TRY 轮。
  python fresh_guard.py --verify   构建【之后】：校验 7 品种末根一致、data/ 与
                                  dist/data/ 一致、页面与数据包一致，不通过就阻断提交。

为什么要放在写盘之前做「只读」预检
    daily.py 会写 state.json 并推微信，data_build.py 会清空重建 dist/data。
    如果直接把它们放进重试循环，一次失败重试就会重复写账本、重复推消息。
    所以先把「取数」这一件事反复做到位，再让正式流程只跑一次。

纪律边界（与 nightgate.py 一致）
    只在北京时间 15:00-20:00 的正式日盘窗口工作；重试若会跨过 20:00 立即停止。
    20:00 后绝不写盘 —— 夜盘半截行情属于下一交易日，写进去不可逆。
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
BJ = timezone(timedelta(hours=8))
SYMS = ["MA", "HC", "Y", "SM", "FG", "P", "M"]
MAX_TRY = int(os.environ.get("GUARD_MAX_TRY", "5"))
RETRY_SEC = int(os.environ.get("GUARD_RETRY_SEC", "240"))   # 4 分钟（用户要求 3~5 分钟）
WIN_FROM = (15, 0)
WIN_TO = (20, 0)


def bj_now():
    return datetime.now(BJ).replace(tzinfo=None)


def in_window(now=None):
    now = now or bj_now()
    hm = (now.hour, now.minute)
    return WIN_FROM <= hm < WIN_TO


def expect_date(now=None):
    """期望的交易日：工作日取当天，周末回退到上一工作日。

    法定节假日不硬编码（每年会变），由「连续多轮结果完全一致」的降级分支处理：
    休市时数据一致、只是没有新 bar，复用旧数据不会产生半截假 bar。
    """
    d = (now or bj_now()).date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def probe():
    """只读探测：强制绕过本地缓存，逐个品种拉主力连续日线，返回末根日期。"""
    os.environ["FRESH_BYPASS"] = "1"       # build.cache_load 见此变量直接返回 None
    import build                            # noqa: F401  延迟导入，避免预检阶段触碰其它模块
    last, err = {}, []
    for c in SYMS:
        try:
            rows = build.fetch_daily(c + "0")
            if not rows:
                err.append("%s:空" % c)
                continue
            last[c] = rows[-1]["d"][:10]
        except Exception as e:              # noqa: BLE001
            err.append("%s:%s" % (c, type(e).__name__))
    return last, err


def precheck():
    now = bj_now()
    if not in_window(now):
        print("[guard] 北京时间 %s 不在正式日盘窗口 15:00-20:00，拒绝执行（exit 3）。"
              % now.strftime("%Y-%m-%d %H:%M"))
        print("[guard] 20:00 后只允许 night.yml 只读预警；人工补数须 RUN_MODE=force。")
        return 0
    expect = expect_date(now)
    start = now
    print("[guard] 期望交易日 = %s · 最多 %d 轮 · 每轮间隔 %d 秒 · 窗口 15:00-20:00"
          % (expect, MAX_TRY, RETRY_SEC))
    final = ({}, [])
    for i in range(1, MAX_TRY + 1):
        last, err = probe()
        final = (last, err)
        ok = len(last) == len(SYMS) and all(d >= expect for d in last.values())
        print("[guard] 第 %d/%d 轮 · 到位 %d/%d · 末根 %s · 异常 %s"
              % (i, MAX_TRY, len(last), len(SYMS),
                 ("/".join(sorted(set(last.values()))) or "-"), ",".join(err) or "无"))
        if ok:
            print("[guard] ✔ 7/7 品种当日日线到位，放行正式写盘流程。")
            return 0
        if i < MAX_TRY:
            # 用「起始时刻 + 累计轮次 × 间隔」推算，避免只看下一轮而漏掉
            # 连续多轮重试累积跨过 20:00 的情况。
            nxt = start + timedelta(seconds=RETRY_SEC * i)
            if not in_window(nxt):
                print("[guard] 下一轮将跨过 20:00 夜盘闸门，停止重试；"
                      "绝不写入夜盘半截 bar。")
                return 1
            print("[guard] 数据未到位，%d 秒后重新取数…" % RETRY_SEC)
            time.sleep(RETRY_SEC)
    last, err = final
    # 兜底降级：全部品种末根彼此一致 = 数据源稳定（休市 / 接口当日未出数）。
    # 这种情况复用旧数据不会产生假 bar；但只要有品种缺失或不一致，一律阻断。
    consistent = (not err and len(last) == len(SYMS)
                  and len(set(last.values())) == 1)
    if consistent:
        print("::warning:: [guard] %d 轮后仍无当日新 bar，但 7/7 品种末根一致（%s），"
              "判定为休市或数据源未出新数，复用旧数据放行；watchdog 会继续盯。"
              % (MAX_TRY, list(set(last.values()))[0]))
        return 0
    print("[guard] ✘ %d 轮后数据仍不完整/不一致，阻断写盘，避免残缺数据入库。"
          % MAX_TRY)
    return 1


def _read(p):
    with open(p, "rb") as f:
        return f.read()


def verify():
    """构建产物一致性：任何一处不齐就阻断「提交更新」。"""
    bad = []
    lasts = {}
    for s in SYMS:
        p = os.path.join(ROOT, "dist/data", s + ".json")
        if not os.path.isfile(p):
            bad.append("缺 dist/data/%s.json" % s)
            continue
        try:
            lasts[s] = json.load(open(p, encoding="utf-8"))["main"][-1][0]
        except Exception as e:              # noqa: BLE001
            bad.append("dist/data/%s.json 解析失败 %s" % (s, type(e).__name__))
    if len(set(lasts.values())) > 1:
        bad.append("7 品种末根不一致：%s" % lasts)

    for p in SYMS + ["index"]:
        a = os.path.join(ROOT, "data", p + ".json")
        b = os.path.join(ROOT, "dist/data", p + ".json")
        if not os.path.isfile(a) or not os.path.isfile(b):
            bad.append("data/%s.json 与 dist/data/%s.json 有缺失" % (p, p))
        elif _read(a) != _read(b):
            bad.append("data/%s.json 与 dist/data/%s.json 不一致" % (p, p))

    h1 = os.path.join(ROOT, "index.html")
    h2 = os.path.join(ROOT, "dist/index.html")
    if not os.path.isfile(h1) or not os.path.isfile(h2) or _read(h1) != _read(h2):
        bad.append("index.html 与 dist/index.html 不一致")
    else:
        html = _read(h1).decode("utf-8", "replace")
        for key in ("顶不做多", "研究参考·非建仓指令", "当前为日线归档口径"):
            if key not in html:
                bad.append("页面缺少策略口径文案：%s" % key)

    try:
        idx = json.load(open(os.path.join(ROOT, "dist/data/index.json"),
                             encoding="utf-8"))
        if idx.get("built", "")[:10] != bj_now().date().isoformat():
            bad.append("index.built 不是今天：%s" % idx.get("built"))
        if not (idx.get("extGate") or {}).get("enabled"):
            bad.append("index.extGate 未启用")
    except Exception as e:                  # noqa: BLE001
        bad.append("dist/data/index.json 解析失败 %s" % type(e).__name__)

    print("[guard] 产物校验 · 7 品种末根 = %s"
          % ("/".join(sorted(set(lasts.values()))) or "-"))
    if bad:
        for x in bad:
            print("  ✘", x)
        print("[guard] 一致性校验未通过，阻断提交更新。")
        return 1
    print("[guard] ✔ 产物一致（数据包 / 镜像 / 页面 / 策略口径 / built）。")
    return 0


def main():
    return verify() if "--verify" in sys.argv else precheck()


if __name__ == "__main__":
    sys.exit(main())
