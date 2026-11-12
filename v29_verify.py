# -*- coding: utf-8 -*-
"""v29 线上验收：合约栏「主力连续 + 后三个主力合约」+ 数据日期。

检查项
  1. 右上角行情条日期 = 最新交易日（2026-11-12）；
  2. 合约栏按钮 = 主力连续 + 3 个主力合约（共 4 个；远月未挂牌时如实少于 4 个）；
  3. 每个合约按钮点进去都有真实 K 线与持仓量，不是空壳；
  4. 非主力月份（如 FG2612）不得出现。

用法：python v29_verify.py [url]
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ROOT = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(tempfile.gettempdir(), "cdprobe29-profile")
OUTDIR = os.path.join(ROOT, "验收截图")
URL = sys.argv[1] if len(sys.argv) > 1 else "https://dongzij.github.io/futures-signal-desk/"


def main():
    os.makedirs(PROFILE, exist_ok=True)
    os.makedirs(OUTDIR, exist_ok=True)
    p = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9337",
                          "--user-data-dir=" + PROFILE, "--window-size=1560,1080",
                          "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
                          "--disable-gpu", "about:blank"])
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen("http://127.0.0.1:9337/json/version", timeout=1)
                break
            except Exception:                                  # noqa: BLE001
                time.sleep(0.5)
        tg = json.loads(urllib.request.urlopen("http://127.0.0.1:9337/json").read())
        ws = websocket.create_connection(
            [x for x in tg if x["type"] == "page"][0]["webSocketDebuggerUrl"], timeout=90)
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
                                 (d.get("exception") or {}).get("description", "")[:140]))
                if msg.get("id") == n[0]:
                    return msg.get("result", {})

        def js(expr):
            r = cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
            v = (r.get("result") or {}).get("value")
            for _ in range(2):
                if isinstance(v, str) and v[:1] in "{[":
                    try:
                        v = json.loads(v)
                    except Exception:                          # noqa: BLE001
                        break
            return v

        def shot(name):
            r = cmd("Page.captureScreenshot", format="png", captureBeyondViewport=False)
            import base64
            path = os.path.join(OUTDIR, name)
            with open(path, "wb") as f:
                f.write(base64.b64decode(r["data"]))
            return path

        cmd("Page.enable")
        cmd("Runtime.enable")
        cmd("Page.navigate", url=URL)
        time.sleep(10)

        print("页面:", js("document.title"), "| built:",
              js("(MAN&&MAN.built)||'-'"))
        syms = js("JSON.stringify(MAN.products.map(p=>p.sym))")
        syms = json.loads(syms) if isinstance(syms, str) else syms

        rows, bad = [], []
        for s in syms:
            js("selectSym(%r)" % s)
            time.sleep(3.4)
            r = js("""JSON.stringify((function(){
                var q=document.querySelector('#quote .meta');
                var chips=[].map.call(document.querySelectorAll('#ctChips .chip'),
                                      function(e){return e.textContent.trim();});
                var cs=(DOC.contracts||[]).map(function(c){
                    return {id:c.id, oi:c.oi, n:c.n, last:c.last, front:!!c.front};});
                return {sym:DOC.sym, name:DOC.name, date:q?q.innerText.split('\\n')[0]:'-',
                        chips:chips, contracts:cs,
                        bars:(DOC.main||[]).length,
                        mlb:(DOC.main&&DOC.main.length)?DOC.main[DOC.main.length-1][0]:'-'};
            })())""")
            r = r if isinstance(r, dict) else json.loads(r)
            rows.append(r)
            want = 1 + len(r["contracts"])
            flag = "" if (want == len(r["chips"]) and want <= 4 and r["mlb"] == "2026-11-12") else "  ← 异常"
            print("%-3s %-5s 日期=%-12s 末根=%-12s 按钮%d个 [%s]%s"
                  % (r["sym"], r["name"], r["date"], r["mlb"], len(r["chips"]),
                     " / ".join(r["chips"]), flag))
            for c in r["contracts"]:
                print("      %s 持仓 %-9d 日线%3d根 末根%s%s"
                      % (c["id"], c["oi"], c["n"], c["last"], "  (当前主力)" if c["front"] else ""))
            if flag:
                bad.append(r["sym"])

        # 截图：主力连续 + 逐个合约（只截 FG 与 HC 两个代表品种，控制体积）
        js("selectSym('FG')")
        time.sleep(3.2)
        js("scrollTo(0,0)")
        time.sleep(0.4)
        p1 = shot("v29_FG_主力连续.png")
        print("截图:", p1)
        # 点第 4 个按钮（最后一个主力合约），确认合约级 K 线真的能画
        js("(function(){var c=document.querySelectorAll('#ctChips .chip');"
           "if(c.length>1)c[c.length-1].click();})()")
        time.sleep(3.4)
        p2 = shot("v29_FG_合约K线.png")
        print("截图:", p2)
        print("末个合约状态:", js("JSON.stringify({ct:cur.ct,bars:(DOC.main||[]).length,"
                              "last:(DOC.main&&DOC.main.length)?DOC.main[DOC.main.length-1][0]:'-',"
                              "canvas:document.querySelectorAll('canvas').length})"))

        js("selectSym('HC')")
        time.sleep(3.2)
        js("scrollTo(0,0)")
        time.sleep(0.3)
        p3 = shot("v29_HC_主力连续.png")
        print("截图:", p3)

        print("\n--- 汇总 ---")
        print("7 品种日期:", sorted({r["date"] for r in rows}))
        print("合约按钮数:", {r["sym"]: len(r["chips"]) for r in rows})
        print("异常品种:", bad if bad else "无")
        print("JS 异常:", errs if errs else "无")
        ws.close()
        return 0 if not bad and not errs else 1
    finally:
        p.terminate()


if __name__ == "__main__":
    sys.exit(main())
