# -*- coding: utf-8 -*-
"""v18 · M/W 形态重写：真正的价格结构识别（替代 v16 的信号配对）

用户 2026-11-08 正式解除冻结，原 v16「信号配对」17 笔统计全部作废。

识别口径（纯价格结构，无信号代理）：
  1) 峰1 / 峰2 由摆动极值（局部高/低，窗口 w）定义；
     两峰价格差 ≤ 2% 或 ≤ 1.5 ATR（取或）。
  2) 颈线谷（M：两峰间最低低；W：两峰间最高高）回撤深度 ≥ 1.5 ATR。
  3) 两峰间隔 10 ~ 60 根（修复 v16 min_gap=3 把同一顶部内部回调误当双峰的缺陷）。
  4) 破位确认：收盘跌破颈线谷（M）/ 涨破颈线峰（W）才正式确认，确认日 = 信号日。
  5) 回测：颈线破位收盘入场，止损在更高/更低峰外 +0.5 ATR，目标 = 等距测幅
     （2×颈线 − 峰），持有 ≤ 30 根；R = （出场−入场）/ ATR（按方向取正负），胜 = R > 0。

叠加过滤（用户 2026-11-08 指定：DIF 背离 / 持仓降 / 缩量，看叠加两个好还是叠加一个好）：
  在纯结构确认集上，再套用 v15 测出的强特征，分单特征 / 双特征 / 三特征对比。

纪律：MA34 进场、MA26 + 吊灯 3ATR 出场、失效判定、弱叉规则一律不变；
本模块只做结构标记、提示与回测，绝不新增任何平仓动作、绝不改动既有信号。
"""
import engine

SIG_LABEL = "结构双顶底"

# ---- 结构参数 ----
W_PIVOT = 4          # 摆动极值窗口
MIN_GAP = 10         # 两峰最小间隔（修复 v16 min_gap=3）
MAX_GAP = 60         # 两峰最大间隔
MIN_DEPTH = 1.5      # 颈线回撤深度（ATR）
PEAK_TOL_PCT = 0.02  # 峰差容忍 ±2%
PEAK_TOL_ATR = 1.5   # 或 ±1.5 ATR
BREAK_HORIZON = 120 # 峰2 后最多 120 根内必须破位，否则不算确认
HOLD = 30            # 回测持有上限（根）
STOP_ATR = 0.5       # 止损距峰外（ATR）


# ---------------------------------------------------------------- 工具
def ema(vals, n):
    return engine.ema(vals, n)


def dif_series(closes):
    e12, e26 = engine.ema(closes, 12), engine.ema(closes, 26)
    return [a - b for a, b in zip(e12, e26)]


def _swings(bars, w=W_PIVOT):
    """返回 (highs, lows)：摆动高点 / 低点的索引列表（时间升序）。"""
    n = len(bars)
    highs, lows = [], []
    for k in range(w, n - w):
        h, l = bars[k]["h"], bars[k]["l"]
        hi_ok = all(h >= bars[k - j]["h"] and h >= bars[k + j]["h"] for j in range(1, w + 1))
        lo_ok = all(l <= bars[k - j]["l"] and l <= bars[k + j]["l"] for j in range(1, w + 1))
        if hi_ok:
            highs.append(k)
        if lo_ok:
            lows.append(k)
    return highs, lows


# ---------------------------------------------------------------- 单品种检测
def detect_all(code, bars, S=None):
    """返回该品种全部【已确认】的 M/W 结构记录（含回测），不重复、按确认日升序。"""
    if S is None:
        S = engine.make_S(bars)
    n = len(bars)
    atr = S["atr"]
    vma20 = S.get("vma20") or [0] * n
    closes = [b["c"] for b in bars]
    dif = dif_series(closes)
    highs, lows = _swings(bars, W_PIVOT)
    recs = []

    def eval_pair(peaks, sd):
        """sd=+1 顶部（双顶 M）；sd=-1 底部（双底 W）。贪心找最佳第二峰并确认。"""
        m = len(peaks)
        a = 0
        while a < m - 1:
            i1 = peaks[a]
            p1 = bars[i1]["h"] if sd == 1 else bars[i1]["l"]
            a1 = atr[i1] or 1e-9
            best = None
            for b in range(a + 1, m):
                i2 = peaks[b]
                gap = i2 - i1
                if gap < MIN_GAP:
                    continue
                if gap > MAX_GAP:
                    break
                p2 = bars[i2]["h"] if sd == 1 else bars[i2]["l"]
                # 峰差容忍：±2% 或 ±1.5 ATR
                diff_pct = abs(p2 - p1) / p1
                diff_atr = abs(p2 - p1) / a1
                if diff_pct > PEAK_TOL_PCT and diff_atr > PEAK_TOL_ATR:
                    continue
                # 颈线：两峰之间的最低低（M）/ 最高高（W）
                if sd == 1:
                    neck = min(bars[k]["l"] for k in range(i1, i2 + 1))
                    depth = (p1 - neck) / a1
                else:
                    neck = max(bars[k]["h"] for k in range(i1, i2 + 1))
                    depth = (neck - p1) / a1
                if depth < MIN_DEPTH:
                    continue
                # 破位确认：峰2 后 BREAK_HORIZON 根内，收盘跌破颈线（M）/ 涨破颈线（W）
                j = None
                for k in range(i2 + 1, min(i2 + BREAK_HORIZON, n) + 1):
                    if k >= n:
                        break
                    if sd == 1 and bars[k]["c"] < neck:
                        j = k
                        break
                    if sd == -1 and bars[k]["c"] > neck:
                        j = k
                        break
                if j is None:
                    continue
                # 候选：取颈线最深（形态最标准）者
                cand = (depth, i2, p2, neck, j)
                if best is None or cand[0] > best[0]:
                    best = cand
            if best is None:
                a += 1
                continue
            depth, i2, p2, neck, j = best
            rec = _make_rec(bars, S, code, sd, i1, i2, neck, j,
                            p1, p2, atr, vma20, closes, dif)
            recs.append(rec)
            a = m  # 该 i1 已配对并消费，跳到末尾（避免重叠复用同一摆动）
            # 注：直接跳到末尾而非 i2 之后，因相邻摆动已无意义，避免重复计数
            # 重新从下一未消费摆动开始：把 a 置为 i2 之后的下一个摆动索引
            a = b_index_after(peaks, i2)
        return

    def b_index_after(peaks, i2):
        for idx in range(len(peaks)):
            if peaks[idx] > i2:
                return idx
        return len(peaks)

    eval_pair(highs, +1)
    eval_pair(lows, -1)
    recs.sort(key=lambda r: r["confirmDate"])
    return recs


def b_index_after(peaks, i2):
    for idx in range(len(peaks)):
        if peaks[idx] > i2:
            return idx
    return len(peaks)


def _make_rec(bars, S, code, sd, i1, i2, neck, j, p1, p2, atr, vma20, closes, dif):
    a1 = atr[i1] or 1e-9
    dir_name = "顶部" if sd == 1 else "底部"
    side = "short" if sd == 1 else "long"
    # ---- 特征（全部取 peak2 当天及之前 / 破位当天，无前视）----
    oi1 = bars[i1].get("oi") or 0
    oi2 = bars[i2].get("oi") or 0
    oi_chg = ((oi2 - oi1) / oi1 * 100) if oi1 else 0.0
    oi_drop10 = bool(oi_chg <= -10)
    vj = bars[j].get("v") or 0
    vma_j = vma20[j] or 0
    vol_ratio = (vj / vma_j) if vma_j else 1.0
    vol_dry = bool(vol_ratio <= 0.8)
    # DIF 背离：峰2 价 ≈ 峰1（同极端），但 DIF 反而走弱（M）/ 走强（W）
    dd = (dif[i2] or 0) - (dif[i1] or 0)
    div_dif = bool((sd == 1 and dd < 0) or (sd == -1 and dd > 0))
    qty_strong = bool(oi_drop10 or vol_dry)
    # ---- 回测（从破位收盘入场）----
    win, R, exit_reason = _trade(bars, S, sd, i1, i2, neck, j)
    return {
        "kind": "mw",
        "mkDate": bars[i2]["d"],          # 图上标记位置（第二峰）
        "date": bars[j]["d"],             # 信号/列表/弹窗/日志（破位确认日）
        "confirmDate": bars[j]["d"],
        "sym": code, "name": engine.NAMES.get(code, code),
        "dir": dir_name, "side": side,
        "peak1Px": round(p1, 1), "peak2Px": round(p2, 1), "neckPx": round(neck, 1),
        "peak1Date": bars[i1]["d"], "peak2Date": bars[i2]["d"],
        "gap": i2 - i1, "depth": round((p1 - neck) / a1 if sd == 1 else (neck - p1) / a1, 2),
        "break": j - i2,                 # 峰2 到破位的根数
        "entry": round(bars[j]["c"], 1),  # 破位收盘
        "atr": round(float(atr[j] or a1), 2),
        "oiChg": round(oi_chg, 1), "oiDrop10": oi_drop10,
        "volRatio": round(vol_ratio, 2), "volDry": vol_dry,
        "divDif": div_dif, "qtyStrong": qty_strong,
        "filterStrong": bool(div_dif and qty_strong),     # v16 胜出组合（双概念叠加）
        "filterDif": div_dif, "filterOi": oi_drop10, "filterVol": vol_dry,
        "win": win, "R": R, "exitReason": exit_reason,
        "price": round(bars[i2]["c"], 1),  # 旧字段兼容
        "pullBetween": round((p1 - neck) / a1 if sd == 1 else (neck - p1) / a1, 2),
        "firstDate": bars[i1]["d"],       # 旧字段兼容（微信卡片已禁用）
        "sigLabel": SIG_LABEL,
    }


def _trade(bars, S, sd, i1, i2, neck, j):
    """颈线破位收盘入场，止损在峰外 +0.5ATR，目标=等距测幅，持有 ≤ HOLD 根。
    胜 = R > 0（R 已按方向正确带符号：做空 R=入场-出场，做多 R=出场-入场）。
    """
    n = len(bars)
    if j + HOLD + 1 > n:
        return None, None, "未平仓（数据不足）"
    atr_j = S["atr"][j] or 1e-9
    entry = bars[j]["c"]
    if sd == 1:  # 双顶做空
        hi = max(bars[i1]["h"], bars[i2]["h"])
        stop = hi + STOP_ATR * atr_j
        target = 2 * neck - hi                       # 等距测幅
        for k in range(j + 1, j + HOLD + 1):
            if bars[k]["l"] <= target:
                R = (entry - target) / atr_j
                return (R > 0), round(R, 2), "target"
            if bars[k]["h"] >= stop:
                R = (entry - stop) / atr_j
                return (R > 0), round(R, 2), "stop"
        exit_px = bars[j + HOLD]["c"]
        R = (entry - exit_px) / atr_j
        return (R > 0), round(R, 2), "horizon"
    else:        # 双底做多
        lo = min(bars[i1]["l"], bars[i2]["l"])
        stop = lo - STOP_ATR * atr_j
        target = 2 * neck - lo
        for k in range(j + 1, j + HOLD + 1):
            if bars[k]["h"] >= target:
                R = (target - entry) / atr_j
                return (R > 0), round(R, 2), "target"
            if bars[k]["l"] <= stop:
                R = (stop - entry) / atr_j
                return (R > 0), round(R, 2), "stop"
        exit_px = bars[j + HOLD]["c"]
        R = (exit_px - entry) / atr_j
        return (R > 0), round(R, 2), "horizon"


# ---------------------------------------------------------------- 对外接口
def build_mw(code, name, bars, S=None):
    """返回该品种全部已确认 M/W 结构事件（按确认日升序）。"""
    recs = detect_all(code, bars, S)
    for r in recs:
        r["name"] = name
    return recs


def summarize(events):
    by_dir = {}
    bt = [e for e in events if e["R"] is not None]
    win = [e for e in bt if e["win"]]
    return {
        "n": len(events),
        "last": max((e["date"] for e in events), default=None),
        "byDir": by_dir,
        "backtestN": len(bt),
        "winN": len(win),
        "winRate": round(len(win) / len(bt) * 100, 1) if bt else None,
        "avgR": round(sum(e["R"] for e in bt) / len(bt), 2) if bt else None,
    }


# ---------------------------------------------------------------- 全样本报告
def full_report():
    rows = []
    for c in engine.CODES:
        bars = engine._bars_of(c, 10)
        recs = detect_all(c, bars)
        rows.extend(recs)
    rows.sort(key=lambda r: r["confirmDate"])

    def stats(recs, label):
        bt = [r for r in recs if r["R"] is not None]
        win = [r for r in bt if r["win"]]
        n_top = sum(1 for r in recs if r["dir"] == "顶部")
        n_bot = sum(1 for r in recs if r["dir"] == "底部")
        return (label, len(recs), n_top, n_bot,
                len(bt), len(win),
                round(len(win) / len(bt) * 100, 1) if bt else 0.0,
                round(sum(r["R"] for r in bt) / len(bt), 2) if bt else 0.0)

    print("=" * 96)
    print("v18 M/W 重写 · 全样本回测（10 年 8 品种，纯价格结构 + 破位确认）")
    print("=" * 96)
    print("结构参数：峰差≤2%%或≤1.5ATR | 颈线深度≥1.5ATR | 间隔10~60根 | 破位确认≤120根")
    print("回测：颈线破位收盘入场，止损=峰外+0.5ATR，目标=等距测幅，持有≤30根；胜=R>0")
    print("-" * 96)
    print("品种       结构数  顶  底   可回测  胜笔   胜率%%   均R")
    per = []
    for c in engine.CODES:
        recs = [r for r in rows if r["sym"] == c]
        per.append(stats(recs, c))
    for s in per:
        print("%-6s %6d %5d %4d %7d %6d %7.1f %7.2f" % s)
    tot = stats(rows, "合计")
    print("-" * 96)
    print("%-6s %6d %5d %4d %7d %6d %7.1f %7.2f" % tot)
    print()

    # ---- 过滤对比 ----
    filters = [
        ("无过滤（纯结构）", lambda r: True),
        ("仅 持仓降≥10%", lambda r: r["filterOi"]),
        ("仅 缩量≤0.8×", lambda r: r["filterVol"]),
        ("仅 DIF背离", lambda r: r["filterDif"]),
        ("量仓强(降或缩)", lambda r: r["qtyStrong"]),
        ("DIF背离+量仓强(双概念)", lambda r: r["filterStrong"]),
        ("DIF+持仓降", lambda r: r["filterDif"] and r["filterOi"]),
        ("DIF+缩量", lambda r: r["filterDif"] and r["filterVol"]),
        ("三特征全有", lambda r: r["filterDif"] and r["filterOi"] and r["filterVol"]),
    ]
    print("过滤对比（在纯结构确认集上叠加；分全期 / 训练<2024 / 盲测≥2024）：")
    print("-" * 96)
    print("%-22s %7s %7s %8s %8s %8s" % ("过滤", "笔数", "胜率%", "均R", "训练胜%", "盲测胜%"))
    out = {}
    for name, fn in filters:
        sub = [r for r in rows if fn(r)]
        bt = [r for r in sub if r["R"] is not None]
        win = [r for r in bt if r["win"]]
        tr = [r for r in bt if r["confirmDate"] < "2024"]
        trw = [r for r in tr if r["win"]]
        bl = [r for r in bt if r["confirmDate"] >= "2024"]
        blw = [r for r in bl if r["win"]]
        wr = round(len(win) / len(bt) * 100, 1) if bt else 0.0
        ar = round(sum(r["R"] for r in bt) / len(bt), 2) if bt else 0.0
        tw = round(len(trw) / len(tr) * 100, 1) if tr else 0.0
        bw = round(len(blw) / len(bl) * 100, 1) if bl else 0.0
        print("%-22s %7d %7.1f %8.2f %8.1f %8.1f" % (name, len(sub), wr, ar, tw, bw))
        out[name] = (len(sub), wr, ar, tw, bw)
    print()
    print("结论阈值：胜率≥55%% 且 均R>0 → 可上线画图。")
    return rows, out


if __name__ == "__main__":
    full_report()
