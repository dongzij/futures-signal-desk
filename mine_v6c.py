# -*- coding: utf-8 -*-
"""v6c：真实换月重构回测
=====================================================================
不再用前复权连续序列生成信号，改为：
  1) 拉取各品种全部月合约的真实日线+持仓量（可回溯到 2018 年中）
  2) 按每日最大持仓量确定当日主力，拼接出「真实主力序列」（价格不复权）
  3) 在真实序列上重算 MA34/ATR/ADX/4 条特征，按线上口径生成反转信号
  4) 交易规则：信号次日开盘在旧主力上进场；持有期间若发生换月
     → 旧主力当日收盘平仓，新主力重新评估（不等反转）
  5) 与复权口径逐笔对比：信号日期差异、收益差异
  6) 换月窗口共振分析：换月前后 2-3 周，新旧主力的持仓与价格
     出现「同向共振」还是「背离」，及其对后续趋势的指示
"""
import statistics as st

import build
import mine_v4b as M

YEARS_BACK = 9           # 合约枚举回溯年数（数据实际起点 2018-05）
MONTHS_FWD = 12


def contracts(variety):
    months = build.MONTHS.get(variety, list(range(1, 13)))
    out = []
    today = build.__dict__.get("_TODAY") or None
    import datetime as _dt
    t = _dt.date.today()
    for y in range(t.year - YEARS_BACK, t.year + 2):
        for mm in months:
            dt = _dt.date(y, mm, 1)
            if dt > t + _dt.timedelta(days=30 * MONTHS_FWD):
                continue
            out.append("%s%02d%02d" % (variety, y % 100, mm))
    return out


def fetch_all(variety):
    """返回 {code: {date: bar}}，只用本地缓存命中的 + 现拉的"""
    series = {}
    for code in contracts(variety):
        try:
            rows = build.fetch_daily(code)
        except Exception:                              # noqa: BLE001
            rows = []
        if rows and len(rows) > 20:
            series[code] = {r["d"]: r for r in rows}
    return series


def build_real(variety):
    """真实主力序列（价格不复权）+ 换月表"""
    series = fetch_all(variety)
    if len(series) < 2:
        return None, None, None
    all_dates = sorted({d for mp in series.values() for d in mp})
    main_of = {}
    for d in all_dates:
        best, boi = None, -1.0
        for code, mp in series.items():
            b = mp.get(d)
            if b and (b.get("oi") or 0) > boi:
                best, boi = code, b.get("oi") or 0
        if best:
            main_of[d] = best
    # 真实序列：每日直接取当日主力合约的真实 bar
    bars, rolls = [], []
    prev = None
    for d in all_dates:
        code = main_of[d]
        b = dict(series[code][d])
        b["main"] = code
        bars.append(b)
        if prev and code != prev:
            rolls.append({"date": d, "from": prev, "to": code})
        prev = code
    return bars, rolls, series


def scan_real(code, bars):
    """在真实序列上按线上口径找首轮触发点（每段一次）"""
    P = M.prep(bars, 34)
    S, segs = P["S"], P["segs"]
    cs = [x for x in M.CONDS if x[0] in V5REF]
    by_i = {r["i"]: r for r in
            (M.rows_of(bars, code, 34)[0])}
    out = []
    for si, (s1, e1, sd) in enumerate(segs):
        if not V5.seg_big(P, s1, e1) or si + 2 >= len(segs):
            continue
        for i in range(max(60, s1 + V5MIN_AGE - 1), e1 + 1):
            r = by_i.get(i)
            if r is None:
                continue
            if not all(M.ok(r, x) for x in cs):
                continue
            adx = S["adx"][i]
            if adx is None or adx < V5ADX:
                continue
            out.append({"si": si, "i": i, "date": bars[i]["d"], "side": sd,
                        "seg": (s1, e1, sd), "seg_end": e1})
            break
    return P, out


import mine_v5 as V5
V5REF = V5.REF
V5MIN_AGE = V5.MIN_AGE
V5ADX = V5.ADX_MIN


def trade_real(P, sig, bars, rolls, code):
    """真实序列交易：换月即平仓"""
    s1, e1, sd = sig["seg"]
    i = sig["i"]
    if i + 1 >= len(bars):
        return None
    side = -sd
    entry = bars[i + 1]["o"]
    entry_i = i + 1
    # 离场：反向穿越 / 换月 / 数据结束
    armed, exit_px, exit_i, why = False, None, None, None
    roll_dates = {r["date"] for r in rolls}
    for j in range(entry_i, min(entry_i + 250, len(bars))):
        b, ma = bars[j], P["S"]["ma"][j]
        if ma is not None and (b["main"] != bars[entry_i]["main"]):
            exit_px, exit_i, why = b["c"], j, "换月平仓"
            break
        if b["d"] in roll_dates and j > entry_i and b["main"] != bars[entry_i]["main"]:
            exit_px, exit_i, why = b["c"], j, "换月平仓"
            break
        if ma is not None:
            if not armed:
                if (side == 1 and b["c"] > ma) or (side == -1 and b["c"] < ma):
                    armed = True
                continue
            if (side == 1 and b["c"] < ma) or (side == -1 and b["c"] > ma):
                exit_px, exit_i, why = b["c"], j, "反向穿越"
                break
    if exit_px is None:
        exit_px, exit_i, why = bars[-1]["c"], len(bars) - 1, "数据结束"
    atr = P["S"]["atr"][i] or entry * 0.01
    pts = (exit_px - entry) * side - M.FEE[code] / M.MULT[code] - 1.0
    return {"in": bars[entry_i]["d"], "exitDate": bars[exit_i]["d"], "why": why,
            "pts": pts, "R": pts / atr, "hold": exit_i - entry_i,
            "entryC": bars[entry_i]["main"]}


def main():
    print("=" * 84)
    print("一、真实主力序列 vs 复权连续序列：信号与交易对比")
    tot_real, tot_adj = [], []
    for c in M.UNIV:
        bars, rolls, series = build_real(c)
        if not bars:
            print("  %-3s 合约数据不足，跳过" % c)
            continue
        P, sigs = scan_real(c, bars)
        trs = [t for t in (trade_real(P, s, bars, rolls, c) for s in sigs) if t]
        # 复权口径同品种交易（mine_v5.collect 已含）
        adj_trs = [h for h in ADJ_HITS.get(c, [])]
        n_r, n_a = len(trs), len(adj_trs)
        rR = sum(t["R"] for t in trs)
        aR = sum(t["R"] for t in adj_trs)
        bywhy = {}
        for t in trs:
            bywhy[t["why"]] = bywhy.get(t["why"], 0) + 1
        print("  %-3s 真实口径: %2d笔 合计%+6.1fR %s | 复权口径: %2d笔 %+6.1fR"
              % (c, n_r, rR, bywhy, n_a, aR))
        for t in trs:
            print("       %s 入 %s → %s (%d根) %s %+6.2fR [%s]" %
                  (t["in"], t["entryC"], t["exitDate"], t["hold"], t["why"], t["R"], c))
        tot_real += trs
    if tot_real:
        n = len(tot_real)
        w = sum(1 for t in tot_real if t["pts"] > 0)
        print("  真实口径合计 %d 笔 胜率 %.0f%% 合计 %+.1fR" %
              (n, w / n * 100, sum(t["R"] for t in tot_real)))

    print()
    print("=" * 84)
    print("二、换月窗口共振分析（换月日前后 ±10 个交易日，约 2-3 周）")
    for c in M.UNIV:
        bars, rolls, series = build_real(c)
        if not bars or not rolls:
            print("  %-3s 无换月数据" % c)
            continue
        idx_of = {b["d"]: k for k, b in enumerate(bars)}
        rows = []
        for rl in rolls:
            k = idx_of.get(rl["date"])
            if k is None or k < 12 or k + 20 >= len(bars):
                continue
            o_old = series[rl["from"]].get(bars[k]["d"])
            n_old = series[rl["to"]].get(bars[k]["d"])
            if not o_old or not n_old:
                continue
            # 迁移质量：新主力持仓增% + 旧主力持仓减% （换月日前5 → 后10日）
            def oi_at(code, kk):
                b = series[code].get(bars[kk]["d"])
                return (b.get("oi") or 0) if b else None
            k0 = max(0, k - 5)
            oi_new0, oi_new1 = oi_at(rl["to"], k0), oi_at(rl["to"], k + 10)
            oi_old0, oi_old1 = oi_at(rl["from"], k0), oi_at(rl["from"], k + 10)
            if not oi_new0 or not oi_old0:
                continue
            mig = (oi_new1 - oi_new0) / oi_new0 * 100 - \
                  (oi_old1 - oi_old0) / oi_old0 * 100 if oi_new1 and oi_old1 else None
            # 新主力随后 20 日真实趋势幅度（点）
            px0 = series[rl["to"]][bars[k]["d"]]["c"]
            px1 = None
            for kk in range(k + 1, min(k + 21, len(bars))):
                b = series[rl["to"]].get(bars[kk]["d"])
                if b:
                    px1 = b["c"]
            if px1 is None:
                continue
            rows.append({"date": rl["date"], "mig": mig, "trend": abs(px1 - px0),
                         "dir": 1 if px1 > px0 else -1})
        if not rows:
            print("  %-3s 有效换月样本不足" % c)
            continue
        migs = [r["mig"] for r in rows if r["mig"] is not None]
        good = [r for r in rows if r["mig"] is not None and r["mig"] >= 10]
        bad = [r for r in rows if r["mig"] is not None and r["mig"] <= -10]
        print("  %-3s 换月 %2d 次  顺畅迁移(mig≥10%%) %d 次：后续20日均幅 %6.0f 点 | "
              "逆迁移(mig≤-10%%) %d 次：%6.0f 点"
              % (c, len(rows), len(good),
                 st.mean([r["trend"] for r in good]) if good else 0,
                 len(bad), st.mean([r["trend"] for r in bad]) if bad else 0))
        if len(migs) >= 6:
            corr = st.correlation(migs, [r["trend"] for r in rows
                                         if r["mig"] is not None]) \
                if hasattr(st, "correlation") else None
            if corr is None:
                import numpy as _np
                corr = _np.corrcoef(migs, [r["trend"] for r in rows
                                           if r["mig"] is not None])[0, 1]
            print("       迁移质量 与 后续趋势幅度 相关系数 %+.2f" % corr)


if __name__ == "__main__":
    import mine_v5 as _v5
    _d = _v5.M.load()
    ADJ_HITS = {}
    for h in _v5.collect(_d):
        ADJ_HITS.setdefault(h["sym"], []).append(
            {"R": h["trade_R"], "pts": h["trade_pts"], "date": h["date"]})
    main()
