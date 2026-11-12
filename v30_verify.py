# -*- coding: utf-8 -*-
"""v30 线上验收：资金背离雷达 L1 状态灯 + L2 ⚠ 标记 + 既有信号无损。"""
import json, os, subprocess, sys, tempfile, time, urllib.request
import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ROOT = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(tempfile.gettempdir(), "cdv30-profile")
URL = sys.argv[1] if len(sys.argv) > 1 else ("https://dongzij.github.io/futures-signal-desk/?t=" + str(int(time.time())))


def main():
    os.makedirs(PROFILE, exist_ok=True)
    p = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9343",
                          "--user-data-dir=" + PROFILE, "--window-size=1560,1080",
                          "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
                          "--disable-gpu", "about:blank"])
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen("http://127.0.0.1:9343/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        tg = json.loads(urllib.request.urlopen("http://127.0.0.1:9343/json").read())
        ws = websocket.create_connection(
            [x for x in tg if x["type"] == "page"][0]["webSocketDebuggerUrl"], timeout=90)
        n = [0]; errs = []

        def cmd(method, **params):
            n[0] += 1
            ws.send(json.dumps({"id": n[0], "method": method, "params": params}))
            while True:
                m = json.loads(ws.recv())
                if m.get("method") == "Runtime.exceptionThrown":
                    d = m["params"]["exceptionDetails"]
                    errs.append((d.get("text"),
                                 (d.get("exception") or {}).get("description", "")[:140]))
                if m.get("id") == n[0]:
                    return m.get("result", {})

        def js(e):
            r = cmd("Runtime.evaluate", expression=e, returnByValue=True, awaitPromise=True)
            v = (r.get("result") or {}).get("value")
            for _ in range(2):
                if isinstance(v, str) and v[:1] in "{[":
                    try:
                        v = json.loads(v)
                    except Exception:
                        break
            return v

        cmd("Page.enable"); cmd("Runtime.enable")
        cmd("Page.navigate", url=URL)
        time.sleep(14)
        print("built:", js("(MAN&&MAN.built)||'-'"))

        # 逐品种：灯 + ⚠ 标记数 + 既有信号计数
        syms = js("JSON.stringify((MAN.products||[]).map(function(p){return p.sym;}))")
        syms = json.loads(syms) if isinstance(syms, str) else syms
        bad = []
        for s in syms:
            js("selectSym(%r)" % s)
            time.sleep(3.0)
            r = js("""JSON.stringify((function(){
                var rgb = document.querySelector('#regimeBox');
                var lamp = rgb ? /无量上行/.test(rgb.innerText) : false;
                var evn = (DOC.divradar && DOC.divradar.events || []).length;
                var sigN = (DOC.signals || []).length;
                var defN = (DOC.defend || []).length;
                var mwN = (DOC.mw || []).length;
                var warn = 0;
                try{
                  var CH = window.CH || CH;
                  (CH.getOption().series||[]).forEach(function(se){
                    (se.data||[]).forEach(function(d){
                      if(d && d.label && d.label.formatter === '⚠') warn++;
                    });
                  });
                }catch(e){}
                return {lamp:lamp, evn:evn, warn:warn, sigN:sigN, defN:defN, mwN:mwN,
                        last:(DOC.main&&DOC.main.length)?DOC.main[DOC.main.length-1][0]:''};
            })())""")
            r = r if isinstance(r, dict) else json.loads(r)
            flag = ""
            if r["evn"] > 0 and r["warn"] != r["evn"]: flag = "  ← ⚠标记数与事件数不符!"
            print("%-3s 灯=%-5s 事件%d 图上⚠%d | 既有: 信号%d 失效%d 双顶%d | 末根%s%s"
                  % (s, r["lamp"], r["evn"], r["warn"], r["sigN"], r["defN"], r["mwN"],
                     r["last"], flag))
            if flag: bad.append(s)

        # 图例
        cleg = js("document.querySelector('.cleg') ? document.querySelector('.cleg').innerText : ''")
        print("图例含⚠顶风险确认:", "✓" if "顶风险确认" in cleg else "✗")

        # FG 详情弹窗演练：直接调用 openDivAlert
        js("selectSym('FG')"); time.sleep(2.8)
        js("openDivAlert({d:'2026-09-14', px:927, lampDays:5, oiDrop:-12.6})")
        time.sleep(0.6)
        sheet_ok = js("/顶部风险确认/.test(document.querySelector('#sheet').innerText||'')")
        print("openDivAlert 详情弹窗:", "✓" if sheet_ok else "✗")
        js("closeSheet()")

        # 截图（FG 路况区 + 图）
        js("scrollTo(0,0)"); time.sleep(0.4)
        r = cmd("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        import base64
        out = os.path.join(ROOT, "验收截图", "v30_FG_背离灯与⚠标记.png")
        open(out, "wb").write(base64.b64decode(r["data"]))
        print("截图:", out)

        print("JS 异常:", errs if errs else "无 ✓")
        print("异常项:", bad if bad else "无 ✓")
        ws.close()
        return 0 if (not errs and not bad and sheet_ok) else 1
    finally:
        try: p.terminate()
        except Exception: pass


if __name__ == "__main__":
    sys.exit(main())
