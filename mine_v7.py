# -*- coding: utf-8 -*-
"""v7：趋势启动（增仓破位）检测器 —— 补「反转雷达」抓不到的趋势起点

背景：玻璃 2026-03→10 的 7 个月空头趋势，Q1 全程无任何信号。原因是反转雷达
只在「够格的大周期段末端」监测（段≥15根且幅度≥3ATR），而 2026Q1 玻璃是 1179-1325
的横盘，9 个小段全部不够格，ADX 也一直在 7-19。这是「趋势启动」而不是「趋势反转」，
需要另一个方向的检测器。

本脚本检验：增仓 + 破位 + 站到 MA34 一侧 → 趋势启动。持有到反向穿越 MA34。
"""
import statistics as st
import build
import mine_v4b as M

UNIV = M.UNIV
HOLD_MAX = 250


def prep(code):
    adj, _ = build.panama_adjust(code)
    keep = 10 * 244
    return adj[-keep:] if len(adj) > keep else adj


def ind(bars):
    """最小指标集：ATR / MA34 / ADX / 量比 / 持仓5日"""
    n = len(bars)
    tr = [0.0] * n
    atr = [None] * n
    for i in range(n):
        b = bars[i]
        if i == 0:
            tr[i] = b["h"] - b["l"]
        else:
            pc = bars[i - 1]["c"]
            tr[i] = max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
    for i in range(13, n):
        atr[i] = sum(tr[i - 13:i + 1]) / 14
    ma = [None] * n
    for i in range(33, n):
        ma[i] = sum(bars[j]["c"] for j in range(i - 33, i + 1)) / 34
    # ADX(14) Wilder
    adx = [None] * n
    pdi = [None] * n
    ndi = [None] * n
    if n > 30:
        smtr = sum(tr[1:15])
        sp = sum(max(bars[i]["h"] - bars[i - 1]["h"], 0) for i in range(1, 15))
        sn = sum(max(bars[i - 1]["l"] - bars[i]["l"], 0) for i in range(1, 15))
        dxs = []
        for i in range(14, n):
            if i > 14:
                smtr = smtr - smtr / 14 + tr[i]
                sp = sp - sp / 14 + max(bars[i]["h"] - bars[i - 1]["h"], 0)
                sn = sn - sn / 14 + max(bars[i - 1]["l"] - bars[i]["l"], 0)
            p = 100 * sp / smtr if smtr else 0
            q = 100 * sn / smtr if smtr else 0
            pdi[i], ndi[i] = p, q
            dxs.append(100 * abs(p - q) / (p + q) if (p + q) else 0)
            if len(dxs) >= 14:
                adx[i] = sum(dxs[-14:]) / 14
    vr = [None] * n
    doi5 = [None] * n
    for i in range(5, n):
        vs = [bars[j].get("v") or 0 for j in range(max(0, i - 19), i + 1)]
        m = sum(vs) / len(vs) if vs else 0
        vr[i] = (bars[i].get("v") or 0) / m if m else None
        o0, o1 = bars[i - 5].get("oi") or 0, bars[i].get("oi") or 0
        doi5[i] = (o1 - o0) / o0 * 100 if o0 else None
    return {"atr": atr, "ma": ma, "adx": adx, "vr": vr, "doi5": doi5}


def detect(code, bars, I, oi_min=5.0, brk=20, need_ma34=True, adx_min=18):
    """增仓破位：收盘破 20 日极值 + 持仓 5 日增幅≥oi_min + 收盘在破位方向一侧的 MA34 外 + ADX≥adx_min"""
    out = []
    n = len(bars)
    for i in range(max(60, brk + 1), n):
        if I["ma"][i] is None or I["atr"][i] is None:
            continue
        if I["doi5"][i] is None or I["doi5"][i] < oi_min:
            continue
        if adx_min is not None and (I["adx"][i] is None or I["adx"][i] < adx_min):
            continue
        hi = max(bars[j]["h"] for j in range(i - brk, i))
        lo = min(bars[j]["l"] for j in range(i - brk, i))
        c = bars[i]["c"]
        if c > hi:
            side = 1
        elif c < lo:
            side = -1
        else:
            continue
        if need_ma34:
            if side == 1 and c <= I["ma"][i]:
                continue
            if side == -1 and c >= I["ma"][i]:
                continue
        out.append({"sym": code, "i": i, "date": bars[i]["d"], "side": side, "c": c,
                    "atr": I["atr"][i], "adx": I["adx"][i], "vr": I["vr"][i],
                    "doi5": I["doi5"][i], "ma": I["ma"][i]})
    return out


def trade(code, bars, I, s):
    """次日开盘进；持有到收盘反向穿越 MA34；最长 HOLD_MAX"""
    i = s["i"]
    if i + 1 >= len(bars):
        return None
    side = s["side"]
    entry = bars[i + 1]["o"]
    for j in range(i + 2, min(i + 1 + HOLD_MAX, len(bars))):
        ma = I["ma"][j]
        if ma is None:
            continue
        if (side == 1 and bars[j]["c"] < ma) or (side == -1 and bars[j]["c"] > ma):
            px = bars[j]["c"]
            return {"sym": code, "in": bars[i + 1]["d"], "out": bars[j]["d"], "side": side,
                    "entry": entry, "exit": px, "pts": (px - entry) * side,
                    "R": (px - entry) * side / s["atr"], "bars": j - i - 1,
                    "adx": s["adx"], "doi5": s["doi5"], "vr": s["vr"], "open": False}
    j = len(bars) - 1
    px = bars[j]["c"]
    return {"sym": code, "in": bars[i + 1]["d"], "out": bars[j]["d"], "side": side,
            "entry": entry, "exit": px, "pts": (px - entry) * side,
            "R": (px - entry) * side / s["atr"], "bars": j - i - 1,
            "adx": s["adx"], "doi5": s["doi5"], "vr": s["vr"], "open": True}


def run(oi_min=5.0, brk=20, need_ma34=True, adx_min=18, quiet=False):
    trs = []
    det = {}
    for c in UNIV:
        bars = prep(c)
        I = ind(bars)
        ss = detect(c, bars, I, oi_min, brk, need_ma34, adx_min)
        det[c] = (ss, bars)
        for s in ss:
            t = trade(c, bars, I, s)
            if t:
                trs.append(t)
    trs.sort(key=lambda x: x["in"])
    n = len(trs)
    if not n:
        if not quiet:
            print("  n=0")
        return None, det
    win = sum(1 for t in trs if t["pts"] > 0)
    eq = peak = dd = 0
    for t in trs:
        eq += t["R"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    if not quiet:
        print("  oi≥%.0f%% 破%d日 MA34=%s ADX≥%s → n=%3d 胜率%.0f%% 累计%+.1fR 均%+.2fR 回撤%+.1fR 持%d根"
              % (oi_min, brk, "需" if need_ma34 else "免", adx_min if adx_min is not None else "免",
                 n, win / n * 100, eq, st.mean([t["R"] for t in trs]), dd,
                 st.median([t["bars"] for t in trs])))
    return {"n": n, "win": win / n, "R": eq, "dd": dd,
            "avgR": st.mean([t["R"] for t in trs]), "trs": trs}, det


if __name__ == "__main__":
    print("=== 一、参数扫描（五品种 10 年）===")
    grid = []
    for oi in (3, 5, 8):
        for adx in (None, 15, 18, 20):
            r, _ = run(oi, 20, True, adx, quiet=False)
            if r:
                grid.append((oi, adx, r))
    print()
    print("=== 二、关掉 MA34 与 ADX（纯破位+增仓）===")
    for oi in (0, 5):
        run(oi, 20, False, None)

    print()
    print("=== 三、选定参数 n / 胜率 / 收益 / 回撤 ===")
    best, det = run(5, 20, True, 15)
    print()
    print("=== 四、命中明细（2025-01 之后）===")
    print("%-4s %-11s %-5s %8s %8s %7s %6s %6s %6s" % ("品种", "进场", "方向", "入场", "出场", "点数", "R", "ADX", "持"))
    for t in best["trs"]:
        if t["in"] >= "2025-01-01":
            print("%-4s %-11s %-5s %8.1f %8.1f %+7.0f %+6.2f %6.1f %6d" % (
                t["sym"], t["in"], "多" if t["side"] == 1 else "空", t["entry"], t["exit"],
                t["pts"], t["R"], t["adx"], t["bars"]))
    print()
    print("=== 五、玻璃 2026-02 ~ 2026-05 触发点 ===")
    ss, bars = det["FG"]
    for s in ss:
        if "2026-02-01" <= s["date"] <= "2026-05-31":
            print("  %s %s 收%.1f MA34=%.1f 破20日%s 持仓5日%+.1f%% 量比%.2f ADX%.1f" % (
                s["date"], "多" if s["side"] == 1 else "空", s["c"], s["ma"],
                "高" if s["side"] == 1 else "低", s["doi5"], s["vr"] or 0, s["adx"] or 0))
