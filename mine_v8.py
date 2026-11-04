# -*- coding: utf-8 -*-
"""v8：趋势段定义重构（时间OR幅度）+ 反转前 7~20 天指纹 + 趋势跟踪对照
结果导向：所有选择都用 胜率 / 累计R / 回撤 / 年交易次数 决定，并做时间分段验证。
"""
import sys
import statistics as st
import build
import mine_v4b as M

UNIV = M.UNIV
NAME = M.NAME


def bars_of(c, years=10):
    a, _ = build.panama_adjust(c)
    keep = int(years * 244)
    return a[-keep:] if len(a) > keep else a


def zigzag(bars, S, k=2.0):
    """因果的摆动划分：从极值回撤 k×ATR 即确认一个转折点。"""
    n = len(bars)
    if n < 60:
        return []
    piv = []
    dirn = 0
    hi = lo = bars[0]["c"]
    hi_i = lo_i = 0

    def thr(i):
        a = S["atr"][i]
        return k * a if a else (bars[i]["c"] * 0.02)

    for i in range(1, n):
        c = bars[i]["c"]
        if c > hi:
            hi, hi_i = c, i
        if c < lo:
            lo, lo_i = c, i
        if dirn >= 0 and hi_i < i and c <= hi - thr(hi_i):
            piv.append((hi_i, hi))
            dirn = -1
            lo, lo_i = c, i
        elif dirn <= 0 and lo_i < i and c >= lo + thr(lo_i):
            piv.append((lo_i, lo))
            dirn = 1
            hi, hi_i = c, i
    piv.append((n - 1, bars[-1]["c"]))
    # 去重、去乱序
    out = []
    for p in piv:
        if not out or p[0] > out[-1][0]:
            out.append(p)
    return out


def merge_piv(piv, bars, D, A):
    """把不满足「持续≥D 根 或 幅度≥A 点」的腿合并掉（双条件满足其一即视为一段趋势）。"""
    piv = list(piv)
    guard = 0
    while len(piv) > 3 and guard < 400:
        guard += 1
        bad = None
        for k in range(1, len(piv) - 1):
            d = piv[k + 1][0] - piv[k][0]
            amp = abs(piv[k + 1][1] - piv[k][1])
            if d >= D or amp >= A:
                continue
            bad = k
            break
        if bad is None:
            break
        piv.pop(bad)
    # 合并后可能出现"同向连续拐点"（假转折），一律消掉
    changed = True
    while changed and len(piv) > 2:
        changed = False
        for k in range(1, len(piv) - 1):
            up1 = piv[k][1] > piv[k - 1][1]
            up2 = piv[k + 1][1] > piv[k][1]
            if up1 == up2:
                piv.pop(k)
                changed = True
                break
    return piv


def make_piv(bars, S, k, D, A):
    return merge_piv(zigzag(bars, S, k), bars, D, A)


def segs_of(piv, bars):
    """piv → (s,e,dir) 段，dir=该段方向（1 上涨 / -1 下跌）"""
    out = []
    for j in range(len(piv) - 1):
        s, e = piv[j][0], piv[j + 1][0]
        if e <= s:
            continue
        d = 1 if piv[j + 1][1] >= piv[j][1] else -1
        out.append((s, e, d))
    return out


def build_P(bars, S, segs):
    n = len(bars)
    side = [None] * n
    seg_of = [None] * n
    lead = [None] * n
    hi = [None] * n
    lo = [None] * n
    for si, (s, e, d) in enumerate(segs):
        h, l = -1e18, 1e18
        for i in range(s, min(e + 1, n)):
            h = max(h, bars[i]["h"])
            l = min(l, bars[i]["l"])
            hi[i], lo[i] = h, l
            side[i] = d
            seg_of[i] = si
            lead[i] = e - i
    return {"bars": bars, "S": S, "segs": segs, "side": side, "lead": lead,
            "hi": hi, "lo": lo, "n": n, "seg_of": seg_of}


# ---------------- A. 段定义扫描 ----------------
def sec_a():
    print("=" * 92)
    print("A. 趋势段定义扫描：ZigZag 阈值 k×ATR 定转折点 + 双条件合并（持续≥D根 或 幅度≥A点）")
    print("=" * 92)
    for c in UNIV:
        bars = bars_of(c)
        S = M.prep(bars, 34)["S"]
        a_now = [x for x in S["atr"][-1:] if x][0]
        print("\n%-3s %-4s 现价 %.0f  ATR %.1f  近10年 %d 根" % (c, NAME[c], bars[-1]["c"], a_now, len(bars)))
        print("  %-10s %-5s %-5s %6s %6s %6s %8s %8s" % ("(D,A)", "k", "段数", "均长", "中位长", "中位幅", "覆盖K线", "≥300点段"))
        for k in (2.0, 3.0):
            for (D, A) in ((20, 200), (20, 300), (40, 200), (40, 300)):
                piv = make_piv(bars, S, k, D, A)
                sg = segs_of(piv, bars)
                if not sg:
                    continue
                durs = [e - s + 1 for s, e, d in sg]
                amps = [abs(piv[j + 1][1] - piv[j][1]) for j in range(len(piv) - 1)]
                cov = sum(durs)
                big = sum(1 for a in amps if a >= 300)
                print("  (%-2d,%-4d) %-5.1f %6d %6.0f %6.0f %6.0f %7d%% %8d" %
                      (D, A, k, len(sg), st.mean(durs), st.median(durs), st.median(amps),
                       cov * 100 // len(bars), big))


def fg_check(k=2.0, D=20, A=200):
    print("\n" + "=" * 92)
    print("A2. 玻璃 2026 年这段空头趋势在新定义下能否被识别（k=%.1f, D≥%d 或 A≥%d）" % (k, D, A))
    print("=" * 92)
    bars = bars_of("FG")
    S = M.prep(bars, 34)["S"]
    piv = make_piv(bars, S, k, D, A)
    sg = segs_of(piv, bars)
    for si, (s, e, d) in enumerate(sg):
        if bars[e]["d"] >= "2025-06-01":
            print("  #%02d %s→%s %s %3d根 %7.1f→%7.1f  %+6.0f点 (%.1f ATR)" %
                  (si, bars[s]["d"], bars[e]["d"], "涨" if d == 1 else "跌", e - s + 1,
                   piv[si][1], piv[si + 1][1], piv[si + 1][1] - piv[si][1],
                   abs(piv[si + 1][1] - piv[si][1]) / (S["atr"][s] or 1)))
    # 找出覆盖 2026-03~2026-10 那段
    for si, (s, e, d) in enumerate(sg):
        if d == -1 and bars[s]["d"] <= "2026-04-01" and bars[e]["d"] >= "2026-08-01":
            print("  → 覆盖该跌势的段：#%d %s→%s  幅度 %+.0f 点" %
                  (si, bars[s]["d"], bars[e]["d"], piv[si + 1][1] - piv[si][1]))
            print("     该段起点前 20 根：%s ~ %s" % (bars[max(0, s - 20)]["d"], bars[s - 1]["d"]))
            break
    else:
        print("  → 未找到覆盖 2026-03→08 的跌段")


if __name__ == "__main__":
    sec_a()
    fg_check()


# ---------------- B. 反转前指纹（新段定义） ----------------
H = 5

CONDS8 = [
    ("B1", "ext",  ">=", 2.0, "偏离 MA34 ≥2ATR（过度延伸）"),
    ("B2", "ext",  ">=", 3.0, "偏离 MA34 ≥3ATR"),
    ("B3", "ext",  ">=", 4.0, "偏离 MA34 ≥4ATR"),
    ("B4", "run",  ">=", 4.0, "自段起点已走 ≥4ATR"),
    ("B5", "run",  ">=", 6.0, "自段起点已走 ≥6ATR"),
    ("B6", "run",  ">=", 8.0, "自段起点已走 ≥8ATR"),
    ("B7", "age",  ">=", 20,  "段已运行 ≥20 根"),
    ("B8", "age",  ">=", 40,  "段已运行 ≥40 根"),
    ("B9", "vr1x", ">=", 1.8, "单日量 ≥1.8×20日均量"),
    ("B10", "vr1x", ">=", 2.5, "单日量 ≥2.5×"),
    ("B11", "doi5", ">=", 8.0, "持仓 5 日增 ≥8%（拥挤）"),
    ("B12", "adx",  ">=", 30,  "ADX ≥30（趋势过热）"),
    ("B13", "adx",  ">=", 35,  "ADX ≥35"),
    ("B14", "rng1", ">=", 2.0, "单日振幅 ≥2ATR"),
    ("B15", "atr_exp", ">=", 1.3, "ATR 放大 ≥30%"),
]


def panel(k=2.0, D=20, A=200, min_age=3):
    """全部 K 线的特征面板；标签 = 距本段结束（转折点）还有几根。"""
    rows = []
    info = {}
    for c in UNIV:
        bars = bars_of(c)
        S = M.prep(bars, 34)["S"]
        piv = make_piv(bars, S, k, D, A)
        sg = segs_of(piv, bars)
        P = build_P(bars, S, sg)
        info[c] = (P, piv, sg)
        for i in range(60, len(bars)):
            if P["side"][i] is None:
                continue
            e = i + P["lead"][i]
            if i - sg[P["seg_of"][i]][0] < min_age or P["lead"][i] < 1:
                continue
            f = M.feats(P, i)
            r = {"sym": c, "date": bars[i]["d"], "i": i, "side": P["side"][i],
                 "lead": P["lead"][i], "seg": P["seg_of"][i],
                 "segLen": e - sg[P["seg_of"][i]][0] + 1, "atr": S["atr"][i]}
            for nm, v in f.items():
                r[nm] = M.adj(nm, v, P["side"][i])
            b = bars[i]
            atr1 = S["atr"][i] or 1e-9
            r["pull"] = ((b["c"] - P["hi"][i]) / atr1 if P["side"][i] == 1
                         else (P["lo"][i] - b["c"]) / atr1)
            s0 = sg[P["seg_of"][i]][0]
            # 延伸度：沿趋势方向偏离 MA34 多少个 ATR（正=过度延伸）
            r["ext"] = (b["c"] - S["ma"][i]) / atr1 * P["side"][i] if S["ma"][i] else None
            # 自段起点累计走了多少 ATR
            r["run"] = (b["c"] - bars[s0]["c"]) / atr1 * P["side"][i]
            r["age"] = i - s0 + 1
            r["vr1x"] = (b["v"] / S["vma20"][i]) if S["vma20"][i] else None
            rows.append(r)
    return rows, info


def lab(r, h=H):
    return 1 if r["lead"] <= h else 0


def bit(rows, fn):
    m = 0
    for k2, r in enumerate(rows):
        if fn(r):
            m |= 1 << k2
    return m


def search8(rows, pool, max_k=4, min_n=50, top=12):
    label = bit(rows, lambda r: lab(r))
    masks = {}
    for c in pool:
        masks[c[0]] = bit(rows, lambda r, c=c: M.ok(r, c))
    base = bin(label).count("1") / len(rows)
    res = []
    n = len(pool)
    if max_k == 4:
        for a in range(n):
            ma = masks[pool[a][0]]
            for b in range(a + 1, n):
                mab = ma & masks[pool[b][0]]
                if bin(mab).count("1") < min_n:
                    continue
                for cc in range(b + 1, n):
                    mabc = mab & masks[pool[cc][0]]
                    if bin(mabc).count("1") < min_n:
                        continue
                    for d in range(cc + 1, n):
                        m = mabc & masks[pool[d][0]]
                        cnt = bin(m).count("1")
                        if cnt < min_n:
                            continue
                        hit = bin(m & label).count("1")
                        res.append((hit / cnt, cnt, (pool[a][0], pool[b][0], pool[cc][0], pool[d][0])))
    res.sort(key=lambda x: -x[0])
    return res[:top], base


def cover_by_offset(rows, cond, maxk=20):
    """条件在各偏移（距转折点还剩几根）上的命中率：用来定窗口。"""
    out = []
    for off in range(1, maxk + 1):
        sub = [r for r in rows if r["lead"] == off]
        if len(sub) < 40:
            out.append((off, len(sub), None))
            continue
        p = sum(1 for r in sub if M.ok(r, cond)) / len(sub)
        out.append((off, len(sub), p))
    return out


ALLC = None


def conds_by_ids(ids):
    global ALLC
    if ALLC is None:
        ALLC = M.CONDS + CONDS8
    return [x for x in ALLC if x[0] in ids]


def sim_rev(P, rows, conds, c, k_zig=2.0, maxhold=250, max_lead=None):
    """命中 → 次日开盘逆势进 → 不设止损 → 新趋势回撤 k×ATR 确认后离场（因果）。"""
    bars, S = P["bars"], P["S"]
    hit = sorted(set(r["i"] for r in rows
                     if all(M.ok(r, x) for x in conds)
                     and (max_lead is None or r["lead"] <= max_lead)))
    used = set()
    out = []
    for i in hit:
        si = P["seg_of"][i]
        if si in used or i + 1 >= len(bars):
            continue
        used.add(si)
        side = -P["side"][i]
        entry = bars[i + 1]["o"]
        atr = S["atr"][i] or entry * 0.01
        thr = k_zig * atr
        ext = entry
        xi = None
        for j in range(i + 1, min(i + 1 + maxhold, len(bars))):
            if side == 1:
                ext = max(ext, bars[j]["h"])
                if bars[j]["c"] <= ext - thr:
                    xi = j
                    break
            else:
                ext = min(ext, bars[j]["l"])
                if bars[j]["c"] >= ext + thr:
                    xi = j
                    break
        if xi is None:
            xi = min(i + maxhold, len(bars) - 1)
        pts = (bars[xi]["c"] - entry) * side
        mae = 0.0
        for j in range(i + 1, xi + 1):
            adv = (bars[j]["l"] - entry) if side == 1 else (entry - bars[j]["h"])
            mae = min(mae, adv)
        out.append({"sym": c, "in": bars[i + 1]["d"], "out": bars[xi]["d"], "side": side,
                    "pts": pts, "R": pts / atr, "bars": xi - i - 1, "mae": mae / atr,
                    "lead": P["lead"][i], "i": i})
    return out


def stat_tr(trs):
    if not trs:
        return None
    n = len(trs)
    w = sum(1 for t in trs if t["pts"] > 0)
    eq = peak = dd = 0
    for t in sorted(trs, key=lambda x: x["in"]):
        eq += t["R"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {"n": n, "win": w / n, "R": sum(t["R"] for t in trs), "dd": dd,
            "avg": st.mean([t["R"] for t in trs]), "hold": st.median([t["bars"] for t in trs]),
            "mae": st.median([t["mae"] for t in trs]), "per_year": n / 10.0}


def sec_b(k=2.0, D=20, A=200):
    print("\n" + "=" * 92)
    print("B. 新段定义（k=%.1f, D≥%d 或 A≥%d）下的反转前指纹" % (k, D, A))
    print("=" * 92)
    rows, info = panel(k, D, A)
    base5 = sum(lab(r) for r in rows) / len(rows)
    print("面板 %d 根；基准 P(5 根内转折) = %.1f%%" % (len(rows), base5 * 100))
    print("\n候选条件（全样本，H=5）：")
    print("  %-4s %-26s %6s %7s %7s %6s %6s" % ("ID", "条件", "n", "命中率", "基准", "lift", "中位提前"))
    cand = []
    for c in M.CONDS + CONDS8:
        m = [r for r in rows if M.ok(r, c)]
        if len(m) < 60:
            continue
        p = sum(lab(r) for r in m) / len(m)
        lead = st.median([r["lead"] for r in m if lab(r)])
        cand.append((p / base5, c, len(m), p, lead))
    cand.sort(key=lambda x: -x[0])
    for lift, c, n, p, lead in cand:
        print("  %-4s %-26s %6d %6.1f%% %6.1f%% %6.2f %6.0f" %
              (c[0], c[4], n, p * 100, base5 * 100, lift, lead))

    print("\n窗口定位：各偏移（距转折点剩余根数）上关键条件的命中率")
    keys = [c for c in M.CONDS if c[0] in ("A1", "A9", "A10", "A15", "A17", "A13")]
    print("  %-4s %-22s %s" % ("ID", "条件", " ".join("%4d" % o for o in range(1, 21))))
    for c in keys:
        cov = cover_by_offset(rows, c)
        print("  %-4s %-22s %s" % (c[0], c[4][:20],
                                   " ".join("%4s" % ("-" if p is None else "%.0f" % (p * 100))
                                            for o, n, p in cov)))

    pool = M.CONDS + CONDS8
    for c in M.CONDS:
        pass
    top, base = search8(rows, pool, 4, 45, 12)
    print("\n四条件组合搜索（全样本，min_n=45）：")
    for p, n, cs in top:
        print("  %-34s n=%4d 命中率 %.1f%%  lift %.2f" % ("+".join(cs), n, p * 100, p / base))
    return rows, info, base5


def sec_b_trade(k=2.0, D=20, A=200, combos=None):
    rows, info, base5 = sec_b(k, D, A)
    print("\n建仓口径回测（每段一次，次日开盘逆势进，不设止损，回撤 %.0fATR 确认离场）：" % 2)
    for cs in combos:
        conds = conds_by_ids(cs)
        trs = []
        for c in UNIV:
            P, piv, sg = info[c]
            r2, _ = M.rows_of(P["bars"], c, 34)  # 只为兼容签名，不用
            # 用本段定义的 rows（重算，保证 seg 对齐）
            rows_c = [r for r in rows if r["sym"] == c]
            trs += sim_rev(P, rows_c, conds, c)
        s = stat_tr(trs)
        pre = sum(1 for t in trs if t["lead"] <= 3) / len(trs) if trs else 0
        print("  %-30s n=%3d 胜率%5.1f%% 累计%+7.1fR 回撤%+6.1fR 均%+.2fR 持%2.0f根 浮亏中位%.1fATR 3根内反转%.0f%%" %
              ("+".join(cs), s["n"], s["win"] * 100, s["R"], s["dd"], s["avg"],
               s["hold"], s["mae"], pre))
    return info


def fwd(rows, info, ids, max_lead=None):
    """入场优势（与出场规则无关）：次日开盘进，持有 h 根的收益（ATR 计）。"""
    conds = conds_by_ids(ids)
    per = {}
    for c in UNIV:
        P = info[c][0]
        bars, S = P["bars"], P["S"]
        seen = set()
        for i in sorted(set(r["i"] for r in rows if r["sym"] == c)):
            r = next(x for x in rows if x["sym"] == c and x["i"] == i)
            if not all(M.ok(r, x) for x in conds):
                continue
            if max_lead is not None and r["lead"] > max_lead:
                continue
            si = r["seg"]
            if si in seen or i + 21 >= len(bars):
                continue
            seen.add(si)
            side = -r["side"]
            e = bars[i + 1]["o"]
            atr = S["atr"][i] or 1e-9
            for h in (3, 5, 10, 20):
                per.setdefault(h, []).append((bars[i + 1 + h]["c"] - e) * side / atr)
    out = {}
    for h, v in per.items():
        if not v:
            continue
        out[h] = {"n": len(v), "avg": st.mean(v),
                  "win": sum(1 for x in v if x > 0) / len(v),
                  "med": st.median(v)}
    return out


def print_fwd(rows, info, combos):
    print("\n入场优势（每段一次，ATR 计，正向=朝预测的反转方向赚钱）：")
    print("  %-30s %5s %7s %6s %7s %7s" % ("组合", "n", "5日均", "5日胜", "10日均", "20日均"))
    for cs in combos:
        o = fwd(rows, info, cs)
        if not o:
            continue
        f5, f10, f20 = o.get(5) or {}, o.get(10) or {}, o.get(20) or {}
        print("  %-30s %5d %+7.2f %5.0f%% %+7.2f %+7.2f" %
              ("+".join(cs), f5.get("n", 0), f5.get("avg", 0), f5.get("win", 0) * 100,
               f10.get("avg", 0), f20.get("avg", 0)))


# ---------------- C. 趋势跟踪（因果） ----------------
def tf_events(bars, S, k, adx_min=0, ma_flt=False, ext_max=None):
    """ZigZag 确认即反手：返回 [(confirm_i, new_dir, pivot_i, pivot_px)]，完全因果。"""
    n = len(bars)
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


def tf_trades(code, bars, S, k, adx_min=0, ma_flt=False, no_stop=True):
    ev = tf_events(bars, S, k)
    out = []
    for j in range(len(ev) - 1):
        ci, nd, pi, pp = ev[j]
        xi = ev[j + 1][0]
        if ci + 1 >= len(bars) or xi <= ci + 1:
            continue
        if adx_min and (S["adx"][ci] is None or S["adx"][ci] < adx_min):
            continue
        if ma_flt and S["ma"][ci] is not None:
            if (nd == 1 and bars[ci]["c"] < S["ma"][ci]) or (nd == -1 and bars[ci]["c"] > S["ma"][ci]):
                continue
        entry = bars[ci + 1]["o"]
        exitp = bars[xi]["c"]
        atr = S["atr"][ci] or entry * 0.01
        pts = (exitp - entry) * nd
        cost = M.FEE[code] / M.MULT[code] + M.TICK[code]
        mae = 0.0
        for t in range(ci + 1, xi + 1):
            adv = (bars[t]["l"] - entry) if nd == 1 else (entry - bars[t]["h"])
            mae = min(mae, adv)
        out.append({"sym": code, "in": bars[ci + 1]["d"], "out": bars[xi]["d"], "side": nd,
                    "pts": pts - cost, "R": (pts - cost) / atr, "bars": xi - ci - 1,
                    "mae": mae / atr, "atr": atr, "yuan": (pts - cost) * M.MULT[code]})
    return out


def sec_c():
    print("\n" + "=" * 96)
    print("C. 趋势跟踪（ZigZag 确认反手，无止损，持有到反向确认）参数寻优")
    print("=" * 96)
    cache = {}
    for c in UNIV:
        b = bars_of(c)
        cache[c] = (b, M.prep(b, 34)["S"])
    print("  %-14s %5s %6s %7s %7s %8s %7s  %s" % ("参数", "n", "年次", "胜率", "累计R", "回撤R", "中位持", "分品种R"))
    rows_out = []
    for k in (2.0, 2.5, 3.0, 4.0, 5.0):
        for adx_min in (0, 20, 25):
            for ma_flt in (False, True):
                trs = []
                per = {}
                for c in UNIV:
                    b, S = cache[c]
                    t = tf_trades(c, b, S, k, adx_min, ma_flt)
                    per[c] = sum(x["R"] for x in t)
                    trs += t
                s = stat_tr(trs)
                tag = "k=%.1f ADX%s%s" % (k, adx_min or "-", " MA" if ma_flt else "")
                print("  %-14s %5d %6.1f %6.1f%% %+7.1f %+7.1f %7.0f  %s" %
                      (tag, s["n"], s["per_year"], s["win"] * 100, s["R"], s["dd"], s["hold"],
                       " ".join("%s%+5.0f" % (c, per[c]) for c in UNIV)))
                rows_out.append((s["R"], s["win"], s["n"], tag, per, s["dd"]))
    return rows_out


def sec_c_train():
    print("\n" + "=" * 96)
    print("C2. 趋势跟踪 训练/盲测分段（2016-2023 训练 / 2024-2026 盲测）")
    print("=" * 96)
    cache = {}
    for c in UNIV:
        b = bars_of(c)
        cache[c] = (b, M.prep(b, 34)["S"])
    print("  %-14s %6s %7s %7s | %6s %7s %7s" % ("参数", "训练n", "训练R", "训练胜", "盲测n", "盲测R", "盲测胜"))
    good = []
    for k in (2.0, 2.5, 3.0, 4.0, 5.0):
        for adx_min in (0, 20):
            for ma_flt in (False, True):
                tr, te = [], []
                for c in UNIV:
                    b, S = cache[c]
                    for t in tf_trades(c, b, S, k, adx_min, ma_flt):
                        (tr if t["in"] < "2024-01-01" else te).append(t)
                if not tr or not te:
                    continue
                st1, st2 = stat_tr(tr), stat_tr(te)
                tag = "k=%.1f ADX%s%s" % (k, adx_min or "-", " MA" if ma_flt else "")
                ok = st1["R"] > 0 and st2["R"] > 0
                print("  %-14s %6d %+7.1f %6.1f%% | %6d %+7.1f %6.1f%%  %s" %
                      (tag, st1["n"], st1["R"], st1["win"] * 100,
                       st2["n"], st2["R"], st2["win"] * 100, "✔" if ok else ""))
                if ok:
                    good.append((st1["R"] + st2["R"], tag))
    good.sort(reverse=True)
    print("\n  两段都为正的参数（按合计R排序）：")
    for r, tag in good[:6]:
        print("    %-16s 合计 %+.1fR" % (tag, r))
    return good
