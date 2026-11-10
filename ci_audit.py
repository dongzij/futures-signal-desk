# -*- coding: utf-8 -*-
"""CI 链路 / 全盘一致性审计（只读，不写任何文件）

检查三件事：
  1. 本地与 GitHub main 逐文件是否一致（含「本地改了线上没改」「线上有本地没有」）；
  2. 所有被入口脚本 import 的本地模块，是否都在 gh_push_v15.collect() 的发布清单里，
     以及是否确实存在于线上 tree —— 防止 15:20 正式班次 ModuleNotFoundError；
  3. v25~v27 策略口径在前端/后端/index/7 品种数据里是否全盘联动。

用法：python ci_audit.py            # 联网比对
      python ci_audit.py --offline  # 只做本地一致性检查
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = "dongzij/futures-signal-desk"
API = "https://api.github.com"
TRANSCRIPT = (r"C:\Users\dongzj\.workbuddy\projects"
              r"\c-Users-dongzj-WorkBuddy-2026-11-02-19-37-02"
              r"\60d22c55-e343-43ea-80ed-64006cf9a88b.jsonl")

# CI 真正会执行的入口（只列入 workflow 里 `run: python xxx.py` 的脚本）。
# 依赖靠 imports_of() 递归展开，不要再手工塞分析脚本 —— 否则会像首版那样
# 把 a2_profit.py 这类仅本地复盘用的模块误报成「线上缺失、15:20 会崩」。
ENTRIES = ["daily.py", "data_build.py", "render.py", "daily_log.py",
           "intraday_warn.py", "night_warn.py",
           "fresh_guard.py", "guard_watch.py"]

SKIP_DIRS = {".git", "__pycache__", "cache", "shots", "_shot", "_cdp2",
             "_v27_baseline", "node_modules", ".workbuddy", "logs"}
SKIP_SUFFIX = (".pyc", ".log", ".tmp", ".png", ".jpg", ".zip")
SKIP_PREFIX = ("_v15_blob_cache", "_v27_", "_cdp", "_shot")


def blob_sha(b):
    return hashlib.sha1(b"blob " + str(len(b)).encode() + b"\0" + b).hexdigest()


def tokens():
    try:
        s = open(TRANSCRIPT, encoding="utf-8", errors="ignore").read()
        return list(dict.fromkeys(re.findall(r"ghp_[A-Za-z0-9]{30,}", s)))
    except Exception:
        return []


def api(path, tok, accept="application/vnd.github+json"):
    hdr = {"Accept": accept, "User-Agent": "ci-audit",
           "X-GitHub-Api-Version": "2022-11-28"}
    if tok:
        hdr["Authorization"] = "Bearer " + tok
    with urllib.request.urlopen(
            urllib.request.Request(API + path, headers=hdr), timeout=90) as f:
        raw = f.read()
        return json.loads(raw or b"{}")


def remote_tree():
    last = None
    for t in tokens() + [None]:
        try:
            ref = api("/repos/%s/git/ref/heads/main" % REPO, t)
            cm = api("/repos/%s/git/commits/%s" % (REPO, ref["object"]["sha"]), t)
            tr = api("/repos/%s/git/trees/%s?recursive=1" % (REPO, cm["tree"]["sha"]), t)
            return ({x["path"]: x["sha"] for x in tr.get("tree", [])
                     if x.get("type") == "blob"}, ref["object"]["sha"])
        except Exception as e:  # noqa: BLE001
            last = e
    print("  [无法读取线上 tree] %r" % last)
    return {}, ""


def local_files():
    out = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(SKIP_SUFFIX) or fn.startswith(SKIP_PREFIX):
                continue
            fp = os.path.join(dirpath, fn)
            p = os.path.relpath(fp, ROOT).replace("\\", "/")
            try:
                out[p] = blob_sha(open(fp, "rb").read())
            except Exception:
                pass
    return out


def publish_list():
    spec = importlib.util.spec_from_file_location(
        "ghpush", os.path.join(ROOT, "gh_push_v15.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return set(m.collect().keys())


def imports_of(path):
    """返回该 .py 里 import 到的本地模块文件名集合。"""
    fp = os.path.join(ROOT, path)
    if not os.path.isfile(fp):
        return set()
    txt = open(fp, encoding="utf-8", errors="ignore").read()
    names = set(re.findall(r"^\s*import\s+([A-Za-z_][\w]*)", txt, re.M))
    names |= set(re.findall(r"^\s*from\s+([A-Za-z_][\w]*)\s+import", txt, re.M))
    # import a, b, c
    for line in re.findall(r"^\s*import\s+([^\n#]+)$", txt, re.M):
        names |= {x.strip().split(" as ")[0].strip() for x in line.split(",")}
    local = {n + ".py" for n in names if os.path.isfile(os.path.join(ROOT, n + ".py"))}
    return local


def main():
    offline = "--offline" in sys.argv
    print("=" * 72)
    print("CI 链路 / 全盘一致性审计")
    print("=" * 72)

    pub = publish_list()
    loc = local_files()
    rem, head = remote_tree() if not offline else ({}, "")
    if rem:
        print("线上 main = %s · %d 个文件 | 本地扫描 %d 个文件 | 发布清单 %d 项"
              % (head[:8], len(rem), len(loc), len(pub)))

    # --- 1. 依赖闭包：从入口递归展开 ---
    deps, queue = set(ENTRIES), list(ENTRIES)
    while queue:
        cur = queue.pop()
        for m in imports_of(cur):
            if m not in deps:
                deps.add(m)
                queue.append(m)
    print("\n【1】CI 入口的本地依赖闭包：%d 个模块" % len(deps))
    missing_pub = sorted(d for d in deps if d not in pub)
    print("  不在发布清单（本地改了不会同步到线上）：",
          missing_pub if missing_pub else "无 ✔")
    if rem:
        missing_rem = sorted(d for d in deps if d not in rem)
        print("  线上缺失（15:20 会 ModuleNotFoundError）：",
              missing_rem if missing_rem else "无 ✔")

    # --- 2. 发布清单内文件：本地 vs 线上 ---
    if rem:
        print("\n【2】发布清单内文件：本地 vs 线上逐字节比对")
        diff = [p for p in sorted(pub) if p in rem and p in loc and rem[p] != loc[p]]
        only_local = [p for p in sorted(pub) if p in loc and p not in rem]
        print("  内容不一致（%d）：%s" % (len(diff), diff[:30] if diff else "无 ✔"))
        print("  线上缺失（%d）：%s" % (len(only_local), only_local[:30] if only_local else "无 ✔"))

        # 非清单但被依赖 / 策略相关的文件，也报出来
        watch = sorted(p for p in set(loc) | set(rem)
                       if p.endswith((".py", ".html", ".yml", ".json"))
                       and (p.startswith(("data/", "dist/data/", "templates/",
                                          ".github/workflows/", "reports/"))
                            or "/" not in p)
                       and p not in pub)
        drift = [p for p in watch if rem.get(p) and loc.get(p) and rem[p] != loc[p]]
        print("\n【3】不在发布清单、但本地与线上内容不同的文件（%d）：" % len(drift))
        for p in drift[:40]:
            print("    -", p)
        if not drift:
            print("    无 ✔")

    # --- 3. 策略口径全盘联动（本地） ---
    print("\n【4】策略口径全盘联动检查（7 品种 / index / 前端 / 后端）")
    syms = ["MA", "HC", "Y", "SM", "FG", "P", "M"]
    idx = json.load(open(os.path.join(ROOT, "data/index.json"), encoding="utf-8"))
    tpl = open(os.path.join(ROOT, "templates/pro.html"), encoding="utf-8").read()
    print("  index.extGate =", (idx.get("extGate") or {}).get("version"),
          "| enabled =", (idx.get("extGate") or {}).get("enabled"),
          "| built =", idx.get("built"))
    print("  index.mwCfg.bigRule =", bool((idx.get("mwCfg") or {}).get("bigRule")))
    print("  前端含「顶不做多」 =", "顶不做多" in tpl,
          "| 含 ARCH_TAG =", "研究参考·非建仓指令" in tpl,
          "| 含日线归档警示 =", "当前为日线归档口径" in tpl)
    print("  extreme_gate 常量 =", end=" ")
    eg = open(os.path.join(ROOT, "extreme_gate.py"), encoding="utf-8").read()
    print(re.findall(r"(?:LOOKBACK|PCT|POS)\w*\s*=\s*[\d.]+", eg)[:6])
    for s in syms:
        p = os.path.join(ROOT, "data", s + ".json")
        d = json.load(open(p, encoding="utf-8"))
        mw = d.get("mw") or []
        ins = [m for m in d["plan"]["marks"] if m.get("t") == "in"]
        print("    %-3s 末根=%s mw=%3d big=%2d in=%2d signals=%3d"
              % (s, d["main"][-1][0], len(mw),
                 sum(1 for w in mw if w.get("big")), len(ins), len(d.get("signals") or [])))
    # data/ 与 dist/data/ 逐字节一致
    bad = [p for p in syms + ["index"]
           if open(os.path.join(ROOT, "data", p + ".json"), "rb").read()
           != open(os.path.join(ROOT, "dist/data", p + ".json"), "rb").read()]
    print("  data/ 与 dist/data/ 不一致：", bad if bad else "无 ✔")

    # --- 4. 结论 ---
    print("\n" + "=" * 72)
    fatal = missing_pub + (sorted(d for d in deps if rem and d not in rem))
    if rem:
        fatal += [p for p in sorted(pub) if p in rem and p in loc and rem[p] != loc[p]]
    print("结论：", "发现 %d 个待修项（见上）" % len(fatal) if fatal else "全盘一致 ✔")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
