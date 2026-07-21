"""配置自签名SSL证书并验证API"""
import paramiko, time

host = '139.199.69.88'
user = 'ubuntu'
key = paramiko.RSAKey.from_private_key_file(r'D:\firefightAI2.pem')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, pkey=key, timeout=10)

def run(cmd, timeout=30):
    print(f'[RUN] {cmd[:80]}...')
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return out, err

print('=' * 60)
print('1. 生成自签名SSL证书（临时，DNS解析后替换为Let\'s Encrypt）')
print('=' * 60)

run('sudo mkdir -p /etc/nginx/ssl')
run('sudo openssl req -x509 -nodes -days 90 -newkey rsa:2048 '
    '-keyout /etc/nginx/ssl/firefightai.key '
    '-out /etc/nginx/ssl/firefightai.crt '
    '-subj "/C=CN/ST=Guangdong/L=Shenzhen/O=FirefightAI/CN=firefightai.top" 2>&1')

# 更新 Nginx 配置添加 SSL
nginx_ssl_config = '''server {
    listen 80;
    server_name firefightai.top www.firefightai.top 139.199.69.88;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name firefightai.top www.firefightai.top 139.199.69.88;

    ssl_certificate /etc/nginx/ssl/firefightai.crt;
    ssl_certificate_key /etc/nginx/ssl/firefightai.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

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

run('sudo tee /etc/nginx/sites-available/firefightai > /dev/null << \'EOF\'\n' + nginx_ssl_config + '\nEOF')
out, err = run('sudo nginx -t 2>&1')
print(f'Nginx 配置测试: {out or err}')
run('sudo systemctl reload nginx 2>&1 || sudo nginx -s reload 2>&1')

print()
print('=' * 60)
print('2. 验证 API 接口')
print('=' * 60)

apis = [
    ('版本信息', 'http://127.0.0.1:5000/api/version'),
    ('系统状态', 'http://127.0.0.1:5000/api/stats'),
    ('学习日志', 'http://127.0.0.1:5000/api/learning_log'),
    ('系统日志', 'http://127.0.0.1:5000/api/system_log'),
    ('GPU状态', 'http://127.0.0.1:5000/api/gpu/status'),
    ('数据集', 'http://127.0.0.1:5000/api/datasets'),
    ('模型列表', 'http://127.0.0.1:5000/api/models'),
    ('ADB状态', 'http://127.0.0.1:5000/api/adb/status'),
    ('GitHub状态', 'http://127.0.0.1:5000/api/github/status'),
    ('服务器状态', 'http://127.0.0.1:5000/api/server/status'),
    ('参数列表', 'http://127.0.0.1:5000/api/params/list'),
    ('知识库', 'http://127.0.0.1:5000/api/web/knowledge'),
    ('前端首页', '-k http://localhost/'),
    ('前端首页(HTTPS)', '-k https://localhost/'),
]

for name, url in apis:
    out, err = run(f'curl -s -o /dev/null -w "%{{http_code}}" {url} 2>/dev/null', timeout=15)
    code = out.strip()
    status = 'OK' if code in ('200', '301', '302') else f'FAIL({code})'
    print(f'  [{status}] {name}: {code}')

print()
print('=' * 60)
print('3. 验证 API Key (DeepSeek)')
print('=' * 60)

out, err = run('curl -s http://127.0.0.1:5000/api/verify_api_http 2>/dev/null')
print(f'API Key 验证: {out[:300]}')

print()
print('=' * 60)
print('4. 验证决策链')
print('=' * 60)

out, err = run('curl -s http://127.0.0.1:5000/api/decision_chain/benchmark 2>/dev/null')
print(f'决策链基准: {out[:500]}')

print()
print('=' * 60)
print('5. 通过公网IP验证前端')
print('=' * 60)

# 通过公网IP访问
out, err = run('curl -s -o /dev/null -w "%{http_code}" http://139.199.69.88/ 2>/dev/null')
print(f'HTTP 公网访问: {out}')

out, err = run('curl -s -k -o /dev/null -w "%{http_code}" https://139.199.69.88/ 2>/dev/null')
print(f'HTTPS 公网访问: {out}')

# 检查前端HTML是否包含关键功能
out, err = run('curl -s http://139.199.69.88/ 2>/dev/null | head -c 500')
print(f'前端HTML: {out[:300]}...')

out, err = run(r'curl -s http://139.199.69.88/ 2>/dev/null | grep -oE "socket.io|chart.umd|dashboard|emulator|github|training" | sort -u')
print(f'前端功能模块: {out}')

print()
print('=' * 60)
print('6. 验证通过Nginx代理的API')
print('=' * 60)

for name, url in apis[:5]:
    nginx_url = url.replace('127.0.0.1:5000', '139.199.69.88')
    out, err = run(f'curl -s -o /dev/null -w "%{{http_code}}" {nginx_url} 2>/dev/null', timeout=15)
    code = out.strip()
    status = 'OK' if code in ('200', '301', '302') else f'FAIL({code})'
    print(f'  [{status}] {name} (via Nginx): {code}')

ssh.close()
print()
print('=' * 60)
print('验证完成!')
print('=' * 60)