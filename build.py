# -*- coding: utf-8 -*-
"""
期货量仓价分析终端 —— 数据管道 + 指标引擎 + 单文件 HTML 构建

数据源：新浪财经期货接口（免费、无需 Key）
  - 主力连续日线：InnerFuturesNewService.getDailyKLine?symbol=RB0
  - 分钟线      ：InnerFuturesNewService.getFewMinLine?symbol=RB0&type=1/5/15/60
  - 单合约日线  ：同上，symbol=RB2601（用于 Panama 复权识别主力切换）

复权：Panama 加法复权。切换日以新旧主力合约收盘价做差，将切换日之前的历史
      整体平移，消除换月假跳空。成交量/持仓量保持原始值，不做任何调整。
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache")
os.makedirs(CACHE, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- 基础工具

def http_get(url, referer="https://finance.sina.com.cn", timeout=25, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": referer,
                "Accept": "*/*", "Connection": "close",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:          # noqa: BLE001
            last = e
            if i < retries:
                time.sleep(0.5 + i * 0.5)
    raise last


def parse_jsonp(raw):
    txt = raw.decode("utf-8", "ignore")
    i = txt.find("([")
    if i != -1:
        j = txt.find("])", i)
        if j != -1:
            return json.loads(txt[i + 1:j + 1])
    i = txt.find("[")
    if i == -1:
        return []
    j = txt.rfind("]")
    if j == -1:
        return []
    return json.loads(txt[i:j + 1])


def cache_file(key):
    return os.path.join(CACHE, re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".json")


def cache_load(key, ttl_hours):
    p = cache_file(key)
    if not os.path.exists(p):
        return None
    try:
        obj = json.load(open(p, "r", encoding="utf-8"))
        if time.time() - obj.get("ts", 0) < ttl_hours * 3600:
            return obj.get("data")
    except Exception:                   # noqa: BLE001
        return None
    return None


def cache_save(key, data):
    try:
        with open(cache_file(key), "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "data": data}, f, ensure_ascii=False)
    except Exception:                   # noqa: BLE001
        pass


# ---------------------------------------------------------------- 行情拉取

def _num(x, default=0.0):
    try:
        if x in (None, "", "-"):
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def fetch_daily(sym):
    """日线（含 OHLC / 成交量 / 持仓量 / 结算价）。sym 形如 RB0 或 RB2601。"""
    key = "daily_" + sym
    hit = cache_load(key, 4)
    if hit is not None:
        return hit
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_"
           + sym + "=/InnerFuturesNewService.getDailyKLine?symbol=" + sym)
    rows = parse_jsonp(http_get(url))
    out = []
    for r in rows:
        d = r.get("d")
        if not d:
            continue
        out.append({
            "d": d[:10],
            "o": _num(r.get("o")), "h": _num(r.get("h")),
            "l": _num(r.get("l")), "c": _num(r.get("c")),
            "v": _num(r.get("v")), "oi": _num(r.get("p")),
        })
    out.sort(key=lambda x: x["d"])
    if out:
        cache_save(key, out)
    return out


def fetch_minute(sym, mtype):
    """分钟线。mtype: 1 / 5 / 15 / 60。"""
    key = "min%d_%s" % (mtype, sym)
    hit = cache_load(key, 1)
    if hit is not None:
        return hit
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_"
           + sym + "_" + str(mtype)
           + "=/InnerFuturesNewService.getFewMinLine?symbol=" + sym
           + "&type=" + str(mtype))
    rows = parse_jsonp(http_get(url))
    out = []
    for r in rows:
        d = r.get("d")
        if not d:
            continue
        out.append({
            "d": d, "o": _num(r.get("o")), "h": _num(r.get("h")),
            "l": _num(r.get("l")), "c": _num(r.get("c")),
            "v": _num(r.get("v")), "oi": _num(r.get("p")),
        })
    out.sort(key=lambda x: x["d"])
    if out:
        cache_save(key, out)
    return out


# ---------------------------------------------------------------- 主力连续复权

# 各品种的挂牌月份（用于只探测可能成为主力的合约，避免无意义请求）
MONTHS = {
    "RB": [1, 5, 10], "HC": [1, 5, 10], "WR": [1, 5, 10],
    "RU": [1, 5, 9], "BU": [1, 5, 9], "SP": [1, 5, 9], "FU": [1, 5, 9],
    "M": [1, 5, 9], "Y": [1, 5, 9], "P": [1, 5, 9], "A": [1, 5, 9],
    "B": [1, 5, 9], "C": [1, 5, 9], "CS": [1, 5, 9], "JD": [1, 5, 9],
    "L": [1, 5, 9], "V": [1, 5, 9], "PP": [1, 5, 9], "EG": [1, 5, 9],
    "J": [1, 5, 9], "JM": [1, 5, 9], "TA": [1, 5, 9], "MA": [1, 5, 9],
    "SR": [1, 5, 9], "CF": [1, 5, 9], "FG": [1, 5, 9], "SA": [1, 5, 9],
    "UR": [1, 5, 9], "PF": [1, 5, 9], "SM": [1, 5, 9], "SF": [1, 5, 9],
    "AP": [1, 5, 10], "AU": [6, 12], "AG": [6, 12],
}


def _month_window(back, ahead):
    now = datetime.now()
    y, m = now.year, now.month
    out = []
    for k in range(-back, ahead + 1):
        mm = m + k
        yy = y + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        out.append((yy, mm))
    return out


def panama_adjust(variety):
    """
    返回 (复权后的日线, 复权元信息)。
    流程：枚举候选合约 → 按每日最大持仓量确定主力 → 识别切换日 →
          用新旧合约切换日收盘价差做加法复权（历史整体平移）。
    """
    base = fetch_daily(variety + "0")
    if not base:
        return [], {"method": "none", "reason": "主力连续日线为空"}

    months = MONTHS.get(variety, list(range(1, 13)))
    cands = []
    for (yy, mm) in _month_window(16, 10):
        if mm not in months:
            continue
        cands.append("%s%02d%02d" % (variety, yy % 100, mm))

    series = {}
    for code in cands:
        try:
            rows = fetch_daily(code)
        except Exception:               # noqa: BLE001
            rows = []
        if len(rows) > 20:
            series[code] = {r["d"]: r for r in rows}

    if len(series) < 2:
        return base, {"method": "none", "reason": "合约数据不足，未做换月复权"}

    # 每个交易日取持仓量最大的合约作为主力
    all_dates = sorted({d for mp in series.values() for d in mp})
    main_of = {}
    for d in all_dates:
        best, best_oi = None, -1.0
        for code, mp in series.items():
            bar = mp.get(d)
            if bar and bar["oi"] > best_oi:
                best, best_oi = code, bar["oi"]
        if best:
            main_of[d] = best

    # 识别切换日 + 价差
    switches, prev = [], None
    for d in all_dates:
        cur = main_of.get(d)
        if cur and prev and cur != prev:
            new_bar = series[cur].get(d)
            old_bar = series[prev].get(d)
            if new_bar and old_bar:
                switches.append((d, new_bar["c"] - old_bar["c"], prev, cur))
        if cur:
            prev = cur

    # 加法复权：切换日之前的所有历史整体平移累计价差
    adj = []
    for bar in base:
        off = 0.0
        for (sd, gap, _o, _n) in switches:
            if bar["d"] < sd:
                off += gap
        nb = dict(bar)
        if off:
            nb["o"] = round(bar["o"] + off, 3)
            nb["h"] = round(bar["h"] + off, 3)
            nb["l"] = round(bar["l"] + off, 3)
            nb["c"] = round(bar["c"] + off, 3)
        # 成交量 / 持仓量：保持原始值，不参与复权
        adj.append(nb)

    meta = {
        "method": "panama",
        "switches": [
            {"date": d, "gap": round(g, 3), "from": o, "to": n}
            for (d, g, o, n) in switches
        ][-12:],
        "covered_from": all_dates[0] if all_dates else None,
        "contracts": len(series),
    }
    return adj, meta
