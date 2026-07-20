"""Verify server endpoints"""
import paramiko

host = '139.199.69.88'
user = 'ubuntu'
key = paramiko.RSAKey.from_private_key_file(r'D:\firefightAI2.pem')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, pkey=key, timeout=10)

# Verify all endpoints
import time

def check(url, desc):
    stdin, stdout, stderr = ssh.exec_command(f'curl -s -o /dev/null -w "%{{http_code}}" {url} --max-time 5 2>/dev/null', timeout=10)
    code = stdout.read().decode().strip()
    print(f'  {desc}: HTTP {code}')

print('=== Endpoint Verification ===')
check('http://127.0.0.1:5000/', 'Home page')
check('http://127.0.0.1:5000/api/version', 'API Version')
check('http://127.0.0.1:5000/api/emulator/type', 'Emulator Type')
check('http://127.0.0.1:5000/api/emulator/screen', 'Screen Toggle')
check('http://127.0.0.1:5000/api/emulator/status', 'Emulator Status')
check('http://127.0.0.1:5000/api/emulator/screenshot', 'Screenshot')
check('http://127.0.0.1:5000/api/scrcpy/status', 'Scrcpy Status')

# Check port binding
stdin, stdout, stderr = ssh.exec_command('ss -tlnp | grep 5000', timeout=10)
print('\nPort binding:', stdout.read().decode().strip())

# Check log for errors
stdin, stdout, stderr = ssh.exec_command('tail -5 /tmp/dashboard.log', timeout=10)
print('\nLast log lines:')
print(stdout.read().decode().strip())

ssh.close()
print('\nVerification complete!')