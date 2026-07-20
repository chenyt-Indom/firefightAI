"""检查并修复服务器"""
import paramiko, os

host = '139.199.69.88'
user = 'ubuntu'
key_path = r'D:\firefightAI2.pem'

key = paramiko.RSAKey.from_private_key_file(key_path)
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, pkey=key, timeout=10)

# 检查当前状态
stdin, stdout, stderr = ssh.exec_command('''
echo "=== DASHBOARD ==="
ps aux | grep dashboard_server | grep -v grep || echo "NOT_RUNNING"
echo "=== NGINX ==="
sudo nginx -t 2>&1
echo "=== NGINX STATUS ==="
sudo systemctl status nginx 2>&1 | head -5
echo "=== PORTS ==="
sudo ss -tlnp 2>/dev/null | grep -E "5000|80|443|3000" || echo "no_listening"
echo "=== FIREWALL ==="
sudo ufw status 2>/dev/null | head -5 || echo "no_ufw"
echo "=== CURL LOCAL ==="
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/ 2>/dev/null || echo "no_response"
''')
print(stdout.read().decode())
ssh.close()