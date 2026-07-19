"""Deploy firefightAI to server - update existing deployment"""
import paramiko
import os
import sys
import time
from pathlib import Path

SERVER_HOST = "139.199.69.88"
SERVER_USER = "ubuntu"
SSH_KEY_PATH = r"D:\firefightAI2.pem"
SSH_PASSWORD = "@Cyt20080102"
PROJECT_ROOT = Path(__file__).parent

def ssh_connect():
    """Connect to server"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    if Path(SSH_KEY_PATH).exists():
        for key_class in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
            try:
                key = key_class.from_private_key_file(SSH_KEY_PATH)
                client.connect(SERVER_HOST, username=SERVER_USER, pkey=key, timeout=10)
                print(f"[OK] Connected with {key_class.__name__}")
                return client
            except:
                continue
    
    try:
        client.connect(SERVER_HOST, username=SERVER_USER, password=SSH_PASSWORD, timeout=10, look_for_keys=False, allow_agent=False)
        print("[OK] Connected with password")
        return client
    except Exception as e:
        print(f"[FAIL] Connection failed: {e}")
        return None

def ssh_exec(client, cmd, timeout=30):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode()
    err = stderr.read().decode()
    return out, err

def main():
    print("=" * 60)
    print("  Firefight AI - Update Server Deployment")
    print(f"  Server: {SERVER_HOST}")
    print("=" * 60)
    
    client = ssh_connect()
    if not client:
        sys.exit(1)
    
    try:
        # 1. Check current state
        print("\n[1/5] Checking current deployment...")
        out, err = ssh_exec(client, "ls -la /home/ubuntu/ | grep -E 'firefight|dashboard' ; echo '---' ; cat /home/ubuntu/dashboard_cloud.py 2>/dev/null | head -5 || echo 'no dashboard_cloud.py'")
        print(out)
        
        # 2. Create deploy directory
        print("\n[2/5] Setting up deploy directory...")
        ssh_exec(client, "mkdir -p /home/ubuntu/firefightAI/src /home/ubuntu/firefightAI/config /home/ubuntu/firefightAI/data/params /home/ubuntu/firefightAI/models")
        
        # 3. Upload files via SFTP
        print("\n[3/5] Uploading files...")
        sftp = client.open_sftp()
        
        # Upload dashboard_server.py
        local_file = str(PROJECT_ROOT / "dashboard_server.py")
        remote_file = "/home/ubuntu/firefightAI/dashboard_server.py"
        print(f"  Uploading {local_file} -> {remote_file}")
        sftp.put(local_file, remote_file)
        
        # Upload config
        local_config = str(PROJECT_ROOT / "config" / "settings.yaml")
        remote_config = "/home/ubuntu/firefightAI/config/settings.yaml"
        if Path(local_config).exists():
            print(f"  Uploading config -> {remote_config}")
            sftp.put(local_config, remote_config)
        
        # Upload requirements.txt
        local_req = str(PROJECT_ROOT / "requirements.txt")
        remote_req = "/home/ubuntu/firefightAI/requirements.txt"
        if Path(local_req).exists():
            print(f"  Uploading requirements -> {remote_req}")
            sftp.put(local_req, remote_req)
        
        # Upload src directory
        local_src = PROJECT_ROOT / "src"
        if local_src.exists():
            print("  Uploading src/ directory...")
            for root, dirs, files in os.walk(str(local_src)):
                for d in dirs:
                    remote_dir = f"/home/ubuntu/firefightAI/src/{os.path.relpath(os.path.join(root, d), str(local_src)).replace(os.sep, '/')}"
                    try:
                        sftp.mkdir(remote_dir)
                    except:
                        pass
                for f in files:
                    local_path = os.path.join(root, f)
                    remote_path = f"/home/ubuntu/firefightAI/src/{os.path.relpath(local_path, str(local_src)).replace(os.sep, '/')}"
                    try:
                        sftp.put(local_path, remote_path)
                    except Exception as e:
                        print(f"    Skip {f}: {e}")
        
        sftp.close()
        print("[OK] Files uploaded")
        
        # 4. Install dependencies
        print("\n[4/5] Installing dependencies...")
        out, err = ssh_exec(client, "cd /home/ubuntu/firefightAI && /home/ubuntu/firefight_env/bin/pip install flask flask-socketio paramiko pyyaml loguru requests python-socketio -q 2>&1 | tail -5", timeout=120)
        print(out[:300])
        
        # 5. Restart dashboard
        print("\n[5/5] Restarting dashboard server...")
        
        # Kill existing dashboard processes
        out, _ = ssh_exec(client, "ps aux | grep dashboard | grep -v grep")
        print(f"  Existing processes:\n{out}")
        
        ssh_exec(client, "pkill -f 'dashboard_cloud.py' 2>/dev/null; pkill -f 'dashboard_server.py' 2>/dev/null; sleep 2")
        
        # Find available port
        out, _ = ssh_exec(client, "python3 -c \"import socket; s=socket.socket(); s.settimeout(1); [print(f'OCCUPIED:{p}') if s.connect_ex(('127.0.0.1',p))==0 else None for p in range(5000,5010)]; s.close()\"")
        occupied = [int(l.split(':')[1]) for l in out.split('\n') if 'OCCUPIED:' in l]
        print(f"  Occupied ports: {occupied}")
        
        free_port = 5000
        for p in range(5000, 5010):
            if p not in occupied:
                free_port = p
                break
        
        print(f"  Using port: {free_port}")
        
        # Start the server
        start_cmd = f"cd /home/ubuntu/firefightAI && nohup /home/ubuntu/firefight_env/bin/python dashboard_server.py --host 0.0.0.0 --port {free_port} > /home/ubuntu/firefightAI/server.log 2>&1 &"
        print(f"  Start command: {start_cmd}")
        ssh_exec(client, start_cmd)
        time.sleep(3)
        
        # Verify
        out, err = ssh_exec(client, f"ss -tlnp | grep {free_port}; echo '---'; ps aux | grep dashboard_server | grep -v grep; echo '---'; tail -10 /home/ubuntu/firefightAI/server.log 2>/dev/null")
        print(f"\n[RESULT]\n{out}")
        
        if str(free_port) in out:
            print(f"\n[SUCCESS] Dashboard deployed!")
            print(f"  Server URL: http://{SERVER_HOST}:{free_port}")
            print(f"  GitHub: https://github.com/chenyt-Indom/firefightAI")
        else:
            print(f"\n[WARN] Server may not have started properly")
            print(f"  Check logs at: /home/ubuntu/firefightAI/server.log")
        
    finally:
        client.close()

if __name__ == "__main__":
    main()