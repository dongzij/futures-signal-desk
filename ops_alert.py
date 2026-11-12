# -*- coding: utf-8 -*-
"""运维告警（v29）—— 数据链路出问题时，推一条微信给用户。

为什么要有它
    这套系统最怕的不是报错，是【静默地没更新】。2026-11-12 当天就出现过
    连续几班失败却没人知道：15:37 数据源没放出、15:55 抓取残缺被守卫阻断、
    后面几班 engine 崩溃 —— Actions 里只是几个红叉，手机一条消息都没有，
    等晚上打开页面才发现还是昨天的图。用户要求：
    「如果失败、报错或没有触发等你要及时处理，如果处理不了的应该在之后推送微信给我」。

两个触发点（其余情况一律不推，避免打扰）
    ① daily.yml 任一环节失败 → python ops_alert.py --job-failed "<原因>"
    ② data-watchdog 收盘终检（北京 20:20 之后，由外部定时器触发）
       → python ops_alert.py --final
       达标就静默；built 没刷新 / 7 品种末根不齐 / 还是昨天的图 → 推告警。

纪律
    · 只读线上产物，不写任何数据文件（与 guard_watch.py 同一约束）；
    · 走 PushPlus（与信号推送同一通道），token 取 PUSHPLUS_TOKEN，兜底读 config.json；
    · 通道缺失时在日志里明说「未通知」，绝不伪造已通知；
    · 本告警是【运维类】，与交易信号无关，不受 daily.post_push 的 15:00-20:00 静音闸门约束
      —— 那个闸门是为了不让半夜收到信号，不是为了让故障也静默。
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                              # noqa: BLE001
    pass

SYMS = ["MA", "HC", "Y", "SM", "FG", "P", "M"]
BJ = timezone(timedelta(hours=8))
FINAL_AT = (20, 20)          # 收盘终检时点：北京 20:20 之后才判定，早于此刻只打印不推
REPO = "dongzij/futures-signal-desk"


def bj_now():
    return datetime.now(BJ).replace(tzinfo=None)


def _token():
    tok = (os.environ.get("PUSHPLUS_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        return (json.load(open("config.json", encoding="utf-8")).get("pushplus") or "").strip()
    except Exception:                                          # noqa: BLE001
        return ""


def push(title, html):
    """返回 True 才算真的发出去了；没通道/失败都返回 False。"""
    tok = _token()
    if not tok:
        print("::error:: [alert] 没有 PUSHPLUS_TOKEN，本条告警【未能通知】：" + title)
        return False
    body = urllib.parse.urlencode({
        "token": tok, "title": title, "content": html, "template": "html"}).encode()
    try:
        req = urllib.request.Request(
            "https://www.pushplus.plus/send", data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8", "ignore"))
        ok = j.get("code") == 200
        print("[alert] 推送%s：%s" % ("成功" if ok else "失败 " + str(j), title))
        return ok
    except Exception as e:                                     # noqa: BLE001
        print("::error:: [alert] 推送异常 %s：%s" % (type(e).__name__, title))
        return False


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def snapshot():
    """线上产物快照：built / 各品种末根 / 是否一致 / 是否刷新到今天。"""
    idx = read_json("data/index.json")
    built = idx.get("built", "")
    lasts, missing = {}, []
    for s in SYMS:
        try:
            lasts[s] = read_json("data/%s.json" % s)["main"][-1][0]
        except Exception:                                      # noqa: BLE001
            missing.append(s)
    consistent = (not missing and len(lasts) == len(SYMS)
                  and len(set(lasts.values())) == 1)
    bdt = None
    try:
        bdt = datetime.strptime(built, "%Y-%m-%d %H:%M")
    except Exception:                                          # noqa: BLE001
        pass
    now = bj_now()
    fresh = bool(bdt and bdt.date() == now.date() and (bdt.hour, bdt.minute) >= (15, 20))
    return {"built": built, "built_at": bdt, "lasts": lasts, "missing": missing,
            "consistent": consistent, "fresh": fresh, "now": now}


def _today_runs(n=15):
    """今天每天班次的成败概况（拿不到就返回空，不影响判定）。"""
    tok = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not tok:
        return None
    url = ("https://api.github.com/repos/%s/actions/workflows/daily.yml/runs?per_page=%d"
           % (REPO, n))
    try:
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + tok,
            "Accept": "application/vnd.github+json",
            "User-Agent": "ops-alert"})
        with urllib.request.urlopen(req, timeout=20) as r:
            runs = json.loads(r.read().decode("utf-8", "ignore")).get("workflow_runs") or []
    except Exception:                                          # noqa: BLE001
        return None
    today = bj_now().date()
    tot = fail = 0
    for r in runs:
        try:
            d = datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc).astimezone(BJ).date()
        except Exception:                                      # noqa: BLE001
            continue
        if d != today:
            continue
        tot += 1
        if r.get("conclusion") == "failure":
            fail += 1
    return {"total": tot, "fail": fail}


def _html(s, headline, reason_lines):
    lasts = s["lasts"]
    uniq = sorted(set(lasts.values()))
    rows = "".join("<tr><td>%s</td><td>%s</td></tr>" % (k, v) for k, v in lasts.items())
    if s["missing"]:
        rows += "<tr><td>缺失</td><td>%s</td></tr>" % "、".join(s["missing"])
    return (
        "<div style='font-size:15px;font-weight:700;color:#c0392b'>%s</div>"
        "<p style='margin:8px 0'>数据构建时间：<b>%s</b><br>7 品种末根：<b>%s</b>%s</p>"
        "%s"
        "<table style='border-collapse:collapse;font-size:13px;margin:8px 0'>"
        "<tr><th style='text-align:left;padding-right:12px'>品种</th>"
        "<th style='text-align:left'>最后交易日</th></tr>%s</table>"
        "<p style='margin:8px 0;font-size:13px;color:#555'>查看运行记录："
        "<a href='https://github.com/%s/actions'>GitHub Actions</a><br>"
        "本条为运维告警（非交易信号），数据恢复后自动停止。</p>"
        % (headline, s["built"] or "（空）",
           "、".join(uniq) or "（无）",
           "" if s["consistent"] else " · <b>品种间不一致</b>",
           "".join("<p style='margin:4px 0;font-size:13px'>%s</p>" % x for x in reason_lines),
           rows, REPO))


def final_check():
    """收盘终检：只在 20:20 之后判定；达标静默，不达标推送。"""
    now = bj_now()
    if (now.hour, now.minute) < FINAL_AT:
        print("[alert] %s 未到终检时点 %02d:%02d，本轮不判定、不推送。"
              % (now.strftime("%H:%M"), FINAL_AT[0], FINAL_AT[1]))
        return 0
    try:
        s = snapshot()
    except Exception as e:                                     # noqa: BLE001
        print("::warning:: [alert] 读不到线上产物（%s），可能正在重建，本轮不判定。"
              % type(e).__name__)
        return 0
    print("[alert] 终检 built=%s · 末根=%s · 一致=%s · 当日已更新=%s"
          % (s["built"], "/".join(sorted(set(s["lasts"].values()))) or "-",
             s["consistent"], s["fresh"]))
    if s["fresh"] and s["consistent"]:
        print("[alert] ✔ 今日数据已到位，保持静默（不打扰）。")
        return 0

    reasons = []
    if not s["fresh"]:
        reasons.append("· 今日数据未入库：构建时间仍停在 <b>%s</b>" % (s["built"] or "空"))
    if not s["consistent"]:
        reasons.append("· 7 品种末根不齐（见下表）")
    runs = _today_runs()
    if runs is not None:
        reasons.append("· 今日 daily-scan 班次：共 %d 个，失败 %d 个"
                       % (runs["total"], runs["fail"]))
    reasons.append("· 已过 20:00 禁写时段，夜盘数据属于下一交易日，<b>今天不能再自动补</b>。"
                   "明早 15:25 班次会自动补上，无需人工干预；若连续两天出现，请检查数据源。")
    push("【运维告警】期货数据未更新 · %s" % now.strftime("%m-%d"),
         _html(s, "⚠️ 今日行情数据没有入库", reasons))
    return 0


def job_failed(reason):
    """某个班次跑挂了：立即推，附班次链接。"""
    now = bj_now()
    try:
        s = snapshot()
    except Exception:                                          # noqa: BLE001
        s = {"built": "（读不到产物）", "lasts": {}, "missing": SYMS,
             "consistent": False, "fresh": False, "now": now}
    reasons = ["· 失败环节：%s" % (reason or "未指明"),
               "· 自愈已尝试：班次内 fresh_guard 会重试 5 轮取数；本条说明最终仍未成功。",
               "· 若今晚 20:00 前仍无恢复，次日 15:25 班次会自动补齐全历史（不会丢数据）。"]
    push("【运维告警】数据班次失败 · %s %s" % (now.strftime("%m-%d"), now.strftime("%H:%M")),
         _html(s, "⚠️ 数据构建班次失败", reasons))
    return 0


def main(argv):
    if "--final" in argv:
        return final_check()
    if "--job-failed" in argv:
        i = argv.index("--job-failed")
        return job_failed(argv[i + 1] if len(argv) > i + 1 else "")
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
