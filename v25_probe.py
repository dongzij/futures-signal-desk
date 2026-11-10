# -*- coding: utf-8 -*-
"""v25 线上页面交互体检（CDP）。

逐个点击 7 个品种按钮 → 检查数据/路况/持仓/白圈是否随品种切换；
再对一个品种切换合约 → 确认合约级 K 线能加载；全程收集 JS 异常。

用法：python v25_probe.py [url]
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ROOT = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(ROOT, "_shot", "cdprobe25")


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://dongzij.github.io/futures-signal-desk/"
    os.makedirs(PROFILE, exist_ok=True)
    p = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9334",
                          "--user-data-dir=" + PROFILE, "--window-size=1560,1000",
                          "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
                          "--disable-gpu", "about:blank"])
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen("http://127.0.0.1:9334/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        tg = json.loads(urllib.request.urlopen("http://127.0.0.1:9334/json").read())
        ws = websocket.create_connection(
            [x for x in tg if x["type"] == "page"][0]["webSocketDebuggerUrl"], timeout=60)
        n = [0]
        errs = []

        def cmd(method, **params):
            n[0] += 1
            ws.send(json.dumps({"id": n[0], "method": method, "params": params}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("method") == "Runtime.exceptionThrown":
                    d = msg["params"]["exceptionDetails"]
                    errs.append((d.get("text"),
                                 (d.get("exception") or {}).get("description", "")[:120]))
                if msg.get("id") == n[0]:
                    return msg.get("result", {})

        def js(expr):
            r = cmd("Runtime.evaluate", expression=expr, returnByValue=True,
                    awaitPromise=True)
            v = (r.get("result") or {}).get("value")
            # CDP 有时把字符串再包一层引号，最多解两次
            for _ in range(2):
                if isinstance(v, str) and v[:1] in "{[":
                    try:
                        v = json.loads(v)
                    except Exception:
                        break
            return v

        cmd("Page.enable")
        cmd("Runtime.enable")
        cmd("Page.navigate", url=url)
        time.sleep(9)

        print("页面:", js("JSON.stringify({t:document.title,cards:document.querySelectorAll('.card').length,"
                         "syms:MAN.products.map(p=>p.sym),"
                         "ch6:document.body.innerText.indexOf('微信只发这')>=0,"
                         "tf:document.body.innerText.indexOf('趋势确认')>=0,"
                         "canvas:document.querySelectorAll('canvas').length})"))

        syms = js("JSON.stringify(MAN.products.map(p=>p.sym))")
        if isinstance(syms, str):
            syms = json.loads(syms)
        rows = []
        for s in syms:
            js("selectSym(%r)" % s)
            time.sleep(3.2)
            r = js("""JSON.stringify((function(){
                var rg=document.querySelector('.rgb');
                var bd=document.querySelectorAll('.bd');
                return {sym:DOC.sym, bars:DOC.main.length,
                        plan:(DOC.plan&&DOC.plan.method)||'-',
                        regime:rg?rg.innerText.replace(/\\n/g,' / ').slice(0,52):'无指示器',
                        open:(DOC.plan&&DOC.plan.open)?(DOC.plan.open.side+'/'+DOC.plan.open.kind+'/'+DOC.plan.open.bars+'根'):'空仓',
                        marks:(DOC.plan&&DOC.plan.marks)?DOC.plan.marks.length:0,
                        defend:(DOC.defend||[]).length,
                        canvas:document.querySelectorAll('canvas').length,
                        chips:document.querySelectorAll('#ctChips .chip').length};
            })())""")
            rows.append(r if isinstance(r, dict) else json.loads(r))
            print("  %-3s K线%-4d 方案%-3s 持仓%-16s 标记%-3d 白圈%-3d 画布%d 合约%d | %s"
                  % (r["sym"], r["bars"], r["plan"], r["open"], r["marks"],
                     r["defend"], r["canvas"], r["chips"], r["regime"]))

        # 每个品种的 K 线根数应等于该品种数据长度；主力连续期数需 ≥1
        bad = [r for r in rows if r["canvas"] < 1 or r["bars"] < 1000 or r["chips"] < 2]
        print("\n品种切换异常:", bad if bad else "无")

        # 切到豆油，换一个具体合约，确认合约级数据能加载
        js("selectSym('Y')")
        time.sleep(3)
        cid = js("(DOC.contracts&&DOC.contracts.length)?DOC.contracts[0].id:''")
        js("selectCt(%r)" % cid)
        time.sleep(3)
        print("合约切换 →", js("JSON.stringify({ct:DOC.contracts.length?cur.ct:'-',"
                             "bars:DOC.main.length,canvas:document.querySelectorAll('canvas').length})"))
        js("selectCt('0')")
        time.sleep(2)

        print("\nJS 异常:", errs if errs else "无")
        ws.close()
    finally:
        p.terminate()


if __name__ == "__main__":
    main()
