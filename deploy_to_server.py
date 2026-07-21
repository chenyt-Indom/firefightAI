"""部署更新到腾讯云服务器"""
import paramiko, time

host = '139.199.69.88'
user = 'ubuntu'
key = paramiko.RSAKey.from_private_key_file(r'D:\firefightAI2.pem')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, pkey=key, timeout=10)

# 1. Upload files
print('=== Uploading files ===')
sftp = ssh.open_sftp()
files = [
    ('d:/firefightAI/zhanluxt/dashboard_server.py', '/home/ubuntu/firefightAI/dashboard_server.py'),
    ('d:/firefightAI/zhanluxt/config/settings.yaml', '/home/ubuntu/firefightAI/config/settings.yaml'),
    ('d:/firefightAI/zhanluxt/src/learning/auto_scheduler.py', '/home/ubuntu/firefightAI/src/learning/auto_scheduler.py'),
]
for local, remote in files:
    try:
        sftp.put(local, remote)
        print(f'  Uploaded: {remote.split("/")[-1]}')
    except Exception as e:
        print(f'  Failed: {remote.split("/")[-1]}: {e}')
sftp.close()

# 2. Install PIL if needed
print('\n=== Checking PIL ===')
stdin, stdout, stderr = ssh.exec_command('python3 -c "from PIL import Image; print(\'PIL OK\')" 2>&1', timeout=10)
pil_out = stdout.read().decode().strip()
print(pil_out)
if 'PIL OK' not in pil_out:
    print('Installing Pillow...')
    stdin, stdout, stderr = ssh.exec_command('pip3 install Pillow -q 2>&1', timeout=60)
    print(stdout.read().decode().strip())

# 3. Kill old process
print('\n=== Killing old process ===')
stdin, stdout, stderr = ssh.exec_command('pkill -f dashboard_server.py 2>/dev/null; sleep 2; echo KILLED', timeout=10)
print(stdout.read().decode().strip())

# 4. Start new process with --host 0.0.0.0
print('\n=== Starting new server ===')
start_cmd = "cd /home/ubuntu/firefightAI && python3 dashboard_server.py --host 0.0.0.0 --port 5000 2>&1 | tee /tmp/dashboard.log"
stdin, stdout, stderr = ssh.exec_command(f'screen -dmS firefight bash -c "{start_cmd}"; echo STARTED', timeout=10)
print(stdout.read().decode().strip())

time.sleep(5)

# 5. Verify
print('\n=== Verify ===')
stdin, stdout, stderr = ssh.exec_command('curl -s http://127.0.0.1:5000/api/version 2>/dev/null', timeout=10)
out = stdout.read().decode().strip()
print('API:', out[:500] if out else 'no response')

if not out or '500' in out or 'error' in out.lower():
    print('\n=== Error Log ===')
    stdin, stdout, stderr = ssh.exec_command('tail -20 /tmp/dashboard.log 2>/dev/null', timeout=10)
    print(stdout.read().decode())

# 6. Check port binding
print('\n=== Port binding ===')
stdin, stdout, stderr = ssh.exec_command('ss -tlnp | grep 5000', timeout=10)
print(stdout.read().decode().strip())

ssh.close()
print('\nDone!')