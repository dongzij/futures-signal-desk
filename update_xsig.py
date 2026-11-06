# -*- coding: utf-8 -*-
"""用 dist/data/*.json 里已有的主力连续 K 线，按最新参数重算「独立出场雷达」，
不联网、不删除文件（用于本地快速重算 / 紧急修补）。"""
import json
import os

import engine

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "data")
SYMS = ["FG", "M", "RB", "MA", "UR"]


def to_bars(arr):
    return [{"d": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4],
             "v": r[5] if len(r) > 5 else 0, "oi": r[6] if len(r) > 6 else 0} for r in arr]


def main():
    live = []
    idx_p = os.path.join(OUT, "index.json")
    idx = json.load(open(idx_p, encoding="utf-8")) if os.path.exists(idx_p) else {}
    for sym in SYMS:
        p = os.path.join(OUT, "%s.json" % sym)
        doc = json.load(open(p, encoding="utf-8"))
        bars = to_bars(doc["main"])
        now = engine.exit_radar(bars=bars)
        doc["xsig"] = {
            "cfg": {"ext": engine.XS_EXT, "pull": engine.XS_PULL, "amp": engine.XS_AMP,
                    "age": engine.XS_AGE, "shadow": engine.XS_SHADOW,
                    "adx": engine.XS_ADX_DROP, "tier": list(engine.XS_TIER)},
            "now": now,
            "hist": engine.exit_history(bars=bars, maxn=16),
            "stat": engine.xs_stat(bars=bars),
        }
        json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False,
                  separators=(",", ":"))
        live.append(dict(now or {}, sym=sym))
        print("%-3s now=%s  gate=%s aux=%s  hist=%d  stat=%s" %
              (sym, (now or {}).get("level"), (now or {}).get("gate"),
               (now or {}).get("aux"), len(doc["xsig"]["hist"]),
               doc["xsig"]["stat"]))

    if idx:
        idx["xsig"] = {"cfg": {"ext": engine.XS_EXT, "pull": engine.XS_PULL,
                               "amp": engine.XS_AMP, "age": engine.XS_AGE,
                               "shadow": engine.XS_SHADOW, "adx": engine.XS_ADX_DROP,
                               "tier": list(engine.XS_TIER)},
                       "live": live}
        json.dump(idx, open(idx_p, "w", encoding="utf-8"), ensure_ascii=False,
                  separators=(",", ":"))
        print("index.json 已更新")
    else:
        print("!! index.json 不存在，需先跑 data_build.py")


if __name__ == "__main__":
    main()
