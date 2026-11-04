# -*- coding: utf-8 -*-
"""v4-A：五个品种（FG/M/RB/MA/UR）· MA 周期寻优 · ADX 过滤检验 · 不设止损
=====================================================================
口径（严格按用户实际做法）：
  - 只做玻璃 FG、豆粕 M、螺纹 RB、甲醇 MA、尿素 UR
  - 趋势定义：收盘 > MA(n) 为多段，< MA(n) 为空段，交叉日 = 反转日
  - 交易：交叉日收盘确认 → 次日开盘反手进场 → 持有到下一次交叉（次日开盘）
  - **不设止损**，只靠反向穿越离场；同时统计「持仓期间最大浮亏 MAE」
  - 手续费按每手往返计，含 1 跳滑点
输出：各 MA 周期的笔数/胜率/平均R/累计盈亏/最大回撤/盈亏比，训练段与盲测段分开
"""
import json
import statistics as st

import build
import cyc

UNIV = ["FG", "M", "RB", "MA", "UR"]
NAME = {"FG": "玻璃", "M": "豆粕", "RB": "螺纹", "MA": "甲醇", "UR": "尿素"}
MULT = {"FG": 20, "M": 10, "RB": 10, "MA": 10, "UR": 20}
FEE = {"FG": 6.0, "M": 1.5, "RB": 6.0, "MA": 2.0, "UR": 6.0}
TICK = {"FG": 1.0, "M": 1.0, "RB": 1.0, "MA": 1.0, "UR": 1.0}
SPLIT = "2024-01-01"
YEARS = 10
MAS = [20, 25, 30, 34, 40, 45, 50, 55, 60, 70]


# ---------------------------------------------------------------- 指标
def adx(bars, n=14):
    """Wilder ADX / +DI / -DI"""
    L = len(bars)
    pdi = [None] * L
    mdi = [None] * L
    ax = [None] * L
    if L < n * 2:
        return ax, pdi, mdi
    trs, pdm, mdm = [], [], []
    for i in range(L):
        if i == 0:
            trs.append(bars[0]["h"] - bars[0]["l"])
            pdm.append(0.0)
            mdm.append(0.0)
            continue
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
        p = 100 * sp / atr if atr else 0
        m = 100 * sm / atr if atr else 0
        pdi[i], mdi[i] = p, m
        dxs.append(100 * abs(p - m) / (p + m) if (p + m) else 0)
        if len(dxs) == 1:
            ax[i] = dxs[0]
        elif len(dxs) <= n:
            ax[i] = sum(dxs) / len(dxs)
        else:
            ax[i] = (ax[i - 1] * (n - 1) + dxs[-1]) / n
    return ax, pdi, mdi


def load(code):
    adj, _ = build.panama_adjust(code)
    keep = int(YEARS * 244)
    return adj[-keep:] if len(adj) > keep else adj


def segs_of(bars, n):
    closes = [b["c"] for b in bars]
    S = cyc.sma(closes, n)
    segs, cur, start = [], 0, 0
    for i in range(len(bars)):
        if S[i] is None:
            continue
        side = 1 if closes[i] > S[i] else -1
        if side != cur:
            if cur != 0:
                segs.append((start, i - 1, cur))
            start, cur = i, side
    if cur:
        segs.append((start, len(bars) - 1, cur))
    return segs, S


def run(bars, code, n, flt=None):
    """不设止损，反向穿越离场。flt(adx, i) -> bool，返回 True 才进场。"""
    segs, S = segs_of(bars, n)
    ax, pdi, mdi = adx(bars)
    atrs = cyc.atr_series(bars)
    cost = FEE[code] / MULT[code] + TICK[code] * 1.0     # 元 → 点位（含1跳滑点×1次单边）
    out = []
    for si in range(len(segs) - 1):
        s, e, side = segs[si]
        nxt = segs[si + 1][0]
        ei = s + 1
        if ei >= len(bars):
            continue
        if flt is not None and not flt(ax, pdi, mdi, s):
            continue
        entry = bars[ei]["o"]
        xi = nxt + 1
        exitp = bars[xi]["o"] if xi < len(bars) else bars[-1]["c"]
        atr = atrs[s] or entry * 0.01
        pts = (exitp - entry) * side - cost
        # 持仓期最大浮亏（ATR 计）
        mae = 0.0
        for j in range(ei, min(xi, len(bars))):
            adv = (bars[j]["l"] - entry) * side if side == 1 else (entry - bars[j]["h"])
            if adv < mae:
                mae = adv
        out.append({"code": code, "in": bars[ei]["d"], "out": bars[xi]["d"] if xi < len(bars) else bars[-1]["d"],
                    "side": side, "entry": entry, "exit": exitp, "pts": pts,
                    "R": pts / atr, "atr": atr, "bars": xi - ei, "mae": mae / atr,
                    "yr": bars[ei]["d"][:4], "adx": ax[s]})
    return out


def curve(trs):
    """按时间顺序 1 手/笔 的累计资金曲线 → 总盈亏、最大回撤"""
    trs = sorted(trs, key=lambda t: t["in"])
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for t in trs:
        eq += t["pts"] * MULT[t["code"]]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return eq, dd


def stat(trs, label=""):
    if not trs:
        return None
    n = len(trs)
    win = sum(1 for t in trs if t["pts"] > 0)
    gp = sum(t["pts"] * MULT[t["code"]] for t in trs if t["pts"] > 0)
    gl = -sum(t["pts"] * MULT[t["code"]] for t in trs if t["pts"] <= 0)
    eq, dd = curve(trs)
    yrs = max(0.5, len({t["in"][:4] for t in trs}))
    return {"label": label, "n": n, "win": win / n, "avgR": st.mean([t["R"] for t in trs]),
            "sumR": sum(t["R"] for t in trs), "yuan": eq, "dd": dd,
            "pf": (gp / gl) if gl else 99, "payoff": (gp / win) / (gl / max(1, n - win)) if gl and win else 0,
            "per_year": eq / yrs, "mae_med": st.median([t["mae"] for t in trs]),
            "mae_p10": sorted(t["mae"] for t in trs)[max(0, n // 10)],
            "hold": st.median([t["bars"] for t in trs])}


def fmt(s):
    return ("n=%3d 胜率%5.1f%% 平均%+6.2fR 累计%+7.1fR 盈亏%+9.0f元 回撤%8.0f元 PF%5.2f 持%3.0f根"
            % (s["n"], s["win"] * 100, s["avgR"], s["sumR"], s["yuan"], s["dd"], s["pf"], s["hold"]))


def main():
    data = {}
    for c in UNIV:
        b = load(c)
        data[c] = b
        print("%-3s %-4s %5d 根  %s → %s" % (c, NAME[c], len(b), b[0]["d"], b[-1]["d"]))
    print()

    print("=" * 118)
    print("一、MA 周期寻优（不设止损，反向穿越离场，1 手/笔，含手续费+1跳滑点）")
    print("=" * 118)
    print("%-5s %s" % ("MA", "全部样本"))
    rows = {}
    for n in MAS:
        trs = []
        for c in UNIV:
            trs += run(data[c], c, n)
        s = stat(trs)
        rows[n] = trs
        print("%-5d %s" % (n, fmt(s)))
    print()
    print("分品种（累计盈亏 元 / 胜率）：")
    print("%-4s %s" % ("MA", "  ".join("%-16s" % NAME[c] for c in UNIV)))
    for n in MAS:
        cells = []
        for c in UNIV:
            sub = [t for t in rows[n] if t["code"] == c]
            if sub:
                stt = stat(sub)
                cells.append("%+8.0f/%4.0f%%" % (stt["yuan"], stt["win"] * 100))
            else:
                cells.append("      —     ")
        print("%-4d %s" % (n, "  ".join("%-16s" % x for x in cells)))

    print()
    print("样本内(<%s) / 样本外分开：") 
    print("%-4s %-52s %-52s" % ("MA", "样本内 2016~2023", "样本外 2024~"))
    for n in MAS:
        tr = [t for t in rows[n] if t["in"] < SPLIT]
        te = [t for t in rows[n] if t["in"] >= SPLIT]
        print("%-4d %-52s %-52s" % (n, fmt(stat(tr)) if tr else "—",
                                    fmt(stat(te)) if te else "—"))

    # 选出综合最优（以 元/最大回撤 为主，兼顾样本外同向）
    best = None
    for n in MAS:
        tr = [t for t in rows[n] if t["in"] < SPLIT]
        te = [t for t in rows[n] if t["in"] >= SPLIT]
        if not tr or not te:
            continue
        a, b = stat(tr), stat(te)
        if a["yuan"] <= 0 or b["yuan"] <= 0:
            score = -1e9
        else:
            score = min(a["yuan"] / max(1, -a["dd"]), b["yuan"] / max(1, -b["dd"]))
        if best is None or score > best[0]:
            best = (score, n)
    print("\n样本内外都赚钱、且 收益/回撤 最差的半年也最优 → MA = %d" % best[1])
    BN = best[1]

    json.dump({"best_ma": BN, "mas": MAS}, open("v4a_ma.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print()
    print("=" * 118)
    print("二、ADX 趋势过滤（在 MA=%d 基础上，进场时点判断）" % BN)
    print("=" * 118)
    FLTS = [
        ("无过滤", None),
        ("ADX>=18", lambda a, p, m, i: a[i] is not None and a[i] >= 18),
        ("ADX>=22", lambda a, p, m, i: a[i] is not None and a[i] >= 22),
        ("ADX>=25", lambda a, p, m, i: a[i] is not None and a[i] >= 25),
        ("ADX>=30", lambda a, p, m, i: a[i] is not None and a[i] >= 30),
        ("ADX<25（弱趋势才做）", lambda a, p, m, i: a[i] is not None and a[i] < 25),
        ("ADX 上行(>5根前)", lambda a, p, m, i: a[i] is not None and i >= 5 and a[i - 5] is not None and a[i] > a[i - 5]),
        ("ADX 下行(<5根前)", lambda a, p, m, i: a[i] is not None and i >= 5 and a[i - 5] is not None and a[i] < a[i - 5]),
        ("ADX>=22 且 上行", lambda a, p, m, i: a[i] is not None and a[i] >= 22 and i >= 5 and a[i - 5] is not None and a[i] > a[i - 5]),
    ]
    res = []
    for name, f in FLTS:
        trs = []
        for c in UNIV:
            trs += run(data[c], c, BN, f)
        s = stat(trs)
        tr = [t for t in trs if t["in"] < SPLIT]
        te = [t for t in trs if t["in"] >= SPLIT]
        s["tr"] = stat(tr) if tr else None
        s["te"] = stat(te) if te else None
        res.append((name, s))
        extra = ""
        if s["tr"] and s["te"]:
            extra = " | 内%+.0f元 外%+.0f元" % (s["tr"]["yuan"], s["te"]["yuan"])
        print("%-22s %s%s" % (name, fmt(s), extra))

    # 分品种 ADX 过滤前后
    print("\n分品种对比（ADX>=22 vs 无过滤）：")
    print("%-4s %-24s %-24s" % ("品种", "无过滤", "ADX>=22"))
    for c in UNIV:
        a = stat(run(data[c], c, BN))
        b = stat(run(data[c], c, BN, FLTS[2][1]))
        print("%-4s %-24s %-24s" % (NAME[c],
              "%+.0f元 胜%2.0f%%" % (a["yuan"], a["win"] * 100) if a else "—",
              "%+.0f元 胜%2.0f%%" % (b["yuan"], b["win"] * 100) if b else "—"))

    # 逐年
    print("\n逐年盈亏（MA=%d，1 手/笔，元）：" % BN)
    trs = rows[BN]
    yrs = sorted({t["in"][:4] for t in trs})
    print("%-6s %s" % ("年份", "  ".join("%-12s" % NAME[c] for c in UNIV) + "   合计"))
    for y in yrs:
        cells, tot = [], 0
        for c in UNIV:
            v = sum(t["pts"] * MULT[c] for t in trs if t["code"] == c and t["in"][:4] == y)
            tot += v
            cells.append("%+12.0f" % v)
        print("%-6s %s %+9.0f" % (y, "  ".join(cells), tot))


if __name__ == "__main__":
    main()
