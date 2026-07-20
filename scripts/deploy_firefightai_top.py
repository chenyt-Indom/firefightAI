#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一键部署 Firefight AI 到 firefightai.top
运行方式: python deploy_firefightai_top.py
前提: 
  1. firefightai.top DNS已解析到 139.199.69.88
  2. SSH密钥在 D:\firefightAI2.pem 或 C:\Users\19853\Downloads\firefightAI.pem
"""
import paramiko, io, tarfile, time, os, sys
from pathlib import Path

SERVER = "139.199.69.88"
DOMAIN = "firefightai.top"
KEY_CANDIDATES = [
    r"D:\firefightAI2.pem",
    r"C:\Users\19853\Downloads\firefightAI.pem",
]
REMOTE_DIR = f"/home/ubuntu/{DOMAIN}"

# ── 找到SSH密钥 ──
key_path = None
for k in KEY_CANDIDATES:
    if os.path.exists(k):
        key_path = k
        break
if not key_path:
    print("❌ 未找到SSH密钥文件")
    sys.exit(1)

print(f"🔑 SSH密钥: {key_path}")
print(f"🌐 域名: {DOMAIN} → {SERVER}")

# ── SSH连接 ──
key = paramiko.RSAKey.from_private_key_file(key_path)
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    c.connect(SERVER, username="ubuntu", pkey=key, timeout=30)
    print("✅ SSH连接成功")
except Exception as e:
    print(f"❌ SSH连接失败: {e}")
    sys.exit(1)

sftp = c.open_sftp()

# ── 1. 打包并上传项目 ──
print("\n📦 打包项目...")
PROJECT = Path(r"D:\firefightAI\zhanluxt")
if not PROJECT.exists():
    PROJECT = Path(r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI")

tarball = Path("/tmp/firefight_deploy.tar.gz")
with tarfile.open(tarball, "w:gz") as tar:
    for item in PROJECT.glob("*"):
        if item.name in (".git", "__pycache__", "sessions"):
            continue
        tar.add(item, arcname=item.name)

size_mb = tarball.stat().st_size / (1024*1024)
print(f"   大小: {size_mb:.1f}MB")

print(f"📤 上传到服务器...")
sftp.put(str(tarball), f"/tmp/firefight_deploy.tar.gz")

# ── 2. 解压并安装依赖 ──
print("📦 安装依赖...")
cmds = [
    f"rm -rf {REMOTE_DIR} && mkdir -p {REMOTE_DIR}",
    f"cd {REMOTE_DIR} && tar xzf /tmp/firefight_deploy.tar.gz",
    f"cd {REMOTE_DIR} && pip3 install flask flask-socketio ultralytics opencv-python numpy pydantic loguru pyyaml httpx openai --break-system-packages -q 2>&1 | tail -3",
]
for cmd in cmds:
    _, out, err = c.exec_command(cmd, timeout=120)
    o = out.read().decode().strip()
    if o: print(f"   {o[:100]}")

# ── 3. 重启后端服务 ──
print("🚀 启动服务...")
c.exec_command("pkill -f dashboard_server 2>/dev/null; sleep 1")
c.exec_command(f"cd {REMOTE_DIR} && nohup python3 dashboard_server.py --port 5000 --host 0.0.0.0 > /tmp/firefight.log 2>&1 &")
time.sleep(3)

# ── 4. 验证本地 ──
_, out, _ = c.exec_command("curl -s http://localhost:5000/api/version")
ver = out.read().decode().strip()
print(f"   版本: {ver[:100]}")

# ── 5. 配置 Nginx ──
print("🌐 配置 Nginx...")
nginx_conf = f'''server {{
    listen 80;
    server_name {DOMAIN} www.{DOMAIN};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl http2;
    server_name {DOMAIN} www.{DOMAIN};

    ssl_certificate /etc/letsencrypt/live/{DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{DOMAIN}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    client_max_body_size 50M;

    location / {{
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
    location /socket.io/ {{
        proxy_pass http://127.0.0.1:5000/socket.io/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection upgrade;
        proxy_set_header Host $host;
    }}
}}
'''
sftp.putfo(io.BytesIO(nginx_conf.encode()), f"/tmp/{DOMAIN}_nginx")

ssl_cmds = [
    f"sudo cp /tmp/{DOMAIN}_nginx /etc/nginx/sites-available/{DOMAIN}",
    f"sudo ln -sf /etc/nginx/sites-available/{DOMAIN} /etc/nginx/sites-enabled/{DOMAIN}",
    f"sudo certbot --nginx -d {DOMAIN} -d www.{DOMAIN} --non-interactive --agree-tos --email admin@{DOMAIN} --redirect 2>&1 | tail -5",
    f"sudo nginx -t 2>&1 && sudo nginx -s reload 2>&1",
]
for cmd in ssl_cmds:
    _, out, _ = c.exec_command(cmd, timeout=60)
    o = out.read().decode().strip()
    if o: print(f"   {o[:150]}")

# ── 6. 最终验证 ──
print(f"\n{'='*50}")
print(f"🎉 部署完成!")
print(f"   HTTPS: https://{DOMAIN}")
print(f"   HTTP:  http://{DOMAIN} (自动跳转HTTPS)")
print(f"{'='*50}")

# 测试
time.sleep(2)
_, out, _ = c.exec_command(f"curl -s -o /dev/null -w '%{{http_code}}' http://{DOMAIN}/ 2>/dev/null")
print(f"   HTTP状态: {out.read().decode()}")
_, out, _ = c.exec_command(f"curl -s -o /dev/null -w '%{{http_code}}' https://{DOMAIN}/ -k 2>/dev/null")
print(f"   HTTPS状态: {out.read().decode()}")

sftp.close()
c.close()
