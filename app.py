# -*- coding: utf-8 -*-
"""手机端页面服务。只读 state.json，不做任何计算，打开即出。"""
import json
import os
import subprocess
import sys
import threading

from flask import Flask, jsonify, request, send_from_directory

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state.json")
CONF = os.path.join(ROOT, "config.json")
app = Flask(__name__, static_folder="static", template_folder="templates")
_running = {"flag": False}


def load(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


@app.route("/")
def index():
    return send_from_directory(os.path.join(ROOT, "templates"), "index.html")


@app.route("/api/state")
def api_state():
    st = load(STATE, {"signals": [], "stats": {}})
    cfg = load(CONF, {})
    st["config"] = {"equity": cfg.get("equity"), "riskPct": cfg.get("riskPct"),
                    "names": __import__("engine").NAMES,
                    "running": _running["flag"]}
    return jsonify(st)


@app.route("/api/refresh", methods=["POST", "GET"])
def api_refresh():
    if _running["flag"]:
        return jsonify({"ok": False, "msg": "正在计算，请稍等"})
    if request.args.get("key") != load(CONF, {}).get("refreshKey", "futures"):
        return jsonify({"ok": False, "msg": "key 不对"})

    def run():
        _running["flag"] = True
        try:
            subprocess.run([sys.executable, os.path.join(ROOT, "daily.py")],
                           cwd=ROOT, timeout=1800,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            _running["flag"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "msg": "已开始重新计算，约 3~5 分钟"})


@app.route("/health")
def health():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
