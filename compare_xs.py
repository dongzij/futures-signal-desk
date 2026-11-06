# -*- coding: utf-8 -*-
"""对候选参数做同口径深比：触发频率 / 20根反转胜率 / 20根内 ≥1ATR 反向概率 / 按档位 / 盲测"""
import mine_xs as MX


def detail(P, ext_th, pull_th):
    sigs = MX.scan(P, ext_th, pull_th)
    n, c, atr = P["n"], P["closes"], P["atr"]
    out = {"n": 0, "per": 0.0, "ok20": 0.0, "hit1": 0.0, "mfe": 0.0, "adv": 0.0,
           "strong": [0, 0], "sug": [0, 0], "blind": [0, 0]}
    ok = hit = m = 0
    mfe = adv = 0.0
    for i, d, a, lv in sigs:
        if i + MX.FWD >= n:
            continue
        m += 1
        good = (c[i] - c[i + MX.FWD]) * d > 0
        ok += 1 if good else 0
        best = max((c[i] - c[i + j]) * d / (atr[i] or 1e-9) for j in range(1, MX.FWD + 1))
        mfe += best
        adv += (c[i] - c[i + MX.FWD]) * d / (atr[i] or 1e-9)
        hit += 1 if best >= 1.0 else 0
        tgt = out["strong"] if lv == "strong" else out["sug"]
        tgt[0] += 1
        tgt[1] += 1 if good else 0
        if i >= int(n * 0.8):
            out["blind"][0] += 1
            out["blind"][1] += 1 if good else 0
    if not m:
        return out
    out.update({"n": len(sigs), "per": len(sigs) / P["years"],
                "ok20": ok / m, "hit1": hit / m,
                "mfe": mfe / m, "adv": adv / m, "m20": m})
    return out


def baseline(P):
    n, c = P["n"], P["closes"]
    ok = tot = 0
    for i in range(40, n - MX.FWD):
        if P["sd"][i] == 0:
            continue
        tot += 1
        ok += 1 if (c[i] - c[i + MX.FWD]) * P["sd"][i] > 0 else 0
    return ok / tot if tot else 0


CAND = [(2.6, 1.1), (2.5, 1.2), (2.4, 1.3), (2.3, 1.3), (2.7, 1.0), (3.0, 1.0), (2.5, 1.1)]


def main():
    raw = MX.load_bars()
    Ps = [MX.prep(s, raw[s]) for s in MX.SYMS]
    print("基准（不看条件，趋势段内 20 根自然反向率）：" +
          "  ".join("%s %.1f%%" % (p["sym"], baseline(p) * 100) for p in Ps) + "\n")

    print("%-6s %-6s %5s %6s %7s %7s %7s %8s %7s" %
          ("EXT", "PULL", "合计", "每年", "20根胜率", "≥1ATR", "均MFE", "强档胜率", "盲测胜率"))
    for ext_th, pull_th in CAND:
        tot_n = tot_m = tot_ok = tot_hit = 0
        mfe = adv = 0.0
        s_hit = s_n = 0
        b_hit = b_n = 0
        for P in Ps:
            d = detail(P, ext_th, pull_th)
            tot_n += d["n"]
            tot_m += d.get("m20", 0)
            tot_ok += d["ok20"] * d.get("m20", 0)
            tot_hit += d["hit1"] * d.get("m20", 0)
            mfe += d["mfe"] * d.get("m20", 0)
            adv += d["adv"] * d.get("m20", 0)
            s_n += d["strong"][0]
            s_hit += d["strong"][1]
            b_n += d["blind"][0]
            b_hit += d["blind"][1]
        print("%-6.1f %-6.1f %5d %6.2f %6.1f%% %6.1f%% %+7.2f %s %s" %
              (ext_th, pull_th, tot_n, tot_n / sum(p["years"] for p in Ps), tot_ok / tot_m * 100,
               tot_hit / tot_m * 100, mfe / tot_m,
               ("%5.1f%%(%d)" % (s_hit / s_n * 100, s_n)) if s_n else "-",
               ("%5.1f%%(%d)" % (b_hit / b_n * 100, b_n)) if b_n else "-"))

    print("\n== 详细：EXT 2.6 / PULL 1.1 ==")
    for P in Ps:
        d = detail(P, 2.6, 1.1)
        print("%-3s 次数%3d 每年%.2f | 20根胜率%5.1f%% | ≥1ATR%5.1f%% | 强档%s | 建议%s" %
              (P["sym"], d["n"], d["per"], d["ok20"] * 100, d["hit1"] * 100,
               ("%d/%d" % (d["strong"][1], d["strong"][0])) if d["strong"][0] else "-",
               ("%d/%d" % (d["sug"][1], d["sug"][0])) if d["sug"][0] else "-"))


if __name__ == "__main__":
    main()
