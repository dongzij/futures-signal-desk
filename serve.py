# -*- coding: utf-8 -*-
"""零依赖手机页服务：标准库 http.server + Basic Auth + gzip。

不需要 flask / nginx。python3 serve.py 即可。
"""
import base64
import gzip
import io
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
PORT = int(os.environ.get("PORT", 8000))


def cfg():
    try:
        return json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    except Exception:
        return {}


C = cfg()
USER = str(C.get("user", "futures"))
PASS = str(C.get("pass", "changeme"))
AUTH = "Basic " + base64.b64encode(("%s:%s" % (USER, PASS)).encode()).decode()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", gz_ok=True):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if gz_ok and "gzip" in self.headers.get("Accept-Encoding", ""):
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as f:
                f.write(body)
            body = buf.getvalue()
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Authorization") != AUTH:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="futures"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            fp = os.path.join(DIST, "index.html")
        elif path == "/health":
            return self._send(200, b"ok", "text/plain")
        else:
            fp = os.path.join(DIST, path.lstrip("/"))
        if not os.path.isfile(fp):
            return self._send(404, b"not found", "text/plain")
        data = open(fp, "rb").read()
        ct = ("text/html; charset=utf-8" if fp.endswith(".html")
              else "application/javascript; charset=utf-8" if fp.endswith(".js")
              else "application/octet-stream")
        self._send(200, data, ct)


if __name__ == "__main__":
    if not os.path.isfile(os.path.join(DIST, "index.html")):
        print("dist/index.html 不存在，先跑 python3 render.py")
        sys.exit(1)
    print("serving http://0.0.0.0:%d  user=%s" % (PORT, USER))
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
