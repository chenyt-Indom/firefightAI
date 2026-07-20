"""Install PIL on server and verify"""
import paramiko

host = '139.199.69.88'
user = 'ubuntu'
key = paramiko.RSAKey.from_private_key_file(r'D:\firefightAI2.pem')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, pkey=key, timeout=10)

# Install PIL
print('Installing Pillow...')
stdin, stdout, stderr = ssh.exec_command('pip3 install Pillow --break-system-packages -q 2>&1', timeout=60)
print(stdout.read().decode().strip())
print(stderr.read().decode().strip())

# Verify
stdin, stdout, stderr = ssh.exec_command('python3 -c "from PIL import Image; print(\'PIL OK\')" 2>&1', timeout=10)
print('Verify:', stdout.read().decode().strip())

# Check main page
print('\n=== Main page check ===')
stdin, stdout, stderr = ssh.exec_command("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/", timeout=10)
print('HTTP Status:', stdout.read().decode().strip())

# Restart server to pick up PIL
print('\nRestarting server...')
stdin, stdout, stderr = ssh.exec_command('pkill -f dashboard_server.py 2>/dev/null; sleep 2; echo KILLED', timeout=10)
print(stdout.read().decode().strip())

start_cmd = "cd /home/ubuntu/firefightAI && python3 dashboard_server.py --host 0.0.0.0 --port 5000 2>&1 | tee /tmp/dashboard.log"
stdin, stdout, stderr = ssh.exec_command(f'screen -dmS firefight bash -c "{start_cmd}"; echo STARTED', timeout=10)
print(stdout.read().decode().strip())

ssh.close()
print('Done!')