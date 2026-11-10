# -*- coding: utf-8 -*-
"""数据更新 watchdog（v27.1）—— 处理「整个班次根本没触发」的情况

fresh_guard.py 负责 job 内部的取数自愈；本脚本负责 job 外部的补触发：
GitHub schedule 在国内/高峰时段会延迟甚至漏跑（本项目今天就整班缺席）。

每 25~30 分钟（15:55~18:40 北京时间）跑一次，只读检查线上产物：
  · data/index.json 的 built 是否是今天 15:20 之后；
  · 7 品种末根是否齐全且彼此一致（防「部分品种更新 / 数据缺失」）。
不满足就通过 GitHub API 触发一次 daily.yml（仍是同一套正式写盘流程）。

纪律边界
  · 北京时间 >= 20:00 一律【不触发】，只打 warning —— 夜盘半截行情属于
    下一交易日，绝不能让补跑把半截 bar 写进日线。
  · 已有 in_progress / queued 的 daily-scan 时跳过，避免打断或重复写 state.json。
  · built 距今 < 12 分钟跳过，避免刚跑完就重复触发。
  · 全程只读，不写任何数据文件。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = "dongzij/futures-signal-desk"
API = "https://api.github.com"
WORKFLOW = "daily.yml"
SYMS = ["MA", "HC", "Y", "SM", "FG", "P", "M"]
BJ = timezone(timedelta(hours=8))
WIN_FROM, WIN_TO = (15, 0), (20, 0)
MIN_GAP_MIN = 12
TOKEN = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def bj_now():
    return datetime.now(BJ).replace(tzinfo=None)


def in_window(now=None):
    hm = ((now or bj_now()).hour, (now or bj_now()).minute)
    return WIN_FROM <= hm < WIN_TO


def api(method, path, body=None, raw=False, tries=4):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    hdr = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
           "User-Agent": "fresh-guard-watch",
           "X-GitHub-Api-Version": "2022-11-28",
           "Cache-Control": "no-cache"}
    if TOKEN:
        hdr["Authorization"] = "Bearer " + TOKEN
    if data is not None:
        hdr["Content-Type"] = "application/json"
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(API + path, data=data, headers=hdr, method=method)
            with urllib.request.urlopen(req, timeout=45) as f:
                b = f.read()
                return b if raw else json.loads(b or b"{}")
        except Exception as e:              # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(min(3 * (2 ** i), 15))
    raise last


def read_json(path):
    p = "/repos/%s/contents/%s?ref=main&_=%d" % (REPO, path, int(time.time()))
    return json.loads(api("GET", p, raw=True))


def parse_built(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except Exception:
        return None


def main():
    now = bj_now()
    print("[watch] 北京时间 %s · 检查线上 %s" % (now.strftime("%Y-%m-%d %H:%M"), REPO))
    if not in_window(now):
        print("::warning:: [watch] 已过 20:00 夜盘闸门，本轮【不触发】任何写盘补跑；"
              "夜盘只读预警由 night.yml 负责。")
        return 0

    try:
        idx = read_json("data/index.json")
        built = idx.get("built", "")
        lasts, errs = {}, []
        for s in SYMS:
            try:
                lasts[s] = read_json("data/%s.json" % s)["main"][-1][0]
            except Exception as e:          # noqa: BLE001
                errs.append("%s:%s" % (s, type(e).__name__))
    except Exception as e:                  # noqa: BLE001
        print("::warning:: [watch] 读取线上产物失败（%s），国内网络可能抖动；"
              "已重试 4 次，本轮不做判断、不误触发。" % type(e).__name__)
        return 0

    consistent = (not errs and len(lasts) == len(SYMS)
                  and len(set(lasts.values())) == 1)
    bdt = parse_built(built)
    fresh = bool(bdt and bdt.date() == now.date()
                 and (bdt.hour, bdt.minute) >= (15, 20))
    print("[watch] built = %s · 末根 = %s · 一致 = %s · 当日已更新 = %s"
          % (built, "/".join(sorted(set(lasts.values()))) or "-", consistent, fresh))

    if fresh and consistent:
        print("[watch] ✔ 今日正式数据已到位且 7 品种一致，无需补跑。")
        return 0
    if bdt and 0 <= (now - bdt).total_seconds() < MIN_GAP_MIN * 60:
        print("[watch] 上次构建距今不足 %d 分钟，跳过本轮，避免重复触发。"
              % MIN_GAP_MIN)
        return 0
    try:
        runs = api("GET", "/repos/%s/actions/workflows/%s/runs?per_page=10"
                   % (REPO, WORKFLOW))
        busy = [r for r in (runs.get("workflow_runs") or [])
                if r.get("status") in ("in_progress", "queued", "pending")]
        if busy:
            print("[watch] 已有 %d 个 daily-scan 正在跑/排队，跳过补触发。" % len(busy))
            return 0
    except Exception as e:                  # noqa: BLE001
        print("::warning:: [watch] 运行状态查询失败（%s），仍按产物判断继续。"
              % type(e).__name__)

    if not TOKEN:
        print("::warning:: [watch] 缺少 GH_TOKEN，无法触发补跑；仅记录检查结果。")
        return 0
    reason = []
    if not fresh:
        reason.append("built 未刷新到今日 15:20 之后")
    if not consistent:
        reason.append("7 品种末根不齐/不一致 %s" % (errs or lasts))
    print("[watch] → 触发 daily.yml 补跑：%s" % "；".join(reason))
    try:
        api("POST", "/repos/%s/actions/workflows/%s/dispatches" % (REPO, WORKFLOW),
            {"ref": "main"})
        print("[watch] ✔ 已派发正式班次（仍走 fresh_guard 预检 + nightgate 闸门）。")
        return 0
    except Exception as e:                  # noqa: BLE001
        print("::warning:: [watch] 派发失败：%s（下一轮会再试）" % e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
