#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""强顺势中继 · 多空衰减监控（纯统计层，不参与任何交易/账本逻辑）

口径（与 tc_core.py / money.py 完全一致，不得自行改动）：
  信号日收盘触发 → 次日开盘进场 → 持 H=20 根收盘离场 → 扣双边成本 0.0008*(entry+exit)
  net = side*(exit-entry) - cost ；net > 0 记为「胜」。

监控规则（用户 2026-11-13 指定）：
  · 每月一次月末快照，统计「过去 3 个自然月」信号的实际胜率（按方向分别统计，7 品种合并）。
  · 空头：连续 2 个月末快照胜率 < 45%  → 暂停空头提示。
  · 多头：最近 1 个月末快照胜率 < 40%  → 暂停多头提示。
  · 样本不足（窗口内已走完的信号数 < MIN_N）不参与判定，只展示，避免小样本误杀。

输出：写进每个 data/{sym}.json 的 "tcHealth" 字段（7 个文件内容相同，7 品种合并口径），
      前端直接读 DOC.tcHealth 渲染横幅与灰化，无需额外请求。
"""
import json
import os
import sys
import datetime

H = 20                 # 持仓根数（与回测一致）
COST = 0.0008          # 双边成本系数（与 money.py 一致）
WINDOW_MONTHS = 3      # 滚动窗口
SHORT_WR = 0.45        # 空头阈值
LONG_WR = 0.40         # 多头阈值
SHORT_CONSEC = 2       # 空头连续月数
MIN_N = 3              # 窗口内最少已走完样本数（单笔即判会把噪声当趋势）
KEEP_SNAPSHOTS = 18    # 输出保留的月末快照数


# ---------------------------------------------------------------- 月份工具
def _month_of(d):
    return d[:7]


def _shift_month(m, k):
    y, mo = int(m[:4]), int(m[5:7])
    idx = y * 12 + (mo - 1) + k
    return "%04d-%02d" % (idx // 12, idx % 12 + 1)


def _month_start(m):
    return m + "-01"


def _month_end(m):
    y, mo = int(m[:4]), int(m[5:7])
    if mo == 12:
        nxt = datetime.date(y + 1, 1, 1)
    else:
        nxt = datetime.date(y, mo + 1, 1)
    return (nxt - datetime.timedelta(days=1)).isoformat()


# ---------------------------------------------------------------- 交易评估
def trades_of(main, tc_sigs):
    """把信号列表转成已走完的交易记录。未走完（不足 H 根）的不计入统计。"""
    n = len(main)
    out = []
    for s in (tc_sigs or []):
        i = s.get("idx")
        side = s.get("side")
        if not isinstance(i, int) or side not in (1, -1):
            continue
        if i + 1 >= n or i + H >= n:
            continue
        entry = main[i + 1][1]          # 次日开盘
        exitp = main[i + H][4]          # 持 H 根收盘
        cost = COST * (entry + exitp)
        pts = side * (exitp - entry) - cost
        out.append({
            "sym": s.get("sym"),
            "side": side,
            "date": s.get("date") or main[i][0],
            "exitDate": main[i + H][0],
            "pts": pts,
            "win": 1 if pts > 0 else 0,
        })
    return out


def _wr(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "wins": 0, "wr": None}
    w = sum(r["win"] for r in rows)
    return {"n": n, "wins": w, "wr": round(w / float(n), 4)}


def compute_health(docs, today=None):
    """docs: {sym: data_dict}，需含 main 与 tcSignals。返回 tcHealth dict。"""
    all_tr = []
    for sym, doc in docs.items():
        main = doc.get("main") or []
        sigs = doc.get("tcSignals") or []
        for t in trades_of(main, sigs):
            t["sym"] = sym
            all_tr.append(t)

    # 全局交易日历（用于确定「该月最后交易日」）
    all_dates = sorted({d for doc in docs.values() for d in
                        [b[0] for b in (doc.get("main") or [])]})

    today = today or datetime.date.today().isoformat()
    cur_m = _month_of(today)
    # 已完成月份：严格早于当前自然月，且必须是【连续自然月】（空仓月也要有快照，
    # 否则「连续两个月」会跨过没有信号的月份，判定失真）。
    sig_months = sorted({_month_of(t["date"]) for t in all_tr})
    months = []
    if sig_months:
        first_m = sig_months[0]
        m = first_m
        last_done = _shift_month(cur_m, -1)
        while m <= last_done:
            months.append(m)
            m = _shift_month(m, 1)

    # 月末快照：以该月最后一个交易日为 as-of，只统计当月及之前已走完的信号
    snaps = []
    for m in months:
        m_end = max([d for d in all_dates if _month_of(d) == m] or [_month_end(m)])
        w_start = _month_start(_shift_month(m, -(WINDOW_MONTHS - 1)))
        elig = [t for t in all_tr if w_start <= t["date"] <= m_end and t["exitDate"] <= m_end]
        snaps.append({
            "m": m, "end": m_end,
            "from": w_start,
            "short": _wr([t for t in elig if t["side"] < 0]),
            "long": _wr([t for t in elig if t["side"] > 0]),
        })

    def _trip_ok(cell):
        return cell["n"] >= MIN_N and cell["wr"] is not None

    status = {}
    if snaps:
        last = snaps[-1]
        # 空头：最近两个月末快照连续 < 45%
        short_paused, short_reason, short_since = False, "", None
        if len(snaps) >= SHORT_CONSEC:
            a, b = snaps[-2], snaps[-1]
            ca, cb = a["short"], b["short"]
            if (_trip_ok(ca) and _trip_ok(cb)
                    and ca["wr"] < SHORT_WR and cb["wr"] < SHORT_WR):
                short_paused = True
                short_since = a["m"]
                short_reason = ("空头中继近 3 月胜率连续 2 个月低于 %d%%（%s %.1f%% / %s %.1f%%，样本 %d/%d）"
                                % (int(SHORT_WR * 100), a["m"], ca["wr"] * 100,
                                   b["m"], cb["wr"] * 100, ca["n"], cb["n"]))
            elif _trip_ok(cb) and cb["wr"] < SHORT_WR:
                short_reason = ("空头中继近 3 月胜率 %s 已降至 %.1f%%（<%d%%），等待下月确认"
                                % (b["m"], cb["wr"] * 100, int(SHORT_WR * 100)))
        status["short"] = {
            "paused": short_paused,
            "since": short_since,
            "wr": last["short"]["wr"],
            "n": last["short"]["n"],
            "reason": short_reason,
            "rule": "连续 %d 个月末 < %d%% 即暂停" % (SHORT_CONSEC, int(SHORT_WR * 100)),
        }

        long_paused = False
        cl = last["long"]
        long_reason = ""
        if _trip_ok(cl) and cl["wr"] < LONG_WR:
            long_paused = True
            long_reason = ("多头中继近 3 月胜率 %s 仅 %.1f%%（<%d%%，样本 %d）"
                           % (last["m"], cl["wr"] * 100, int(LONG_WR * 100), cl["n"]))
        status["long"] = {
            "paused": long_paused,
            "since": last["m"] if long_paused else None,
            "wr": cl["wr"],
            "n": cl["n"],
            "reason": long_reason,
            "rule": "单月 < %d%% 即暂停" % int(LONG_WR * 100),
        }

    # 实时口径（截至今日、滚动 3 个自然月），仅展示，不参与暂停判定
    w_start_live = _month_start(_shift_month(cur_m, -(WINDOW_MONTHS - 1)))
    live_elig = [t for t in all_tr if t["date"] >= w_start_live and t["exitDate"] <= today]
    live = {
        "from": w_start_live, "to": today,
        "short": _wr([t for t in live_elig if t["side"] < 0]),
        "long": _wr([t for t in live_elig if t["side"] > 0]),
    }

    return {
        "version": 1,
        "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asOf": today,
        "rule": {
            "windowMonths": WINDOW_MONTHS, "H": H, "cost": COST, "minN": MIN_N,
            "short": {"wr": SHORT_WR, "consec": SHORT_CONSEC},
            "long": {"wr": LONG_WR, "consec": 1},
        },
        "live": live,
        "snapshots": snaps[-KEEP_SNAPSHOTS:],
        "status": status,
        "totalSignals": len(all_tr),
    }


def apply_health_to_dir(data_dir):
    """读取 data_dir 下 7 个品种 json，计算合并健康度并回写各自 tcHealth。返回 health。"""
    if not os.path.isdir(data_dir):
        return None
    files = [f for f in sorted(os.listdir(data_dir))
             if f.endswith(".json") and f not in ("index.json", "tcHealth.json")]
    docs, paths = {}, {}
    for f in files:
        if "_" in f[:-5]:      # 分合约文件 {sym}_{cid}.json，跳过（否则与主档重复统计）
            continue
        p = os.path.join(data_dir, f)
        try:
            doc = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if "main" not in doc:
            continue
        sym = doc.get("sym") or f[:-5]
        docs[sym] = doc
        paths[sym] = p
    if not docs:
        return None
    health = compute_health(docs)
    for sym, p in paths.items():
        docs[sym]["tcHealth"] = health
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(docs[sym], fh, ensure_ascii=False, separators=(",", ":"))
    return health


def _print_table(h):
    print("规则: 窗口 %d 月 / 空 <%.0f%% 连续 %d 月 / 多 <%.0f%% 单月 / 最少样本 %d"
          % (h["rule"]["windowMonths"], h["rule"]["short"]["wr"] * 100,
             h["rule"]["short"]["consec"], h["rule"]["long"]["wr"] * 100, h["rule"]["minN"]))
    print("月末快照（近 12）：")
    print("  月份     空n  空胜率   多n  多胜率")
    for s in h["snapshots"][-12:]:
        f = lambda c: ("%5.1f%%" % (c["wr"] * 100)) if c["wr"] is not None else "    -"
        print("  %s  %3d  %7s  %3d  %7s" % (s["m"], s["short"]["n"], f(s["short"]),
                                            s["long"]["n"], f(s["long"])))
    lv = h["live"]
    print("实时（%s~%s）：空 n=%d wr=%s / 多 n=%d wr=%s"
          % (lv["from"], lv["to"], lv["short"]["n"],
             "%.1f%%" % (lv["short"]["wr"] * 100) if lv["short"]["wr"] is not None else "-",
             lv["long"]["n"],
             "%.1f%%" % (lv["long"]["wr"] * 100) if lv["long"]["wr"] is not None else "-"))
    for side in ("short", "long"):
        st = h["status"][side]
        print("%s: paused=%s wr=%s n=%s %s" % (side, st["paused"], st["wr"], st["n"], st["reason"]))


if __name__ == "__main__":
    # 本地校验：python tc_monitor.py <dir>
    d = sys.argv[1] if len(sys.argv) > 1 else "dist/data"
    hs = apply_health_to_dir(d)
    if not hs:
        print("no data in", d)
        sys.exit(1)
    _print_table(hs)
