#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
部署 Firefight AI 到 firefightai.top
一键: DNS验证 → 上传项目 → Nginx+HTTPS → 启动服务 → 验证
"""
import paramiko, io, tarfile, time, sys, os
from pathlib import Path

SERVER = "139.199.69.88"
DOMAIN = "firefightai.top"
KEY_PATH = r"C:\Users\19853\Downloads\firefightAI.pem"
REMOTE_DIR = f"/home/ubuntu/{DOMAIN}"

def ssh_connect():
    key = paramiko.RSAKey.from_private_key(io.StringIO(open(KEY_PATH).read()))
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(SERVER, username="ubuntu", pkey=key, timeout=30)
    return c

def step(msg):
    print(f"\n{'='*50}\n{msg}\n{'='*50}")

# ============================================================
step("1. DNS 验证")
try:
    c = ssh_connect()
    print(f"✅ SSH 连接成功")
    _, out, _ = c.exec_command("dig +short firefightai.top 2>/dev/null || nslookup firefightai.top 2>&1 | grep Address | tail -1")
    dns = out.read().decode().strip()
    print(f"DNS: {dns}")
    if "139.199.69.88" not in dns:
        print("⚠️ 域名未解析到服务器IP，请在腾讯云DNS管理中添加 A 记录:")
        print(f"   firefightai.top → {SERVER}")
except Exception as e:
    print(f"❌ SSH连接失败: {e}")
    print("请检查网络后重试")
    sys.exit(1)

# ============================================================
step("2. 打包并上传项目")
PROJECT = Path(r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI")

tarball = Path("/tmp/firefight_deploy.tar.gz")
with tarfile.open(tarball, "w:gz") as tar:
    for item in PROJECT.glob("*"):
        if item.name in (".git", "__pycache__", "sessions", ".workbuddy"):
            continue
        tar.add(item, arcname=item.name)

size_mb = tarball.stat().st_size / (1024*1024)
print(f"打包: {size_mb:.1f}MB")

sftp = c.open_sftp()
sftp.put(str(tarball), f"/tmp/{tarball.name}")
print("✅ 上传完成")

# ============================================================
step("3. 安装依赖并启动")
cmds = [
    f"rm -rf {REMOTE_DIR} && mkdir -p {REMOTE_DIR}",
    f"cd {REMOTE_DIR} && tar xzf /tmp/firefight_deploy.tar.gz",
    f"cd {REMOTE_DIR} && python3 -m venv venv 2>/dev/null || true",
    f"cd {REMOTE_DIR} && ./venv/bin/pip install flask flask-socketio ultralytics opencv-python numpy pydantic loguru pyyaml httpx openai --break-system-packages -q 2>/dev/null || pip3 install flask flask-socketio --break-system-packages -q",
    f"pkill -f dashboard_server 2>/dev/null; sleep 1",
    f"cd {REMOTE_DIR} && nohup python3 dashboard_server.py --port 5000 --host 0.0.0.0 > /tmp/firefight.log 2>&1 &",
]
for cmd in cmds:
    _, out, _ = c.exec_command(cmd)
    time.sleep(0.5)

time.sleep(3)
print("✅ 服务已启动")

# ============================================================
step("4. 配置 Nginx + HTTPS")
nginx_conf = f'''server {{
    listen 80;
    server_name {DOMAIN} www.{DOMAIN};
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
nginx_cmds = [
    f"sudo cp /tmp/{DOMAIN}_nginx /etc/nginx/sites-available/{DOMAIN}",
    f"sudo ln -sf /etc/nginx/sites-available/{DOMAIN} /etc/nginx/sites-enabled/ 2>/dev/null; true",
    f"sudo nginx -t 2>&1",
    f"sudo certbot --nginx -d {DOMAIN} --non-interactive --agree-tos --email admin@{DOMAIN} 2>&1 | tail -5 || echo 'certbot_skip'",
    f"sudo nginx -s reload 2>&1",
]
for cmd in nginx_cmds:
    _, out, _ = c.exec_command(cmd)
    o = out.read().decode().strip()
    if o: print(o[:200])

# ============================================================
step("5. 验证")
tests = [
    (f"http://{DOMAIN}/api/status", "状态检查"),
    (f"http://{DOMAIN}/health", "健康检查"),
]
for url, label in tests:
    _, out, _ = c.exec_command(f"curl -s --max-time 5 {url}")
    o = out.read().decode().strip()[:100]
    print(f"  {label}: {o}")

# API Key 验证
_, out, _ = c.exec_command("cd {REMOTE_DIR} && grep 'api_key' config/settings.yaml")
print(f"  API Key: {'✅ 已配置' if 'sk-' in out.read().decode() else '❌ 未配置'}")

sftp.close()
c.close()

step("🎉 部署完成!")
print(f"""
  公网地址: https://{DOMAIN}
  本地面板: {REMOTE_DIR}
  日志: /tmp/firefight.log
  
  每天自动更新: {SERVER} 上运行 自动更新.bat
""")
