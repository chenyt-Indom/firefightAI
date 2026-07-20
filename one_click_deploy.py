"""一键部署: 修复防火墙 + 上传代码 + 启动Flask + 配置Nginx + DNS + SSL + 验证"""
import paramiko, time, urllib3, json, requests, sys

urllib3.disable_warnings()

HOST = '139.199.69.88'
USER = 'ubuntu'
KEY_PATH = r'D:\firefightAI2.pem'
DOMAIN = 'firefightai.top'
PROJECT_DIR = '/home/ubuntu/firefightAI'

# ========== 连接服务器 ==========
print('=' * 60)
print('连接服务器...')
key = paramiko.RSAKey.from_private_key_file(KEY_PATH)
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, pkey=key, timeout=15)
print('SSH连接成功!')

def run(cmd, timeout=30, show=True):
    if show:
        print(f'  [RUN] {cmd[:100]}')
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if err and show:
        print(f'  [ERR] {err[:200]}')
    return out, err

# ========== 1. 修复防火墙 ==========
print('\n' + '=' * 60)
print('1. 修复防火墙')
run('sudo ufw allow 22/tcp')
run('sudo ufw allow 5000/tcp')
run('sudo ufw allow 80/tcp')
run('sudo ufw allow 443/tcp')
out, _ = run('sudo ufw status numbered')
print(f'  防火墙状态:\n{out}')

# ========== 2. 停止旧服务 ==========
print('\n' + '=' * 60)
print('2. 停止旧服务')
run('pkill -f dashboard_server.py 2>/dev/null; sleep 2; echo "旧进程已停止"')
run('screen -S firefight -X quit 2>/dev/null; echo "screen已清理"')

# ========== 3. 上传最新代码 ==========
print('\n' + '=' * 60)
print('3. 上传代码')
sftp = ssh.open_sftp()
files = [
    (r'd:/firefightAI/zhanluxt/dashboard_server.py', f'{PROJECT_DIR}/dashboard_server.py'),
    (r'd:/firefightAI/zhanluxt/config/settings.yaml', f'{PROJECT_DIR}/config/settings.yaml'),
]
for local, remote in files:
    try:
        sftp.put(local, remote)
        print(f'  [OK] {remote.split("/")[-1]}')
    except Exception as e:
        print(f'  [FAIL] {remote.split("/")[-1]}: {e}')
sftp.close()

# ========== 4. 安装依赖 ==========
print('\n' + '=' * 60)
print('4. 检查依赖')
run(f'cd {PROJECT_DIR} && pip3 install flask flask-socketio flask-cors pyyaml requests paramiko -q 2>&1 | tail -3', timeout=60)

# ========== 5. 启动Flask ==========
print('\n' + '=' * 60)
print('5. 启动Flask应用')
start_cmd = f"cd {PROJECT_DIR} && python3 dashboard_server.py --host 0.0.0.0 --port 5000"
run(f'screen -dmS firefight bash -c "{start_cmd} 2>&1 | tee /tmp/dashboard.log"')
time.sleep(5)

# 验证Flask
out, _ = run('curl -s http://127.0.0.1:5000/api/version 2>/dev/null')
print(f'  Flask版本: {out[:200] if out else "无响应"}')

out, _ = run('ss -tlnp | grep -E ":5000 "')
print(f'  端口5000: {out[:100]}')

# ========== 6. 配置Nginx + SSL ==========
print('\n' + '=' * 60)
print('6. 配置Nginx')

# 生成自签名SSL证书
run('sudo mkdir -p /etc/nginx/ssl')
run('sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 '
    f'-keyout /etc/nginx/ssl/{DOMAIN}.key '
    f'-out /etc/nginx/ssl/{DOMAIN}.crt '
    f'-subj "/C=CN/ST=Guangdong/L=Shenzhen/O=FirefightAI/CN={DOMAIN}" 2>&1')

nginx_conf = f'''server {{
    listen 80;
    server_name {DOMAIN} www.{DOMAIN} {HOST};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl;
    server_name {DOMAIN} www.{DOMAIN} {HOST};

    ssl_certificate /etc/nginx/ssl/{DOMAIN}.crt;
    ssl_certificate_key /etc/nginx/ssl/{DOMAIN}.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {{
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
    }}

    location /socket.io/ {{
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }}

    location /static/ {{
        alias {PROJECT_DIR}/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }}
}}
'''

run('sudo tee /etc/nginx/sites-available/firefightai > /dev/null << \'NGINXEOF\'\n' + nginx_conf + '\nNGINXEOF')
run('sudo ln -sf /etc/nginx/sites-available/firefightai /etc/nginx/sites-enabled/')
run('sudo rm -f /etc/nginx/sites-enabled/default')
out, err = run('sudo nginx -t 2>&1')
print(f'  Nginx配置: {out}')
run('sudo systemctl reload nginx 2>&1 || sudo nginx -s reload 2>&1')

# ========== 7. 验证服务 ==========
print('\n' + '=' * 60)
print('7. 验证服务')

# 通过本地端口
out, _ = run('curl -s http://127.0.0.1:5000/api/version 2>/dev/null')
print(f'  Flask直接: {out[:150]}')

# 通过Nginx
out, _ = run('curl -s http://localhost/api/version 2>/dev/null')
print(f'  Nginx HTTP: {out[:150]}')

out, _ = run('curl -s -k https://localhost/api/version 2>/dev/null')
print(f'  Nginx HTTPS: {out[:150]}')

# 端口监听
out, _ = run('ss -tlnp | grep -E ":(22|80|443|5000) "')
print(f'  端口监听:\n{out}')

# ========== 8. Flask进程状态 ==========
out, _ = run('ps aux | grep dashboard_server | grep -v grep')
print(f'  Flask进程: {"运行中" if out else "未运行!"}')

# ========== 9. 前端首页 ==========
out, _ = run('curl -s -k https://localhost/ 2>/dev/null | head -c 300')
print(f'  前端HTML: {out[:200]}...')

ssh.close()
print('\n' + '=' * 60)
print('服务器端部署完成！正在从本地验证...')
print('=' * 60)

# ========== 10. 本地公网验证 ==========
time.sleep(2)
print('\n10. 公网验证:')

tests = [
    ('版本API', f'https://{HOST}/api/version'),
    ('系统状态', f'https://{HOST}/api/stats'),
    ('API Key', f'https://{HOST}/api/verify_api_http'),
    ('学习日志', f'https://{HOST}/api/learning_log'),
    ('系统日志', f'https://{HOST}/api/system_log'),
    ('前端首页', f'https://{HOST}/'),
    ('决策链', f'https://{HOST}/api/decision_chain/benchmark'),
]

for name, url in tests:
    try:
        r = requests.get(url, timeout=15, verify=False)
        ok = r.status_code == 200
        content = r.text[:120].replace('\n', ' ')
        print(f'  [{"OK" if ok else "FAIL"} {r.status_code}] {name}: {content}')
    except Exception as e:
        print(f'  [ERR] {name}: {str(e)[:80]}')

# ========== 11. DNS提醒 ==========
print('\n' + '=' * 60)
print('11. DNS配置提醒')
print(f'  域名: {DOMAIN}')
print(f'  服务器IP: {HOST}')
print('  请在腾讯云DNS控制台添加A记录:')
print(f'    @   -> A -> {HOST}')
print(f'    www -> A -> {HOST}')
print('  DNS生效后访问: https://firefightai.top')
print('=' * 60)
print('部署完成!')
print('=' * 60)