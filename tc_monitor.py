#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""强顺势中继 · 多空衰减监控（纯统计层，不参与任何交易/账本逻辑）

口径（与 tc_core.py / money.py 完全一致，不得自行改动）：
  信号日收盘触发 → 次日开盘进场 → 持 H=20 根收盘离场 → 扣双边成本 0.0008*(entry+exit)
  net = side*(exit-entry) - cost ；net > 0 记为「胜」。

监控规则（用户 2026-11-13 二次指定：改为按「笔数」而非「时间窗口」）：
  · 统计对象：最近 LOOKBACK_N=12 笔【已完成】的该方向信号（7 品种合并，按离场时间排序）。
  · 多头：满 12 笔且胜率 < 40% → 暂停多头提示。
  · 空头：满 12 笔且胜率 < 45% → 暂停空头提示。
  · 不满 12 笔一律不判定（保持正常显示），避免用小样本误杀。
  说明：本策略极低频（≈0.37 次/品种/年），按时间窗口取样会出现「0~2 笔就定生死」，
        故按笔数取样更稳；样本量随交易自然累积，不需要额外调参。

输出：写进每个 data/{sym}.json 的 "tcHealth" 字段（7 个文件内容相同，7 品种合并口径），
      前端直接读 DOC.tcHealth 渲染横幅与灰化，无需额外请求。
"""
import json
import os
import sys
import datetime

H = 20                 # 持仓根数（与回测一致）
COST = 0.0008          # 双边成本系数（与 money.py 一致）
LOOKBACK_N = 12        # 判定窗口：最近 N 笔已完成信号
SHORT_WR = 0.45        # 空头阈值
LONG_WR = 0.40         # 多头阈值


# ---------------------------------------------------------------- 月份工具
def _month_of(d):
    return d[:7]


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


def _cell(rows):
    """rows 已经是「要统计的那批」，返回样本与胜率。"""
    n = len(rows)
    if not n:
        return {"n": 0, "wins": 0, "wr": None}
    w = sum(r["win"] for r in rows)
    return {"n": n, "wins": w, "wr": round(w / float(n), 4)}


def _side_status(rows, thr, side_name, today):
    """按「最近 LOOKBACK_N 笔」判定某方向是否暂停。rows 已按离场时间升序。"""
    total = len(rows)
    tail = rows[-LOOKBACK_N:] if total > LOOKBACK_N else rows
    cell = _cell(tail)
    recent = [{
        "date": r["date"], "sym": r.get("sym") or "",
        "exitDate": r["exitDate"], "pts": round(r["pts"], 2), "win": r["win"],
    } for r in tail]

    paused, reason, since = False, "", None
    if total < LOOKBACK_N:
        reason = ("样本累积中：已完成 %d/%d 笔，未达判定门槛，暂不判定"
                  % (total, LOOKBACK_N))
    else:
        span = "%s ~ %s" % (tail[0]["date"], tail[-1]["date"])
        if cell["wr"] < thr:
            paused = True
            since = _month_of(today)
            reason = ("%s最近 %d 笔胜率 %.1f%%（%d/%d，%s）低于 %d%%，已暂停该方向提示"
                      % (side_name, LOOKBACK_N, cell["wr"] * 100, cell["wins"], cell["n"],
                         span, int(thr * 100)))
        else:
            reason = ("%s最近 %d 笔胜率 %.1f%%（%d/%d，%s），高于 %d%%，正常"
                      % (side_name, LOOKBACK_N, cell["wr"] * 100, cell["wins"], cell["n"],
                         span, int(thr * 100)))

    return {
        "paused": paused,
        "since": since,
        "wr": cell["wr"],
        "n": cell["n"],
        "wins": cell["wins"],
        "need": LOOKBACK_N,
        "total": total,
        "reason": reason,
        "rule": "最近 %d 笔满额且胜率 < %d%% 即暂停" % (LOOKBACK_N, int(thr * 100)),
        "recent": recent,
    }


def compute_health(docs, today=None):
    """docs: {sym: data_dict}，需含 main 与 tcSignals。返回 tcHealth dict。"""
    all_tr = []
    for sym, doc in docs.items():
        main = doc.get("main") or []
        sigs = doc.get("tcSignals") or []
        for t in trades_of(main, sigs):
            t["sym"] = sym
            all_tr.append(t)

    # 按「完成时间」排序：先离场的先计入，最新离场的排最后
    all_tr.sort(key=lambda t: (t["exitDate"], t["date"], t.get("sym") or ""))

    today = today or datetime.date.today().isoformat()
    shorts = [t for t in all_tr if t["side"] < 0]
    longs = [t for t in all_tr if t["side"] > 0]

    return {
        "version": 2,
        "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asOf": today,
        "rule": {
            "mode": "lastN",
            "lookback": LOOKBACK_N,
            "H": H,
            "cost": COST,
            "short": {"wr": SHORT_WR},
            "long": {"wr": LONG_WR},
            "requireFull": True,
        },
        "status": {
            "short": _side_status(shorts, SHORT_WR, "空头中继", today),
            "long": _side_status(longs, LONG_WR, "多头中继", today),
        },
        "totalSignals": len(all_tr),
        "totalShort": len(shorts),
        "totalLong": len(longs),
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
    print("规则: 最近 %d 笔已完成 / 空 <%.0f%% / 多 <%.0f%% / 不满 %d 笔不判定"
          % (h["rule"]["lookback"], h["rule"]["short"]["wr"] * 100,
             h["rule"]["long"]["wr"] * 100, h["rule"]["lookback"]))
    print("已完成合计 %d 笔（多 %d / 空 %d）"
          % (h["totalSignals"], h["totalLong"], h["totalShort"]))
    for side in ("short", "long"):
        st = h["status"][side]
        f = ("%.1f%%" % (st["wr"] * 100)) if st["wr"] is not None else "-"
        print("%s: paused=%-5s wr=%6s (%d/%d, 累计 %d) %s"
              % (side, st["paused"], f, st["wins"], st["n"], st["total"], st["reason"]))


if __name__ == "__main__":
    # 本地校验：python tc_monitor.py <dir>
    d = sys.argv[1] if len(sys.argv) > 1 else "dist/data"
    hs = apply_health_to_dir(d)
    if not hs:
        print("no data in", d)
        sys.exit(1)
    _print_table(hs)
