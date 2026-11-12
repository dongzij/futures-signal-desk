# -*- coding: utf-8 -*-
"""v16 上线：通过 GitHub Git Data API 提交（本机代理只放行 api.github.com）。

只提交线上运行所需文件，按远程 tree 的 blob sha 做增量对比，避免全量重传。
"""
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
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
MSG = ("v27.1：数据更新自愈 + CI 全链路加固（不含任何策略/数据改动）。"
       "① 用户拍板：P1 白圈坚决不扩到 mw/tf（白圈只留给已开仓后的防守，两套机制清晰隔离）；"
       "P2 熔断跳空成交价暂不修改（保留 -3412 元极端账面缺口做风控极限测试）。两项均未动代码。"
       "② 连夜核对完整 CI 依赖闭包：从 6 个真实入口展开 16 个模块，"
       "发现 build.py / cyc.py 是取数与 ATR 的核心依赖却不在发布清单（线上靠历史遗留存在），"
       "已补进清单，杜绝「本地改了 GitHub 没改」；sig_pack.py / extreme_gate.py 等 v27 依赖"
       "逐字节核对与线上一致。③ 自愈：build.http_get 改指数退避重试 + FRESH_BYPASS 绕过 4 小时缓存；"
       "新增 fresh_guard.py —— 写盘前只读预检 7 品种当日日线，未到位按 4 分钟重取最多 5 轮，"
       "渲染后 --verify 校验 7 品种末根 / data 与 dist/data / 页面与数据包 / 策略口径 / built，"
       "不通过阻断提交；重试若跨 20:00 立即停止，绝不写夜盘半截 bar。"
       "④ 新增 guard.yml + guard_watch.py：15:55~18:40 共 7 班只读巡检，"
       "built 未刷新或 7 品种不齐就派发 daily.yml 补跑（检查 in_progress 避重复、12 分钟冷却），"
       "20:00 后硬断言不触发。⑤ 新增 ci_audit.py 可复跑全盘一致性审计。"
       "⑥ 修 daily.yml 新鲜度校验残留的旧品种 RB/UR/JD（v25 已删除，会抛异常阻断提交）。"
       "cron 仍为 15:20/15:50 两班，K 线、信号、账本与 6 条推送通道一字未动。"
       "—— 以下保留上一版提交说明存档："
       "v27：【顶不做多·底不做空】极值偏离度铁律 + 全链路落地。"
       "起因是玻璃 FG 2025-07-25 的「进多」（结构双底在急涨末端确认，进场 1728 → 2 根后 30% 熔断 1578，"
       "净亏 3012 元；信号日距 60 日最低点 +7.37ATR / +25.9%，区间位置 0.929）。"
       "① 回测：7 品种 × 10 年 × 990 笔进场事件，复用 cyc.atr_series / sig_pack / engine.exit_track "
       "（MA26 + 吊灯 3ATR + v24 容错 + 30% 熔断），遍历 N=30/60/90/120 与 ATR / 百分比 / 区间位置三套口径。"
       "重要反直觉结论：按最初设想的 ATR 口径，全 10 年【不能】证明高位追多胜率低（N60/T4 被拦组 41.4% "
       "反而高于保留组 39.7%，因急涨时 ATR 同步放大）；是改用百分比 + 区间位置双条件才切出坏区。"
       "② 定稿：N=60，做多 (C/LL−1)≥20% 且 pos=(C−LL)/(HH−LL)≥0.90，做空对称 (1−C/HH)≥20% 且 pos≤0.10；"
       "信号生成日判定、无未来函数。被拦 38 笔胜率 28.9% / 盈亏比 0.91 / 净 R −3.6，"
       "样本外 2023-12 后 8 笔 0% 胜率、平均 −1.48R 全亏；被拦组 mw 29 / tf 9，多 23 / 空 15。"
       "③ 17 组候选规则接进真实 v22 方案账本复跑：拦截不是免费的，多数规则净收益下降且回撤变大；"
       "A1 是能拦下玻璃那笔的规则里损伤最小的（147→138 笔，净收益 153996→142852 即 −7.2%，"
       "回撤 45.8→46.5，pf 2.48→2.44）。这是为纪律付的保险费，不宣称收益优化；"
       "train 段被拦组仍为正收益、样本外仅 8 笔，已如实标注过拟合风险。"
       "④ 落地不是前端隐藏，而是引擎层不接受：新增 extreme_gate.py 单一真源；"
       "sig_pack.build(gate=...) 编译期剔除；data_build.build_plan 传 gate 重跑同一套账本，"
       "plan.verify 保留 v22 原始双段盲测报告并加 extGate 说明口径差异；"
       "页面 signals/mw/defend/defendSrc/sigW/sigM 同步清理并删成对白圈；"
       "daily.py 违规进场信号不入库不推微信并在 best_match 加兜底；出场类 6 通道一条不动，未新增第 7 条通道。"
       "⑤ 前端「图上标记对照」与「当前生效口径」新增铁律说明。"
       "⑥ 数据纪律：以线上 data/*.json 为基线增量注入，不联网、不重算 K 线/分钟线/出场账本，"
       "built 保持 2026-11-10 13:54；变化字段仅 mw / plan / sigW 与 index.extGate。"
       "验收：进多进空方块 147→138 与账本复跑完全一致，玻璃 2025-07-25 已清除，"
       "违规残留 0，孤儿白圈与基线一致，JS 异常 0。"
       "—— 以下保留上一版提交说明存档："
       "v26.2：图上标记全量点击闭环 + 微信口径同步 + 大结构信息推送。"
       "① 所有图标支持点击：方案 ▮进/出方块 → 该笔回测明细，黄◆ → 具体离场原因，"
       "青◇/白箭头 → 离场复盘，动能真空点 / 白圈 / ★ / 三角菱形全部接上弹窗；"
       "ECharts 加全局 tooltip（悬停 / 长按先给文字，K 线另给 OHLC）。"
       "② 信号栏补齐 v25 ②通道口径：微信推过的 mw / tf 形态在页面留只读归档，"
       "每行强制 [研究参考·非建仓指令]，卡片标题 / 计数 / 筛选一并中性化，"
       "运行时纪律断言扫描整卡禁词，不合格直接拦下整行。"
       "③ 「🏔 大级别结构双顶/底」做信息推送（两峰间隔 ≥20 根且颈线深度 ≥3.5 ATR，"
       "10 年 370 条中 78 条、近 1 年 6 条）：并入既有第 ⑤ 条通道的同一次 post_push，"
       "不新增第 7 条通道、不改 v25 定稿的 6 通道结构；小结构不推、只归档；"
       "推送卡片强制带纪律标签、零动作字眼，另有运行时硬断言。"
       "④ 信号栏加可收起「图上标记对照」，周 / 月 / 分钟线时标题旁亮"
       "「⚠️ 当前为日线归档口径」。阈值单一真源是 mw_events.is_big()，"
       "daily.py 与 data_build.py 共用；数据仅新增 big 标注字段，"
       "K 线 / 信号 / 账本 / 盲测结论一律未动。"
       "验收：真实点击进多与黄菱形均弹出正确明细，26 条归档 26/26 带标签、整卡禁词 0，"
       "周线警示与对照展开正常，JS 异常 0。"
       "—— 以下保留上一版提交说明存档："
       "v26.1：页面内容整合 —— 路况二合一、三卡去重。① 删掉 K 线下方旧的"
       "「当前路况 / 市场状态」大字卡与其 renderMarket()，全页只留行情条里的横向两行路况条；"
       "旧卡独有的布林带宽分位 / MA60 斜率 / 20 日穿中轨、路况起始日、旧标签收进顶部"
       "「ⓘ 路况判据」可展开的两行里，并写明「旧标签仅追溯、不作结论，结论只认 plan.regime」"
       "（旧口径在甲醇上判上涨趋势、定稿方案判过渡态，两个结论同屏必打架）。"
       "② 策略持仓看板去掉重复的路况两行，改成定稿方案的唯一持仓口径，新增一处"
       "「当前生效口径」4 条（白圈 v24 连 2 根 ±0.5ATR / 单根 0.8ATR 及其覆盖边界、"
       "MA26+吊灯 3ATR 基础出场、30% 熔断、6 通道与震荡只静音进场）；"
       "③ 进场雷达删掉重复的「策略参考持仓」，CTA 由「可尝试做多/做空」改为"
       "「仅作方向参考 · 非建仓指令」，唯一指令明确为 ②方案内信号；"
       "④ 出场雷达合并全站唯一一份「持仓防守线」（吊灯 / MA26 数值、盘整预警、"
       "1.5ATR 保本、10 年出场统计均来自已下线成死代码的 v9 renderExit），"
       "明确标注雷达口径只作复盘；删除该死函数但保留 DOC.exit 数据供 K 线使用。"
       "⑤ 模拟盘页的持仓卡改名区分口径。纯前端信息架构整理，不动引擎、数据、推送与盲测结论。"
       "实测：7 品种路况条 43~44px 与 K 线零重叠，判据展开 81px/收起 44px，"
       "三卡无重复块，JS 异常 0；窄屏 700px 自动换行仍零重叠。"
       "—— 以下是上一版 v26 夜盘隔离的提交说明（保留存档）："
       "v26：夜盘时段与K线彻底隔离。① 原来 23:10 的 cron 被加在 daily.yml 的 schedule 里，"
       "而那条 job 会跑 daily.py + data_build.py（清空重建 dist/data）并 git push，"
       "等于把夜盘那根半截行情写进 state.json 与日线图，且夜盘按交易所口径属于【下一交易日】，"
       "污染不可逆；现拆成独立的 night.yml（permissions 只读 + git status 断言 + 独立 concurrency），"
       "daily.yml 恢复为 15:20/15:50 两班并加禁止注释。"
       "② 新增 night_warn.py：只读线上 data/ 与新浪分钟线，夜盘 bar 只在内存里、d 标成下一交易日，"
       "用它复跑同一套引擎回答「明早开盘会不会出事」——出场判定读引擎 audit 的 signal_exit"
       "（白圈/吊灯/MA26/到期本来是收盘裁决、次日开盘执行，不产生 trade，这是原实现的漏点），"
       "熔断读 trades；再叠加路况翻转与逆向≥0.5ATR。命中才推「🌙 夜盘预警」，空仓+震荡静默。"
       "③ 新增 nightgate.py：北京时间 20:00-08:00 一律拒绝 daily.py / data_build.py 写盘（exit 3），"
       "15:20/15:50 不受影响；人工补数用 RUN_MODE=force。"
       "④ intraday_warn.py 加日盘时段断言，防止被误指到 23:10 拼出「今日日盘+今晚夜盘」的脏 bar。"
       "⑤ 11:33 盘中预警保持不变（intraday.yml 未改）。")


def tokens():
    try:
        s = open(TRANSCRIPT, encoding="utf-8", errors="ignore").read()
        return list(dict.fromkeys(re.findall(r"ghp_[A-Za-z0-9]{30,}", s)))
    except Exception as e:                                     # noqa: BLE001
        print("凭据读取失败：", type(e).__name__)
        return []


def req(method, path, tok, body=None):
    url = API + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    hdr = {"Accept": "application/vnd.github+json",
           "User-Agent": "futures-signal-desk-deploy",
           "X-GitHub-Api-Version": "2022-11-28"}
    if tok:
        hdr["Authorization"] = "Bearer " + tok
    if data is not None:
        hdr["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, method=method, headers=hdr)
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(r, timeout=90) as f:
                raw = f.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500 or attempt == 4:
                raise
        except Exception as e:                             # noqa: BLE001
            last = e
            if attempt == 4:
                raise
        time.sleep(1.5 * (attempt + 1))
    raise last


def blob_sha(b):
    return hashlib.sha1(b"blob " + str(len(b)).encode() + b"\0" + b).hexdigest()


DROP_RE = re.compile(r"^(?:dist/)?data/(RB|UR|JD)(?:_|\.)")


def collect():
    out = {}
    fixed = [
        "index.html", "echarts.min.js", ".nojekyll",
        "state.json", "config.ci.json", "README.md", "LIVE_LOG.md",
        "engine.py", "daily.py", "data_build.py", "defend_rules.py",
        # ---- v30：资金背离雷达（data_build / daily 都 import，漏发会 ImportError）----
        "div_radar.py",
        # ---- v27.1：CI 真实依赖闭环。build.py / cyc.py 此前只在线上历史遗留，
        #      不在清单 → 本地改了不会同步（本次就改了 build.py 的取数重试）。
        #      fresh_guard / guard_watch / ci_audit / guard.yml 是自愈与审计入口。----
        "build.py", "cyc.py", "fresh_guard.py", "guard_watch.py",
        "ci_audit.py", ".github/workflows/guard.yml",
        "daily_log.py", "mw_events.py", "defend_false_alarm.py",
        "notify.py", "render.py", "sig_pack.py",
        # ---- v27：极值偏离度铁律（sig_pack 必须上线，否则 data_build 传 gate 会报错）----
        "extreme_gate.py", "extreme_gate_backtest.py",
        "extreme_gate_verify.py", "apply_extreme_gate.py",
        "templates/pro.html", ".github/workflows/daily.yml",
        # ---- v23：盘中预警（11:33 只读班次）----
        "intraday_warn.py", ".github/workflows/intraday.yml",
        # ---- v26：夜盘预警（23:10 只读班次）+ 写盘闸门 ----
        "night_warn.py", "nightgate.py", ".github/workflows/night.yml",
        "dist/index.html", "dist/echarts.min.js",
        "reports/失效误报_特征对比表.md",
        "reports/系统至今准确率复盘与未来方向.md",
        "reports/系统方法说明_存档.md",
        "reports/MW第二峰_特征叠加数据表.md",
        "reports/MW_v18_结构识别数据表.md",
        "reports/v17_弱叉降级与静默观察期.md",
        "reports/v17_形态识别与失效源信号_诊断.md",
        # ---- v21 / v22 引擎与报告 ----
        "engine_v21.py", "regime_lib.py", "sig_pack.py",
        "v22_lib.py", "v22_universe.py", "v22_gated.py", "v22_final.py",
        "report_v22.py", "test_push_v22.py",
        "export_v22_tables.py", "export_v22_screen.py",
        "reports/v21_回测重构_四模块报告.md",
        "reports/v22_全市场海选与六池定稿报告.md",
        "reports/v22_表1_全市场海选.csv",
        "reports/v22_表2_六池定稿.csv",
        "reports/v22_表3_六池逐笔清单.csv",
        # ---- v22.1：SM 补入 / 七池定稿 ----
        "reports/v22.1_SM补入与七池定稿说明.md",
        "reports/系统操作说明_v23.md",
        "reports/v24_失效容错实验.md",
        # ---- v26：夜盘时段隔离 / 页面内容整合 ----
        "reports/v26_夜盘时段隔离.md",
        "reports/v26.1_页面内容整合.md",
        "reports/v26.2_前端闭环与大结构推送.md",
        # ---- v27 报告与可复现数据表 ----
        "reports/v27_极值偏离度回测.md",
        "reports/v27_极值偏离度_网格表.csv",
        "reports/v27_极值偏离度_事件明细.csv",
        "reports/v27_极值偏离度_结果.json",
        "reports/v27_账本复跑对比.json",
        "reports/v27_被拦信号清单.json",
        "reports/v27.1_数据自愈与CI加固.md",
        # ---- v25：tf 通道关停 / 6 条通道定稿 ----
        "reports/v25_推送通道收敛.md",
        "reports/系统体检_v25.md",
        # ---- v28：K 线点击盘感模拟 + 数据自愈 + 外部定时器 ----
        "cron_setup.py",
        "reports/v28_盘感模拟与数据自愈.md",
        "reports/v28.2_外部定时器与推送静音.md",
        "v25_probe.py",
        "reports/v22_表2_核心池7定稿.csv",
        "reports/v22_表3_核心池7逐笔清单.csv",
        "reports/v22_final.json",
    ]
    for p in fixed:
        fp = os.path.join(ROOT, *p.split("/"))
        if os.path.isfile(fp):
            out[p] = fp
    logs_dir = os.path.join(ROOT, "logs")
    if os.path.isdir(logs_dir):
        for fn in sorted(os.listdir(logs_dir)):
            if fn.endswith(".md"):
                out["logs/" + fn] = os.path.join(logs_dir, fn)
    for sub in ("data", "dist/data"):
        d = os.path.join(ROOT, *sub.split("/"))
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            p = sub + "/" + fn
            if DROP_RE.match(p):
                continue                 # 已下架品种：不传，交给 tree 删除
            out[p] = os.path.join(d, fn)
    return out


def main():
    tk = None
    for t in tokens():
        try:
            info = req("GET", "/repos/" + REPO, t)
            if info.get("permissions", {}).get("push"):
                tk = t
                print("凭据可用（具备 push 权限）")
                break
            print("一个凭据无 push 权限，尝试下一个")
        except urllib.error.HTTPError as e:
            print("一个凭据不可用：HTTP %s，尝试下一个" % e.code)
        except Exception as e:                                 # noqa: BLE001
            print("一个凭据连接失败：%s，尝试下一个" % type(e).__name__)
    if not tk:
        print("FAIL：没有可用的 GitHub 写凭据，需要用户重新提供 PAT")
        return 2

    ref = req("GET", "/repos/" + REPO + "/git/ref/heads/main", tk)
    base_commit = ref["object"]["sha"]
    cm = req("GET", "/repos/" + REPO + "/git/commits/" + base_commit, tk)
    base_tree = cm["tree"]["sha"]
    tree = req("GET", "/repos/" + REPO + "/git/trees/" + base_tree + "?recursive=1", tk)
    remote = {x["path"]: x["sha"] for x in tree.get("tree", []) if x["type"] == "blob"}
    print("远程 main：%s · tree %s · 已有 %d 个文件" % (base_commit[:8], base_tree[:8], len(remote)))
    print("远程依赖检查：build.py=%s cyc.py=%s a2_profit.py=%s" %
          ("build.py" in remote, "cyc.py" in remote, "a2_profit.py" in remote))

    files = collect()
    changed, unchanged = [], 0
    for p, fp in files.items():
        b = open(fp, "rb").read()
        if remote.get(p) == blob_sha(b):
            unchanged += 1
            continue
        changed.append((p, b))
    # 下架品种的远程残留文件：即使本地零变化也要走一次提交把它们删掉
    dels = [{"path": p, "mode": "100644", "type": "blob", "sha": None}
            for p in remote if DROP_RE.match(p)]
    print("本地待提交 %d 个文件：%d 个有变化，%d 个与远程一致；待删除 %d 个（已下架品种）" %
          (len(files), len(changed), unchanged, len(dels)))
    if not changed and not dels:
        print("无需提交")
        return 0

    # 断点续传：已上传过的 blob 内容 sha 记在本地，代理 502 重跑时不重复上传
    cache_path = os.path.join(ROOT, "_v15_blob_cache.json")
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.isfile(cache_path) else {}
    entries = []
    for i, (p, b) in enumerate(changed, 1):
        key = hashlib.sha256(b).hexdigest()
        if key in cache:
            sha = cache[key]
        else:
            r = req("POST", "/repos/" + REPO + "/git/blobs", tk,
                    {"content": base64.b64encode(b).decode(), "encoding": "base64"})
            sha = r["sha"]
            cache[key] = sha
            json.dump(cache, open(cache_path, "w", encoding="utf-8"))
        entries.append({"path": p, "mode": "100644", "type": "blob", "sha": sha})
        if i % 25 == 0 or i == len(changed):
            print("  blob %d/%d" % (i, len(changed)))

    # v22：被剔除品种（RB/UR/JD）的历史数据文件要从仓库删掉，
    # 否则 ?sym=RB 深链接还能打开一个已下架品种的旧页面。
    if dels:
        print("删除已下架品种数据 %d 个文件" % len(dels))
    entries += dels

    nt = req("POST", "/repos/" + REPO + "/git/trees", tk,
             {"base_tree": base_tree, "tree": entries})
    nc = req("POST", "/repos/" + REPO + "/git/commits", tk,
             {"message": MSG, "tree": nt["sha"], "parents": [base_commit]})
    req("PATCH", "/repos/" + REPO + "/git/refs/heads/main", tk, {"sha": nc["sha"]})
    print("提交成功：%s" % nc["sha"][:8])
    print("变更文件：")
    for p, _ in changed:
        print("  " + p)
    chk = req("GET", "/repos/" + REPO + "/contents/defend_rules.py?ref=main", tk)
    content = base64.b64decode(chk["content"]).decode("utf-8", "ignore")
    print("回读 defend_rules.py：%d 字符 · 含 REV_ATR=%s" %
          (len(content), "REV_ATR = 1.5" in content))
    return 0


if __name__ == "__main__":
    sys.exit(main())
