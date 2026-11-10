# -*- coding: utf-8 -*-
"""v18.1 · M/W 形态识别：4 条硬约束的严格价格结构（用户 2026-11-08 二次重写指令）

v18 初版缺陷（用户图上实测）：ZigZag 缺失 → 主峰之后 1.0 ATR 内的微毛刺
被当成了第二峰；半山腰两个小土包也被当成了 M/W。此版彻底修正。

4 条硬约束（缺一不可）+ 1 条防假峰约束：
  ① 强制过滤小毛刺（类 ZigZag）：摆动极值之间，反向波幅 < 1.0 ATR 的微波动
     一律平滑掉，绝不允许成为峰/谷。
  ② 绝对主峰强约束：峰1 / 峰2 至少一个必须是该品种过去 60 根 K 线内的
     绝对最高点（M）/ 绝对最低点（W）。半山腰的小土包一律不标。
  ③ 颈线深度达标：两峰之间的回撤必须 ≥ 1.5 ATR（真实洗盘）。
  ④ 双峰高度差限制：峰1 与 峰2 价格差必须 ≤ 1.5 ATR（超了就是趋势，不是 M/W）。
  ⑤ 无更极端插入极值：两峰之间不许出现比两峰更高（M）/更深（W）的极值——
     否则真主极值在中间，标记的两个"峰"只是真底两侧的肩部假峰（SR/SA 实测修复）。

其余口径：
  · 两峰间隔 10 ~ 60 根；破位确认：收盘跌破颈线谷（M）/ 涨破颈线峰（W），
    峰2 后 120 根内必须破位才算确认（不 repaint，未破位的摆动不进结果）。
  · 回测：颈线破位收盘入场，止损在峰外 +0.5 ATR，目标 = 等距测幅
    （2×颈线 − 峰），持有 ≤ 30 根；R 按方向带符号，胜 = R > 0。

纪律：MA34 进场、MA26 + 吊灯 3ATR 出场、失效判定、弱叉规则一律不变；
本模块只做结构标记与回测，绝不新增任何平仓动作、绝不改动既有信号。
"""
import engine

SIG_LABEL = "结构双顶底"

# ---- 4 条硬约束 ----
MIN_SWING_ATR = 1.0   # ① ZigZag 最小摆动波幅（ATR）：小于此的毛刺一律平滑
MAIN_LOOKBACK = 60    # ② 主峰窗口：峰须为过去 60 根内的绝对最高/最低点
MIN_DEPTH = 1.5       # ③ 颈线回撤深度下限（ATR）
PEAK_TOL_ATR = 1.5    # ④ 双峰价格差上限（ATR，唯一容忍口径，废除 ±2% 宽松项）

# ---- 结构/确认参数 ----
MIN_GAP = 5           # 两峰最小间隔（根）。①的 ZigZag 已滤毛刺，此值仅防同一段
                      # 折返被拆成双峰（SR 实测：真双底 07-31/08-10 仅隔 6 根，10 会漏）
MAX_GAP = 60          # 两峰最大间隔（根）
BREAK_HORIZON = 120   # 峰2 后最多 120 根内必须破位，否则不算确认
HOLD = 30             # 回测持有上限（根）
STOP_ATR = 0.5        # 止损距峰外（ATR）

# ---- v26.2 大级别结构（信息推送门槛）----
# 用户 2026-11-10：「大的结构性双顶、双底要做信息推送，重要程度类似于现在的顶和底。
# 小的顶和底可不推」。这是【唯一口径】，daily.py（推送筛选）与 data_build.py（页面标记）
# 都必须调 is_big()，不许各自写阈值。
# 10 年 370 条 M/W 中命中 78 条（21%），近 1 年 6 条 —— 频率与顶底雷达相当。
BIG_GAP = 20          # 两峰间隔 ≥20 根：形态跨度约一个月以上
BIG_DEPTH = 3.5       # 颈线回撤 ≥3.5 ATR：真实洗盘而非小毛刺
BIG_TAG = "[研究参考·非建仓指令]"


def is_big(r):
    """是否大级别结构（只用于「信息推送 / 页面标注」，不改动任何判定口径）。"""
    return ((r.get("gap") or 0) >= BIG_GAP
            and (r.get("depth") or 0) >= BIG_DEPTH)


# ---------------------------------------------------------------- 工具
def ema(vals, n):
    return engine.ema(vals, n)


def dif_series(closes):
    e12, e26 = engine.ema(closes, 12), engine.ema(closes, 26)
    return [a - b for a, b in zip(e12, e26)]


def _zigzag(bars, atr, k=MIN_SWING_ATR):
    """类 ZigZag 摆动识别（硬约束①）：返回 [(idx, 'H'|'L'), ...] 时间升序。

    交替 H/L 极值；只有当反向波幅 ≥ k×ATR 时才确认新摆动。
    期间价格创出更极端者，候选极值向后推移（毛刺自然被吸收平滑）。
    尾部未确认的候选极值不输出（不 repaint）。
    """
    n = len(bars)
    piv = []
    if n < 5:
        return piv
    mode = 0            # 0=定向中  1=上升腿（cur_i 为候选高点）  -1=下降腿
    cur_i = 0
    hi_i = lo_i = 0
    for i in range(1, n):
        a = atr[i] or atr[i - 1] or 1e-9
        thr = k * a
        if mode == 0:
            if bars[i]["h"] >= bars[hi_i]["h"]:
                hi_i = i
            if bars[i]["l"] <= bars[lo_i]["l"]:
                lo_i = i
            if bars[hi_i]["h"] - bars[lo_i]["l"] >= thr:
                if hi_i > lo_i:
                    piv.append((lo_i, "L"))
                    mode, cur_i = 1, hi_i
                else:
                    piv.append((hi_i, "H"))
                    mode, cur_i = -1, lo_i
        elif mode == 1:
            if bars[i]["h"] >= bars[cur_i]["h"]:
                cur_i = i
            elif bars[cur_i]["h"] - bars[i]["l"] >= thr:
                piv.append((cur_i, "H"))
                mode, cur_i = -1, i
        else:
            if bars[i]["l"] <= bars[cur_i]["l"]:
                cur_i = i
            elif bars[i]["h"] - bars[cur_i]["l"] >= thr:
                piv.append((cur_i, "L"))
                mode, cur_i = 1, i
    return piv


def _is_main_extreme(bars, i, sd, lookback=MAIN_LOOKBACK):
    """硬约束②：峰 i 是否为过去 lookback 根（含当根）内的绝对最高点（sd=1）
    / 绝对最低点（sd=-1）。半山腰的小土包在此被淘汰。"""
    lo = max(0, i - lookback + 1)
    if sd == 1:
        best = max(bars[k]["h"] for k in range(lo, i + 1))
        return bars[i]["h"] >= best
    best = min(bars[k]["l"] for k in range(lo, i + 1))
    return bars[i]["l"] <= best


def _piv_extremes(piv):
    hs = [i for i, t in piv if t == "H"]
    ls = [i for i, t in piv if t == "L"]
    return hs, ls


# ---------------------------------------------------------------- 单品种检测
def detect_all(code, bars, S=None):
    """返回该品种全部【已确认】且通过 4 条硬约束的 M/W 结构记录（确认日升序）。"""
    if S is None:
        S = engine.make_S(bars)
    n = len(bars)
    atr = S["atr"]
    vma20 = S.get("vma20") or [0] * n
    closes = [b["c"] for b in bars]
    dif = dif_series(closes)
    highs, lows = _piv_extremes(_zigzag(bars, atr))
    recs = []

    def eval_pair(peaks, sd):
        """sd=+1 顶部（双顶 M）；sd=-1 底部（双底 W）。贪心取形态最深的有效第二峰。"""
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
                # 硬约束④：双峰差 ≤ 1.5 ATR（唯一口径）
                diff_atr = abs(p2 - p1) / a1
                if diff_atr > PEAK_TOL_ATR:
                    continue
                # 硬约束②：至少一个峰是过去 60 根内的绝对主峰
                if not (_is_main_extreme(bars, i1, sd) or _is_main_extreme(bars, i2, sd)):
                    continue
                # 硬约束③：颈线回撤 ≥ 1.5 ATR
                if sd == 1:
                    neck = min(bars[k]["l"] for k in range(i1, i2 + 1))
                    depth = (p1 - neck) / a1
                else:
                    neck = max(bars[k]["h"] for k in range(i1, i2 + 1))
                    depth = (neck - p1) / a1
                if depth < MIN_DEPTH:
                    continue
                # 硬约束⑤：两峰之间不得出现比两峰更极端的插入极值
                # （M：中间不许有更高的峰；W：中间不许有更深的坑——否则真正的
                #   主极值在中间，标记的两个"峰"只是真底两侧的肩部假峰）
                if sd == 1:
                    mid_hi = max(bars[k]["h"] for k in range(i1, i2 + 1))
                    if mid_hi > max(p1, p2) + 1e-9:
                        continue
                else:
                    mid_lo = min(bars[k]["l"] for k in range(i1, i2 + 1))
                    if mid_lo < min(p1, p2) - 1e-9:
                        continue
                # 破位确认：峰2 后 BREAK_HORIZON 根内收盘破颈线
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
            a = _idx_after(peaks, i2)   # 消费 i2 之前的所有摆动，避免重叠复用
        return

    eval_pair(highs, +1)
    eval_pair(lows, -1)
    recs.sort(key=lambda r: r["confirmDate"])
    return recs


def _idx_after(peaks, i2):
    for idx in range(len(peaks)):
        if peaks[idx] > i2:
            return idx
    return len(peaks)


def _make_rec(bars, S, code, sd, i1, i2, neck, j, p1, p2, atr, vma20, closes, dif):
    a1 = atr[i1] or 1e-9
    dir_name = "顶部" if sd == 1 else "底部"
    side = "short" if sd == 1 else "long"
    # 主峰归属（供弹窗展示）
    if _is_main_extreme(bars, i1, sd) and _is_main_extreme(bars, i2, sd):
        main_peak = "双主峰"
    elif _is_main_extreme(bars, i1, sd):
        main_peak = "峰1"
    else:
        main_peak = "峰2"
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
        "date": bars[j]["d"],             # 信号/列表/弹窗（破位确认日）
        "confirmDate": bars[j]["d"],
        "sym": code, "name": engine.NAMES.get(code, code),
        "dir": dir_name, "side": side,
        "peak1Px": round(p1, 1), "peak2Px": round(p2, 1), "neckPx": round(neck, 1),
        "peak1Date": bars[i1]["d"], "peak2Date": bars[i2]["d"],
        "gap": i2 - i1, "depth": round((p1 - neck) / a1 if sd == 1 else (neck - p1) / a1, 2),
        "break": j - i2,                 # 峰2 到破位的根数
        "entry": round(bars[j]["c"], 1),  # 破位收盘
        "atr": round(float(atr[j] or a1), 2),
        "mainPeak": main_peak,           # ② 主峰归属
        "oiChg": round(oi_chg, 1), "oiDrop10": oi_drop10,
        "volRatio": round(vol_ratio, 2), "volDry": vol_dry,
        "divDif": div_dif, "qtyStrong": qty_strong,
        "filterStrong": bool(div_dif and qty_strong),
        "filterDif": div_dif, "filterOi": oi_drop10, "filterVol": vol_dry,
        "hiConf": bool(div_dif and oi_drop10),   # 高置信（DIF背离+持仓降）：只有它画上图
        "win": win, "R": R, "exitReason": exit_reason,
        "price": round(bars[i2]["c"], 1),  # 旧字段兼容
        "pullBetween": round((p1 - neck) / a1 if sd == 1 else (neck - p1) / a1, 2),
        "firstDate": bars[i1]["d"],       # 旧字段兼容
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
    print("v18.1 M/W · 硬约束全样本回测（10 年 8 品种）")
    print("=" * 96)
    print("硬约束：① ZigZag 摆动波幅≥1.0ATR  ② 峰为过去60根绝对主峰（至少一峰）"
          "  ③ 颈线深度≥1.5ATR  ④ 峰差≤1.5ATR  ⑤ 两峰间无更极端插入极值")
    print("辅助：间隔5~60根 | 破位确认≤120根 | 回测止损=峰外+0.5ATR 目标=等距测幅 持有≤30根；胜=R>0")
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
        ("DIF背离+量仓强", lambda r: r["filterStrong"]),
        ("DIF+持仓降", lambda r: r["filterDif"] and r["filterOi"]),
        ("DIF+缩量", lambda r: r["filterDif"] and r["filterVol"]),
        ("三特征全有", lambda r: r["filterDif"] and r["filterOi"] and r["filterVol"]),
    ]
    print("过滤对比（分全期 / 训练<2024 / 盲测≥2024）：")
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

    # ---- 最近信号一览（供人工核对图上标记是否为真主峰）----
    print("最近 15 条结构（按确认日）：")
    print("-" * 96)
    print("确认日      品种  方向  峰1日        峰2日        主峰  峰差ATR  颈线深ATR")
    for r in rows[-15:]:
        pa = r["peak1Px"]
        pb = r["peak2Px"]
        a1 = r["atr"] or 1
        d = abs(pb - pa) / a1
        print("%-11s %-5s %-4s %-12s %-12s %-5s %7.2f %9.2f" % (
            r["confirmDate"], r["sym"], r["dir"], r["peak1Date"], r["peak2Date"],
            r["mainPeak"], d, r["depth"]))
    print()
    print("结论阈值：胜率≥55%% 且 均R>0 → 可上线画图。")
    return rows, out


if __name__ == "__main__":
    full_report()
