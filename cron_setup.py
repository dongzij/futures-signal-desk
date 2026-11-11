# -*- coding: utf-8 -*-
"""v28.2 外部定时器接入（cron-job.org）—— 一键创建 / 查看 / 清理。

为什么要有这个东西
────────────────────────────────────────────────────────────────
GitHub 原生 schedule 是 best-effort：官方明说高负载会延迟，低活跃仓库
的班次会被直接丢弃。本仓库实测丢弃率约 80%（每天 9 个 cron 只来 1~2 个，
且延迟 6~10 小时落到深夜），完全不适合实盘。

解法：由外部定时器在【北京时间 15:25 / 15:50】精确打一次
`repository_dispatch`，事件名 `daily-scan`，直接触发 daily.yml。
这条路不经过 GitHub 的调度队列，触发率接近 100%，且与用户的电脑、手机
是否开机完全无关。

用法
────────────────────────────────────────────────────────────────
    # 1) 只看不建：核对将要提交的任务清单
    python cron_setup.py --dry

    # 2) 建任务（幂等：同名任务已存在就跳过，不会重复创建）
    CRONJOB_API_KEY=xxxx  GH_PAT=ghp_xxxx  python cron_setup.py --apply

    # 3) 看现在有哪些任务、下次什么时候跑、上次什么结果
    CRONJOB_API_KEY=xxxx  python cron_setup.py --list

    # 4) 删掉本脚本建的任务（不会动你手动建的其它任务）
    CRONJOB_API_KEY=xxxx  python cron_setup.py --remove

凭据从哪来
────────────────────────────────────────────────────────────────
  CRONJOB_API_KEY  cron-job.org → 右上头像 → Settings → API → 生成 API key
                   （免费账号就有这个入口，不需要付费）
  GH_PAT           GitHub → Settings → Developer settings → Personal access tokens
                   classic 勾 repo + workflow；fine-grained 给
                   Contents: Read and write（dispatches 属于 contents 写权限）

安全
────────────────────────────────────────────────────────────────
脚本不会把 PAT 写进任何文件、不会打印完整 PAT（只打印头尾）。PAT 只出现在
发给 api.cron-job.org 的 job 配置里，保存在你 cron-job.org 账号的服务端。
"""
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cron-job.org"
REPO = "dongzij/futures-signal-desk"
DISPATCH = "https://api.github.com/repos/%s/dispatches" % REPO

# 北京时间。cron-job.org 的 schedule 用「时区 + 小时/分钟/星期」表达，
# 这里显式写 Asia/Shanghai，不用 UTC 换算，避免夏令时/时区误会。
TZ = "Asia/Shanghai"
WD = [1, 2, 3, 4, 5]          # 周一~周五（0=周日）

JOBS = [
    {
        "title": "期货信号·日盘入库 15:25",
        "event": "daily-scan",
        "hours": [15], "minutes": [25],
        "note": "主班次。收盘 15:00 后数据源一般 15:10-15:20 放出日线。",
    },
    {
        "title": "期货信号·日盘入库 15:50",
        "event": "daily-scan",
        "hours": [15], "minutes": [50],
        "note": "补班次。主班次遇到数据源延迟时由这一班补上（已写过则幂等跳过）。",
    },
    {
        "title": "期货信号·数据看门狗 16:40",
        "event": "data-watchdog",
        "hours": [16], "minutes": [40],
        "note": "兜底。guard_watch 核对线上 7 品种是否已是当日日线，没到位就自己补跑一次。",
    },
    {
        "title": "期货信号·晚班尾单 21:53（静默）",
        "event": "daily-scan",
        "hours": [21], "minutes": [53],
        "note": "极少数数据源当晚才补发当日日线时才会写盘；该班次走 late 模式，微信已强制静音。",
    },
]


# ------------------------------------------------------------------ 工具

def _mask(s):
    if not s:
        return "(空)"
    return s[:7] + "…" + s[-4:] if len(s) > 14 else s[:3] + "…"


def cron_req(method, path, key, body=None):
    url = API + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    hdr = {"Authorization": "Bearer " + key,
           "Content-Type": "application/json",
           "User-Agent": "futures-signal-desk-setup/1.0"}
    r = urllib.request.Request(url, data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(r, timeout=45) as f:
            raw = f.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:400]
        raise SystemExit("cron-job.org %s %s 失败：HTTP %s\n%s" % (method, path, e.code, detail))


def job_payload(spec, pat):
    """构造 cron-job.org 的任务对象。requestMethod 1 = POST。"""
    return {
        "job": {
            "title": spec["title"],
            "url": DISPATCH,
            "enabled": True,
            "saveResponses": True,          # 保存响应体，出问题时能直接看到 GitHub 的错误
            "requestMethod": 1,             # POST
            "schedule": {
                "timezone": TZ,
                "expiresAt": 0,
                "hours": spec["hours"],
                "minutes": spec["minutes"],
                "mdays": [-1],              # 每天
                "months": [-1],             # 每月
                "wdays": WD,
            },
            "extendedData": {
                "headers": {
                    "Authorization": "Bearer " + pat,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Content-Type": "application/json",
                    "User-Agent": "cron-job.org",
                },
                "body": json.dumps({"event_type": spec["event"]}),
            },
            "notifications": {
                "onFailure": True,          # 失败发邮件（比微信安静，也不会半夜炸）
                "onSuccess": False,
                "onDisable": True,
            },
        }
    }


# ------------------------------------------------------------------ 动作

def do_dry(pat):
    print("将要创建 %d 个任务（时区 %s，周一~周五）：\n" % (len(JOBS), TZ))
    for s in JOBS:
        print("  %-34s 北京 %02d:%02d  %s" % (s["title"], s["hours"][0], s["minutes"][0], s["event"]))
        print("      %s" % s["note"])
    print("\n  POST %s" % DISPATCH)
    print("  Header  Authorization: Bearer %s" % _mask(pat))
    print("  每个任务的 body 例如：%s" % json.dumps({"event_type": "daily-scan"}))
    print("\n（这是 dry-run，没有发出任何请求）")


def do_list(key):
    d = cron_req("GET", "/jobs", key)
    jobs = d.get("jobs") or []
    if not jobs:
        print("账号下还没有任何任务。")
        return
    print("账号下共 %d 个任务：\n" % len(jobs))
    for j in jobs:
        sc = j.get("schedule") or {}
        print("  [%s] %s" % (j.get("jobId"), j.get("title")))
        print("       enabled=%s  method=%s  url=%s" %
              (j.get("enabled"), j.get("requestMethod"), j.get("url")))
        print("       时区=%s  hours=%s  minutes=%s  wdays=%s" %
              (sc.get("timezone"), sc.get("hours"), sc.get("minutes"), sc.get("wdays")))
        print("       上次 status=%s  下次执行=%s" %
              (j.get("lastStatus"), _ts(j.get("nextExecution"))))


def _ts(v):
    if not v:
        return "—"
    import datetime
    return datetime.datetime.fromtimestamp(
        int(v), datetime.timezone(datetime.timedelta(hours=8))).strftime("%m-%d %H:%M")


def do_apply(key, pat):
    d = cron_req("GET", "/jobs", key)
    have = {j.get("title"): j.get("jobId") for j in (d.get("jobs") or [])}
    print("账号下已有 %d 个任务，开始对账：\n" % len(have))
    for s in JOBS:
        if s["title"] in have:
            print("  ✓ 已存在，跳过  %s (id=%s)" % (s["title"], have[s["title"]]))
            continue
        body = job_payload(s, pat)
        # 先试 POST，个别版本只认 PUT
        try:
            r = cron_req("POST", "/jobs", key, body)
        except SystemExit as e:
            if "405" not in str(e):
                raise
            r = cron_req("PUT", "/jobs", key, body)
        jid = r.get("jobId") or r.get("job", {}).get("jobId")
        print("  + 已创建        %s (id=%s)" % (s["title"], jid))
    print("\n完成。下面是从服务端读回来的真实配置：\n")
    do_list(key)


def do_remove(key):
    d = cron_req("GET", "/jobs", key)
    titles = {s["title"] for s in JOBS}
    hit = [j for j in (d.get("jobs") or []) if j.get("title") in titles]
    if not hit:
        print("没有找到本脚本创建的任务。")
        return
    for j in hit:
        cron_req("DELETE", "/jobs/%s" % j["jobId"], key)
        print("  - 已删除  %s (id=%s)" % (j.get("title"), j.get("jobId")))


def main():
    argv = sys.argv[1:]
    key = (os.environ.get("CRONJOB_API_KEY") or "").strip()
    pat = (os.environ.get("GH_PAT") or "").strip()

    if "--dry" in argv:
        do_dry(pat)
        return
    if not key:
        raise SystemExit("缺少 CRONJOB_API_KEY。用法见文件头注释，或先跑 --dry 看清单。")
    if "--list" in argv:
        do_list(key)
    elif "--remove" in argv:
        do_remove(key)
    elif "--apply" in argv:
        if not pat:
            raise SystemExit("缺少 GH_PAT（GitHub 个人访问令牌），否则 cron-job.org 打不动 dispatch。")
        do_apply(key, pat)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
