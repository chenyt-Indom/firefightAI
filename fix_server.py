"""修复服务器 - 查看日志并上传最新代码"""
import paramiko, time

host = '139.199.69.88'
user = 'ubuntu'
key = paramiko.RSAKey.from_private_key_file(r'D:\firefightAI2.pem')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, pkey=key, timeout=10)

# Check error log
print("=== Server Log (last 30 lines) ===")
_, stdout, _ = ssh.exec_command("tail -30 /tmp/dashboard.log 2>/dev/null", timeout=10)
print(stdout.read().decode())

# Upload latest files
print("\n=== Uploading files ===")
sftp = ssh.open_sftp()
files_to_upload = [
    ('d:/firefightAI/zhanluxt/dashboard_server.py', '/home/ubuntu/firefightAI/dashboard_server.py'),
    ('d:/firefightAI/zhanluxt/config/settings.yaml', '/home/ubuntu/firefightAI/config/settings.yaml'),
    ('d:/firefightAI/zhanluxt/src/learning/auto_scheduler.py', '/home/ubuntu/firefightAI/src/learning/auto_scheduler.py'),
]
for local, remote in files_to_upload:
    try:
        sftp.put(local, remote)
        print(f"  Uploaded: {remote.split('/')[-1]}")
    except Exception as e:
        print(f"  Failed: {remote.split('/')[-1]}: {e}")
sftp.close()

# Restart
print("\n=== Restarting ===")
_, stdout, _ = ssh.exec_command("pkill -f dashboard_server.py 2>/dev/null; sleep 1; echo killed", timeout=10)
print(stdout.read().decode().strip())

_, stdout, _ = ssh.exec_command("screen -dmS firefight bash -c 'cd /home/ubuntu/firefightAI && python3 dashboard_server.py 2>&1 | tee /tmp/dashboard.log'; echo STARTED", timeout=10)
print(stdout.read().decode().strip())

time.sleep(5)

# Verify
print("\n=== Verify ===")
_, stdout, _ = ssh.exec_command("curl -s http://127.0.0.1:5000/api/version 2>/dev/null", timeout=10)
out = stdout.read().decode().strip()
print("API:", out[:300] if out else "no response")

if not out or "500" in out or "error" in out.lower():
    print("\n=== Error Log ===")
    _, stdout, _ = ssh.exec_command("tail -20 /tmp/dashboard.log 2>/dev/null", timeout=10)
    print(stdout.read().decode())

ssh.close()
print("\nDone!")