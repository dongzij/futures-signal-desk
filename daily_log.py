# -*- coding: utf-8 -*-
"""v17 每日极简日志（用户 2026-11-08 拍板：进入静默观察期，只记录不优化）

在 GitHub Actions 的 15:20 扫描 + 数据包重建之后运行：
    python daily.py → python data_build.py → python render.py → python daily_log.py

只读 dist/data/*.json 与 state.json，输出：
    logs/YYYY-MM-DD.md   当天极简日志（同一天重跑覆盖，幂等）
    LIVE_LOG.md          累计总日志（由 logs/*.md 重新生成，重复跑不会产生重复条目）

**只做记录**：不产生信号、不改 MA34 进场、不改 MA26 + 吊灯 3ATR 出场、
不新增指标或过滤规则、不额外发微信推送。
"""
import datetime as dt
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "dist", "data")
LOGS = os.path.join(ROOT, "logs")
LIVE = os.path.join(ROOT, "LIVE_LOG.md")
TZ = dt.timezone(dt.timedelta(hours=8))


def jload(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def fmt_yuan(x):
    try:
        return "{:+,}".format(int(round(float(x))))
    except Exception:
        return "—"


def one_line(items, empty="无"):
    return "、".join(items) if items else empty


def collect():
    man = jload(os.path.join(DATA, "index.json"), {}) or {}
    state = jload(os.path.join(ROOT, "state.json"), {}) or {}
    codes = [p["sym"] for p in (man.get("products") or [])] or \
        ["FG", "SA", "M", "SP", "SM", "MA", "SR", "C"]
    docs = {c: jload(os.path.join(DATA, c + ".json"), {}) or {} for c in codes}
    bar_date = max((d["main"][-1][0] for d in docs.values()
                    if d.get("main")), default="—")

    # —— 当日（最新一根 K 线）触发的全部事件 ——
    sig_in, xs_out, def_strong, def_weak, mw_star = [], [], [], [], []
    for c, d in docs.items():
        nm = d.get("name") or c
        for s in d.get("signals") or []:
            if s.get("date") != bar_date or s.get("kind") == "confirm":
                continue
            k = "建仓" if s.get("kind") == "pred" else "预警"
            m = s.get("meta") or {}
            tier = "顺势" if m.get("weekAlign") is not False else \
                   ("观察" if m.get("observe") else "逆势强档")
            sig_in.append("%s %s %s·%s(%s)" % (
                c, nm, k, "多" if s.get("side") == "long" else "空", tier))
        x = (d.get("xsig") or {}).get("now") or {}
        if x.get("date") == bar_date and x.get("level") in ("suggest", "strong"):
            xs_out.append("%s %s %s·%s" % (
                c, nm, x.get("dir") or "—",
                "强烈离场" if x.get("level") == "strong" else "建议离场"))
        for e in d.get("defend") or []:
            if e.get("date") != bar_date:
                continue
            txt = "%s %s %s(%s)" % (
                c, nm, e.get("sigLabel") or "—",
                "多" if e.get("side") == "long" else "空")
            (def_weak if e.get("weak") else def_strong).append(txt)
        for w in d.get("mw") or []:
            if w.get("date") == bar_date:
                mw_star.append("%s %s %s·%s" % (
                    c, nm, w.get("dir") or "—", w.get("price")))

    # —— 持仓 / A' 1.5ATR 状态 ——
    opens = man.get("strategyOpen") or []
    pos, a_prime = [], []
    for p in opens:
        r = float(p.get("R") or 0)
        mfe = float(p.get("mfe") or 0)
        pos.append("%s%s %+.2fR" % (
            p.get("sym"), "多" if p.get("side") == "long" else "空", r))
        if max(mfe, r) >= 1.5:
            a_prime.append("%s(峰值 +%.2fATR)" % (p.get("sym"), max(mfe, r)))

    # —— 累计盈亏与回撤（按平仓日排序的权益曲线）——
    closed = [s for s in (state.get("signals") or [])
              if s.get("kind") == "pred" and s.get("status") == "closed"
              and s.get("exitDate")]
    closed.sort(key=lambda s: s["exitDate"])
    cum = peak = 0.0
    maxdd = 0.0
    for s in closed:
        cum += float(s.get("yuan") or 0)
        peak = max(peak, cum)
        maxdd = min(maxdd, cum - peak)
    stats = state.get("stats") or {}
    return {
        "man": man, "state": state, "codes": codes, "docs": docs,
        "bar_date": bar_date, "sig_in": sig_in, "xs_out": xs_out,
        "def_strong": def_strong, "def_weak": def_weak, "mw_star": mw_star,
        "pos": pos, "a_prime": a_prime, "n_open": len(opens),
        "n_closed": len(closed), "win": stats.get("win"),
        "cum": cum, "maxdd": maxdd, "curdd": cum - peak,
        # 全量口径（含弱叉），用于观察期核对数据完整性
        "def_total": sum(len(d.get("defend") or []) for d in docs.values()),
        "weak_total": sum(1 for d in docs.values()
                          for e in (d.get("defend") or []) if e.get("weak")),
        "mw_total": sum(len(d.get("mw") or []) for d in docs.values()),
    }


def render(c, day):
    L = []
    L.append("# %s · 极简日志" % day)
    L.append("")
    L.append("数据截至 **%s** ｜ 品种 %d ｜ 模式：**静默观察期**（只记录，不加新指标/规则）"
             % (c["bar_date"], len(c["codes"])))
    L.append("")
    L.append("- **进场**：%s" % one_line(c["sig_in"]))
    L.append("- **出场雷达**：%s" % one_line(c["xs_out"]))
    L.append("- **○ 失效（强）**：%d 条 · %s" %
             (len(c["def_strong"]), one_line(c["def_strong"])))
    L.append("- **◌ 弱叉（仅记录）**：%d 条 · %s" %
             (len(c["def_weak"]), one_line(c["def_weak"])))
    L.append("- **★ 二段M/W**：%d 条 · %s" %
             (len(c["mw_star"]), one_line(c["mw_star"])))
    L.append("- **策略持仓**：%d 笔 · %s" %
             (c["n_open"], one_line(c["pos"], "无")))
    L.append("- **A' 1.5ATR 触发**：%s" % one_line(c["a_prime"]))
    L.append("- **累计**：已平 %d 笔 · 胜率 %s%% · 净 ¥%s · 最大回撤 ¥%s · 当前回撤 ¥%s" %
             (c["n_closed"], c["win"] if c["win"] is not None else "—",
              fmt_yuan(c["cum"]), fmt_yuan(c["maxdd"]), fmt_yuan(c["curdd"])))
    L.append("- **历史口径**：失效 %d 条（其中弱叉 %d）· ★M/W %d 条（观察期 3~6 个月，不加码）" %
             (c["def_total"], c["weak_total"], c["mw_total"]))
    L.append("")
    L.append("> MA34 进场、MA26 + 吊灯 3ATR 出场不变；弱叉只上空心圈 / 标「弱」/ 不单独推微信。")
    L.append("")
    return "\n".join(L)


def main():
    cfg = jload(os.path.join(ROOT, "config.json"), {}) or {}
    if cfg.get("dailyLog") is False:
        print("dailyLog=false：已按配置关闭每日极简日志")
        return
    c = collect()
    today = dt.datetime.now(TZ).strftime("%Y-%m-%d")
    # 允许用环境变量覆盖日期（补跑 / 预览用）
    day = os.environ.get("LOG_DATE") or today
    os.makedirs(LOGS, exist_ok=True)
    text = render(c, day)
    with open(os.path.join(LOGS, day + ".md"), "w", encoding="utf-8") as f:
        f.write(text)

    # 累计总日志：由全部单日文件重建，天然幂等（15:50 兜底重跑不会重复追加）
    days = sorted((f[:-3] for f in os.listdir(LOGS)
                   if f.endswith(".md") and len(f) == 13), reverse=True)
    head = [
        "# 实盘极简日志 · 静默观察期",
        "",
        "自 2026-11-09 起，每个交易日 15:20 自动扫描后自动生成。",
        "只记录信号 / ○ 失效 / ◌ 弱叉 / ★ M/W / 持仓与回撤，不做任何优化。",
        "MA34 进场、MA26 + 吊灯 3ATR 出场不变。",
        "",
        "共 %d 天 ｜ 最新 %s" % (len(days), days[0] if days else "—"),
        "",
    ]
    body = []
    for d in days:
        with open(os.path.join(LOGS, d + ".md"), encoding="utf-8") as f:
            body.append(f.read().strip())
    with open(LIVE, "w", encoding="utf-8") as f:
        f.write("\n".join(head) + "\n---\n\n" + "\n\n---\n\n".join(body) + "\n")

    print("daily log %s  data=%s  in=%d out=%d def=%d weak=%d mw=%d  pos=%d  cum=%s dd=%s"
          % (day, c["bar_date"], len(c["sig_in"]), len(c["xs_out"]),
             len(c["def_strong"]), len(c["def_weak"]), len(c["mw_star"]),
             c["n_open"], fmt_yuan(c["cum"]), fmt_yuan(c["maxdd"])))


if __name__ == "__main__":
    main()
