# -*- coding: utf-8 -*-
"""反转预测引擎 v4（FG/M/RB/MA/UR 五品种专用口径）
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
import datetime as _dt
import json
import os

import build
import cyc

CODES = ["FG", "M", "RB", "MA", "UR"]
NAMES = {"FG": "玻璃", "M": "豆粕", "RB": "螺纹钢", "MA": "甲醇", "UR": "尿素"}
MULT = {"FG": 20, "M": 10, "RB": 10, "MA": 10, "UR": 20}
FEE = {"FG": 6.0, "M": 1.5, "RB": 6.0, "MA": 2.0, "UR": 6.0}
MARGIN = {"FG": 0.09, "M": 0.08, "RB": 0.08, "MA": 0.08, "UR": 0.09}

MA = 34
MIN_AGE = 2        # 新段至少站稳 2 根才接受反向信号（第 1 根属“新段反抽”，另计）
MIN_LEN = 15       # 大周期段最短长度
MIN_AMP = 3.0      # 大周期段最小幅度（ATR）
BUILD_K = 4        # 建仓阈值（命中条数）
WARN_K = 3         # 预警阈值
ADX_MIN = 20       # 趋势门槛：ADX(14) 低于此值不建仓
MAXHOLD = 250      # 安全上限（回测最长 83 根，正常靠反向穿越离场）
BUF_ATR = 2.45     # 历史浮亏 P10（ATR），用于估算需要预留的浮亏空间

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
            "ma10": cyc.sma(closes, 10),
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
            "holdTo": "反向穿越 MA%d" % MA,
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



# ==================== 趋势跟踪模块（v8，独立于反转雷达） ====================
# 口径来自 mine_v8.py 的参数寻优（5 品种 / 10 年 / 训练段与盲测段双正）：
#   转折确认：自极值回撤 ≥3.5×ATR → 认定趋势转向，次日开盘顺新方向入场
#   出场：下一次反向确认 或 持有满 40 根（先到者）
#   成绩：255 笔、胜率 49%、累计 +134R、最大回撤 -27R、多空对称、五品种全正
TF_K = 3.5
TF_HOLD = 40


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


def tf_trades(code, bars, S=None, k=None, hold=None):
    """历史成交（与回测同源）：确认日次日开盘入场，反向确认或满 hold 根离场。"""
    hold = TF_HOLD if hold is None else hold
    S = S or make_S(bars)
    ev = tf_events(bars, S, k)
    out = []
    for j in range(len(ev) - 1):
        ci, nd, _pi, _pp = ev[j]
        if ci + 1 >= len(bars):
            continue
        xi = min(ci + hold, len(bars) - 1, ev[j + 1][0])
        if xi <= ci + 1:
            continue
        entry = bars[ci + 1]["o"]
        exitp = bars[xi]["c"]
        atr = S["atr"][ci] or entry * 0.01
        cost = FEE[code] / MULT[code] + 1.0
        pts = (exitp - entry) * nd
        out.append({"date": bars[ci + 1]["d"], "side": "long" if nd == 1 else "short",
                    "entry": round(entry, 1), "exit": round(exitp, 1),
                    "exitDate": bars[xi]["d"], "pts": round(pts - cost, 1),
                    "R": round((pts - cost) / atr, 2), "bars": xi - ci - 1,
                    "why": "反向确认" if xi == ev[j + 1][0] else "持有满 %d 根" % hold,
                    "atr": round(atr, 1)})
    return out


def tf_live(code, bars=None, years=10):
    """当前趋势跟踪状态：方向 / 入场 / 已持有 / 反向确认价 / 浮动盈亏"""
    bars = bars or _bars_of(code, years)
    S = make_S(bars)
    ev = tf_events(bars, S)
    if not ev:
        return None
    ci, nd, _pi, _pp = ev[-1]
    ei = ci + 1
    atr = S["atr"][ci] or bars[ci]["c"] * 0.01
    last = len(bars) - 1
    entry = bars[ei]["o"] if ei < len(bars) else bars[-1]["c"]
    held = last - ei if ei <= last else 0
    ext = entry
    for j in range(ei, last + 1):
        if nd == 1:
            ext = max(ext, bars[j]["h"])
        else:
            ext = min(ext, bars[j]["l"])
    flip = ext - TF_K * atr if nd == 1 else ext + TF_K * atr
    cur = bars[-1]["c"]
    ptsv = (cur - entry) * nd
    return {"side": "long" if nd == 1 else "short",
            "since": bars[ei]["d"] if ei < len(bars) else bars[-1]["d"],
            "entry": round(entry, 1), "held": held, "hold": TF_HOLD,
            "remain": max(0, TF_HOLD - held),
            "extreme": round(ext, 1), "flip": round(flip, 1),
            "floatR": round(ptsv / atr, 2), "floatPts": round(ptsv, 1),
            "atr": round(atr, 1), "last": round(cur, 1),
            "expired": held >= TF_HOLD, "k": TF_K}


def tf_stat(code=None, years=10):
    trs = []
    for c in ([code] if code else CODES):
        b = _bars_of(c, years)
        trs += tf_trades(c, b)
    if not trs:
        return None
    n = len(trs)
    w = sum(1 for t in trs if t["R"] > 0)
    eq = peak = dd = 0.0
    for t in sorted(trs, key=lambda x: x["date"]):
        eq += t["R"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {"n": n, "win": round(w / n, 3), "R": round(eq, 1), "dd": round(dd, 1),
            "avg": round(eq / n, 2), "perYear": round(n / 10.0, 1)}


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
