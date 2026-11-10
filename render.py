# -*- coding: utf-8 -*-
"""构建 dist/：pro.html 主页面 + data/ 分合约数据包 + echarts.min.js。

页面运行时通过 fetch data/*.json 懒加载品种数据（同源，带 sharecode 透传），
并通过 JSONP 直连新浪刷新最新行情。无后端依赖。
"""
import os
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(ROOT, "templates", "pro.html")
OUTDIR = os.path.join(ROOT, "dist")
# v27.1：补齐站点根实际存放的位置。此前只找 vendor/，导致每次 CI 都打印
# 「找不到 echarts.min.js」的误导性警告（dist/echarts.min.js 其实一直在）。
EC_CANDS = [os.path.join(ROOT, "vendor", "echarts.min.js"),
            os.path.join(os.path.dirname(ROOT), "vendor", "echarts.min.js"),
            os.path.join(ROOT, "echarts.min.js"),
            os.path.join(OUTDIR, "echarts.min.js")]
EC = next((p for p in EC_CANDS if os.path.isfile(p)), None)


def render():
    os.makedirs(OUTDIR, exist_ok=True)
    out = os.path.join(OUTDIR, "index.html")
    shutil.copy(TPL, out)
    out_ec = os.path.join(OUTDIR, "echarts.min.js")
    if os.path.isfile(out_ec):
        pass                       # 已在站点根，无需重建（避免 SameFileError）
    elif EC:
        shutil.copy(EC, out_ec)
    else:
        print("警告：找不到 echarts.min.js，页面图表将无法显示")
    shutil.copy(out, os.path.join(ROOT, "index.html"))
    print("rendered %s  %.1f KB" % (out, os.path.getsize(out) / 1024))
    return out


if __name__ == "__main__":
    render()
