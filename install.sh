#!/usr/bin/env bash
# 腾讯云轻量服务器一键部署 · 零第三方依赖
# 用法：bash install.sh
set -e
cd "$(dirname "$0")"
APP=$(pwd)
PY=$(command -v python3 || echo /usr/bin/python3)

echo "==> 1/7 检查环境"
$PY -V
if ! command -v crontab >/dev/null 2>&1; then
  echo "    安装 cron"
  (apt-get update -qq && apt-get install -y -qq cron) >/dev/null 2>&1 || \
  (yum install -y cronie) >/dev/null 2>&1 || true
fi

echo "==> 1b/7 下载图表库"
if [ ! -s vendor/echarts.min.js ] && [ ! -s dist/echarts.min.js ]; then
  mkdir -p vendor dist
  for U in "https://cdn.staticfile.org/echarts/5.5.0/echarts.min.js" \
           "https://cdn.bootcdn.net/ajax/libs/echarts/5.5.0/echarts.min.js" \
           "https://unpkg.com/echarts@5.5.0/dist/echarts.min.js"; do
    echo "    尝试 $U"
    curl -sL --max-time 30 -o vendor/echarts.min.js "$U" && [ -s vendor/echarts.min.js ] && break
  done
  [ -s vendor/echarts.min.js ] && echo "    ok $(du -h vendor/echarts.min.js | cut -f1)" || echo "    下载失败，稍后手动放一个"
fi

echo "==> 2/7 首次扫描（拉 10 个品种历史数据，约 5~10 分钟）"
$PY daily.py

echo "==> 3/7 构建分品种数据包 + 渲染手机页面"
$PY data_build.py
$PY render.py

echo "==> 4/7 注册 systemd 服务（开机自启 + 崩溃自动重启）"
cat >/etc/systemd/system/futures-web.service <<EOF
[Unit]
Description=Futures Signal Web
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY $APP/serve.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable futures-web >/dev/null 2>&1
systemctl restart futures-web
sleep 2
systemctl is-active futures-web

echo "==> 5/7 定时任务：交易日 15:10 扫描+重建数据+渲染+推送，15:40 兜底"
( crontab -l 2>/dev/null | grep -v "futures-bot" ; \
  echo "10 15 * * 1-5 cd $APP && $PY daily.py >> $APP/cron.log 2>&1 && $PY data_build.py >> $APP/cron.log 2>&1 && $PY render.py >> $APP/cron.log 2>&1 && $PY notify.py >> $APP/cron.log 2>&1  # futures-bot" ; \
  echo "40 15 * * 1-5 cd $APP && $PY daily.py >> $APP/cron.log 2>&1 && $PY data_build.py >> $APP/cron.log 2>&1 && $PY render.py >> $APP/cron.log 2>&1 && $PY notify.py >> $APP/cron.log 2>&1  # futures-bot-retry" \
) | crontab -

echo "==> 6/7 自检"
curl -s -o /dev/null -w "    本机 http 状态: %{http_code}\n" http://127.0.0.1:8000/health || true
IP=$(curl -s --max-time 5 ifconfig.me || echo "<服务器公网IP>")
USER=$(python3 -c "import json;print(json.load(open('config.json'))['user'])")
PASS=$(python3 -c "import json;print(json.load(open('config.json'))['pass'])")
echo
echo "======================================================"
echo " 部署完成"
echo " 手机访问：http://$IP:8000"
echo " 用户名：$USER    密码：$PASS"
echo " 日志：tail -f $APP/cron.log"
echo " 服务：systemctl status futures-web"
echo "======================================================"
