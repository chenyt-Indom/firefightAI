#!/bin/bash
# FirefightAI 一键部署脚本 - 在服务器VNC终端中运行
# 用法: bash <(curl -sL https://raw.githubusercontent.com/chenyt-Indom/firefightAI/master/remote_deploy.sh)
# 或直接在VNC中复制粘贴下面所有内容

set -e
echo "============================================"
echo "  FirefightAI 一键部署"
echo "============================================"

PROJECT_DIR="/home/ubuntu/firefightAI"
DOMAIN="firefightai.top"
HOST="139.199.69.88"

# 1. 修复防火墙
echo "[1/8] 修复防火墙..."
sudo ufw allow 22/tcp 2>/dev/null || true
sudo ufw allow 5000/tcp 2>/dev/null || true
sudo ufw allow 80/tcp 2>/dev/null || true
sudo ufw allow 443/tcp 2>/dev/null || true
echo "  防火墙已配置"

# 2. 停止旧服务
echo "[2/8] 停止旧服务..."
sudo pkill -f dashboard_server.py 2>/dev/null || true
screen -S firefight -X quit 2>/dev/null || true
sleep 2
echo "  旧服务已停止"

# 3. 从GitHub拉取最新代码
echo "[3/8] 拉取最新代码..."
if [ -d "$PROJECT_DIR/.git" ]; then
    cd $PROJECT_DIR
    git fetch origin master 2>/dev/null
    git reset --hard origin/master 2>/dev/null
    echo "  Git更新完成"
else
    cd /home/ubuntu
    git clone git@github.com:chenyt-Indom/firefightAI.git firefightAI 2>/dev/null || \
    git clone https://github.com/chenyt-Indom/firefightAI.git firefightAI 2>/dev/null
    echo "  Git克隆完成"
fi

# 4. 安装依赖
echo "[4/8] 安装Python依赖..."
cd $PROJECT_DIR
pip3 install flask flask-socketio flask-cors pyyaml requests paramiko -q 2>&1 | tail -1
echo "  依赖已安装"

# 5. 创建必要目录
mkdir -p $PROJECT_DIR/static $PROJECT_DIR/sessions $PROJECT_DIR/models $PROJECT_DIR/config

# 6. 启动Flask应用
echo "[5/8] 启动Flask应用..."
cd $PROJECT_DIR
screen -dmS firefight python3 dashboard_server.py --host 0.0.0.0 --port 5000
sleep 4
echo "  Flask已启动"

# 7. 配置Nginx
echo "[6/8] 配置Nginx..."
# 安装nginx（如果需要）
sudo apt-get update -qq && sudo apt-get install -y -qq nginx 2>&1 | tail -1

# 生成SSL证书
sudo mkdir -p /etc/nginx/ssl
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/${DOMAIN}.key \
    -out /etc/nginx/ssl/${DOMAIN}.crt \
    -subj "/C=CN/ST=Guangdong/L=Shenzhen/O=FirefightAI/CN=${DOMAIN}" 2>/dev/null

# Nginx配置
sudo tee /etc/nginx/sites-available/firefightai > /dev/null << 'NGINXEOF'
server {
    listen 80;
    server_name firefightai.top www.firefightai.top 139.199.69.88;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name firefightai.top www.firefightai.top 139.199.69.88;

    ssl_certificate /etc/nginx/ssl/firefightai.top.crt;
    ssl_certificate_key /etc/nginx/ssl/firefightai.top.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
        proxy_buffering off;
    }

    location /socket.io/ {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }

    location /static/ {
        alias /home/ubuntu/firefightAI/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
NGINXEOF

sudo ln -sf /etc/nginx/sites-available/firefightai /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx || sudo nginx -s reload
echo "  Nginx已配置"

# 8. 验证
echo "[7/8] 验证服务..."
echo "  Flask: $(curl -s http://127.0.0.1:5000/api/version 2>/dev/null || echo '检查中...')"
echo "  Nginx: $(curl -s -o /dev/null -w '%{http_code}' http://localhost/ 2>/dev/null || echo '检查中...')"
echo "  HTTPS: $(curl -s -k -o /dev/null -w '%{http_code}' https://localhost/ 2>/dev/null || echo '检查中...')"
echo "  端口: $(ss -tlnp | grep -E ':(22|80|443|5000) ' | awk '{print $4}' | paste -sd ',' -)"

echo ""
echo "============================================"
echo "  部署完成!"
echo "  访问: https://${HOST}"
echo "  域名: https://${DOMAIN} (需配置DNS)"
echo "============================================"