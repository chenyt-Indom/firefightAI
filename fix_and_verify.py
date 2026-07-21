"""修复服务器：开放SSH端口 + 重新部署Flask应用
需要通过腾讯云控制台安全组开放端口22和5000后运行
"""
import paramiko, time, sys

host = '139.199.69.88'
user = 'ubuntu'
key_path = r'D:\firefightAI2.pem'

print('=' * 60)
print('尝试连接服务器...')
print('=' * 60)

try:
    key = paramiko.RSAKey.from_private_key_file(key_path)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, pkey=key, timeout=15)
    print('SSH连接成功!')
except Exception as e:
    print(f'SSH连接失败: {e}')
    print()
    print('=' * 60)
    print('需要先在腾讯云控制台开放端口!')
    print('=' * 60)
    print('请按以下步骤操作:')
    print()
    print('步骤1: 登录腾讯云控制台 https://console.cloud.tencent.com/')
    print('步骤2: 进入"云服务器CVM" -> "安全组"')
    print('步骤3: 找到关联到 139.199.69.88 的安全组')
    print('步骤4: 添加入站规则:')
    print('   - 端口22 (SSH), 来源: 0.0.0.0/0')
    print('   - 端口5000 (Flask), 来源: 0.0.0.0/0')
    print('步骤5: 保存后重新运行此脚本')
    print()
    print('或者使用腾讯云"登录"功能(VNC)登录服务器后执行:')
    print('  sudo ufw allow 22/tcp')
    print('  sudo ufw allow 5000/tcp')
    print('=' * 60)
    sys.exit(1)

def run(cmd, timeout=30):
    print(f'[RUN] {cmd[:80]}...')
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if err:
        print(f'  STDERR: {err[:200]}')
    return out, err

print()
print('=' * 60)
print('1. 修复防火墙 - 开放SSH和Flask端口')
print('=' * 60)

run('sudo ufw allow 22/tcp 2>&1')
run('sudo ufw allow 5000/tcp 2>&1')
out, _ = run('sudo ufw status numbered')
print(f'防火墙状态:\n{out}')

print()
print('=' * 60)
print('2. 检查当前服务状态')
print('=' * 60)

out, _ = run('ss -tlnp | grep -E ":(22|80|443|5000) "')
print(f'端口监听:\n{out}')

out, _ = run('ps aux | grep -E "python|dashboard" | grep -v grep')
print(f'Python进程:\n{out}')

print()
print('=' * 60)
print('3. 上传最新代码')
print('=' * 60)

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

print()
print('=' * 60)
print('4. 重启Flask应用')
print('=' * 60)

# 杀掉旧进程
run('pkill -f dashboard_server.py 2>/dev/null; sleep 2; echo KILLED')

# 启动新进程
start_cmd = "cd /home/ubuntu/firefightAI && python3 dashboard_server.py --host 0.0.0.0 --port 5000 2>&1 | tee /tmp/dashboard.log"
run(f'screen -dmS firefight bash -c "{start_cmd}"; echo STARTED')
time.sleep(5)

print()
print('=' * 60)
print('5. 验证Flask应用')
print('=' * 60)

out, _ = run('curl -s http://127.0.0.1:5000/api/version 2>/dev/null')
print(f'Flask版本API: {out[:200]}')

out, _ = run('curl -s http://127.0.0.1:5000/api/verify_api_http 2>/dev/null')
print(f'API Key验证: {out[:200]}')

out, _ = run('curl -s http://127.0.0.1:5000/api/stats 2>/dev/null')
print(f'系统状态: {out[:200]}')

print()
print('=' * 60)
print('6. 验证Nginx代理')
print('=' * 60)

out, _ = run('curl -s -o /dev/null -w "%{http_code}" http://localhost/ 2>/dev/null')
print(f'Nginx HTTP: {out}')

out, _ = run('curl -s -o /dev/null -w "%{http_code}" https://localhost/ -k 2>/dev/null')
print(f'Nginx HTTPS: {out}')

out, _ = run('curl -s http://localhost/api/version 2>/dev/null')
print(f'通过Nginx的API: {out[:200]}')

out, _ = run('curl -s http://localhost/api/verify_api_http 2>/dev/null')
print(f'通过Nginx的API Key: {out[:200]}')

print()
print('=' * 60)
print('7. 验证决策链')
print('=' * 60)

out, _ = run('curl -s http://127.0.0.1:5000/api/decision_chain/benchmark 2>/dev/null')
print(f'决策链: {out[:500]}')

print()
print('=' * 60)
print('8. 检查端口监听')
print('=' * 60)

out, _ = run('ss -tlnp | grep -E ":(22|80|443|5000) "')
print(f'端口:\n{out}')

ssh.close()
print()
print('=' * 60)
print('修复完成!')
print(f'访问地址: http://139.199.69.88')
print(f'安全访问: https://139.199.69.88')
print('=' * 60)