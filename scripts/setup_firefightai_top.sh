#!/bin/bash
# 🔥 firefightai.top 完整部署脚本
# 在服务器上运行: bash setup_firefightai_top.sh

set -e
DOMAIN="firefightai.top"
SERVER_IP="139.199.69.88"
APP_DIR="/home/ubuntu/firefight_models"
VENV_DIR="$APP_DIR/firefight_env"

echo "🚀 Firefight AI 域名部署: $DOMAIN"

# 1. 确保Python和依赖
echo "📦 检查环境..."
python3 --version
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv $VENV_DIR
fi
source $VENV_DIR/bin/activate
pip install flask flask-socketio flask-cors pyyaml loguru -q

# 2. 从GitHub拉取最新代码
echo "📥 拉取最新代码..."
cd $APP_DIR
if [ -d ".git" ]; then
    git pull origin master 2>/dev/null || echo "⚠ git pull跳过，使用本地代码"
fi

# 3. 重启服务
echo "🔄 重启服务..."
pkill -f dashboard_server.py 2>/dev/null || true
sleep 2
nohup $VENV_DIR/bin/python dashboard_server.py --port 5000 --host 0.0.0.0 > /tmp/firefight.log 2>&1 &
sleep 3

# 4. 验证服务
if curl -s http://localhost:5000/api/version > /dev/null 2>&1; then
    echo "✅ 服务已启动: http://localhost:5000"
    VERSION=$(curl -s http://localhost:5000/api/version | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('version','unknown'))" 2>/dev/null)
    echo "   版本: $VERSION"
else
    echo "❌ 服务启动失败，查看日志:"
    tail -20 /tmp/firefight.log
    exit 1
fi

# 5. 配置Nginx
echo "🌐 配置Nginx..."
sudo tee /etc/nginx/conf.d/firefightai.conf > /dev/null << 'NGINX_EOF'
server {
    listen 80;
    server_name firefightai.top www.firefightai.top 139.199.69.88;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name firefightai.top www.firefightai.top;

    ssl_certificate /etc/letsencrypt/live/lvbaixing.top/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/lvbaixing.top/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;
    }

    location /socket.io/ {
        proxy_pass http://127.0.0.1:5000/socket.io/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
NGINX_EOF

sudo nginx -t && sudo systemctl reload nginx
echo "✅ Nginx 配置完成"

# 6. 验证公网访问
echo "🔍 验证公网..."
sleep 1
if curl -s -o /dev/null -w "%{http_code}" --max-time 5 https://$DOMAIN/ -k | grep -q "200"; then
    echo "✅ 公网访问正常: https://$DOMAIN/"
else
    echo "⚠ 公网访问可能受限（需配置DNS和安全组）"
fi

echo ""
echo "🎉 部署完成!"
echo "   域名: https://$DOMAIN/"
echo "   日志: /tmp/firefight.log"
echo "   管理: systemctl reload nginx"
