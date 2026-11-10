# -*- coding: utf-8 -*-
"""v27 数据落地：以线上 data/*.json 为基线，只增量应用【顶不做多·底不做空】。

严守纪律（v26 夜盘隔离 + v26.2 数据基线）：
  · 不联网拉行情、不重算 main / m15 / m60 / contracts / market / weekly / exit / xsig
  · built 保持线上原值（正式 K 线只由 15:20 / 15:50 两班生成）
  · 只改：signals / mw / defend / defendSrc / sigW / sigM / plan，以及 index.extGate
  · data/ 与 dist/data/ 同步写入，两边逐字节一致
"""
import bisect
import json
import os
import shutil
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import engine
import extreme_gate
import data_build
import sig_pack as SP

ROOT = os.path.dirname(os.path.abspath(__file__))
SYMS = ["MA", "HC", "Y", "SM", "FG", "P", "M"]
BACKUP = os.path.join(ROOT, "_v27_baseline")


def dump(path, d):
    json.dump(d, open(path, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))


def marks_stat(d):
    ms = (d.get("plan") or {}).get("marks") or []
    ins = [m for m in ms if m.get("t") == "in"]
    return len(ms), len(ins), sum(1 for m in ins if m.get("side") == "long"), \
        sum(1 for m in ins if m.get("side") == "short")


def clean_product(d):
    """返回 (清理条数, 明细)，直接就地修改 d。"""
    bars = SP.bars_of(d)
    S = engine.make_S(bars)
    di = extreme_gate.date_index(bars)
    all_d = [b["d"] for b in bars]

    def i_of(dt):
        if not dt:
            return None
        j = di.get(dt)
        if j is None:
            j = bisect.bisect_right(all_d, dt) - 1
        return j if j >= 0 else None

    def ban(dt, side, kind=None):
        i = i_of(dt)
        return i is not None and extreme_gate.blocked(bars, i, side, kind)

    n0 = sum(len(d.get(k) or []) for k in ("signals", "mw", "defend", "defendSrc"))
    removed = []
    banned_keys = set()

    kept = []
    for s in (d.get("signals") or []):
        if ban(s.get("date"), s.get("side"), s.get("kind")):
            banned_keys.add((s.get("date"), s.get("kind")))
            removed.append(("signals", s.get("date"), s.get("kind"), s.get("side")))
        else:
            kept.append(s)
    d["signals"] = kept

    kept = []
    for w in (d.get("mw") or []):
        dt = w.get("confirmDate") or w.get("date") or w.get("mkDate")
        if ban(dt, w.get("side"), "mw"):
            banned_keys.add((dt, "mw"))
            removed.append(("mw", dt, "mw", w.get("side")))
        else:
            kept.append(w)
    d["mw"] = kept

    km = {"radar": "pred", "pred": "pred", "warn": "warn",
          "mw": "mw", "confirm": "confirm"}
    d["defend"] = [e for e in (d.get("defend") or [])
                   if (e.get("sigDate"), km.get(e.get("sigKind"), e.get("sigKind")))
                   not in banned_keys]
    d["defendSrc"] = [e for e in (d.get("defendSrc") or [])
                      if not ban(e.get("d"), e.get("side"))]
    for k in ("sigW", "sigM"):
        d[k] = [x for x in (d.get(k) or [])
                if not ban(x.get("date"), x.get("side"), x.get("kind"))]

    n1 = sum(len(d.get(k) or []) for k in ("signals", "mw", "defend", "defendSrc"))
    # plan 用正式 build_plan 重算（内部已带引擎层 gate），保证与线上代码同源
    d["plan"] = data_build.build_plan(d["sym"], d, bars, S)
    return n0 - n1, removed, bars, S


def main():
    os.makedirs(BACKUP, exist_ok=True)
    for s in SYMS + ["index"]:
        src = os.path.join(ROOT, "data", s + ".json")
        dst = os.path.join(BACKUP, s + ".json")
        if not os.path.isfile(dst):
            shutil.copy2(src, dst)
    print("线上基线已备份到：", BACKUP)

    total = 0
    all_removed = []
    print("\n=== 逐品种增量应用 ===")
    for sym in SYMS:
        p = os.path.join(ROOT, "data", sym + ".json")
        d = json.load(open(p, encoding="utf-8"))
        b0 = marks_stat(d)
        n, removed, _bars, _S = clean_product(d)
        total += n
        all_removed += [(sym,) + x for x in removed]
        b1 = marks_stat(d)
        print("  %s  页面标识清理 %2d 条 | plan.marks %d→%d（进多 %d→%d，进空 %d→%d）"
              % (sym, n, b0[0], b1[0], b0[2], b1[2], b0[3], b1[3]))
        dump(p, d)
        dump(os.path.join(ROOT, "dist", "data", sym + ".json"), d)

    # ---- index.json ----
    ip = os.path.join(ROOT, "data", "index.json")
    idx = json.load(open(ip, encoding="utf-8"))
    old_built = idx.get("built")
    cfg = extreme_gate.cfg()
    cfg["stat"] = {
        "clearedMarks": total,
        "bySym": {s: sum(1 for x in all_removed if x[0] == s) for s in SYMS},
        "scope": "仅清进场标识；出场雷达 / 白圈失效 / 吊灯 / MA26 / 30% 熔断一律不动",
    }
    idx["extGate"] = cfg
    assert idx.get("built") == old_built, "built 不得变化"
    dump(ip, idx)
    dump(os.path.join(ROOT, "dist", "data", "index.json"), idx)
    print("\nindex.extGate 已写入；built 保持 %s" % old_built)

    # ---- 关键验收：玻璃 2025-07-25 ----
    d = json.load(open(os.path.join(ROOT, "data", "FG.json"), encoding="utf-8"))
    ms = (d.get("plan") or {}).get("marks") or []
    hit = [m for m in ms if m.get("d") == "2025-07-25" and m.get("t") == "in"]
    print("\n=== 玻璃 FG 2025-07-25「进多」方块 ===")
    print("  清理后是否仍存在：", "是 ✘（失败）" if hit else "否 ✔ 已清除")
    for m in ms:
        if "2025-07-20" <= m.get("d", "") <= "2025-08-05":
            print("   ", json.dumps(m, ensure_ascii=False))
    print("  plan.extGate =", json.dumps(d["plan"].get("extGate"), ensure_ascii=False))

    out = os.path.join(ROOT, "reports", "v27_被拦信号清单.json")
    json.dump({"total": total,
               "rows": [{"sym": r[0], "from": r[1], "date": r[2],
                         "kind": r[3], "side": r[4]} for r in all_removed]},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n共清理 %d 条页面标识，清单：%s" % (total, out))


if __name__ == "__main__":
    main()
