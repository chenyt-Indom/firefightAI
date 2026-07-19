#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Firefight AI 战术指挥系统 — 桌面启动器
一键启动: 自动检查环境 → 启动AI服务 → 打开控制面板
"""

import subprocess, sys, os, time, webbrowser, json, shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
PYTHON = sys.executable
PORT = 5000

# ── 颜色输出 ──
def green(s):  return f"\033[92m{s}\033[0m"
def yellow(s): return f"\033[93m{s}\033[0m"
def red(s):    return f"\033[91m{s}\033[0m"
def cyan(s):   return f"\033[96m{s}\033[0m"

def check_deps():
    """检查Python依赖"""
    required = ["flask", "flask_socketio", "ultralytics", "opencv_python", 
                 "numpy", "pydantic", "loguru", "pyyaml", "httpx", "openai"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            missing.append(pkg)
    
    if missing:
        print(yellow(f"⚠️ 缺少依赖: {', '.join(missing)}"))
        print(cyan("正在安装..."))
        for pkg in missing:
            subprocess.run([PYTHON, "-m", "pip", "install", pkg, "-q"], 
                         cwd=str(PROJECT_ROOT), check=False)
        print(green("✅ 依赖安装完成"))

def check_settings():
    """检查配置文件"""
    settings_path = PROJECT_ROOT / "config" / "settings.yaml"
    example_path = PROJECT_ROOT / "config" / "settings.yaml.example"
    
    if not settings_path.exists() and example_path.exists():
        shutil.copy(example_path, settings_path)
        print(yellow("⚠️ settings.yaml 已从模板创建，请配置DeepSeek API Key后重新启动"))
        print(yellow(f"   编辑: {settings_path}"))
        sys.exit(1)
    
    if settings_path.exists():
        content = settings_path.read_text(encoding="utf-8")
        if "sk-your-key" in content or "YOUR_" in content:
            print(red("❌ 请先配置 config/settings.yaml 中的 API Key!"))
            sys.exit(1)

def is_server_running():
    """检查服务是否已运行"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", PORT))
        s.close()
        return True
    except:
        return False

def main():
    os.chdir(str(PROJECT_ROOT))
    
    print(cyan("=" * 55))
    print(cyan("  🔥 Firefight AI 战术指挥系统 v2.0"))
    print(cyan("  桌面版 — 一键启动"))
    print(cyan("=" * 55))
    
    # 1. 检查环境
    print("\n📦 检查依赖...")
    check_deps()
    print(green("  ✅ 依赖就绪"))
    
    # 2. 检查配置
    print("⚙️  检查配置...")
    check_settings()
    print(green("  ✅ 配置就绪"))
    
    # 3. 检查服务
    if is_server_running():
        print(yellow("  ⚠️ 服务已在运行, 直接打开面板"))
    else:
        print("🚀 启动 AI 服务...")
        server_script = PROJECT_ROOT / "dashboard_server.py"
        subprocess.Popen(
            [PYTHON, str(server_script), "--port", str(PORT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        
        # 等待服务就绪
        for i in range(15):
            time.sleep(0.5)
            if is_server_running():
                print(green("  ✅ AI 服务已启动"))
                break
        else:
            print(red("  ❌ 服务启动超时"))
            sys.exit(1)
    
    # 4. 打开控制面板
    url = f"http://localhost:{PORT}"
    print(f"\n🌐 打开控制面板: {url}")
    time.sleep(0.5)
    webbrowser.open(url)
    
    # 5. 保持运行
    print(green("\n✅ Firefight AI 已上线！"))
    print(yellow("   关闭此窗口将停止 AI 服务"))
    print(cyan("   按 Ctrl+C 退出\n"))
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(yellow("\n👋 正在关闭..."))

if __name__ == "__main__":
    main()
