# -*- coding: utf-8 -*-
"""夜盘闸门（night gate）——所有「会写盘」的入口都必须先过这里

为什么需要它
    期货交易日 = 前一晚 21:00-23:00 夜盘 + 当日 09:00-15:00 日盘。
    23:10 那一刻，夜盘只走了半截，而且这段行情属于【下一个交易日】。
    只要有人（或某个 cron 配错）在这个时点跑 daily.py / data_build.py，
    夜盘就会被当成「今天」或「明天」的完整日线写进 K 线与 state.json，
    日线图从此永久带一条半截假 bar —— 不可逆。

闸门规则
    北京时间 20:00 - 次日 08:00 视为夜盘窗口。
    · 15:20 / 15:50 两班永远在窗口外，不受影响；
    · 窗口内，写盘入口直接拒绝执行（exit 3），日志写明原因与替代命令；
    · 只读入口（night_warn.py / intraday_warn.py）不过闸门，它们本来就不写盘。

留出 20:00 起的余量
    GitHub schedule 在高峰会延迟几十分钟，15:50 那班可能落到 16:3x，
    离 20:00 仍有充足间隔；反过来 21:00 开盘前也不会误伤任何正常班次。

人工补数
    RUN_MODE=force python daily.py
    —— 只有明确知道自己在补哪一天的数据时才用，CI 永远不带这个变量。
"""
import datetime as _dt
import os
import sys

BJ = _dt.timezone(_dt.timedelta(hours=8))
NIGHT_FROM = 20      # 北京时间 20:00 起算夜盘窗口
NIGHT_TO = 8         # 次日 08:00 结束


def bj_now():
    """北京时间（naive）。本机与 GitHub runner（UTC）得到同一个值。"""
    return _dt.datetime.now(BJ).replace(tzinfo=None)


def in_night(now=None):
    now = now or bj_now()
    return now.hour >= NIGHT_FROM or now.hour < NIGHT_TO


def forced(argv=None):
    argv = sys.argv if argv is None else argv
    return (os.environ.get("RUN_MODE", "").strip().lower() in ("force", "manual")
            or "--force" in argv)


def run_mode():
    """CI 里由 fresh_guard 的 run_mode 输出决定：'' / late / force。"""
    return os.environ.get("RUN_MODE", "").strip().lower()


def enforce(who, argv=None):
    """写盘入口的第一行调用。夜盘窗口内直接退出，绝不往下走。"""
    if forced(argv):
        print("[gate] RUN_MODE=force（人工补数），跳过夜盘闸门：%s" % who)
        return
    now = bj_now()
    if not in_night(now):
        return
    if run_mode() == "late":
        # v28：late 班次（20:00-23:00 兜底）。
        # 安全性不再靠时间，而靠 build.asof_day()：所有日线在离开 build.py
        # 之前已按「最后一个已收盘交易日」截断，夜盘那半截（属于下一交易日）
        # 物理上不可能进入 K 线。所以这里可以放行，写的是今天 15:00 的终值。
        import build
        print("[gate] RUN_MODE=late —— 数据已按 asof=%s 截断（夜盘半截 bar 已剔除），"
              "允许写入正式日线：%s" % (build.asof_str(), who))
        return
    print("[gate] 北京时间 %s —— 处于夜盘窗口 %02d:00-08:00。" % (
        now.strftime("%Y-%m-%d %H:%M"), NIGHT_FROM))
    print("[gate] %s 会写 state.json / data/，夜盘半截行情属于下一交易日，" % who)
    print("[gate] 入库会永久污染日线图与账本，已拒绝执行（exit 3）。")
    print("[gate] 夜盘只看不写：python night_warn.py")
    print("[gate] late 班次请用 RUN_MODE=late（数据自动截断到已收盘交易日）")
    print("[gate] 确需人工补数：RUN_MODE=force python %s" % who)
    sys.exit(3)


if __name__ == "__main__":
    now = bj_now()
    print("北京时间 %s" % now.strftime("%Y-%m-%d %H:%M"))
    print("夜盘窗口 %02d:00-08:00：%s" % (NIGHT_FROM, "是" if in_night(now) else "否"))
