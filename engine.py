# -*- coding: utf-8 -*-
"""反转预测引擎 v4（8 品种关注池：FG/SA/M/SP/SM/MA/SR/C）
=====================================================================
方法论（每一个数字都来自这五个品种的回测）：

 1) 趋势切片：收盘价在 MA34 上方 = 多头段，下方 = 空头段；穿越点 = 反转点
    —— MA 周期在 20/25/30/34/40/45/50 上逐一回测（7 套周期 × 7 套条件组合），
       MA34 在全部组合里收益最高、回撤最小，故维持 34 不变。
 2) 前置窗口：反转前 10 根内开始出现共性变化（事件研究法结论），
    因此「区间累计」特征用 10 日窗口、（逼近度/斜率/MACD）用单日特征
 3) 指标筛选（28 条候选 → 保留 4 条 + 1 道门槛）：
      淘汰  放量类 （lift 0.6~0.9，实为「放量=原趋势延续」的反向指标）
      淘汰  持仓减仓类（lift 1.14，品种间不稳定）
      保留  A1  距 MA34 ≤0.3 ATR
            A9  10 日累计逆行 ≥1.5 ATR
            A15 MA10 斜率转平/转向
            A17 MACD 柱翻向
      门槛  ADX(14) ≥ 20 —— 把「趋势还没起来」的震荡信号排除
            （无门槛 60 笔胜率 33.3%、年均 3,575 元/手；加门槛 40.0%、4,181 元/手，
              盲测段同样变好，且把唯一亏损品种甲醇翻正）
 4) 概率标定（命中 k 条 → 未来 N 根内反转概率，ADX≥20 样本）：
       H=2   4.1 / 17.1 / 23.4 / 41.0 / 57.9 %
       H=3   6.7 / 23.9 / 32.1 / 46.0 / 67.1 %
       H=5  12.4 / 34.9 / 44.5 / 57.9 / 78.9 %     基准 22.9%
       H=10 27.5 / 54.4 / 67.1 / 76.2 / 92.1 %
       提前量中位 1 根，≤2 根占 73%
 5) 持仓口径：**不设止损**。命中 4 条 → 次日开盘逆势建仓 → 持有到价格反向穿越 MA34
    （整段新趋势走完）→ 反手。回测（10 年、5 品种、1 手/笔）：
       60 笔，胜率 40.0%，盈亏比 3.43，平均 +0.64 ATR，累计 +41,814 元，
       最大回撤 10,149 元，持仓中位 20 根，年均约 6 笔。
       代价：持仓期浮亏中位 -0.95 ATR、P10 -2.45 ATR、极值 -8.70 ATR
       （≈ 8,860 元/手，占 10 万本金 8.9%），所以必须留足浮亏缓冲、不能满仓。
"""
import bisect
import datetime as _dt
import json
import math
import os

import build
import cyc

# 关注品种（2026-11 按用户指定：玻璃 / 纯碱 / 豆粕 / 纸浆 / 锰硅 / 甲醇 / 白糖 / 玉米）
# 方法、参数、阈值全部沿用原口径。
# 配置模式（v10.3）：UNIFORM —— 8 个品种共用同一套参数，不按品种单独配置。
#   依据：tune_grid.py / tune_eval.py / tune_eval2.py 的逐品种样本外检验（10 组方案），
#   逐品种调参在样本内提升明显，但盲测段与逐年滚动样本外准确率、盈利率均无显著差异，
#   且参数逐年漂移。详见 README「v10.3」。若日后要改为逐品种，请先重跑该检验并通过判定阈值。
CONFIG_MODE = "uniform"

# ---- v11「甲方案」：周线 MA45 智能降级 + 复盘标签（只管推送，绝不删信号）----
# 依据 reports/终极融合_AB对比表.txt（10 年 × 8 品种 A/B，304 条信号）：
#   P1 仅周线 MA45 降级：净收益 +21.4 → +24.5，假信号 133 → 74（-44.4%），
#   胜率 56.2% → 60.4%，正确点保留 66.1%，选择性 +10.5pp —— 是唯一双达标模块。
#   「市场状态机」为拖累项（叠加后净收益 +11.0），故本版状态判定【只做信息展示】，
#   绝不拦截、隐藏、降级任何信号，也不参与推送决策。
# 红线：不延迟发信号、不删除信号。降级 = 页面照常显示并标【观察】，只是不推微信。
WEEKLY_FILTER = "downgrade"   # 不是 hard / delay
WEEKLY_MA = 45               # 用户指定 45（不是 34）
KEEP_STRONG_ON_OPP = True    # 逆势时强档照常推送
CONFIRM2_BARS = 2            # 复盘标签：信号后 2 根内是否顺向破极值
VOL_RATIO_TAG = 1.0          # 当日标签：ATR / ATR20 < 1.0 → 低波动待观察

# ---- 市场状态判定（纯信息展示，三选一）----
MKT_FLAT = 0.02              # |MA60 斜率| < 此值 → 走平（ATR 归一化/每根）
MKT_STRONG_SLOPE = 0.05      # |斜率| ≥ 此值 → 明显趋势
MKT_BW_LOW_Q = 0.20          # 布林带宽处于过去 120 日最底部 20% 分位
MKT_BW_HIGH_Q = 0.60         # 开口放大所需分位
MKT_BW_EXPAND = 1.10         # 相对 20 日前放大比例
MKT_MID_CROSS = 3            # 「价格在中轨反复」= 20 日内穿越中轨次数

# v22：6 大核心池（全市场海选后定稿，2026-11-10）
CODES = ["M", "FG", "MA", "Y", "P", "HC", "SM"]
NAMES = {"FG": "玻璃", "SA": "纯碱", "M": "豆粕", "SP": "纸浆",
         "SM": "锰硅", "MA": "甲醇", "SR": "白糖", "C": "玉米",
         "P": "棕榈油", "HC": "热卷", "Y": "豆油"}
MULT   = {"FG": 20, "SA": 20, "M": 10, "SP": 10, "SM": 5, "MA": 10, "SR": 10, "C": 10,
          "RB": 10, "UR": 20, "JD": 10, "P": 10, "HC": 10, "Y": 10}
FEE    = {"FG": 6.0, "SA": 3.5, "M": 1.5, "SP": 2.5, "SM": 3.0, "MA": 2.0, "SR": 3.0, "C": 1.2,
          "RB": 1.0, "UR": 4.0, "JD": 4.0, "P": 2.5, "HC": 3.3, "Y": 2.5}
MARGIN = {"FG": 0.09, "SA": 0.09, "M": 0.08, "SP": 0.08, "SM": 0.09,
          "MA": 0.08, "SR": 0.08, "C": 0.08, "RB": 0.08, "UR": 0.08, "JD": 0.08,
          "P": 0.08, "HC": 0.08, "Y": 0.08}

MA = 34
MIN_AGE = 2        # 新段至少站稳 2 根才接受反向信号（第 1 根属“新段反抽”，另计）
MIN_LEN = 15       # 大周期段最短长度
MIN_AMP = 3.0      # 大周期段最小幅度（ATR）
BUILD_K = 4        # 建仓阈值（命中条数）
WARN_K = 3         # 预警阈值
ADX_MIN = 20       # 趋势门槛：ADX(14) 低于此值不建仓
MAXHOLD = 250      # 安全上限（回测最长 83 根，正常靠反向穿越离场）
BUF_ATR = 2.45     # 历史浮亏 P10（ATR），用于估算需要预留的浮亏空间

# ---- 慢进快出（v9）：出场与进场解耦 ----
EXIT_MA = 26       # 出场短均线（进场仍用 MA34 定义大趋势）
CH_K = 3.0         # 吊灯止损：极值 ∓ 3 ATR
WARN_PULL = 1.2    # 盘整预警：自极值回撤 ≥1.2 ATR（仅提示，不平仓）

# ---- 独立出场雷达（见顶/见底探测器，与进场、与是否持仓都无关）----
# 参数由 10 年 5 品种网格寻优得出（mine_xs.py，EXT 2.0~3.0 × PULL 0.8~1.5，0.1 步长 88 组）
# 约束「每品种每年 ≥3 次」下取 20 根反转胜率最高：EXT 2.6 / PULL 1.1
#   158 次 / 10 年 = 3.35 次/品种/年；20 根方向胜率 59.6%（基准 51.4%）；
#   20 根内反向 ≥1ATR 占 67.9%；辅助≥2 的强档 63.6%；盲测段(2024+) 55.0%
XS_EXT = 2.6       # 门槛①：收盘距 MA34 ≥2.6 ATR（趋势伸展）
XS_PULL = 1.1      # 门槛②：自段内极值回撤 ≥1.1 ATR（已经开始回头）
XS_AMP = 12.0      # 辅助：段内幅度 ≥12 ATR
XS_AGE = 30        # 辅助：段龄 ≥30 根
XS_SHADOW = 0.6    # 辅助：当根反向影线 ≥0.6 ATR（冲高回落）
XS_ADX_DROP = 3.0  # 辅助：ADX 自 20 日高点回落 ≥3
XS_TIER = (1, 2)   # 辅助条数 ≥1 建议 / ≥2 强烈

DEFAULT_MODEL = {
    "version": 4, "ma": 34,
    "calib": {
        "2": {"0": 0.041, "1": 0.171, "2": 0.234, "3": 0.410, "4": 0.579},
        "3": {"0": 0.067, "1": 0.239, "2": 0.321, "3": 0.460, "4": 0.671},
        "5": {"0": 0.124, "1": 0.349, "2": 0.446, "3": 0.579, "4": 0.789},
        "10": {"0": 0.275, "1": 0.544, "2": 0.672, "3": 0.763, "4": 0.921},
    },
    "base": {"2": 0.108, "3": 0.151, "5": 0.229, "10": 0.399},
    "gate": {"adx_min": 20},
    "stat": {"n": 60, "win": 0.400, "payoff": 3.43, "yuan": 41814, "dd": -10149,
             "per_year": 4181, "avgR": 0.638, "hold": 20},
    "lead": {"median": 1, "le2": 0.73},
}

_HERE = os.path.dirname(os.path.abspath(__file__))


def load_model():
    for p in (os.path.join(_HERE, "model_v4.json"), "model_v4.json",
              os.path.join(_HERE, "model_v3.json"), "model_v3.json"):
        try:
            m = json.load(open(p, encoding="utf-8"))
            if m:
                return m
        except Exception:
            continue
    return DEFAULT_MODEL


MODEL = load_model()
ADX_MIN = int((MODEL.get("gate") or {}).get("adx_min", ADX_MIN))
MA = int(MODEL.get("ma", MA))


# ---------------------------------------------------------------- 基础指标

def resample(bars, unit="W"):
    """日线聚合为周/月 bars，保留 last_idx（该周期最后一个交易日的索引）。"""
    out, cur, cur_key = [], None, None
    for j, b in enumerate(bars):
        d = _dt.date.fromisoformat(b["d"][:10])
        key = tuple(d.isocalendar()[:2]) if unit == "W" else (d.year, d.month)
        if key != cur_key:
            if cur is not None:
                out.append(cur)
            cur_key = key
            cur = {"d": b["d"][:10], "o": b["o"], "h": b["h"], "l": b["l"],
                   "c": b["c"], "v": b["v"], "oi": b["oi"], "last_idx": j}
        else:
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
            cur["v"] += b["v"]
            cur["oi"] = b["oi"]
            cur["d"] = b["d"][:10]
            cur["last_idx"] = j
    if cur is not None:
        out.append(cur)
    return out


def ema(vals, n):
    out, k, prev = [], 2.0 / (n + 1), None
    for v in vals:
        prev = v if prev is None else prev + k * (v - prev)
        out.append(prev)
    return out


def macd_hist(closes):
    e12, e26 = ema(closes, 12), ema(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    return [(a - b) * 2 for a, b in zip(dif, dea)]


def adx_series(bars, n=14):
    """Wilder ADX / +DI / -DI"""
    L = len(bars)
    ax = [None] * L
    pdi = [None] * L
    mdi = [None] * L
    if L < n * 2 + 1:
        return ax, pdi, mdi
    trs = [bars[0]["h"] - bars[0]["l"]]
    pdm = [0.0]
    mdm = [0.0]
    for i in range(1, L):
        up = bars[i]["h"] - bars[i - 1]["h"]
        dn = bars[i - 1]["l"] - bars[i]["l"]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
        pc = bars[i - 1]["c"]
        trs.append(max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - pc), abs(bars[i]["l"] - pc)))
    atr = sum(trs[1:n + 1]) / n
    sp = sum(pdm[1:n + 1]) / n
    sm = sum(mdm[1:n + 1]) / n
    dxs = []
    for i in range(n, L):
        if i > n:
            atr = (atr * (n - 1) + trs[i]) / n
            sp = (sp * (n - 1) + pdm[i]) / n
            sm = (sm * (n - 1) + mdm[i]) / n
        p = 100 * sp / atr if atr else 0.0
        m = 100 * sm / atr if atr else 0.0
        pdi[i], mdi[i] = p, m
        dxs.append(100 * abs(p - m) / (p + m) if (p + m) else 0.0)
        if len(dxs) == 1:
            ax[i] = dxs[0]
        elif len(dxs) <= n:
            ax[i] = sum(dxs) / len(dxs)
        else:
            ax[i] = (ax[i - 1] * (n - 1) + dxs[-1]) / n
    return ax, pdi, mdi


def make_S(bars):
    closes = [b["c"] for b in bars]
    ax, pdi, mdi = adx_series(bars)
    return {"bars": bars, "n": len(bars),
            "atr": cyc.atr_series(bars),
            "ma": cyc.sma(closes, MA),
            "ma34": cyc.sma(closes, MA),
            "ma5": cyc.sma(closes, 5),
            "ma10": cyc.sma(closes, 10),
            "ma18": cyc.sma(closes, 18),
            "ma26": cyc.sma(closes, EXIT_MA),
            "vma20": cyc.sma([float(b["v"]) for b in bars], 20),
            "macd": macd_hist(closes),
            "adx": ax, "pdi": pdi, "mdi": mdi}


def segments(S):
    """按收盘 vs MA 划分趋势段 [(start, end, side)]，side=+1 多 / -1 空。"""
    bars, ma = S["bars"], S["ma"]
    segs, cur_side, start = [], 0, 0
    for i, b in enumerate(bars):
        if ma[i] is None:
            continue
        side = 1 if b["c"] > ma[i] else -1
        if side != cur_side:
            if cur_side != 0 and i - 1 >= start:
                segs.append((start, i - 1, cur_side))
            start, cur_side = i, side
    if cur_side != 0:
        segs.append((start, len(bars) - 1, cur_side))
    return segs


def seg_big(S, s, e):
    """是否够格称「大周期段」：长度 + 幅度过滤 MA 附近的小抖动。"""
    if e - s + 1 < MIN_LEN:
        return False
    bars, atr = S["bars"], S["atr"][s] or 1e-9
    hi = max(bars[k]["h"] for k in range(s, e + 1))
    lo = min(bars[k]["l"] for k in range(s, e + 1))
    return (hi - lo) / atr >= MIN_AMP


# ---------------------------------------------------------------- 反转雷达

def facts(bars, S, i, seg_hi=None, seg_lo=None):
    """第 i 根的特征值（已按趋势方向归一：≤0 恒表示「朝反转方向」）。
    seg_hi/seg_lo：当前段截至 i 的最高/最低价，用于「自极值回撤」。"""
    b = bars[i]
    atr = S["atr"][i] or 1e-9
    ma, ma10 = S["ma"][i], S["ma10"][i]
    side = 1 if (ma is None or b["c"] > ma) else -1
    v = {
        "dma": (b["c"] - ma) / atr if ma else None,
        "ret10": (b["c"] - bars[i - 10]["c"]) / atr if i >= 10 else None,
        "ma10_sl": ((ma10 - S["ma10"][i - 5]) / atr
                    if i >= 5 and ma10 is not None and S["ma10"][i - 5] else None),
        "macd": (S["macd"][i] / atr) if S["macd"][i] is not None else None,
        # 持仓量 5 日变化（原始值，不做方向归一：≤0 = 减仓）
        "doi5": ((b.get("oi") - bars[i - 5].get("oi")) / bars[i - 5].get("oi") * 100
                 if i >= 5 and b.get("oi") and bars[i - 5].get("oi") else None),
    }
    # 自极值回撤：≤0 = 已从段极值逆行（数值 = 回撤深度；≤-2 = 深回撤）
    if seg_hi is not None and seg_lo is not None:
        v["pull"] = ((b["c"] - seg_hi) / atr if side == 1 else (b["c"] - seg_lo) / atr)
    if side == -1:
        v = {k: (-x if (x is not None and k != "doi5") else x) for k, x in v.items()}
    return side, v


def radar(bars, S, i, seg_hi=None, seg_lo=None):
    """反转预测雷达：命中条数 k + ADX 门槛 → 概率 → 阶段。
    seg_hi/seg_lo：当前段截至 i 的最高/最低价（用于强确认层的回撤深度）。"""
    side, v = facts(bars, S, i, seg_hi, seg_lo)
    atr = S["atr"][i] or 1e-9
    adx = S["adx"][i]
    checks = [
        {"k": "A1", "t": "价格逼近 MA34（≤0.3 ATR）",
         "ok": v["dma"] is not None and v["dma"] <= 0.3,
         "v": ("%.2f ATR" % v["dma"]) if v["dma"] is not None else "—",
         "need": "≤0.30"},
        {"k": "A9", "t": "10 日累计逆行 ≥1.5 ATR",
         "ok": v["ret10"] is not None and v["ret10"] <= -1.5,
         "v": ("%+.2f ATR" % v["ret10"]) if v["ret10"] is not None else "—",
         "need": "≤-1.50"},
        {"k": "A15", "t": "MA10 斜率转平或转向",
         "ok": v["ma10_sl"] is not None and v["ma10_sl"] <= 0.0,
         "v": ("%+.2f ATR" % v["ma10_sl"]) if v["ma10_sl"] is not None else "—",
         "need": "≤0.00"},
        {"k": "A17", "t": "MACD 柱翻向",
         "ok": v["macd"] is not None and v["macd"] <= 0.0,
         "v": ("%+.2f ATR" % v["macd"]) if v["macd"] is not None else "—",
         "need": "≤0.00"},
    ]
    k = sum(1 for c in checks if c["ok"])
    gate = adx is not None and adx >= ADX_MIN
    calib = MODEL.get("calib") or {}
    prob = {}
    for h in ("2", "3", "5", "10"):
        row = calib.get(h) or {}
        prob[h] = row.get(str(k), 0.0)
    # 强确认层（v5 失误专项分析）：持仓 5 日下降 = 趋势资金离场，反转把握大增
    doi5 = v.get("doi5")
    pull = v.get("pull")
    checks_s = [{"k": "S1", "t": "持仓 5 日下降（趋势资金离场）",
                 "ok": doi5 is not None and doi5 <= 0,
                 "v": ("%+.1f%%" % doi5) if doi5 is not None else "—", "need": "≤0"},
                {"k": "S2", "t": "自极值回撤 ≥2 ATR",
                 "ok": pull is not None and pull <= -2,
                 "v": ("%.2f ATR" % abs(pull)) if pull is not None else "—", "need": "≥2.00"}]
    strong = all(c["ok"] for c in checks_s)
    # 建仓层概率：线上口径（每段只触发一次）实测值；强确认层用强确认样本实测值
    fh = MODEL.get("firsthit") or {}
    stt = (MODEL.get("strong") or {}).get("stat") or {}
    src = stt if strong else fh
    probA = {h: (src.get("p" + h) or 0.0) for h in ("2", "3", "5", "10")} \
        if k >= BUILD_K else prob
    return {"side": "long" if side == 1 else "short", "k": k, "checks": checks,
            "checksS": checks_s, "strong": strong,
            "doi5": doi5, "pull": pull,
            "prob": prob, "probA": probA,
            "gate": bool(gate), "adx": (round(adx, 1) if adx is not None else None),
            "adxMin": ADX_MIN,
            "dist": (v["dma"] if v["dma"] is not None else 0.0),
            "atr": atr, "ma": S["ma"][i], "ma34": S["ma"][i]}


STAGES = {0: "平静", 1: "起变化", 2: "关注", 3: "预警", 4: "准备反转"}


# ---------------------------------------------------------------- 出场雷达（v9）
def exit_track(bars, S, ei, side, atr, entry=None,
               ma_buf=0.0, ma_confirm=1, ma_fast=0.0):
    """慢进快出的出场跟踪（与 mine_v9.sim 同源）。

    ei    进场根（次日开盘进，ei 即该日）
    side  +1 多 / -1 空（持仓方向，非原趋势方向）
    离场  ① 吊灯：自进场后极值回撤 CH_K×ATR，收盘触及无条件走
          ② 短均线：先站到仓位一侧（armed），之后收盘反向穿越 MA{EXIT_MA}
    预警  自极值回撤 ≥WARN_PULL ATR 且（ATR 收缩 / 持仓 5 日下降 / 量能萎缩）
          —— 只提示，不平仓

    v24（2026-11）：短均线离场可选容错，默认关闭以保持向后兼容。
      ma_buf     = 0.0  收盘要破到 MA±buf×ATR 之外才算
      ma_confirm = 1    连续 N 根收盘都在外侧才算（防假突破洗盘）
      ma_fast    = 0.0  单根就破 ≥fast×ATR → 立即算（保命快通道，不等确认）
    """
    n = len(bars)
    mam = S.get("ma%d" % EXIT_MA) or S.get("ma")
    entry = bars[ei]["o"] if entry is None else entry
    atr = atr or (entry * 0.01)
    peak = bars[ei]["h"] if side == 1 else bars[ei]["l"]
    armed = False
    mfe = mae = 0.0
    res = {"side": "long" if side == 1 else "short", "entry": round(entry, 1),
           "armed": False, "status": "open", "exitIdx": None, "exitPx": None,
           "exitDate": None, "why": None, "hold": max(0, n - 1 - ei),
           "maLine": None, "chLine": None, "distMa": None, "distCh": None,
           "warn": False, "warnWhy": [], "warnDate": None, "warnIdx": None,
           "mfe": 0.0, "mae": 0.0,
           "atr": atr, "ei": ei, "exitMa": EXIT_MA, "chK": CH_K}
    for j in range(ei, n):
        b = bars[j]
        c = b["c"]
        if side == 1:
            peak = max(peak, b["h"])
        else:
            peak = min(peak, b["l"])
        mj = mam[j] if mam else None
        ch_line = (peak - CH_K * atr) if side == 1 else (peak + CH_K * atr)
        if not armed and mj is not None:
            if (side == 1 and c > mj) or (side == -1 and c < mj):
                armed = True
        ma_hit = False
        if armed and mj is not None:
            thr = mj - ma_buf * atr if side == 1 else mj + ma_buf * atr
            out = (c < thr) if side == 1 else (c > thr)
            if out:
                ma_hit = True
                if ma_fast > 0:                      # 保命快通道：暴破不等确认
                    thin = mj - ma_fast * atr if side == 1 else mj + ma_fast * atr
                    ma_hit = (c < thin) if side == 1 else (c > thin)
                if not ma_hit and ma_confirm > 1:    # 慢通道：连续 N 根都在外侧
                    for k in range(1, ma_confirm):
                        jj = j - k
                        mk = mam[jj] if (mam and 0 <= jj < len(mam)) else None
                        if jj < ei or mk is None:
                            ma_hit = False
                            break
                        tk = mk - ma_buf * atr if side == 1 else mk + ma_buf * atr
                        pk = bars[jj]["c"]
                        if not ((pk < tk) if side == 1 else (pk > tk)):
                            ma_hit = False
                            break
        ch_hit = (c <= ch_line) if side == 1 else (c >= ch_line)
        fav = (b["h"] - entry) if side == 1 else (entry - b["l"])
        adv = (b["l"] - entry) if side == 1 else (entry - b["h"])
        mfe = max(mfe, fav)
        mae = min(mae, adv)
        pull = ((c - peak) / atr) if side == 1 else ((peak - c) / atr)
        a20 = [S["atr"][k] for k in range(max(0, j - 19), j + 1) if S["atr"][k]]
        shrink = bool(a20 and S["atr"][j] and S["atr"][j] / (sum(a20) / len(a20)) <= 0.85)
        oi5 = None
        if j >= 5 and b.get("oi") and bars[j - 5].get("oi"):
            oi5 = (b["oi"] - bars[j - 5]["oi"]) / bars[j - 5]["oi"] * 100
        dry = bool(S["vma20"][j] and b["v"] / S["vma20"][j] <= 0.8)
        why = []
        if shrink:
            why.append("ATR 收缩")
        if oi5 is not None and oi5 <= 0:
            why.append("持仓 %+.1f%%" % oi5)
        if dry:
            why.append("缩量")
        warn = bool(armed and pull <= -WARN_PULL and why)

        if warn and res["warnDate"] is None:
            res["warnDate"], res["warnIdx"] = b["d"], j
        res.update({"armed": armed, "maLine": (round(mj, 1) if mj else None),
                    "chLine": round(ch_line, 1), "warn": warn, "warnWhy": why,
                    "mfe": round(mfe / atr, 2), "mae": round(mae / atr, 2),
                    "distMa": (round((mj - c) / atr * side, 2) if mj else None),
                    "distCh": round((c - ch_line) / atr * side, 2),
                    "pull": round(pull, 2)})
        if ch_hit or ma_hit:
            if ch_hit:
                _why = "吊灯止损"
            else:
                _why = "反向穿越 MA%d" % EXIT_MA
                if ma_fast > 0 and ma_buf > 0:
                    thin = mj - ma_fast * atr if side == 1 else mj + ma_fast * atr
                    if ((c < thin) if side == 1 else (c > thin)):
                        _why += "（单根暴破）"
                    elif ma_confirm > 1:
                        _why += "（连 %d 根）" % ma_confirm
                elif ma_confirm > 1:
                    _why += "（连 %d 根）" % ma_confirm
            res.update({"status": "closed", "exitIdx": j, "exitPx": round(c, 1),
                        "exitDate": b["d"], "hold": j - ei,
                        "why": _why,
                        "R": round((c - entry) * side / atr, 2)})
            break
    return res


# ---------------------------------------------------------------- 独立出场雷达
# 与进场、与是否持仓都无关：只回答「现在是不是一段趋势的顶/底，该考虑走了」
# 10 年五品种标定（2026-11 寻优后）：158 次 / 50 品种年 ≈ 每品种每年 3.35 次
#   辅助 0 条 → 之后 20 根净优势 +0.35 ATR
#   辅助 1 条 → +0.39 ATR   辅助 ≥2 条 → +0.87 ATR（10 根内反向≥1ATR 55.8%）

def _xs_segs(S):
    if "_xs" not in S:
        S["_xs"] = segments(S)
    return S["_xs"]


def _xs_seg_at(S, i):
    for s, e, sd in _xs_segs(S):
        if s <= i <= e:
            return s, e, sd
    return None


def xs_facts(bars, S, i, sd):
    """sd = 当前趋势方向（+1 上涨趋势→找顶；-1 下跌趋势→找底）。
    返回 8 条证据，每条 True = 支持「趋势到顶/到底」。"""
    b = bars[i]
    atr = S["atr"][i] or 1e-9
    c = b["c"]
    seg = _xs_seg_at(S, i)
    s0 = seg[0] if seg else max(0, i - 30)
    hi = max(x["h"] for x in bars[s0:i + 1])
    lo = min(x["l"] for x in bars[s0:i + 1])
    m34 = S["ma34"][i]
    ext = ((c - m34) * sd / atr) if m34 else 0.0
    extp = (hi if sd == 1 else lo)
    pull = ((c - extp) / atr) if sd == 1 else ((extp - c) / atr)
    up = (b["h"] - max(b["o"], c)) / atr
    dn = (min(b["o"], c) - b["l"]) / atr
    ax = S["adx"][i]
    hi20 = max([x for x in S["adx"][max(0, i - 19):i + 1] if x is not None] or [0])
    oi = b.get("oi") or 0
    oi5 = (bars[i - 5].get("oi") or 0) if i >= 5 else 0
    doi = (oi / oi5 - 1) if oi5 else 0.0
    m18 = S["ma18"][i]
    return {
        "ext": ext >= XS_EXT,
        "pull": pull <= -XS_PULL,
        "amp": (hi - lo) / atr >= XS_AMP,
        "shadow": (up >= XS_SHADOW) if sd == 1 else (dn >= XS_SHADOW),
        "age": (i - s0 + 1) >= XS_AGE,
        "adx": (hi20 - ax) >= XS_ADX_DROP if ax is not None else False,
        "ma18": ((c < m18) if sd == 1 else (c > m18)) if m18 else False,
        "oi": (doi <= 0) if sd == 1 else (doi >= 0),
    }


XS_GATES = ("ext", "pull")
XS_AUX = ("amp", "shadow", "age", "adx", "ma18", "oi")
XS_DESC = {
    "ext": ("趋势伸展", "收盘距 MA34 ≥2.6ATR"),
    "pull": ("极值回撤", "自段内高/低点回撤 ≥1.1ATR"),
    "amp": ("段幅足够", "本段振幅 ≥12ATR"),
    "shadow": ("冲高回落", "当根反向影线 ≥0.6ATR"),
    "age": ("趋势老化", "本段已走 ≥30 根"),
    "adx": ("动能衰减", "ADX 自 20 日高点回落 ≥3"),
    "ma18": ("短均失守", "收盘反向穿越 MA18"),
    "oi": ("资金撤退", "持仓 5 日净减"),
}


def exit_radar(bars=None, S=None, i=None, code=None, years=10):
    """独立出场雷达：当前这根 K 线的顶/底评估（不需要持仓）"""
    if bars is None:
        bars = _bars_of(code, years)
    if S is None:
        S = make_S(bars)
    if i is None:
        i = len(bars) - 1
    b = bars[i]
    seg = _xs_seg_at(S, i)
    if seg is None:
        return None
    sd = seg[2]
    f = xs_facts(bars, S, i, sd)
    gates = [k for k in XS_GATES if f[k]]
    aux = [k for k in XS_AUX if f[k]]
    level, advice = "none", "趋势未到极端位置"
    if len(gates) == len(XS_GATES):
        if len(aux) >= XS_TIER[1]:
            level, advice = "strong", "强烈建议离场"
        elif len(aux) >= XS_TIER[0]:
            level, advice = "suggest", "建议减仓/离场"
        else:
            level, advice = "watch", "到顶/底区，留意"
    atr = S["atr"][i] or 1e-9
    extp = (max(x["h"] for x in bars[seg[0]:i + 1]) if sd == 1
            else min(x["l"] for x in bars[seg[0]:i + 1]))
    return {
        "side": "long" if sd == 1 else "short",     # 该走的是哪个方向的仓
        "dir": "顶部" if sd == 1 else "底部",
        "date": b["d"], "close": round(b["c"], 1),
        "k": len(gates) + len(aux), "gate": len(gates), "aux": len(aux),
        "level": level, "advice": advice,
        "conds": [{"k": k, "n": XS_DESC[k][0], "d": XS_DESC[k][1],
                   "ok": f[k], "gate": k in XS_GATES} for k in XS_GATES + XS_AUX],
        "ext": round(((b["c"] - S["ma34"][i]) * sd / atr) if S["ma34"][i] else 0, 2),
        "pull": round(((b["c"] - extp) / atr) if sd == 1 else ((extp - b["c"]) / atr), 2),
        "m34": round(S["ma34"][i], 1) if S["ma34"][i] else None,
        "extPx": round(extp, 1),
        "age": i - seg[0] + 1,
        "segSide": sd,
    }


def exit_history(code=None, bars=None, years=10, maxn=20, dedupe=8):
    """历史独立出场信号（建议及以上），用于列表与图上标记"""
    if bars is None:
        bars = _bars_of(code, years)
    S = make_S(bars)
    out, last = [], -99
    for i in range(40, len(bars)):
        r = exit_radar(bars, S, i)
        if not r or r["level"] not in ("suggest", "strong"):
            continue
        if i - last < dedupe:
            continue
        last = i
        out.append({"d": r["date"], "sd": r["side"], "k": r["k"], "aux": r["aux"],
                    "level": r["level"], "dir": r["dir"], "px": r["close"],
                    "pull": r["pull"], "age": r["age"]})
    return out[-maxn:]


def xs_stat(code=None, years=10):
    """本品种历史独立出场信号统计（供页面显示可信度）"""
    hs = exit_history(code, years=years, maxn=10 ** 6)
    if not hs:
        return None
    n = len(hs)
    bars = _bars_of(code, years)
    span = max(1.0, len(bars) / 244.0)      # 实际样本年数（尿素上市不足 10 年）
    S = make_S(bars)
    idx = {b["d"]: k for k, b in enumerate(bars)}
    ok20, ok10 = 0, 0
    for h in hs:
        i = idx.get(h["d"])
        if i is None:
            continue
        sd = 1 if h["sd"] == "long" else -1
        if i + 10 < len(bars):
            ok10 += 1 if (bars[i]["c"] - bars[i + 10]["c"]) * sd > 0 else 0
        if i + 20 < len(bars):
            ok20 += 1 if (bars[i]["c"] - bars[i + 20]["c"]) * sd > 0 else 0
    return {"n": n, "perYear": round(n / span, 2),
            "ok10": round(ok10 / max(1, n) * 100), "ok20": round(ok20 / max(1, n) * 100),
            "strong": sum(1 for h in hs if h["level"] == "strong")}


def stage_of(k, gate=True):
    if k >= BUILD_K:
        return "准备反转" if gate else "形态到位·等趋势"
    if k == WARN_K:
        return "预警"
    if k == 2:
        return "关注"
    if k == 1:
        return "起变化"
    return "平静"


# ---------------------------------------------------------------- 信号扫描

def scan_symbol(code, bars=None, years=10, unit="D"):
    """扫描一个品种的反转预测事件。

    events:
      pred    命中 4 条 且 ADX≥门槛 → 次日开盘逆势建仓，持有到反向穿越
      warn    命中 3 条（或 4 条但 ADX 未达门槛）→ 只提示，不建仓
      confirm 实际穿越 MA（反转兑现）
    """
    if bars is None:
        adj, _ = build.panama_adjust(code)
        keep = int(years * 244)
        bars = adj[-keep:] if len(adj) > keep else adj
    if len(bars) < 200:
        return [], None
    pb = resample(bars, unit) if unit in ("W", "M") else bars
    S = make_S(pb)
    segs = segments(S)
    to_daily = (lambda k: k) if pb is bars else (lambda k: pb[k]["last_idx"])
    out = []
    for si in range(len(segs)):
        s1, e1, side1 = segs[si]
        if not seg_big(S, s1, e1):
            continue
        warn_i = pred_i = None
        r_warn = r_pred = None
        h_run, l_run = -1e18, 1e18
        for i in range(max(60, s1 + MIN_AGE - 1), e1 + 1):
            h_run = max(h_run, pb[i]["h"])
            l_run = min(l_run, pb[i]["l"])
            r = radar(pb, S, i, h_run, l_run)
            if warn_i is None and r["k"] >= WARN_K:
                warn_i, r_warn = i, r
            if r["k"] >= BUILD_K and r["gate"]:
                pred_i, r_pred = i, r
                break
        if warn_i is None and pred_i is None:
            continue
        if warn_i is not None and pred_i is None:
            r = r_warn or radar(pb, S, warn_i)
            out.append({"sym": code, "kind": "warn", "unit": unit,
                        "side": "short" if side1 == 1 else "long",
                        "date": pb[warn_i]["d"], "idx": to_daily(warn_i),
                        "price": round(pb[warn_i]["c"], 1), "k": r["k"],
                        "prob": r["prob"], "checks": r["checks"],
                        "adx": r["adx"], "gate": r["gate"],
                        "segStart": pb[s1]["d"], "segAge": warn_i - s1 + 1})
        if pred_i is None:
            continue
        r = r_pred
        new_side = -1 if side1 == 1 else 1
        d_i = to_daily(pred_i)
        ei = d_i + 1
        entry = bars[ei]["o"] if ei < len(bars) else pb[pred_i]["c"]
        atr = S["atr"][pred_i] or entry * 0.01
        nxt = segs[si + 1][0] if si + 1 < len(segs) else None
        xt = (exit_track(bars, S, ei, new_side, atr, entry)
              if (pb is bars and ei < len(bars)) else None)
        out.append({
            "sym": code, "kind": "pred", "unit": unit,
            "side": "short" if new_side == -1 else "long",
            "dir": "top" if side1 == 1 else "bot",
            "date": pb[pred_i]["d"], "idx": d_i,
            "price": round(pb[pred_i]["c"], 1),
            "k": r["k"], "prob": r["probA"], "checks": r["checks"],
            "checksS": r["checksS"], "strong": r["strong"],
            "doi5": (round(r["doi5"], 1) if r["doi5"] is not None else None),
            "adx": r["adx"], "gate": r["gate"],
            "segStart": pb[s1]["d"], "segAge": pred_i - s1 + 1,
            "entryIdx": ei, "entry": round(entry, 1),
            "atr": round(atr, 2),
            "bufYuan": round(BUF_ATR * atr * MULT.get(code, 10)),
            "lead": (nxt - pred_i) if nxt is not None else None,
            "holdTo": "反向穿越 MA%d / 吊灯 %.0fATR" % (EXIT_MA, CH_K),
            "exit": xt,
        })
        if nxt is not None:
            out.append({"sym": code, "kind": "confirm", "unit": unit,
                        "side": "long" if segs[si + 1][2] == 1 else "short",
                        "date": pb[nxt]["d"], "idx": to_daily(nxt),
                        "price": round(pb[nxt]["c"], 1), "k": 0,
                        "predDate": pb[pred_i]["d"], "lead": nxt - pred_i})
    out.sort(key=lambda x: x["idx"])
    return out, S


def live_status(code, bars=None, years=10):
    """当前雷达状态：趋势侧 / 段龄 / 距离 / 命中条数 / ADX / 概率 / 阶段 / 是否已发信号。"""
    if bars is None:
        adj, _ = build.panama_adjust(code)
        keep = int(years * 244)
        bars = adj[-keep:] if len(adj) > keep else adj
    if len(bars) < 200:
        return None
    S = make_S(bars)
    n = S["n"]
    i = n - 1
    segs = segments(S)
    cur = segs[-1] if segs else (0, i, 1)
    s1, e1, side1 = cur
    h_i = max(bars[k0]["h"] for k0 in range(s1, i + 1))
    l_i = min(bars[k0]["l"] for k0 in range(s1, i + 1))
    r = radar(bars, S, i, h_i, l_i)
    fired = False
    for j in range(max(60, s1 + MIN_AGE - 1), n):
        rr = radar(bars, S, j)
        if rr["k"] >= BUILD_K and rr["gate"]:
            fired = True
            break
    ma = S["ma"][i]
    new_long = r["side"] == "short"          # 当前空头 → 预测转多
    atr = r["atr"]
    return {"side": r["side"], "age": i - s1 + 1, "big": seg_big(S, s1, e1),
            "dist": round(r["dist"], 2), "k": r["k"],
            "stage": stage_of(r["k"], r["gate"]),
            "prob": r["prob"], "probA": r["probA"], "checks": r["checks"],
            "checksS": r["checksS"], "strong": r["strong"],
            "doi5": (round(r["doi5"], 1) if r["doi5"] is not None else None),
            "fired": fired,
            "adx": r["adx"], "adxMin": r["adxMin"], "gate": r["gate"],
            "ma34": round(ma, 2) if ma else None,
            "buf": round(BUF_ATR * atr, 2),
            "bufYuan": round(BUF_ATR * atr * MULT.get(code, 10)),
            "marginPerLot": round(bars[i]["c"] * MULT.get(code, 10) * MARGIN.get(code, 0.1)),
            "newSide": "long" if new_long else "short",
            "close": round(bars[i]["c"], 2), "atr": round(atr, 2),
            "date": bars[i]["d"], "segStart": bars[s1]["d"]}



# ==================== 转折确认信号（tf）与旧趋势跟踪模块 ====================
# 【v25 关停】旧的「趋势确认」**推送通道**已删除（tf_live / tf_trades / tf_stat 一并删掉）：
#   它按「自极值回撤 3.5×ATR」快照变化推微信，走独立于 7 品种方案的旧逻辑，属纯噪音。
# 【保留】tf_events 必须留：它是 sig_pack 里 kind="tf"（转折确认）信号的唯一来源，
#   而 Y 豆油 / P 棕榈油的 G2 方案，趋势档的进场集合里就含 'tf'。
#   删掉它 = 改方案账本，实测直接导致 plan 构建失败，故信号本体保留、只关推送与展示。
TF_K = 3.5


def _bars_of(code, years=10):
    adj, _ = build.panama_adjust(code)
    keep = int(years * 244)
    return adj[-keep:] if len(adj) > keep else adj


def tf_events(bars, S, k=None):
    """因果的转折确认序列：[(确认根, 新方向, 极值根, 极值价)]"""
    k = TF_K if k is None else k
    n = len(bars)
    if n < 30:
        return []
    ev = []
    dirn = 0
    hi = lo = bars[0]["c"]
    hi_i = lo_i = 0
    for i in range(1, n):
        c = bars[i]["c"]
        if c > hi:
            hi, hi_i = c, i
        if c < lo:
            lo, lo_i = c, i
        a = S["atr"][hi_i] or S["atr"][i] or 1e-9
        a2 = S["atr"][lo_i] or S["atr"][i] or 1e-9
        if dirn >= 0 and hi_i < i and c <= hi - k * a:
            ev.append((i, -1, hi_i, hi))
            dirn = -1
            lo, lo_i = c, i
        elif dirn <= 0 and lo_i < i and c >= lo + k * a2:
            ev.append((i, 1, lo_i, lo))
            dirn = 1
            hi, hi_i = c, i
    return ev


def position_of(sig):
    """无止损口径：不设认错价，按「需要预留的浮亏空间」给出规模建议。"""
    entry = sig.get("entry") or sig.get("price")
    atr = sig.get("atr") or entry * 0.01
    code = sig.get("sym")
    buf = BUF_ATR * atr * MULT.get(code, 10)
    return {"entry": entry, "atr": round(atr, 2), "bufYuan": round(buf),
            "holdTo": "反向穿越 MA%d" % MA}


def lots(equity, risk_pct, entry, stop, code, atr=None):
    """按「历史浮亏 P10（2.45 ATR）不超过可用预算」给手数；无止损必须有缓冲。"""
    if atr is None:
        atr = (stop and abs(entry - stop)) or entry * 0.01
    per = BUF_ATR * atr * MULT.get(code, 10)
    if per <= 0:
        return 0
    return max(0, int(equity * risk_pct / per))


# ================================================================ v11 甲方案
def weekly_pos_series(bars, ma_n=None):
    """日线每根 i 对应的【信号日之前最后一根已收盘周线】的位置方向。

    +1 = 周线收盘在 MA45 上方（周线偏多），-1 = 下方，0 = 历史不足不判定。
    纯因果：当天所在周尚未收盘，只能看上一根已收盘周线，杜绝未来函数。
    """
    ma_n = ma_n or WEEKLY_MA
    w = resample(bars, "W")
    ma = cyc.sma([x["c"] for x in w], ma_n)
    last = [x["last_idx"] for x in w]
    out = []
    for i in range(len(bars)):
        k = bisect.bisect_left(last, i) - 1
        if k >= ma_n and ma[k] is not None:
            out.append(1 if w[k]["c"] > ma[k] else -1)
        else:
            out.append(0)
    return out


# ---------------------------------------------------------------- v12：三层风险标签
# 纪律：以下全部是【只读】函数，只写进 meta 供页面/推送展示，
# 绝不参与 scan_symbol / exit_track / 入场的任何判断。
# 口径与三份专项回测逐点对齐（reports/终极融合方案_标签预警体系.md）：
#   T 日   动能真空  5 维打分，分池阈值 = 训练段(<2024)中位数
#   T+1    首日失效  A1 破信号日极值 + B1 失守 MA5，窗口=1（专项一）
#   T+3~8  结构已破坏 波谷缓冲消耗比 ≥50% 且入场缓冲 ≥1ATR（专项三）
RISK_CUTS = {
    "in":    {"dist_ma60": -1.289, "ret20": -0.589, "adx": 25.437,
              "vol_chg20": -3.298, "mkt_adx": 25.524},
    "radar": {"dist_ma60": -4.508, "ret20": -4.492, "adx": 39.360,
              "vol_chg20": 38.600, "mkt_adx": 28.034},
}
RISK_VAC_MIN = {"in": 4, "radar": 5}
RISK_SCORE_SIDE = (("dist_ma60", "high"), ("ret20", "high"), ("adx", "low"),
                   ("vol_chg20", "low"), ("mkt_adx", "low"))
RISK_DAY1_WINDOW = 1
RISK_STRUCT_MAX = 18
RISK_STRUCT_EATEN = 0.5
RISK_STRUCT_MIN_CUSHION = 1.0
_RISK_MEMO = {"bars": {}, "S": {}, "mkt": None}


def _risk_bars(code, years=10):
    k = (code, years)
    if k not in _RISK_MEMO["bars"]:
        _RISK_MEMO["bars"][k] = _bars_of(code, years)
    return _RISK_MEMO["bars"][k]


def _risk_S(code, years=10):
    k = (code, years)
    if k not in _RISK_MEMO["S"]:
        _RISK_MEMO["S"][k] = make_S(_risk_bars(code, years))
    return _RISK_MEMO["S"][k]


def _ma_n(S, n):
    """按需补 ma45/ma60（make_S 不预置），算一次后写回 S 复用。"""
    key = "ma%d" % n
    if S.get(key) is None:
        S[key] = cyc.sma([b["c"] for b in S["bars"]], n)
    return S[key]


def _mkt_adx_table():
    if _RISK_MEMO["mkt"] is None:
        tbl = {}
        for c in CODES:
            bars, S = _risk_bars(c), _risk_S(c)
            tbl[c] = ([b["d"] for b in bars], S["adx"])
        _RISK_MEMO["mkt"] = tbl
    return _RISK_MEMO["mkt"]


def mkt_adx_at(code, date):
    """其余品种 ADX(14) 等权均值，各取 <= date 的最后一根（与回测 ENV.adx 同口径）。"""
    vals = []
    for c, (ds, ax) in _mkt_adx_table().items():
        if c == code:
            continue
        k = bisect.bisect_right(ds, date) - 1
        if 0 <= k < len(ax) and ax[k] is not None:
            vals.append(ax[k])
    return (sum(vals) / len(vals)) if vals else 0.0


def vac_score(code, bars, S, i, exp, pool="in"):
    """T 日动能真空打分：5 维，全部信号日及之前可得。返回 0~5 与命中维度。"""
    cut = RISK_CUTS.get(pool) or RISK_CUTS["in"]
    b = bars[i]
    a0 = S["atr"][i] or 1e-9
    ma60 = _ma_n(S, 60)
    f = {}
    if ma60[i] is not None:
        f["dist_ma60"] = exp * (b["c"] - ma60[i]) / a0
    if i >= 20:
        f["ret20"] = exp * (b["c"] - bars[i - 20]["c"]) / a0
        v0 = bars[i - 20].get("v") or 0
        if v0:
            f["vol_chg20"] = (float(b["v"]) - v0) / float(v0) * 100.0
    f["adx"] = S["adx"][i] or 0.0
    f["mkt_adx"] = mkt_adx_at(code, b["d"])
    hit = []
    for k, side in RISK_SCORE_SIDE:
        v = f.get(k)
        if v is None:
            continue
        if (side == "high" and v > cut[k]) or (side == "low" and v < cut[k]):
            hit.append(k)
    n = len(hit)
    lo = RISK_VAC_MIN.get(pool, 4)
    return {"score": n, "hits": hit, "tag": n >= lo, "min": lo,
            "feats": {k: round(v, 3) for k, v in f.items() if v is not None}}


def day1_fail(bars, S, i, exp):
    """T+1 首日失效：A1 破信号日极值 / B1 失守 MA5（需先站上），窗口=1 根。

    与专项一 stop_probe(window=1) 完全同口径：检查入场日与次一日，共 2 个收盘。
    """
    n = len(bars)
    ei = i + 1
    if ei >= n:
        return None
    sig = bars[i]
    ma5 = S.get("ma5")
    armed = False
    for j in range(ei, min(n, ei + 1 + RISK_DAY1_WINDOW)):
        b = bars[j]
        c = b["c"]
        a5 = ma5[j] if ma5 else None
        why = []
        if (exp == 1 and c < sig["l"]) or (exp == -1 and c > sig["h"]):
            why.append("破信号日极值")
        if armed and a5 is not None and ((exp == 1 and c < a5) or (exp == -1 and c > a5)):
            why.append("失守MA5")
        if why:
            return {"day": j - i, "date": b["d"], "why": "+".join(why)}
        if a5 is not None and ((exp == 1 and c > a5) or (exp == -1 and c < a5)):
            armed = True
    return None


def _pivot(bars, lo, hi, w=3, high=False):
    # v28.5 边界修复：一个 pivot 要成立，必须左右各 w 根 K 线都存在。
    # 调用方 swing_ref 传的 hi = _trend_start(...)，它可能返回 len(bars)
    # （趋势起点正好是最后一根 K 线）。此时 k = hi-1 = n-1，读 bars[k+w]
    # 直接 IndexError，整条日更在 signal_meta → struct_break → swing_ref 处崩掉。
    # 钳制点：k + w <= n - 1，即 hi <= n - w，只在数组末端那几根不判定 pivot
    # （它们本来就没有右侧确认，不可能是合格 pivot）。
    # 当 hi <= n - w 时 min() 是恒等变换，已有信号的判定结果一个字节都不变。
    n = len(bars)
    hi = min(hi, n - w)
    for k in range(hi - 1, max(lo + w, w), -1):
        if high:
            if all(bars[k]["h"] >= bars[k - j]["h"] for j in range(1, w + 1)) and \
               all(bars[k]["h"] >= bars[k + j]["h"] for j in range(1, w + 1)):
                return k
        else:
            if all(bars[k]["l"] <= bars[k - j]["l"] for j in range(1, w + 1)) and \
               all(bars[k]["l"] <= bars[k + j]["l"] for j in range(1, w + 1)):
                return k
    return None


def _trend_start(S, i, s):
    bars, ma = S["bars"], S["ma34"]
    j = i
    while j > 40 and ma[j] is not None and s * (bars[j]["c"] - ma[j]) > 0:
        j -= 1
    return j + 1


def swing_ref(bars, S, i, s):
    """起涨/起跌之前的那个波谷/波峰（专项三同口径）。"""
    j = _trend_start(S, i, s)
    lo = max(0, j - 80)
    if j <= lo:
        return (bars[i]["l"] if s > 0 else bars[i]["h"]), i, i
    if s > 0:
        k = _pivot(bars, lo, j)
        if k is None:
            k = min(range(lo, j), key=lambda x: bars[x]["l"])
        return bars[k]["l"], j, k
    k = _pivot(bars, lo, j, high=True)
    if k is None:
        k = max(range(lo, j), key=lambda x: bars[x]["h"])
    return bars[k]["h"], j, k


def struct_break(bars, S, i, exp):
    """T+3~8 结构已破坏：入场缓冲 ≥1ATR，之后收盘吃掉该缓冲 ≥50%。纯事后确认。"""
    n = len(bars)
    if i + 1 >= n:
        return None
    entry = bars[i + 1]["o"]
    atr0 = S["atr"][i] or 1e-9
    sw, _, _ = swing_ref(bars, S, i, exp)
    denom = exp * (entry - sw)
    cushion0 = denom / atr0
    if denom <= 1e-9 or cushion0 < RISK_STRUCT_MIN_CUSHION:
        return None
    for j in range(i + 1, min(n, i + 1 + RISK_STRUCT_MAX)):
        eaten = (exp * (entry - bars[j]["c"])) / denom
        if eaten >= RISK_STRUCT_EATEN:
            return {"day": j - i, "date": bars[j]["d"], "eaten": round(eaten, 2),
                    "cushion0": round(cushion0, 2)}
    return None


def signal_meta(bars, S, i, exp, tier="normal", wpos=None, code=None, pool="in",
                review_scope="all"):
    """给一条信号打「周线降级档位」与「复盘标签」。

    exp:  预期方向，+1 涨 / -1 跌
    tier: 'strong' / 'normal'
    observe=True → 降级【观察】：页面照常显示、后台照常算盈亏，只是不推微信。
    confirm2 需要事后 2 根 K 才可知，只做事后标签/复盘，绝不用于当日推送决策。
    """
    n = len(bars)
    arr = weekly_pos_series(bars) if wpos is None else wpos
    pos = arr[i] if 0 <= i < n and i < len(arr) else 0
    align = (pos == 0) or (pos == exp)      # 周线数据不足按顺势，不误杀
    strong = (tier == "strong")
    observe = (not align) and not (KEEP_STRONG_ON_OPP and strong)

    atr = S["atr"]
    a0 = atr[i] or 1e-9
    past = [x for x in atr[max(0, i - 20):i] if x]
    ratio = a0 / (sum(past) / len(past)) if past else 1.0

    c2 = "pending"                          # 数据不足 → 待复盘
    if i + CONFIRM2_BARS < n:
        H0, L0 = bars[i]["h"], bars[i]["l"]
        broke = False
        for j in range(i + 1, i + CONFIRM2_BARS + 1):
            cc = bars[j]["c"]
            if (cc > H0) if exp == 1 else (cc < L0):
                broke = True
                break
        c2 = "followed" if broke else "noFollow"
    # v12：三层风险标签（纯展示；不参与任何决策，平仓口径仍由 exit_track 决定）
    vac = {"score": 0, "hits": [], "tag": False, "min": RISK_VAC_MIN.get(pool, 4),
           "feats": {}}
    if code:
        try:
            vac = vac_score(code, bars, S, i, exp, pool)
        except Exception:
            pass
    # review_scope: all=建仓信号（首日失效+结构）/ struct=仅结构（出场雷达）/
    #               none=只留 T 日动能真空（预警、兑现档位）
    d1 = day1_fail(bars, S, i, exp) if review_scope == "all" else None
    st = struct_break(bars, S, i, exp) if review_scope in ("all", "struct") else None
    return {"weekMA": WEEKLY_MA, "weekPos": pos, "weekAlign": bool(align),
            "tier": tier, "observe": bool(observe), "push": not observe,
            "volRatio": round(ratio, 3), "lowVol": bool(ratio < VOL_RATIO_TAG),
            "confirm2": c2,
            "risk": {"pool": pool, "vac": vac, "day1": d1, "struct": st,
                     "warn": bool(vac.get("tag")), "review": bool(d1 or st)}}


def _market_series(bars, S, flat=None, strong_slope=None):
    """市场状态序列，纯因果（只用 i 及之前的数据）。"""
    flat = MKT_FLAT if flat is None else flat
    strong_slope = MKT_STRONG_SLOPE if strong_slope is None else strong_slope
    n = len(bars)
    cl = [b["c"] for b in bars]
    mid = [None] * n
    sd = [None] * n
    bw = [None] * n
    for i in range(n):
        if i < 19:
            continue
        win = cl[i - 19:i + 1]
        m = sum(win) / 20.0
        v = math.sqrt(sum((x - m) ** 2 for x in win) / 20.0)
        mid[i], sd[i] = m, v
        bw[i] = 4 * v / m if m else 0.0
    ma60 = cyc.sma(cl, 60)
    atr = S["atr"]
    states = ["Range"] * n
    for i in range(n):
        if i < 130 or bw[i] is None or ma60[i] is None or ma60[i - 10] is None:
            continue
        win = [bw[j] for j in range(i - 119, i + 1) if bw[j] is not None]
        q = sum(1 for x in win if x <= bw[i]) / len(win)
        narrow = q <= MKT_BW_LOW_Q
        crosses = 0
        for j in range(i - 19, i + 1):
            if j < 20 or mid[j] is None or mid[j - 1] is None:
                continue
            if (cl[j] - mid[j]) * (cl[j - 1] - mid[j - 1]) < 0:
                crosses += 1
        churn = crosses >= MKT_MID_CROSS
        a0 = atr[i] or 1e-9
        slope = (ma60[i] - ma60[i - 10]) / (10 * a0)
        prev20 = bw[i - 20]
        expand = (prev20 is not None and prev20 > 0
                  and bw[i] > prev20 * MKT_BW_EXPAND and q >= MKT_BW_HIGH_Q)
        brk = cl[i] > mid[i] + 2 * sd[i] or cl[i] < mid[i] - 2 * sd[i]
        if (narrow and churn) or abs(slope) < flat:
            states[i] = "Range"
        elif (expand and brk) or abs(slope) >= strong_slope:
            states[i] = "Up" if slope > 0 else "Down"
        else:
            # 三选一：过渡态按 MA60 斜率方向归类，近乎完全走平归入震荡
            if abs(slope) < flat * 0.5:
                states[i] = "Range"
            else:
                states[i] = "Up" if slope > 0 else "Down"
    return states, {"mid": mid, "sd": sd, "bw": bw, "ma60": ma60}


def market_state_now(bars, S=None):
    """当前市场状态（三选一）。纯信息展示，不参与任何信号/推送决策。"""
    S = S or make_S(bars)
    n = len(bars)
    states, aux = _market_series(bars, S)
    i = n - 1
    st = states[i]
    cl = [b["c"] for b in bars]
    slope = None
    q = None
    cross = None
    if i >= 130 and aux["bw"][i] is not None and aux["ma60"][i] is not None \
            and aux["ma60"][i - 10] is not None:
        win = [aux["bw"][j] for j in range(i - 119, i + 1) if aux["bw"][j] is not None]
        q = sum(1 for x in win if x <= aux["bw"][i]) / len(win)
        slope = (aux["ma60"][i] - aux["ma60"][i - 10]) / (10 * (S["atr"][i] or 1e-9))
        cross = sum(1 for j in range(i - 19, i + 1)
                    if j >= 20 and aux["mid"][j] is not None
                    and aux["mid"][j - 1] is not None
                    and (cl[j] - aux["mid"][j]) * (cl[j - 1] - aux["mid"][j - 1]) < 0)
    label = {"Range": "震荡（Range）", "Up": "上涨趋势（Up Trend）",
             "Down": "下跌趋势（Down Trend）"}[st]
    return {"state": st, "label": label, "date": bars[i]["d"],
            "bwQ": round(q, 3) if q is not None else None,
            "ma60Slope": round(slope, 4) if slope is not None else None,
            "midCross": cross, "close": cl[i],
            "cfg": {"flat": MKT_FLAT, "strongSlope": MKT_STRONG_SLOPE,
                    "bwLowQ": MKT_BW_LOW_Q, "bwExpand": MKT_BW_EXPAND,
                    "midCross": MKT_MID_CROSS, "ma": 60, "boll": 20},
            "advisoryOnly": True}
