# -*- coding: utf-8 -*-
"""预测式趋势信号：无前视 walk-forward 验证 v2
====================================
方法论（用户定义）：
  大趋势 = 收盘价在 MA34 上方（多头段）/下方（空头段）；穿越点 = 反转点
  在趋势段内监测「反转点前 10 根」的共性指纹（trend_mine 挖出）：
    R1 回落/反弹距段极值 ≥1.5 ATR
    R2 5日动量反向
    R3 连续 ≥2 根反向
    R4 跌破/站上 MA10
    R5 持仓 5 日降 ≥2%（多空反转前都常见：多杀多/空头回补）
    R6 近 5 日放量 ≥1.4×
  分层：狙击=R1+R4+(R5或R6)  重点=R1+R4  观察=R1+R2+R3+R4
  信号在段内首次命中时发射 → 预测未来 20 根内出现 MA34 穿越反转。
交易模拟（顺信号方向）：
  entry 模式：sig=信号次日开盘 / conf=等收盘穿越MA34后次日开盘（预测的事件）
  exit  模式：X=MA34反向平仓 / XS=MA34反向平仓+3ATR止损 / T=吊灯移动止损3ATR
无前视：train(~2023)定参，test(2024+)盲测。
"""
import sys

import build  # noqa: F401  (cache预热)
import cyc
from trend_mine import CODES, load, atr_series, segments, feats

MA = 34
MAXHOLD = 90
SPLIT = "2024-01-01"
MODE = "conf"    # sig / conf / bench
EXIT = "T"       # X / XS / T
TRAIL = 3.0


def context(bars):
    closes = [b["c"] for b in bars]
    return {"ma34": cyc.sma(closes, MA), "ma10": cyc.sma(closes, 10),
            "atr": atr_series(bars),
            "vma20": cyc.sma([float(b["v"]) for b in bars], 20)}


def conds_top(f):
    return {
        "R1": f["dist_hi"] <= -1.5,
        "R2": f["mom5"] < 0,
        "R3": f["dn"] >= 2,
        "R4": f["d_ma10"] is not None and f["d_ma10"] < 0,
        "R5": f["doi5"] is not None and f["doi5"] <= -2,
        "R6": (f["vr5"] or 0) >= 1.4,
    }


def conds_bot(f):
    return {
        "R1": f["dist_lo"] >= 1.5,
        "R2": f["mom5"] > 0,
        "R3": f["up"] >= 2,
        "R4": f["d_ma10"] is not None and f["d_ma10"] > 0,
        "R5": f["doi5"] is not None and f["doi5"] <= -2,
        "R6": (f["vr5"] or 0) >= 1.4,
    }


def tier(cs, kind):
    n14 = cs["R1"] and cs["R4"]
    if n14 and (cs["R5"] or cs["R6"]):
        return "狙击"
    if n14:
        return "重点"
    if cs["R1"] and cs["R2"] and cs["R3"] and cs["R4"]:
        return "观察"
    return None


def seg_qual(bars, ctx, s, e):
    if e - s + 1 < 15:
        return False
    atr_ref = ctx["atr"][s] or 1e-9
    amp = (max(b["h"] for b in bars[s:e + 1]) - min(b["l"] for b in bars[s:e + 1])) / atr_ref
    return amp >= 3.0


def scan(bars, ctx, dmin=None):
    segs = segments(bars, ctx["ma34"])
    out = []
    if MODE in ("bench", "bench_all"):
        # 基准 = 纯趋势跟踪：新段起点顺方向进场（下穿做空/上穿做多）
        for si in range(1, len(segs)):
            s1, e1, side1 = segs[si]
            if MODE == "bench" and not seg_qual(bars, ctx, s1, e1):
                continue
            i, t, side_pos = s1, "基准", side1
            nxt = segs[si + 1][0] if si + 1 < len(segs) else None
            out.append(_trade(bars, ctx, i, side_pos, t, nxt, None, dmin))
        return [s for s in out if s]

    for si in range(1, len(segs)):
        s1, e1, side1 = segs[si]
        if not seg_qual(bars, ctx, s1, e1):
            continue
        fired = None
        for i in range(max(60, s1), e1 + 1):
            f = feats(bars, i, ctx)
            if f["age"] < 5:
                continue
            cs = conds_top(f) if side1 == 1 else conds_bot(f)
            t = tier(cs, "top" if side1 == 1 else "bot")
            if t:
                fired = (i, f, t)
                break
        if not fired:
            continue
        i, f, t = fired
        side_pos = -1 if side1 == 1 else 1
        nxt = segs[si + 1][0] if si + 1 < len(segs) else None
        sig = _trade(bars, ctx, i, side_pos, t, nxt, f, dmin)
        if sig:
            out.append(sig)
    return out


def _trade(bars, ctx, i, side_pos, t, nxt, f, dmin):
    if dmin and bars[i]["d"] < dmin:
        return None
    lead = (nxt - i) if nxt is not None else None
    hit = lead is not None and 0 <= lead <= 20
    ei = i + 1
    if ei >= len(bars):
        return None
    atr = ctx["atr"][i] or 1e-9
    entry_px, entry_i = None, None
    if MODE == "sig":
        entry_px, entry_i = bars[ei]["o"], ei
    else:  # conf：等收盘穿越（预测的事件）再上车
        for k in range(ei, min(ei + 20, len(bars))):
            c_ = bars[k]["c"]
            if (side_pos == -1 and c_ < ctx["ma34"][k]) or \
               (side_pos == 1 and c_ > ctx["ma34"][k]):
                entry_px, entry_i = (bars[k + 1]["o"] if k + 1 < len(bars) else c_), k + 1
                break
        if entry_px is None:
            return None
    entry = entry_px
    stop = entry - TRAIL * atr if side_pos == 1 else entry + TRAIL * atr
    trail_hi, trail_lo = entry, entry
    exit_px, xi = None, None
    confirmed = MODE == "conf"   # conf 进场即已确认
    k_end = min(ei + MAXHOLD + 40, len(bars))
    for k in range(entry_i, k_end):
        b = bars[k]
        if side_pos == 1:
            trail_hi = max(trail_hi, b["h"])
            trail_stop = trail_hi - TRAIL * atr
        else:
            trail_lo = min(trail_lo, b["l"])
            trail_stop = trail_lo + TRAIL * atr
        # 平仓判定
        if EXIT in ("X", "XS"):
            if confirmed and EXIT == "XS":
                if (side_pos == 1 and b["l"] <= stop) or (side_pos == -1 and b["h"] >= stop):
                    exit_px, xi = stop, k
                    break
            if not confirmed:
                if (side_pos == -1 and b["c"] < ctx["ma34"][k]) or \
                   (side_pos == 1 and b["c"] > ctx["ma34"][k]):
                    confirmed = True
                    continue
            elif (side_pos == -1 and b["c"] > ctx["ma34"][k]) or \
                 (side_pos == 1 and b["c"] < ctx["ma34"][k]):
                exit_px, xi = b["c"], k
                break
        else:  # T 吊灯移动止损
            if (side_pos == 1 and b["c"] < trail_stop) or \
               (side_pos == -1 and b["c"] > trail_stop):
                exit_px, xi = b["c"], k
                break
        if k - entry_i >= MAXHOLD:
            exit_px, xi = b["c"], k
            break
    if exit_px is None:
        k = k_end - 1
        exit_px, xi = bars[k]["c"], k
    pnl = (exit_px - entry) * side_pos / atr
    return {"sym": None, "date": bars[i]["d"], "side": side_pos, "tier": t,
            "lead": lead, "hit": hit, "entry": entry, "exit": exit_px,
            "pnl_atr": pnl, "held": xi - entry_i + 1}


def run_mode(m, ex):
    global MODE, EXIT
    MODE, EXIT = m, ex
    res = {"train": [], "test": []}
    for code in CODES:
        bars = load(code)
        ctx = context(bars)
        for k in ("train", "test"):
            dmin = None if k == "train" else SPLIT
            dmax = SPLIT if k == "train" else None
            sigs = scan(bars, ctx, dmin=dmin)
            if dmax:
                sigs = [s for s in sigs if s["date"] < dmax]
            for s in sigs:
                s["sym"] = code
            res[k].extend(sigs)
    return res


def show(tag, sigs):
    if not sigs:
        return
    n = len(sigs)
    win = sum(1 for s in sigs if s["pnl_atr"] > 0) / n * 100
    avg = sum(s["pnl_atr"] for s in sigs) / n
    wins = [s["pnl_atr"] for s in sigs if s["pnl_atr"] > 0]
    loss = [s["pnl_atr"] for s in sigs if s["pnl_atr"] <= 0]
    aw = sum(wins) / len(wins) if wins else 0
    al = sum(loss) / len(loss) if loss else 0
    lead = [s["lead"] for s in sigs if s["lead"] is not None]
    al_lead = sum(lead) / len(lead) if lead else 0
    hit = sum(s["hit"] for s in sigs) / n * 100
    print("  %-6s %4d个 | 预测命中 %5.1f%% | 提前%.1f根 | 胜率 %5.1f%% | 均盈 %+.2f | 均盈笔 %+.2f / 均亏笔 %+.2f | 持有%.0f根"
          % (tag, n, hit, al_lead, win, avg, aw, al, sum(s["held"] for s in sigs) / n))


def main():
    for m in (sys.argv[1:] or ["sig", "conf", "bench"]):
        for ex in (["X", "XS", "T"] if m != "bench" else ["X", "T"]):
            res = run_mode(m, ex)
            print("\n=== 入场=%s 平仓=%s ===" % (m, ex))
            for k in ("train", "test"):
                sigs = res[k]
                if not sigs:
                    continue
                print(" %s" % ("训练 ~2023" if k == "train" else "盲测 2024+"))
                for tv in ("狙击", "重点", "观察", "基准"):
                    show(tv, [s for s in sigs if s["tier"] == tv])


if __name__ == "__main__":
    main()
