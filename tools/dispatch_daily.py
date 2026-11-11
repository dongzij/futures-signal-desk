# -*- coding: utf-8 -*-
"""每日准时触发器（v27.2） —— 绕开 GitHub Actions schedule 的 6~10 小时延迟

背景
    本仓库所有 schedule 班次实测都晚点 6~10 小时（07:20 UTC 的 cron 在 15:37 UTC
    才落跑）。GitHub 官方文档也说明：高负载时 schedule 事件会排队、可能延迟甚至漏跑，
    因此**不能**把「每天按时更新」押在 schedule 上。
    workflow_dispatch / repository_dispatch 是即时触发的：外部定时器在北京时间
    15:20 调一次 GitHub API，workflow 立刻开跑，fresh_guard.py 再确认当日日线到位。

用法
    # 1) 只体检，不需要 token（可随时跑）
    python tools/dispatch_daily.py --check

    # 2) 触发正式班次（需 PAT）
    set GH_TOKEN=ghp_xxx            # Windows CMD
    export GH_TOKEN=ghp_xxx        # bash / 云函数
    python tools/dispatch_daily.py

    # 3) 白天人工补数（跳过窗口预检；夜盘 20:00-08:00 仍被 nightgate 拦截）
    python tools/dispatch_daily.py --force 1

    # 4) 触发后等待运行结果（供自动化任务判断是否成功）
    python tools/dispatch_daily.py --wait

    # 5) 其它三个只读班次也用外部定时器准点触发（repository_dispatch）
    python tools/dispatch_daily.py --workflow intraday   # 北京时间 11:33
    python tools/dispatch_daily.py --workflow night      # 北京时间 23:10
    python tools/dispatch_daily.py --workflow watchdog   # 15:55~18:40 巡检

PAT 要求
    经典 PAT 勾选 repo；或细粒度 PAT 对该仓库授予 Actions: read and write。
    也可把 token 放在 ~/.config/futures-signal-desk/github_pat（不要写进仓库）。
    外部定时器建议：cron-job.org / 腾讯云函数 SCF / 服务器 crontab / Windows 任务计划，
    北京时间周一至周五 15:20 触发一次，15:50 再补一次更保险。

安全边界
    本脚本只负责「触发」，不写任何数据文件；真正能否写盘仍由 fresh_guard.py 的
    15:00-20:00 窗口 + 7 品种当日日线预检 + nightgate.py 三重闸门决定。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = "dongzij/futures-signal-desk"
WORKFLOW = "daily.yml"
API = "https://api.github.com"
SYMS = ["MA", "HC", "Y", "SM", "FG", "P", "M"]
EVENTS = {
    "daily": "daily-scan",            # 走 workflow_dispatch（可带 force 输入）
    "intraday": "intraday-warn",
    "night": "night-warn",
    "watchdog": "data-watchdog",
}
BJ = timezone(timedelta(hours=8))
TOKEN_FILE = os.path.expanduser("~/.config/futures-signal-desk/github_pat")


def token():
    t = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if t:
        return t
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def api(method, path, body=None, auth=False, timeout=30, tries=3):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    hdr = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "futures-signal-desk-on-time-trigger",
        "Cache-Control": "no-cache",
    }
    t = token()
    if auth and t:
        hdr["Authorization"] = "Bearer " + t
    if data is not None:
        hdr["Content-Type"] = "application/json"
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(API + path, data=data, headers=hdr, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw or b"{}")
        except urllib.error.HTTPError as e:
            last = e
            detail = e.read().decode("utf-8", "ignore")[:300]
            print("[trigger] HTTP %d %s: %s" % (e.code, e.reason, detail), file=sys.stderr)
            if e.code in (401, 403, 404, 422):
                break                       # 凭据/参数问题，重试无意义
        except Exception as e:              # noqa: BLE001
            last = e
        if i < tries - 1:
            time.sleep(min(2 * (2 ** i), 8))
    raise last or RuntimeError("API 调用失败")


def check():
    """只读体检：线上 built、7 品种末根、最近 3 次 daily-scan 状态。无需 token。"""
    now = datetime.now(BJ)
    print("[check] 北京时间 %s" % now.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        req = urllib.request.Request(
            "https://raw.githubusercontent.com/%s/main/data/index.json?_=%d"
            % (REPO, int(time.time())),
            headers={"User-Agent": "futures-signal-desk-check", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=30) as r:
            idx = json.loads(r.read())
        print("[check] index.built = %s" % idx.get("built"))
        lasts = {}
        for s in SYMS:
            req = urllib.request.Request(
                "https://raw.githubusercontent.com/%s/main/data/%s.json?_=%d"
                % (REPO, s, int(time.time())),
                headers={"User-Agent": "futures-signal-desk-check", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=30) as r:
                lasts[s] = json.loads(r.read())["main"][-1][0]
        print("[check] 7 品种末根 = %s" % "/".join(sorted(set(lasts.values()))))
        expect = now.date().isoformat()
        fresh = all(d >= expect for d in lasts.values())
        print("[check] 当日(%s)数据已到位 = %s" % (expect, fresh))
    except Exception as e:                  # noqa: BLE001
        print("[check] 读取线上产物失败：%s: %s" % (type(e).__name__, e), file=sys.stderr)
    try:
        runs = api("GET", "/repos/%s/actions/workflows/%s/runs?per_page=3" % (REPO, WORKFLOW))
        for r in (runs.get("workflow_runs") or []):
            print("[check] run #%s %s %s %s (%s)"
                  % (r["run_number"], r["created_at"], r["status"],
                     r["conclusion"], r["event"]))
    except Exception as e:                  # noqa: BLE001
        print("[check] 运行列表读取失败：%s" % type(e).__name__, file=sys.stderr)
    return 0


def dispatch(force="0", ref="main", workflow="daily"):
    if not token():
        print("[trigger] ✘ 缺少 GH_TOKEN / GITHUB_TOKEN（也可放 %s）。" % TOKEN_FILE,
              file=sys.stderr)
        print("[trigger] 创建 PAT：GitHub → Settings → Developer settings → "
              "Personal access tokens；经典 PAT 勾选 repo，或细粒度 PAT 授予 Actions: write。",
              file=sys.stderr)
        return 2
    stamp = datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")
    if workflow == "daily":
        body = {"ref": ref, "inputs": {"force": str(force)}}
        api("POST", "/repos/%s/actions/workflows/%s/dispatches" % (REPO, WORKFLOW),
            body, auth=True)
        print("[trigger] ✔ 已派发 %s @ %s (force=%s)，北京时间 %s"
              % (WORKFLOW, ref, force, stamp))
    else:
        event = EVENTS[workflow]
        api("POST", "/repos/%s/dispatches" % REPO,
            {"event_type": event, "client_payload": {"source": "external-cron"}},
            auth=True)
        print("[trigger] ✔ 已派发 repository_dispatch[%s]，北京时间 %s" % (event, stamp))
    return 0


def wait(ref="main", max_sec=1800):
    """等待本次派发的 daily-scan 出结果（供自动化任务判定成败）。"""
    url = "/repos/%s/actions/workflows/%s/runs?per_page=5" % (REPO, WORKFLOW)
    started = time.time()
    seen = set()
    while time.time() - started < max_sec:
        try:
            runs = api("GET", url)
            for r in (runs.get("workflow_runs") or []):
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                if r["event"] != "workflow_dispatch":
                    continue
                if r["status"] == "completed":
                    print("[wait] run #%s conclusion=%s %s"
                          % (r["run_number"], r["conclusion"], r["html_url"]))
                    return 0 if r["conclusion"] == "success" else 1
                print("[wait] run #%s status=%s ..." % (r["run_number"], r["status"]))
        except Exception as e:              # noqa: BLE001
            print("[wait] 查询失败 %s，稍后重试" % type(e).__name__, file=sys.stderr)
        time.sleep(30)
    print("[wait] 等待超时（%d 秒）" % max_sec, file=sys.stderr)
    return 1


def main():
    p = argparse.ArgumentParser(description="每日准时触发 daily-scan")
    p.add_argument("--check", action="store_true", help="只体检线上数据与最近运行状态")
    p.add_argument("--force", choices=["0", "1"], default="0", help="1=白天人工补数模式")
    p.add_argument("--ref", default="main")
    p.add_argument("--workflow", choices=sorted(EVENTS), default="daily",
                   help="要触发的班次（默认 daily）")
    p.add_argument("--wait", action="store_true", help="触发后等待运行结果（仅 daily）")
    a = p.parse_args()
    if a.check:
        return check()
    rc = dispatch(a.force, a.ref, a.workflow)
    if rc == 0 and a.wait and a.workflow == "daily":
        return wait(a.ref)
    return rc


if __name__ == "__main__":
    sys.exit(main())
