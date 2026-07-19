#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
部署模型到远程服务器 (腾讯云 139.199.69.88)
"""
import subprocess, sys, os, json, tarfile, shutil
from pathlib import Path

PROJECT = Path(__file__).parent.parent.resolve()
SERVER = "139.199.69.88"
REMOTE_DIR = "/opt/firefight_models"

def main():
    user = input("SSH用户名 [root]: ").strip() or "root"
    password = input("SSH密码: ").strip()
    port = input("SSH端口 [22]: ").strip() or "22"
    
    if not password:
        print("❌ 需要SSH密码")
        return
    
    # 1. 打包 models_registry
    print("\n📦 打包模型...")
    registry = PROJECT / "models_registry"
    tarball = PROJECT / "models_registry.tar.gz"
    
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(registry, arcname="models_registry")
    size_mb = tarball.stat().st_size / (1024*1024)
    print(f"   ✅ models_registry.tar.gz ({size_mb:.1f}MB)")
    
    # 2. 上传 (用 sshpass + scp, 或 expect)
    print(f"\n📤 上传到 {user}@{SERVER}:{port}...")
    
    # 尝试 sshpass
    sshpass = shutil.which("sshpass")
    if sshpass:
        cmd = [
            sshpass, "-p", password,
            "scp", "-P", port, "-o", "StrictHostKeyChecking=no",
            str(tarball),
            f"{user}@{SERVER}:/tmp/models_registry.tar.gz"
        ]
    else:
        # 用 paramiko (纯Python)
        print("   sshpass 未安装, 使用 Python paramiko...")
        try:
            import paramiko
            transport = paramiko.Transport((SERVER, int(port)))
            transport.connect(username=user, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            remote_tmp = "/tmp/models_registry.tar.gz"
            sftp.put(str(tarball), remote_tmp)
            print(f"   ✅ 已上传 {size_mb:.1f}MB")
            
            # 解压
            stdin, stdout, stderr = transport.open_session()
            stdin.get_pty = lambda: None
            cmds = [
                f"mkdir -p {REMOTE_DIR}",
                f"cd {REMOTE_DIR} && tar xzf /tmp/models_registry.tar.gz --strip-components=1",
                f"rm /tmp/models_registry.tar.gz",
                f"ls -lh {REMOTE_DIR}/",
            ]
            for c in cmds:
                print(f"   🔧 {c}")
                channel = transport.open_session()
                channel.exec_command(c)
                channel.recv_exit_status()
                
            transport.close()
            print(f"\n✅ 部署完成! 模型位置: {REMOTE_DIR}/")
            
        except ImportError:
            print("   ❌ 需要 paramiko: pip install paramiko")
            print(f"   或手动上传: scp {tarball} {user}@{SERVER}:{REMOTE_DIR}/")
            return
        except Exception as e:
            print(f"   ❌ 连接失败: {e}")
            return
    
    if sshpass:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("   ✅ 上传成功")
            # 远程解压
            scp_cmd = [
                sshpass, "-p", password,
                "ssh", "-p", port, "-o", "StrictHostKeyChecking=no",
                f"{user}@{SERVER}",
                f"mkdir -p {REMOTE_DIR} && cd {REMOTE_DIR} && tar xzf /tmp/models_registry.tar.gz --strip-components=1 && rm /tmp/models_registry.tar.gz && ls -lh {REMOTE_DIR}/"
            ]
            subprocess.run(scp_cmd, text=True)
            print(f"\n✅ 部署完成! 模型位置: {REMOTE_DIR}/")
        else:
            print(f"   ❌ 上传失败: {result.stderr}")
    
    # 3. 创建远程加载器
    print(f"\n📋 在远程 project 中使用:")
    print(f"   from model_loader import load_model")
    print(f"   model, info = load_model('faction_30')")
    
    # 清理本地 tar
    tarball.unlink()

if __name__ == "__main__":
    main()
