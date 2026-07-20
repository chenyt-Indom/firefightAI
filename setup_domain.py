"""部署 firefightai.top 域名到腾讯云服务器"""
import paramiko, time, os

host = '139.199.69.88'
user = 'ubuntu'
key = paramiko.RSAKey.from_private_key_file(r'D:\firefightAI2.pem')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, pkey=key, timeout=10)

def run(cmd, timeout=30):
    """执行远程命令并返回输出"""
    print(f'[RUN] {cmd[:80]}...')
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return out, err

print('=' * 60)
print('1. 检查服务器环境')
print('=' * 60)

out, err = run('which nginx 2>/dev/null && nginx -v 2>&1 || echo NGINX_NOT_FOUND')
print(f'Nginx: {out}')

out, err = run('which certbot 2>/dev/null && certbot --version 2>&1 || echo CERTBOT_NOT_FOUND')
print(f'Certbot: {out}')

out, err = run('nslookup firefightai.top 2>/dev/null || echo DNS_CHECK_FAILED')
print(f'DNS: {out[:300]}')

out, err = run('curl -s http://127.0.0.1:5000/api/version 2>/dev/null || echo SERVER_DOWN')
print(f'Flask: {out[:200]}')

print()
print('=' * 60)
print('2. 安装/配置 Nginx')
print('=' * 60)

out, err = run('which nginx 2>/dev/null')
if not out:
    print('安装 Nginx...')
    out, err = run('sudo apt-get update -qq && sudo apt-get install -y -qq nginx 2>&1', timeout=120)
    print(out[-500:] if out else err[-500:])
else:
    print('Nginx 已安装')

# 创建 Nginx 配置文件
nginx_config = '''server {
    listen 80;
    server_name firefightai.top www.firefightai.top;

    # 前端页面和API
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

    # WebSocket (SocketIO)
    location /socket.io/ {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }

    # 静态文件
    location /static/ {
        alias /home/ubuntu/firefightAI/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
'''

# 写入配置文件
run('sudo tee /etc/nginx/sites-available/firefightai > /dev/null << \'EOF\'\n' + nginx_config + '\nEOF', timeout=10)
print('Nginx 配置已写入')

# 启用站点
out, err = run('sudo ln -sf /etc/nginx/sites-available/firefightai /etc/nginx/sites-enabled/ 2>&1')
print(f'启用站点: {out or err}')

# 删除默认站点
run('sudo rm -f /etc/nginx/sites-enabled/default 2>/dev/null')

# 测试配置
out, err = run('sudo nginx -t 2>&1')
print(f'Nginx 配置测试: {out or err}')

# 重新加载 Nginx
out, err = run('sudo systemctl reload nginx 2>&1 || sudo nginx -s reload 2>&1')
print(f'Nginx 重载: {out or err}')

print()
print('=' * 60)
print('3. 安装/配置 SSL 证书 (Let\'s Encrypt)')
print('=' * 60)

out, err = run('which certbot 2>/dev/null')
if not out:
    print('安装 Certbot...')
    out, err = run('sudo apt-get install -y -qq certbot python3-certbot-nginx 2>&1', timeout=120)
    print(out[-300:] if out else err[-300:])
else:
    print('Certbot 已安装')

# 申请 SSL 证书
print('申请 SSL 证书（需要域名已解析到本服务器IP）...')
out, err = run('sudo certbot --nginx -d firefightai.top -d www.firefightai.top --non-interactive --agree-tos --email admin@firefightai.top --redirect 2>&1', timeout=120)
print(f'SSL 结果: {out[:500] if out else err[:500]}')

# 设置自动续期
out, err = run('sudo systemctl enable certbot.timer 2>&1; sudo systemctl start certbot.timer 2>&1')
print(f'Certbot 自动续期: {out or err}')

print()
print('=' * 60)
print('4. 配置防火墙')
print('=' * 60)

for port in ['80', '443', '5000']:
    out, err = run(f'sudo ufw allow {port}/tcp 2>&1')
    print(f'开放端口 {port}: {out or err}')

out, err = run('sudo ufw --force enable 2>&1')
print(f'防火墙启用: {out or err}')

print()
print('=' * 60)
print('5. 部署最新代码')
print('=' * 60)

# 上传文件
sftp = ssh.open_sftp()
files = [
    ('d:/firefightAI/zhanluxt/dashboard_server.py', '/home/ubuntu/firefightAI/dashboard_server.py'),
    ('d:/firefightAI/zhanluxt/config/settings.yaml', '/home/ubuntu/firefightAI/config/settings.yaml'),
]
for local, remote in files:
    try:
        sftp.put(local, remote)
        print(f'  上传: {remote.split("/")[-1]}')
    except Exception as e:
        print(f'  失败: {remote.split("/")[-1]}: {e}')
sftp.close()

# 重启 Flask 服务
run('pkill -f dashboard_server.py 2>/dev/null; sleep 2; echo KILLED')
start_cmd = "cd /home/ubuntu/firefightAI && python3 dashboard_server.py --host 0.0.0.0 --port 5000 2>&1 | tee /tmp/dashboard.log"
run(f'screen -dmS firefight bash -c "{start_cmd}"; echo STARTED')
time.sleep(5)

print()
print('=' * 60)
print('6. 验证部署')
print('=' * 60)

# 验证 HTTP
out, err = run('curl -s http://127.0.0.1:5000/api/version 2>/dev/null')
print(f'Flask API: {out[:200]}')

# 验证 Nginx 代理
out, err = run('curl -s -o /dev/null -w "%{http_code}" http://localhost/ 2>/dev/null')
print(f'HTTP 首页: {out}')

# 检查端口
out, err = run('ss -tlnp | grep -E ":(80|443|5000) "')
print(f'端口监听:\n{out}')

# 检查 Nginx 状态
out, err = run('sudo systemctl status nginx --no-pager 2>&1 | head -5')
print(f'Nginx 状态: {out}')

ssh.close()
print()
print('=' * 60)
print('部署完成!')
print(f'访问地址: http://firefightai.top')
print(f'安全访问: https://firefightai.top')
print('=' * 60)