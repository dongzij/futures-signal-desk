# -*- coding: utf-8 -*-
"""数据新鲜度守卫（v27.2）—— 自愈重试 + 班次闸门 + 产物一致性校验

三个模式，对应 daily.yml 的三个不同阶段：

  python fresh_guard.py            写盘【之前】：只读预检行情源，取不到当日完整日线
                                  就等 3~5 分钟重取，最多 GUARD_MAX_TRY 轮。
  python fresh_guard.py --verify   构建【之后】：校验 7 品种末根一致、data/ 与
                                  dist/data/ 一致、页面与数据包一致，不通过就阻断提交。
  python fresh_guard.py --gate     仅输出「本班是否放行」（供 workflow 判断，不联网）。

为什么要放在写盘之前做「只读」预检
    daily.py 会写 state.json 并推微信，data_build.py 会清空重建 dist/data。
    如果直接把它们放进重试循环，一次失败重试就会重复写账本、重复推消息。
    所以先把「取数」这一件事反复做到位，再让正式流程只跑一次。

v27.2 修复（GitHub schedule 会延迟数小时）
    旧版：班次迟到 -> exit 3 -> job 标红 + 每天发失败邮件；
    有人临时把 return 3 改成 return 0，后果更严重：迟到班次直接跳过预检并写盘，
    把【昨天的 K 线】配上【今天的 built】发布上线，等于守卫被绕过。
    现在：CI 里通过 GITHUB_OUTPUT 输出 proceed=1/0，由 daily.yml 给每个写盘步骤
    加 if 条件。迟到 / 当日数据未到位 -> proceed=0 -> 全部写盘步骤跳过，
    job 绿色收尾且不产生任何文件改动；本地直接调用仍返回非零退出码，便于排查。

纪律边界（与 nightgate.py 一致）
    只在北京时间 15:00-20:00 的正式日盘窗口工作；重试若会跨过 20:00 立即停止。
    20:00 后绝不写盘 —— 夜盘半截行情属于下一交易日，写进去不可逆。
    人工补数用 workflow_dispatch 的 force=1（仍受 nightgate 夜盘窗口约束）。
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
CI = bool(os.environ.get("GITHUB_OUTPUT"))       # 在 Actions 里用「输出」代替退出码
FORCE = os.environ.get("GUARD_FORCE", "").strip() in ("1", "true", "yes")


def emit(key, val):
    """把决策写进 GITHUB_OUTPUT；本地直跑时只是空操作。"""
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s=%s\n" % (key, val))


def skip(reason, local_code):
    """统一收口「本班不写盘」：CI 里 proceed=0 + exit 0；本地返回非零码。"""
    emit("proceed", "0")
    emit("skip_reason", reason)
    print("::warning:: [guard] 本班跳过写盘：%s" % reason)
    return 0 if CI else local_code


def allow(reason):
    emit("proceed", "1")
    emit("skip_reason", "")
    print("[guard] ✔ 放行正式写盘：%s" % reason)
    return 0


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


def already_fresh(expect):
    """v27.2 幂等：仓库里已有当日完整日线且 built 是今天，就不重复写盘。

    15:20 与 15:50 是两班正式班次；旧代码两班都会跑 daily.py / data_build.py，
    会重复写 state.json、重复推微信。15:50 那班应在前班已成功时直接跳过。
    """
    try:
        idx = json.load(open(os.path.join(ROOT, "dist/data/index.json"),
                             encoding="utf-8"))
        if idx.get("built", "")[:10] != bj_now().date().isoformat():
            return False
        lasts = []
        for s in SYMS:
            d = json.load(open(os.path.join(ROOT, "dist/data", s + ".json"),
                               encoding="utf-8"))
            lasts.append(d["main"][-1][0])
        return len(lasts) == len(SYMS) and all(v >= expect for v in lasts)
    except Exception:                  # noqa: BLE001
        return False


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
            # v27.2：旧版只记异常类名，线上排障时看不出是超时、JSONP 变化还是 DNS。
            msg = str(e).replace("\n", " ").strip()[:120]
            err.append("%s:%s(%s)" % (c, type(e).__name__, msg))
    return last, err


def precheck():
    now = bj_now()
    print("[guard] 北京时间 %s · 窗口 15:00-20:00 · CI=%s · force=%s"
          % (now.strftime("%Y-%m-%d %H:%M"), CI, FORCE))
    if FORCE:
        # 人工补数：明确跳过新鲜度预检；nightgate.py 仍会拦住 20:00-08:00 的夜盘。
        print("::warning:: [guard] GUARD_FORCE=1，跳过新鲜度预检（人工补数）；"
              "夜盘窗口仍由 nightgate.py 强制拦截。")
        return allow("人工补数 force=1")
    if not in_window(now):
        print("[guard] 不在正式日盘窗口 15:00-20:00，拒绝写盘（GitHub schedule 迟到"
              " 6~10 小时属已知现象）。")
        print("[guard] 20:00 后只允许 night.yml 只读预警；本班不产生任何文件改动。")
        return skip("outside-window", 3)
    expect = expect_date(now)
    if already_fresh(expect):
        print("[guard] 仓库产物已含 %s 完整日线且 built 为今天，无需重复写盘。" % expect)
        return skip("already-fresh-today", 0)
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
            print("[guard] ✔ 7/7 品种当日日线到位。")
            return allow("7/7 品种当日日线到位")
        if i < MAX_TRY:
            # 用「起始时刻 + 累计轮次 × 间隔」推算，避免只看下一轮而漏掉
            # 连续多轮重试累积跨过 20:00 的情况。
            nxt = start + timedelta(seconds=RETRY_SEC * i)
            if not in_window(nxt):
                print("[guard] 下一轮将跨过 20:00 夜盘闸门，停止重试；"
                      "绝不写入夜盘半截 bar。")
                return skip("retry-crosses-nightgate", 1)
            print("[guard] 数据未到位，%d 秒后重新取数…" % RETRY_SEC)
            time.sleep(RETRY_SEC)
    last, err = final
    # v27.2：旧版在这里「复用旧数据放行」，会把昨天的末根配上今天的 built 发布，
    # 页面看起来更新了、实际是陈旧数据。休市 / 接口当日未出数一律跳过写盘，
    # 由外部精确触发器和 watchdog 在下一轮继续检查，绝不制造「假新鲜」。
    consistent = (not err and len(last) == len(SYMS)
                  and len(set(last.values())) == 1)
    if consistent:
        print("[guard] %d 轮后仍无当日新 bar，但 7/7 品种末根一致（%s）；"
              "判定为休市或数据源未出新数。" % (MAX_TRY, list(set(last.values()))[0]))
        return skip("no-new-bar-but-consistent", 1)
    print("[guard] ✘ %d 轮后数据仍不完整/不一致，阻断写盘，避免残缺数据入库。"
          % MAX_TRY)
    return skip("data-incomplete", 1)


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
    # v27.2：正式窗口内不允许「旧 K 线 + 今日 built」上线（#26 跑出来的假新鲜）。
    if lasts and in_window() and not FORCE:
        expect = expect_date()
        stale = sorted(v for v in lasts.values() if v < expect)
        if stale:
            bad.append("当日日线未到位却要发布（末根 %s < 期望 %s）"
                       % ("/".join(sorted(set(stale))), expect))

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


def gate():
    """纯时间闸门：不联网、不取数，只告诉 workflow 该不该往下走。"""
    now = bj_now()
    print("[guard] 北京时间 %s · force=%s" % (now.strftime("%Y-%m-%d %H:%M"), FORCE))
    if FORCE:
        return allow("人工补数 force=1")
    if not in_window(now):
        print("::warning:: [guard] 班次不在 15:00-20:00 正式日盘窗口，跳过全部写盘步骤。")
        return skip("outside-window", 3)
    return allow("班次在正式日盘窗口内")


def main():
    if "--gate" in sys.argv:
        return gate()
    if "--verify" in sys.argv:
        return verify()
    return precheck()


if __name__ == "__main__":
    sys.exit(main())
