# -*- coding: utf-8 -*-
"""v16 模块三：二段 M/W 结构（DIF 背离 + 量仓强）事件生成 · 自包含版

与回测表 `reports/MW第二峰_特征叠加数据表.md` 完全同源（口径逐行复刻，只依赖 engine.py，
避免在 CI 里引入 mw_study / mw_features / a2_profit 这三个研究脚本）：
  · 事件源 = engine.exit_radar（suggest/strong），同波间隔 ≥3 根
  · 簇 = 同方向、间隔 ≤60 根、第二峰重回首峰极值 ±1ATR 或创新极值
  · 只取簇内【第二个峰】，且当天可得特征满足：
        DIF 线背离（价格更极端、DIF 反而走弱）
      + 量仓强特征（相对首峰持仓降 ≥10% 或 当日量 ≤0.8×20 日均量）
  即 dif_qty == True（17 笔，64.7% / +1.79R，训练 64.3% / 盲测 66.7%）。

与回测唯一差别：事件扫描包含【最后一根 K 线】，这样当天新形成的第二峰
不会被漏掉、可当天独立微信推送；历史部分逐根完全一致。

全部特征只用第二峰当天及之前的数据，无前视；只做标记与提示，
不改 MA34 进场、MA26 + 吊灯 3ATR 出场，也不新增任何平仓动作。
"""
import engine

SIG_LABEL = "二段M/W结构"


# ---------------------------------------------------------------- 事件与簇
def radar_events(bars, S, min_gap=3):
    """同 mw_study.radar_events，但把最后一根纳入（线上当天可用）。"""
    ev, last = [], -99
    for i in range(40, len(bars)):
        r = engine.exit_radar(bars, S, i)
        if not r or r["level"] not in ("suggest", "strong"):
            continue
        if i - last < min_gap:
            continue
        last = i
        ev.append({"i": i, "d": r["date"], "dir": r["dir"], "sd": r["segSide"],
                   "level": r["level"], "extPx": r["extPx"], "k": r["k"],
                   "atr": S["atr"][i] or 1e-9, "age": r["age"]})
    return ev


def build_clusters(ev, bars):
    """同 mw_study.build_clusters：同方向、间隔≤60根、重回极值 ±1ATR。"""
    out = []
    used = [False] * len(ev)
    for a in range(len(ev)):
        if used[a]:
            continue
        head = ev[a]
        group = [head]
        used[a] = True
        for b in range(a + 1, len(ev)):
            if used[b]:
                continue
            p = group[-1]
            if ev[b]["dir"] != head["dir"] or ev[b]["i"] - p["i"] > 60:
                continue
            if head["sd"] == 1:
                back = ev[b]["extPx"] >= head["extPx"] - 1.0 * head["atr"]
            else:
                back = ev[b]["extPx"] <= head["extPx"] + 1.0 * head["atr"]
            if not back:
                continue
            lo, hi = group[-1]["i"], ev[b]["i"]
            if head["sd"] == 1:
                mid = min(bars[q]["l"] for q in range(lo, hi + 1))
                pull = (head["extPx"] - mid) / head["atr"]
            else:
                mid = max(bars[q]["h"] for q in range(lo, hi + 1))
                pull = (mid - head["extPx"]) / head["atr"]
            ev[b]["pull_between"] = pull
            ev[b]["ext_delta"] = (ev[b]["extPx"] - head["extPx"]) / head["atr"] * head["sd"]
            group.append(ev[b])
            used[b] = True
            if len(group) >= 2:
                break
        out.append(group)
    return out


# ---------------------------------------------------------------- 当天特征
def dif_series(closes):
    e12, e26 = engine.ema(closes, 12), engine.ema(closes, 26)
    return [a - b for a, b in zip(e12, e26)]


def features(bars, S, first, second):
    """只复刻回测里入选的两类特征：DIF 线背离 + 量仓强。"""
    i1, i2 = first["i"], second["i"]
    sd = first["sd"]                      # +1 顶部段 / -1 底部段
    closes = [b["c"] for b in bars]
    dif = dif_series(closes)
    ext_delta = second.get("ext_delta") or 0.0
    near_ext = ext_delta >= -0.5
    dd = (dif[i2] or 0) - (dif[i1] or 0)
    div_dif = bool(near_ext and sd * dd < 0)

    oi1 = bars[i1].get("oi") or 0
    oi2 = bars[i2].get("oi") or 0
    oi5 = bars[i2 - 5].get("oi") or 0 if i2 >= 5 else 0
    oi_chg = ((oi2 - oi1) / oi1 * 100) if oi1 else 0.0
    oi5_chg = ((oi2 - oi5) / oi5 * 100) if oi5 else 0.0
    oi_drop10 = bool(oi_chg <= -10)
    v2 = bars[i2].get("v") or 0
    v20 = [bars[k].get("v") or 0 for k in range(max(0, i2 - 20), i2)]
    vma = (sum(v20) / len(v20)) if v20 else 0
    vol_ratio = (v2 / vma) if vma else 1.0
    vol_dry = bool(vol_ratio <= 0.8)
    qty_strong = bool(oi_drop10 or vol_dry)
    return {"div_dif": div_dif, "dif_qty": bool(div_dif and qty_strong),
            "oi_chg": oi_chg, "oi5_chg": oi5_chg,
            "vol_ratio": vol_ratio, "oi_drop10": oi_drop10,
            "vol_dry": vol_dry, "qty_strong": qty_strong,
            "qty_both": bool(oi_drop10 and vol_dry),
            "ext_delta": ext_delta,
            "pull_between": second.get("pull_between") or 0}


# ---------------------------------------------------------------- 对外接口
def build_mw(code, name, bars, S):
    """返回该品种全部 ★ 事件（按日期升序）。"""
    clusters = build_clusters(radar_events(bars, S), bars)
    out = []
    for g in clusters:
        if len(g) < 2:
            continue
        first, second = g[0], g[1]
        ft = features(bars, S, first, second)
        if not ft["dif_qty"]:
            continue
        i2 = second["i"]
        b = bars[i2]
        out.append({
            "kind": "mw",
            "date": second["d"],
            "sym": code, "name": name,
            "sigLabel": SIG_LABEL,
            "dir": second["dir"],                      # 顶部 / 底部
            "side": "short" if second["dir"] == "顶部" else "long",
            "level": second["level"],
            "firstDate": first["d"],
            "gap": i2 - first["i"],
            "price": round(b["c"], 1),
            "atr": round(float(second["atr"] or 0), 2),
            "extDelta": round(float(ft["ext_delta"]), 2),
            "pullBetween": round(float(ft["pull_between"]), 2),
            "oiChg": round(float(ft["oi_chg"]), 1),
            "oi5Chg": round(float(ft["oi5_chg"]), 1),
            "volRatio": round(float(ft["vol_ratio"]), 2),
            "divDif": ft["div_dif"],
            "oiDrop10": ft["oi_drop10"],
            "volDry": ft["vol_dry"],
            "qtyStrong": ft["qty_strong"],
            "qtyBoth": ft["qty_both"],
        })
    out.sort(key=lambda x: x["date"])
    return out


def summarize(events):
    by_dir = {}
    for e in events:
        by_dir[e["dir"]] = by_dir.get(e["dir"], 0) + 1
    return {"n": len(events),
            "last": max((e["date"] for e in events), default=None),
            "byDir": by_dir}
