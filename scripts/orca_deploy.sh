#!/bin/bash
# 🔥 Firefight AI 一键部署 - 在 OrcaTerm 粘贴运行
set -e
echo "🚀 Firefight AI 域名部署 firefightai.top"

# 1. 修复 SSH (以防sshd挂了)
sudo systemctl restart sshd 2>/dev/null || true
echo "✅ SSH 已重启"

# 2. 重启 dashboard
APP_DIR="/home/ubuntu/firefight_models"
cd $APP_DIR
git pull origin master 2>/dev/null || echo "⚠ git pull 跳过"

VENV_DIR="$APP_DIR/firefight_env"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv $VENV_DIR
fi
source $VENV_DIR/bin/activate
pip install flask flask-socketio flask-cors pyyaml loguru -q 2>/dev/null

pkill -f dashboard_server.py 2>/dev/null || true
sleep 1
nohup $VENV_DIR/bin/python dashboard_server.py --port 5000 --host 0.0.0.0 > /tmp/firefight.log 2>&1 &
sleep 3
curl -s http://localhost:5000/api/version && echo "" && echo "✅ Dashboard 启动成功" || echo "❌ 启动失败"

# 3. 配置 Nginx
sudo tee /etc/nginx/conf.d/firefightai.conf > /dev/null << 'NGINX_EOF'
server {
    listen 80;
    server_name firefightai.top www.firefightai.top;
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
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 86400s;
    }
    location /socket.io/ {
        proxy_pass http://127.0.0.1:5000/socket.io/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
NGINX_EOF

sudo nginx -t && sudo systemctl reload nginx
echo "✅ Nginx 已配置"

# 4. 验证
echo ""
echo "🎉 部署完成!"
echo "   域名: https://firefightai.top"
echo "   状态: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:5000/ --max-time 3) (本地)"
echo ""
echo "⚠ 请确保 DNS: firefightai.top → A → 139.199.69.88"
