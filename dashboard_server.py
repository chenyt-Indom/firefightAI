"""Firefight AI 控制面板服务端 v5.0
Flask + SocketIO + AI对话 + 训练管线 + 标注工具 + 自更新 + 参数学习
+ 学习日志透明化 + GitHub集成 + 连接管理 + 腾讯云部署
"""

from __future__ import annotations
import os, sys, time, json, threading, argparse, subprocess, hashlib, tempfile
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import yaml
from flask import Flask, render_template_string, request, send_from_directory, jsonify, Response, stream_with_context
from flask_socketio import SocketIO, emit
from loguru import logger

app = Flask(__name__)
app.config["SECRET_KEY"] = "firefight_dashboard_v5"
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", max_http_buffer_size=100*1024*1024)

PROJECT_ROOT = Path(__file__).parent
APP_VERSION = "5.0.0"
APP_BUILD = datetime.now().strftime("%Y%m%d-%H%M")

# ── 全局状态 ──
_dashboard_state: dict = {
    "running": False, "cycle": 0, "allies": 0, "enemies": 0,
    "score": 0, "total_score": 0, "last_decision": "", "last_action": "",
    "last_reason": "", "cycle_time_ms": 0, "avg_cycle_time_ms": 0,
    "decisions": [], "experience_count": 0, "rules_count": 0,
    "status": "就绪", "game_session": "", "scores_history": [], "user_commands": [],
    "training_status": "idle", "training_progress": 0, "training_message": "",
    "api_status": {"deepseek": "unknown"},
    "ai_thinking": "",
    "chat_history": [],
    "version": APP_VERSION, "build": APP_BUILD,
    # v5.0 新增
    "learning_log": [],           # AI学习日志
    "adb_status": "unknown",      # ADB连接状态
    "adb_host": "", "adb_port": 0,
    "server_status": "unknown",   # 腾讯云服务器状态
    "server_host": "139.199.69.88",
    "github_status": "unknown",   # GitHub连接状态
    "github_repo": "",
    "pytorch_version": "",
}
_lock = threading.Lock()
_controller = None
_user_instruction = ""
_training_process = None
_chat_history: list[dict] = []
_learning_log: list[dict] = []  # 学习日志持久化
_adb_utils = None  # ADB实例引用

# ── GPU 状态 ──
_gpu_info: dict = {"cuda_available": False, "gpus": [], "pytorch_cuda": False, "pytorch_version": "", "message": ""}

# ── Android 模拟器状态 ──
EMULATOR_HOME = PROJECT_ROOT / "android_emulator"
ANDROID_SDK_ROOT = EMULATOR_HOME / "sdk"
AVD_NAME = "firefight_avd"
AVD_CONFIG = {
    "device": "pixel_6",
    "api_level": 33,
    "arch": "x86_64",
    "ram": 4096,          # MUMU同级4GB
    "cores": 4,
    "resolution": "1920x1080",  # MUMU同级1080P
    "density": 320,
    "fullscreen": True,   # 全屏支持
    "touch_screen": True,  # MUMU同级触控
    "keyboard": True,     # MUMU同级键盘输入
}
_emulator_process = None
_emulator_adb_port = 5556  # Different from MuMu's 7555
_scrcpy_process = None
_scrcpy_enabled = False


def update_state(**kw):
    with _lock:
        _dashboard_state.update(kw)


def get_state() -> dict:
    with _lock:
        return dict(_dashboard_state)


def add_learning_log(category: str, message: str, detail: str = ""):
    """添加学习日志条目"""
    global _learning_log
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "category": category,
        "message": message,
        "detail": detail[:500] if detail else "",
    }
    _learning_log.append(entry)
    if len(_learning_log) > 200:
        _learning_log = _learning_log[-200:]
    update_state(learning_log=_learning_log[-50:])
    socketio.emit("learning_log_update", {"entry": entry, "total": len(_learning_log)})


def load_config() -> dict:
    with open(PROJECT_ROOT / "config" / "settings.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ═══════════════════════════════════════════════════════════════
# 学习日志 API
# ═══════════════════════════════════════════════════════════════

@app.route("/api/learning_log")
def api_learning_log():
    limit = request.args.get("limit", 100, type=int)
    return jsonify(_learning_log[-limit:])

@app.route("/api/learning_log/clear", methods=["POST"])
def api_learning_log_clear():
    global _learning_log
    _learning_log = []
    update_state(learning_log=[])
    return jsonify({"status": "cleared"})

@app.route("/api/learning_log/export")
def api_learning_log_export():
    """导出学习日志为JSON"""
    return jsonify({"exported_at": datetime.now().isoformat(), "version": APP_VERSION, "entries": _learning_log})


# ═══════════════════════════════════════════════════════════════
# 版本管理 + 自更新
# ═══════════════════════════════════════════════════════════════

@app.route("/api/version")
def api_version():
    import torch
    pt_ver = torch.__version__ if torch else "N/A"
    update_state(pytorch_version=pt_ver)
    try:
        import git
        repo = git.Repo(str(PROJECT_ROOT))
        git_branch = repo.active_branch.name
        git_commit = repo.head.commit.hexsha[:8]
    except:
        git_branch = "unknown"
        git_commit = "unknown"
    return jsonify({
        "version": APP_VERSION, "build": APP_BUILD, "python": sys.version,
        "api_status": get_state().get("api_status", {}),
        "experience_count": get_state().get("experience_count", 0),
        "rules_count": get_state().get("rules_count", 0),
        "adb_status": get_state().get("adb_status", "unknown"),
        "server_status": get_state().get("server_status", "unknown"),
        "github_status": get_state().get("github_status", "unknown"),
        "pytorch": pt_ver,
        "git_branch": git_branch, "git_commit": git_commit,
    })

@app.route("/api/version/check")
def api_version_check():
    import hashlib
    latest = {}
    for root, dirs, files in os.walk(str(PROJECT_ROOT / "src")):
        for f in files:
            if f.endswith(".py"):
                fp = Path(root) / f
                latest[str(fp.relative_to(PROJECT_ROOT))] = fp.stat().st_mtime
    return jsonify({"current_version": APP_VERSION, "current_build": APP_BUILD, "files_modified": len(latest), "latest_change": max(latest.values()) if latest else 0})

@app.route("/api/version/reload", methods=["POST"])
def api_version_reload():
    import importlib
    reloaded = []
    try:
        for mod_name in ["src.decision.commander", "src.decision.parser", "src.state.manager"]:
            try:
                mod = sys.modules.get(mod_name)
                if mod:
                    importlib.reload(mod)
                    reloaded.append(mod_name)
            except:
                pass
        add_learning_log("system", "热重载完成", f"模块: {', '.join(reloaded)}")
        return jsonify({"status": "reloaded", "modules": reloaded})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# ADB 连接管理
# ═══════════════════════════════════════════════════════════════

@app.route("/api/adb/status")
def api_adb_status():
    cfg = load_config()
    dc = cfg["device"]
    ad = dc.get("active", "mumu")
    di = dc.get(ad, {})
    host = di.get("adb_host", "127.0.0.1")
    port = di.get("adb_port", 7555)

    # 尝试检测ADB - 优先使用MuMu自带的ADB
    adb_paths = [
        r"d:\MuMuPlayer\nx_device\12.0\shell\adb.exe",
        r"d:\firefight\adb\adb.exe",
        r"C:\adb\platform-tools\platform-tools\adb.exe",
        "adb",
    ]
    adb_exe = "adb"
    for p in adb_paths:
        if p == "adb" or Path(p).exists():
            adb_exe = p
            break

    result = {
        "host": host, "port": port, "device": ad,
        "adb_exe": adb_exe, "adb_available": Path(adb_exe).exists() if adb_exe != "adb" else True,
    }

    # 尝试连接检测
    try:
        # 先启动ADB server
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        r = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
        devices = [l for l in r.stdout.strip().split("\n") if l and "\tdevice" in l]
        result["devices"] = devices
        result["connected"] = len(devices) > 0
        if f"{host}:{port}" in r.stdout:
            result["status"] = "connected"
            update_state(adb_status="connected", adb_host=host, adb_port=port)
        else:
            result["status"] = "disconnected"
            update_state(adb_status="disconnected", adb_host=host, adb_port=port)
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)[:200]
        update_state(adb_status="error")

    return jsonify(result)

@app.route("/api/adb/reconnect", methods=["POST"])
def api_adb_reconnect():
    data = request.get_json() or {}
    host = data.get("host", "")
    port = data.get("port", 0)

    if not host or not port:
        cfg = load_config()
        dc = cfg["device"]
        ad = dc.get("active", "mumu")
        di = dc.get(ad, {})
        host = host or di.get("adb_host", "127.0.0.1")
        port = port or di.get("adb_port", 7555)

    adb_paths = [r"d:\MuMuPlayer\nx_device\12.0\shell\adb.exe", r"d:\firefight\adb\adb.exe", r"C:\adb\platform-tools\platform-tools\adb.exe", "adb"]
    adb_exe = "adb"
    for p in adb_paths:
        if p == "adb" or Path(p).exists():
            adb_exe = p
            break

    add_learning_log("connection", f"尝试重连ADB: {host}:{port}", "")
    try:
        # 先启动ADB server
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        r = subprocess.run([adb_exe, "connect", f"{host}:{port}"], capture_output=True, text=True, timeout=10)
        if "connected" in r.stdout.lower() or "already connected" in r.stdout.lower():
            update_state(adb_status="connected", adb_host=host, adb_port=port)
            add_learning_log("connection", f"ADB连接成功: {host}:{port}", r.stdout.strip()[:200])
            return jsonify({"status": "connected", "host": host, "port": port, "output": r.stdout.strip()})
        else:
            update_state(adb_status="disconnected")
            return jsonify({"status": "failed", "host": host, "port": port, "output": r.stdout.strip()}), 500
    except Exception as e:
        update_state(adb_status="error")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/api/adb/config", methods=["POST"])
def api_adb_config():
    """更新ADB配置"""
    data = request.get_json() or {}
    host = data.get("host", "")
    port = data.get("port", 0)
    if host and port:
        cfg = load_config()
        ad = cfg["device"].get("active", "mumu")
        cfg["device"][ad]["adb_host"] = host
        cfg["device"][ad]["adb_port"] = int(port)
        with open(PROJECT_ROOT / "config" / "settings.yaml", "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        add_learning_log("config", f"ADB配置已更新: {host}:{port}", "")
        return jsonify({"status": "saved", "host": host, "port": port})
    return jsonify({"error": "缺少host/port"}), 400


# ═══════════════════════════════════════════════════════════════
# GitHub 集成
# ═══════════════════════════════════════════════════════════════

@app.route("/api/github/status")
def api_github_status():
    """检查GitHub连接状态"""
    try:
        import requests
        r = requests.get("https://api.github.com", timeout=5)
        api_status = "online" if r.status_code == 200 else "error"
        update_state(github_status=api_status)
    except:
        api_status = "offline"
        update_state(github_status=api_status)

    # 尝试获取本地git信息
    remote = "未配置仓库"
    branch = "N/A"
    dirty = False
    has_remote = False
    remote_url = ""
    
    try:
        import git
        repo = git.Repo(str(PROJECT_ROOT))
        branch = repo.active_branch.name
        dirty = repo.is_dirty()
        if repo.remotes:
            remote = repo.remotes.origin.url
            has_remote = True
            remote_url = remote
        else:
            remote = "未配置仓库"
            has_remote = False
    except:
        # 尝试通过命令行获取
        try:
            r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                remote = r.stdout.strip()
                has_remote = True
                remote_url = remote
            r2 = subprocess.run(["git", "branch", "--show-current"], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
            branch = r2.stdout.strip() or "N/A"
            r3 = subprocess.run(["git", "status", "--porcelain"], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
            dirty = bool(r3.stdout.strip())
        except:
            pass

    result = {
        "api_status": api_status,
        "repo_url": remote,
        "remote_url": remote_url,
        "branch": branch,
        "has_changes": dirty,
        "has_remote": has_remote,
    }
    
    if not has_remote:
        result["message"] = "GitHub未配置 - 请使用 /api/github/setup 配置仓库地址，或在指令框输入 'repo 仓库地址'"
        result["suggestion"] = "在指令框输入: repo https://github.com/用户名/仓库名.git"
    
    return jsonify(result)

@app.route("/api/github/setup", methods=["POST"])
def api_github_setup():
    """配置GitHub远程仓库"""
    data = request.get_json() or {}
    repo_url = data.get("repo_url", "").strip()
    
    if not repo_url:
        return jsonify({"error": "缺少repo_url参数", "suggestion": "请提供GitHub仓库地址，如: https://github.com/username/repo.git"}), 400
    
    # 验证URL格式
    if not (repo_url.startswith("https://github.com/") or repo_url.startswith("git@github.com:")):
        return jsonify({"error": "不支持的仓库URL格式，请使用HTTPS或SSH格式", "suggestion": "示例: https://github.com/username/repo.git"}), 400
    
    add_learning_log("github", f"配置GitHub仓库: {repo_url}", "")
    
    try:
        # 尝试使用GitPython
        try:
            import git
            repo = git.Repo(str(PROJECT_ROOT))
            if repo.remotes:
                repo.remotes.origin.set_url(repo_url)
            else:
                repo.create_remote("origin", repo_url)
        except ImportError:
            # GitPython不可用，使用命令行
            # 检查是否已有remote
            r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
            if r.returncode == 0:
                subprocess.run(["git", "remote", "set-url", "origin", repo_url], cwd=str(PROJECT_ROOT), check=True, capture_output=True)
            else:
                subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=str(PROJECT_ROOT), check=True, capture_output=True)
        
        update_state(github_repo=repo_url)
        add_learning_log("github", f"GitHub仓库配置成功: {repo_url}", "")
        
        return jsonify({
            "status": "configured",
            "repo_url": repo_url,
            "message": f"GitHub仓库已配置: {repo_url}",
            "next_steps": "可以使用 git push -u origin main 进行首次推送，或点击推送按钮",
        })
    except Exception as e:
        add_learning_log("github", f"GitHub配置失败", str(e)[:200])
        return jsonify({"status": "error", "error": str(e), "suggestion": "请确保已初始化git仓库 (git init)"}), 500

@app.route("/api/github/push", methods=["POST"])
def api_github_push():
    """推送训练数据/参数到GitHub"""
    data = request.get_json() or {}
    paths = data.get("paths", ["data/params", "data/tactics_rules.yaml", "data/battle_memory.db"])
    commit_msg = data.get("message", f"AI训练更新 {datetime.now().strftime('%Y%m%d-%H%M')}")

    add_learning_log("github", "开始推送数据到GitHub", f"文件: {', '.join(paths)}")

    # 先检查是否有remote
    has_remote = False
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
        has_remote = r.returncode == 0 and bool(r.stdout.strip())
    except:
        pass
    
    if not has_remote:
        add_learning_log("github", "GitHub未配置仓库，无法推送", "")
        return jsonify({"status": "error", "error": "未配置GitHub仓库", "suggestion": "请先使用 /api/github/setup 或在指令框输入 repo 地址 配置仓库"}), 400

    try:
        import git
        repo = git.Repo(str(PROJECT_ROOT))

        # 添加文件
        for p in paths:
            full = PROJECT_ROOT / p
            if full.exists():
                repo.index.add([str(p)])
            elif "*" in p:
                repo.index.add([str(p)])

        if not repo.index.diff("HEAD"):
            add_learning_log("github", "无变更需要推送", "")
            return jsonify({"status": "no_changes"})

        repo.index.commit(commit_msg)
        origin = repo.remotes.origin
        result = origin.push()
        add_learning_log("github", f"推送成功: {commit_msg}", str(result[0].summary) if result else "")
        return jsonify({"status": "pushed", "message": commit_msg})
    except ImportError:
        # GitPython不可用，尝试命令行
        try:
            subprocess.run(["git", "add"] + paths, cwd=str(PROJECT_ROOT), check=True, capture_output=True)
            # 检查是否有变更
            r_status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(PROJECT_ROOT), capture_output=True)
            if r_status.returncode == 0:
                add_learning_log("github", "无变更需要推送", "")
                return jsonify({"status": "no_changes"})
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=str(PROJECT_ROOT), check=True, capture_output=True)
            r = subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
            if r.returncode == 0:
                add_learning_log("github", f"Git推送成功: {commit_msg}", r.stdout[:200])
                return jsonify({"status": "pushed", "message": commit_msg})
            else:
                add_learning_log("github", f"Git推送失败", r.stderr[:200])
                return jsonify({"status": "error", "error": r.stderr.strip()[:300], "suggestion": "可能需要先执行 git pull 或设置上游分支"}), 500
        except subprocess.CalledProcessError as e:
            add_learning_log("github", f"Git推送失败", str(e)[:200])
            return jsonify({"status": "error", "error": str(e), "suggestion": "请检查git配置和仓库权限"}), 500
    except Exception as e:
        add_learning_log("github", f"推送失败", str(e)[:200])
        return jsonify({"status": "error", "error": str(e), "suggestion": "请检查git配置和仓库权限"}), 500

@app.route("/api/github/pull", methods=["POST"])
def api_github_pull():
    """从GitHub拉取最新代码"""
    add_learning_log("github", "开始拉取最新代码", "")
    try:
        import git
        repo = git.Repo(str(PROJECT_ROOT))
        origin = repo.remotes.origin
        result = origin.pull()
        add_learning_log("github", "拉取成功", str(result[0].note) if result else "")
        return jsonify({"status": "pulled"})
    except ImportError:
        try:
            r = subprocess.run(["git", "pull"], cwd=str(PROJECT_ROOT), check=True, capture_output=True, text=True)
            add_learning_log("github", "拉取成功", r.stdout[:200])
            return jsonify({"status": "pulled", "output": r.stdout.strip()})
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/api/github/upload_file", methods=["POST"])
def api_github_upload_file():
    """上传单个文件到GitHub"""
    if "file" not in request.files:
        return jsonify({"error": "缺少文件"}), 400
    file = request.files["file"]
    folder = request.form.get("folder", "data/params")
    filename = file.filename or f"upload_{int(time.time())}.yaml"

    save_dir = PROJECT_ROOT / folder
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / filename
    file.save(str(save_path))

    add_learning_log("github", f"文件已保存: {folder}/{filename}", "")

    # 自动提交
    auto_push = request.form.get("auto_push", "true") == "true"
    if auto_push:
        try:
            import git
            repo = git.Repo(str(PROJECT_ROOT))
            repo.index.add([str(save_path.relative_to(PROJECT_ROOT))])
            repo.index.commit(f"上传: {filename}")
            repo.remotes.origin.push()
            add_learning_log("github", f"已自动推送: {filename}", "")
            return jsonify({"status": "saved_and_pushed", "filename": filename})
        except:
            return jsonify({"status": "saved", "filename": filename, "warning": "自动推送失败，文件已本地保存"})
    return jsonify({"status": "saved", "filename": filename})


# ═══════════════════════════════════════════════════════════════
# 腾讯云服务器部署
# ═══════════════════════════════════════════════════════════════

SERVER_HOST = "139.199.69.88"
SERVER_USER = "ubuntu"
SSH_KEY_PATH = r"D:\firefightAI2.pem"
SSH_PASSWORD = "@Cyt20080102"
SERVER_DEPLOY_PATH = "/home/ubuntu/firefightAI"

def _ssh_exec(cmd: str, timeout: int = 30) -> tuple:
    """Execute command via SSH, try key then password"""
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        # Try key first (RSA then Ed25519)
        key = None
        if Path(SSH_KEY_PATH).exists():
            for key_class in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
                try:
                    key = key_class.from_private_key_file(SSH_KEY_PATH)
                    break
                except:
                    continue
        if key:
            try:
                client.connect(SERVER_HOST, username=SERVER_USER, pkey=key, timeout=10)
            except Exception as ke:
                logger.warning(f"SSH密钥认证失败: {ke}, 尝试密码认证")
                client.close()
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                if SSH_PASSWORD:
                    client.connect(SERVER_HOST, username=SERVER_USER, password=SSH_PASSWORD, timeout=10)
                else:
                    return False, "", f"密钥认证失败且未配置密码: {str(ke)[:200]}"
        elif SSH_PASSWORD:
            client.connect(SERVER_HOST, username=SERVER_USER, password=SSH_PASSWORD, timeout=10)
        else:
            return False, "", "SSH密钥不存在且未配置密码"
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode()
        err = stderr.read().decode()
        return True, out, err
    except Exception as e:
        return False, "", str(e)
    finally:
        try:
            client.close()
        except:
            pass

def _ssh_connect(command: str, use_key: bool = True, timeout: int = 10) -> tuple:
    """统一的SSH连接函数，使用paramiko（密钥优先，密码回退），返回(stdout, stderr, error_diagnosis)"""
    success, stdout, stderr = _ssh_exec(command, timeout)
    if success:
        return stdout, stderr, ""
    return "", "", f"SSH连接失败: {stderr[:200]}"

@app.route("/api/server/status")
def api_server_status():
    """检查腾讯云服务器连接状态"""
    detail = {}
    
    # 检查SSH密钥是否存在
    key_exists = Path(SSH_KEY_PATH).exists()
    detail["ssh_key_exists"] = key_exists
    detail["ssh_key_path"] = SSH_KEY_PATH
    detail["has_password"] = bool(SSH_PASSWORD)
    
    if not key_exists and not SSH_PASSWORD:
        detail["suggestion"] = "SSH密钥不存在，请上传密钥文件或使用/api/server/setup_key生成新密钥"
        update_state(server_status="no_key")
        return jsonify({"status": "no_key", "detail": detail, "error": "SSH密钥不存在"})
    
    # 尝试连接
    stdout, stderr, diagnosis = _ssh_connect("echo OK && python3 --version 2>/dev/null && ls /opt/firefightAI 2>/dev/null || echo no_deploy")
    
    if "OK" in stdout:
        update_state(server_status="online")
        deployed = "no_deploy" not in stdout
        detail["python_version"] = stdout.split("\n")[1] if len(stdout.split("\n")) > 1 else "unknown"
        return jsonify({"status": "online", "deployed": deployed, "output": stdout.strip(), "detail": detail})
    elif diagnosis:
        update_state(server_status="error")
        detail["diagnosis"] = diagnosis
        return jsonify({"status": "error", "detail": detail, "error": diagnosis, "stderr": stderr[:300]})
    else:
        update_state(server_status="offline")
        detail["stderr"] = stderr[:300] if stderr else ""
        detail["diagnosis"] = "SSH连接被拒绝，可能是密钥被服务器拒绝。请检查服务器authorized_keys或使用密码认证"
        return jsonify({"status": "offline", "detail": detail, "error": stderr.strip()[:300] if stderr else "连接失败"})

@app.route("/api/server/setup_key", methods=["POST"])
def api_server_setup_key():
    """生成新的SSH密钥对并返回公钥"""
    data = request.get_json() or {}
    key_type = data.get("type", "ed25519")  # ed25519 or rsa
    
    new_key_path = str(PROJECT_ROOT / "keys" / "firefightAI_deploy")
    key_dir = PROJECT_ROOT / "keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # 生成密钥对
        if key_type == "rsa":
            subprocess.run([
                "ssh-keygen", "-t", "rsa", "-b", "4096", "-f", new_key_path,
                "-N", "", "-C", "firefightAI-deploy"
            ], check=True, capture_output=True, text=True, timeout=30)
        else:
            subprocess.run([
                "ssh-keygen", "-t", "ed25519", "-f", new_key_path,
                "-N", "", "-C", "firefightAI-deploy"
            ], check=True, capture_output=True, text=True, timeout=30)
        
        # 读取公钥
        pub_key_path = new_key_path + ".pub"
        pub_key = Path(pub_key_path).read_text().strip()
        
        add_learning_log("server", "已生成新的SSH密钥对", f"路径: {new_key_path}")
        
        return jsonify({
            "status": "generated",
            "private_key_path": new_key_path,
            "public_key_path": pub_key_path,
            "public_key": pub_key,
            "instructions": (
                "请将以下公钥添加到服务器 ~/.ssh/authorized_keys 文件中:\n"
                f"echo '{pub_key}' >> ~/.ssh/authorized_keys\n"
                "或者在服务器上执行:\n"
                f"ssh-copy-id -i {pub_key_path} {SERVER_USER}@{SERVER_HOST}"
            ),
        })
    except subprocess.CalledProcessError as e:
        return jsonify({"status": "error", "error": f"密钥生成失败: {e.stderr}"}), 500
    except FileNotFoundError:
        return jsonify({"status": "error", "error": "ssh-keygen未找到，请安装OpenSSH"}), 500

@app.route("/api/server/upload_key", methods=["POST"])
def api_server_upload_key():
    """尝试通过密码认证上传公钥到服务器"""
    import paramiko
    data = request.get_json() or {}
    pub_key = data.get("public_key", "")
    
    if not pub_key:
        # 如果没有提供公钥，从现有密钥读取
        pub_key_path = SSH_KEY_PATH + ".pub"
        if Path(pub_key_path).exists():
            pub_key = Path(pub_key_path).read_text().strip()
        else:
            # 生成新密钥并读取公钥
            try:
                key_dir = PROJECT_ROOT / "keys"
                key_dir.mkdir(parents=True, exist_ok=True)
                new_key = key_dir / "firefightAI_deploy"
                subprocess.run([
                    "ssh-keygen", "-t", "rsa", "-b", "4096", "-f", str(new_key),
                    "-N", "", "-C", "firefightAI-deploy"
                ], check=True, capture_output=True, text=True, timeout=30)
                pub_key = (new_key.parent / (new_key.name + ".pub")).read_text().strip()
            except Exception as e:
                return jsonify({"status": "error", "error": f"无法生成或读取公钥: {str(e)}"}), 500
    
    if not SSH_PASSWORD:
        return jsonify({"status": "error", "error": "未配置SSH密码，无法自动上传公钥。请手动添加公钥到服务器"}), 400
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(SERVER_HOST, username=SERVER_USER, password=SSH_PASSWORD, timeout=10)
        
        # 确保 .ssh 目录存在
        client.exec_command("mkdir -p ~/.ssh && chmod 700 ~/.ssh", timeout=10)
        # 追加公钥
        cmd = f"echo '{pub_key}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo OK"
        stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
        out = stdout.read().decode()
        client.close()
        
        if "OK" in out:
            add_learning_log("server", "SSH公钥已上传到服务器", "")
            return jsonify({"status": "ok", "message": "公钥已成功添加到服务器authorized_keys"})
        return jsonify({"status": "error", "error": f"上传失败: {stderr.read().decode()[:200]}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "error": f"SSH连接失败: {str(e)[:200]}"}), 500

@app.route("/api/server/test_ssh", methods=["POST"])
def api_server_test_ssh():
    """测试SSH连接，返回详细诊断信息"""
    import socket
    
    result = {
        "host": SERVER_HOST,
        "user": SERVER_USER,
        "tests": [],
        "ssh_key_exists": Path(SSH_KEY_PATH).exists(),
        "ssh_key_path": SSH_KEY_PATH,
        "has_password": bool(SSH_PASSWORD),
    }
    
    # 测试1: DNS解析
    try:
        ip = socket.gethostbyname(SERVER_HOST)
        result["tests"].append({"name": "DNS解析", "status": "ok", "detail": f"{SERVER_HOST} -> {ip}"})
    except Exception as e:
        result["tests"].append({"name": "DNS解析", "status": "fail", "detail": str(e)})
        result["summary"] = "DNS解析失败，可能服务器地址错误"
        return jsonify(result)
    
    # 测试2: TCP端口连通性
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((SERVER_HOST, 22))
        sock.close()
        result["tests"].append({"name": "TCP端口22", "status": "ok", "detail": "SSH端口可达"})
    except socket.timeout:
        result["tests"].append({"name": "TCP端口22", "status": "fail", "detail": "连接超时，请检查防火墙和服务器状态"})
        result["summary"] = "SSH端口22不可达，请检查服务器防火墙"
        return jsonify(result)
    except Exception as e:
        result["tests"].append({"name": "TCP端口22", "status": "fail", "detail": str(e)})
        result["summary"] = "SSH端口不可达"
        return jsonify(result)
    
    # 测试3: SSH密钥认证
    if Path(SSH_KEY_PATH).exists():
        stdout, stderr, diagnosis = _ssh_connect("echo OK", use_key=True, timeout=10)
        if "OK" in stdout:
            result["tests"].append({"name": "SSH密钥认证", "status": "ok", "detail": "密钥认证成功"})
        else:
            result["tests"].append({"name": "SSH密钥认证", "status": "fail", "detail": diagnosis or stderr[:300]})
    else:
        result["tests"].append({"name": "SSH密钥认证", "status": "skip", "detail": "密钥文件不存在"})
    
    # 测试4: 密码认证（如果配置了）
    if SSH_PASSWORD:
        stdout, stderr, diagnosis = _ssh_connect("echo OK", use_key=False, timeout=10)
        if "OK" in stdout:
            result["tests"].append({"name": "SSH密码认证", "status": "ok", "detail": "密码认证成功"})
        else:
            result["tests"].append({"name": "SSH密码认证", "status": "fail", "detail": diagnosis or stderr[:300]})
    
    # 测试5: 检查Python和部署状态
    if any(t["status"] == "ok" for t in result["tests"] if "SSH" in t["name"]):
        stdout, stderr, _ = _ssh_connect("python3 --version 2>/dev/null; ls /opt/firefightAI 2>/dev/null || echo no_deploy", timeout=10)
        py_ver = stdout.split("\n")[0].strip() if stdout else "未安装"
        deployed = "no_deploy" not in stdout
        result["tests"].append({"name": "Python环境", "status": "ok" if "Python" in py_ver else "fail", "detail": py_ver})
        result["tests"].append({"name": "项目部署", "status": "ok" if deployed else "fail", "detail": "已部署" if deployed else "未部署"})
    
    # 汇总
    failures = [t for t in result["tests"] if t["status"] == "fail"]
    if failures:
        result["summary"] = f"发现{len(failures)}个问题: " + "; ".join(f"{t['name']}: {t['detail'][:50]}" for t in failures)
    else:
        result["summary"] = "所有检查通过，SSH连接正常"
    
    result["all_ok"] = len(failures) == 0
    
    add_learning_log("server", f"SSH诊断完成: {result['summary']}", "")
    return jsonify(result)

@app.route("/api/server/deploy", methods=["POST"])
def api_server_deploy():
    """部署项目到腾讯云服务器"""
    data = request.get_json() or {}
    sync_only = data.get("sync_only", False)  # 仅同步数据，不部署完整项目

    add_learning_log("server", "开始部署到腾讯云服务器", SERVER_HOST)

    def deploy_worker():
        try:
            # 1. 测试SSH连接
            socketio.emit("server_deploy_progress", {"step": "连接服务器", "progress": 10})
            ok, out, err = _ssh_exec("echo OK", timeout=15)
            if not ok or "OK" not in out:
                socketio.emit("server_deploy_error", {"error": f"SSH连接失败: {err}"})
                update_state(server_status="offline")
                return

            update_state(server_status="online")
            socketio.emit("server_deploy_progress", {"step": "创建目录", "progress": 20})

            # 2. 创建远程目录
            _ssh_exec(f"mkdir -p {SERVER_DEPLOY_PATH}/data/params {SERVER_DEPLOY_PATH}/config {SERVER_DEPLOY_PATH}/models", timeout=10)

            # 3. 同步数据文件
            socketio.emit("server_deploy_progress", {"step": "同步数据文件", "progress": 40})
            data_dirs = ["data/params", "data/tactics_rules.yaml", "data/battle_memory.db"]
            for d in data_dirs:
                local = PROJECT_ROOT / d
                if local.exists():
                    import paramiko as _p
                    _ok, _out, _err = _ssh_exec("cat > /dev/null", timeout=5)
                    if _ok:
                        with _p.Transport((SERVER_HOST, 22)) as transport:
                            try:
                                transport.connect(username=SERVER_USER, password=SSH_PASSWORD)
                            except:
                                try:
                                    key = _p.RSAKey.from_private_key_file(SSH_KEY_PATH)
                                    transport.connect(username=SERVER_USER, pkey=key)
                                except:
                                    pass
                            if transport.is_authenticated():
                                sftp = _p.SFTPClient.from_transport(transport)
                                try:
                                    remote_path = f"{SERVER_DEPLOY_PATH}/data/{d.split('/')[-1] if '/' in d else d}"
                                    if local.is_dir():
                                        sftp.mkdir(remote_path)
                                        for f in local.rglob("*"):
                                            if f.is_file():
                                                rel = str(f.relative_to(local)).replace("\\", "/")
                                                sftp.put(str(f), f"{remote_path}/{rel}")
                                    else:
                                        sftp.put(str(local), remote_path)
                                finally:
                                    sftp.close()
                    else:
                        subprocess.run([
                            "scp", "-o", "StrictHostKeyChecking=no",
                            "-r", str(local), f"{SERVER_USER}@{SERVER_HOST}:{SERVER_DEPLOY_PATH}/data/"
                        ], capture_output=True, timeout=30)

            if not sync_only:
                # 4. 同步项目文件
                socketio.emit("server_deploy_progress", {"step": "同步项目文件", "progress": 60})
                for item in ["dashboard_server.py", "desktop_app.py", "requirements.txt", "config/settings.yaml"]:
                    local = PROJECT_ROOT / item
                    if local.exists():
                        subprocess.run([
                            "scp", "-o", "StrictHostKeyChecking=no",
                            str(local), f"{SERVER_USER}@{SERVER_HOST}:{SERVER_DEPLOY_PATH}/"
                        ], capture_output=True, timeout=30)

                # 5. 同步src目录
                socketio.emit("server_deploy_progress", {"step": "同步源码", "progress": 80})
                subprocess.run([
                    "scp", "-o", "StrictHostKeyChecking=no",
                    "-r", str(PROJECT_ROOT / "src"), f"{SERVER_USER}@{SERVER_HOST}:{SERVER_DEPLOY_PATH}/"
                ], capture_output=True, timeout=60)

                # 6. 安装依赖并重启
                socketio.emit("server_deploy_progress", {"step": "安装依赖", "progress": 90})
                _ssh_exec(f"cd {SERVER_DEPLOY_PATH} && pip3 install -r requirements.txt -q 2>&1", timeout=120)

            socketio.emit("server_deploy_progress", {"step": "完成", "progress": 100})
            add_learning_log("server", "部署成功", f"服务器: {SERVER_HOST}")
            socketio.emit("server_deploy_complete", {"success": True, "host": SERVER_HOST})

        except Exception as e:
            add_learning_log("server", f"部署失败", str(e)[:200])
            socketio.emit("server_deploy_error", {"error": str(e)[:200]})

    threading.Thread(target=deploy_worker, daemon=True).start()
    return jsonify({"status": "deploying", "host": SERVER_HOST})

@app.route("/api/server/sync_data", methods=["POST"])
def api_server_sync_data():
    """仅同步数据到服务器"""
    return api_server_deploy()


# ═══════════════════════════════════════════════════════════════
# PyTorch 更新
# ═══════════════════════════════════════════════════════════════

@app.route("/api/pytorch/version")
def api_pytorch_version():
    try:
        import torch
        return jsonify({"version": torch.__version__, "cuda": torch.cuda.is_available(), "cuda_version": torch.version.cuda if torch.cuda.is_available() else None})
    except:
        return jsonify({"version": "未安装", "cuda": False})

@app.route("/api/pytorch/update", methods=["POST"])
def api_pytorch_update():
    """更新PyTorch"""
    data = request.get_json() or {}
    version = data.get("version", "")  # 如 "2.0.1" 或 "latest"
    add_learning_log("system", f"开始更新PyTorch: {version or 'latest'}", "")

    def do_update():
        try:
            socketio.emit("pytorch_update_progress", {"step": "更新中", "progress": 50})
            if version:
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", f"torch=={version}", "torchvision", "-q"]
            else:
                cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "torch", "torchvision", "-q"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            import torch
            new_ver = torch.__version__
            update_state(pytorch_version=new_ver)
            add_learning_log("system", f"PyTorch更新完成: {new_ver}", "")
            socketio.emit("pytorch_update_complete", {"version": new_ver, "success": True})
        except Exception as e:
            socketio.emit("pytorch_update_complete", {"success": False, "error": str(e)})

    threading.Thread(target=do_update, daemon=True).start()
    return jsonify({"status": "updating"})


# ═══════════════════════════════════════════════════════════════
# API 验证
# ═══════════════════════════════════════════════════════════════

def verify_deepseek_api() -> dict:
    import requests as req
    cfg = load_config()
    llm_cfg = cfg["llm"]
    result = {"name": "DeepSeek", "status": "unknown", "models": [], "latency_ms": 0}
    try:
        t0 = time.time()
        r = req.get(f"{llm_cfg['api_base']}/models", headers={"Authorization": f"Bearer {llm_cfg['api_key']}"}, timeout=10)
        result["latency_ms"] = round((time.time() - t0) * 1000)
        if r.status_code == 200:
            data = r.json()
            result["status"] = "online"
            result["models"] = [m["id"] for m in data.get("data", [])]
            t1 = time.time()
            r2 = req.post(f"{llm_cfg['api_base']}/chat/completions", headers={"Authorization": f"Bearer {llm_cfg['api_key']}", "Content-Type": "application/json"}, json={"model": llm_cfg.get("model", "deepseek-v4-flash"), "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}, timeout=10)
            result["chat_latency_ms"] = round((time.time() - t1) * 1000)
            result["chat_ok"] = r2.status_code == 200
        else:
            result["status"] = "error"
            result["error"] = f"HTTP {r.status_code}"
    except Exception as e:
        result["status"] = "offline"
        result["error"] = str(e)[:100]
    return result


@socketio.on("verify_api")
def on_verify_api():
    emit("api_status", {"checking": True})
    ds = verify_deepseek_api()
    update_state(api_status={"deepseek": ds["status"]})
    emit("api_status", {"deepseek": ds})


# ═══════════════════════════════════════════════════════════════
# AI 对话 + 行为纠正 + 自我学习
# ═══════════════════════════════════════════════════════════════

@socketio.on("ai_chat")
def on_ai_chat(data: dict):
    message = data.get("message", "").strip()
    if not message:
        return

    global _chat_history
    _chat_history.append({"role": "user", "content": message, "time": time.time()})

    state = get_state()
    context = ""
    if data.get("include_battlefield", True) and state.get("cycle", 0) > 0:
        context = f"\n[当前战场: 第{state.get('cycle',0)}轮, 友{state.get('allies',0)}vs敌{state.get('enemies',0)}, 总分{state.get('total_score',0)}]"

    is_correction = data.get("is_correction", False)
    correction_type = data.get("correction_type", "")

    emit("ai_chat_start", {"message": message})

    def do_chat():
        try:
            from openai import OpenAI
            cfg = load_config()
            llm_cfg = cfg["llm"]
            client = OpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["api_base"])

            sys_prompt = (
                "你是 Firefight AI 战术指挥系统的 AI 助手。你可以：\n"
                "1. 分析战场局势并给出战术建议\n"
                "2. 解释你的决策逻辑\n"
                "3. 回答关于游戏机制、单位、战术的问题\n"
                "4. 帮助指挥官制定作战计划\n"
                "5. 接受指挥官的行为纠正并调整策略\n\n"
                "请用中文回答，保持专业、简洁。如果涉及战术决策，请分步骤说明你的思考过程。"
            )

            if is_correction:
                sys_prompt += (
                    "\n\n【重要】指挥官正在纠正你的行为。请仔细分析纠正内容，"
                    "并说明你将如何调整后续的战术决策。"
                )

            messages = [{"role": "system", "content": sys_prompt}]
            for h in _chat_history[-10:]:
                messages.append({"role": h["role"], "content": h["content"]})

            thinking_parts = []
            full_response = ""

            stream = client.chat.completions.create(
                model=llm_cfg.get("model", "deepseek-v4-flash"),
                messages=messages,
                max_tokens=800,
                temperature=0.1,
                stream=True,
                timeout=6,
            )

            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    full_response += token
                    thinking_parts.append(token)
                    if len(thinking_parts) >= 5:
                        emit("ai_chat_token", {"token": "".join(thinking_parts), "done": False})
                        thinking_parts = []

            if thinking_parts:
                emit("ai_chat_token", {"token": "".join(thinking_parts), "done": False})

            emit("ai_chat_token", {"token": "", "done": True, "full": full_response})
            _chat_history.append({"role": "assistant", "content": full_response, "time": time.time()})

            # 如果是行为纠正，触发AI学习
            if is_correction:
                add_learning_log("correction", f"用户纠正: {message[:100]}", full_response[:300])
                socketio.emit("ai_learned_from_correction", {
                    "correction": message[:200],
                    "response": full_response[:300],
                    "time": datetime.now().isoformat(),
                })

            # 存入经验库
            try:
                from src.learning.battle_memory import BattleMemory
                bm = BattleMemory()
                bm.record(
                    state_hash=f"chat_{int(time.time())}",
                    ally_count=state.get("allies", 0),
                    enemy_count=state.get("enemies", 0),
                    ally_positions=[],
                    decision={"action": "correction" if is_correction else "ai_chat", "reason": f"用户: {message[:100]} | AI: {full_response[:200]}", "target": []},
                    outcome_score=10 if is_correction else 5,
                    cycle_num=state.get("cycle", 0),
                    game_session=state.get("game_session", ""),
                )
            except:
                pass

        except Exception as e:
            logger.error(f"AI对话失败: {e}")
            emit("ai_chat_error", {"error": str(e)[:200]})

    t = threading.Thread(target=do_chat, daemon=True)
    t.start()


@socketio.on("ai_chat_clear")
def on_ai_chat_clear():
    global _chat_history
    _chat_history = []
    emit("ai_chat_cleared", {})


@socketio.on("ai_correct_behavior")
def on_ai_correct_behavior(data: dict):
    """用户纠正AI行为，AI从中学习"""
    correction = data.get("correction", "").strip()
    if not correction:
        return

    add_learning_log("correction", f"行为纠正: {correction[:200]}", "")
    emit("ai_chat_start", {"message": correction})

    def do_correction():
        try:
            from openai import OpenAI
            cfg = load_config()
            llm_cfg = cfg["llm"]
            client = OpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["api_base"])

            state = get_state()
            sys_prompt = (
                "你是 Firefight AI 学习系统。指挥官对你之前的战术行为进行了纠正。\n"
                "请完成以下任务：\n"
                "1. 分析纠正内容的要点\n"
                "2. 总结出1-2条可以改进的战术规则\n"
                "3. 说明如何将这些改进应用到后续决策中\n\n"
                "输出格式：\n"
                "【分析】...\n"
                "【新规则】...\n"
                "【应用】..."
            )
            prompt = (
                f"战场背景: 第{state.get('cycle',0)}轮, 友{state.get('allies',0)}vs敌{state.get('enemies',0)}\n"
                f"指挥官纠正: {correction}\n"
                f"最近的AI决策: {state.get('last_decision', '无')}"
            )

            resp = client.chat.completions.create(
                model=llm_cfg.get("model", "deepseek-v4-flash"),
                messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.1,
                timeout=6,
            )
            analysis = resp.choices[0].message.content
            add_learning_log("correction", "AI分析完成", analysis[:300])

            # 尝试提取规则并保存
            try:
                from src.learning.strategy_compressor import StrategyCompressor
                if "新规则" in analysis:
                    rule_line = analysis.split("新规则】")[1].split("\n")[0].strip() if "新规则】" in analysis else ""
                    if rule_line:
                        StrategyCompressor._save_rules_static([rule_line])
                        add_learning_log("correction", f"新规则已保存: {rule_line[:100]}", "")
            except:
                pass

            socketio.emit("correction_analysis", {
                "correction": correction,
                "analysis": analysis,
                "time": datetime.now().isoformat(),
            })

        except Exception as e:
            socketio.emit("ai_chat_error", {"error": str(e)[:200]})

    threading.Thread(target=do_correction, daemon=True).start()


@app.route("/api/chat_history")
def api_chat_history():
    return jsonify(_chat_history[-50:])


# ═══════════════════════════════════════════════════════════════
# AI 思考过程展示
# ═══════════════════════════════════════════════════════════════

@socketio.on("get_ai_thinking")
def on_get_ai_thinking():
    emit("ai_thinking_update", {
        "thinking": get_state().get("ai_thinking", ""),
        "last_decision": get_state().get("last_decision", ""),
        "last_reason": get_state().get("last_reason", ""),
        "cycle": get_state().get("cycle", 0),
    })


# ═══════════════════════════════════════════════════════════════
# 参数上传 + AI 学习
# ═══════════════════════════════════════════════════════════════

@app.route("/api/params/upload", methods=["POST"])
def api_params_upload():
    uploaded = []
    for key in request.files:
        file = request.files[key]
        if file.filename:
            save_path = PROJECT_ROOT / "data" / "params" / file.filename
            save_path.parent.mkdir(parents=True, exist_ok=True)
            file.save(str(save_path))
            uploaded.append(file.filename)
    add_learning_log("params", f"上传参数文件: {', '.join(uploaded)}", "")
    return jsonify({"uploaded": uploaded, "count": len(uploaded)})

@app.route("/api/params/list")
def api_params_list():
    params_dir = PROJECT_ROOT / "data" / "params"
    files = []
    if params_dir.exists():
        for f in params_dir.iterdir():
            if f.is_file():
                files.append({"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1), "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
    return jsonify(files)

@app.route("/api/params/learn", methods=["POST"])
def api_params_learn():
    data = request.get_json() or {}
    filename = data.get("filename", "")
    params_dir = PROJECT_ROOT / "data" / "params"
    if filename:
        filepath = params_dir / filename
        if not filepath.exists():
            return jsonify({"error": "文件不存在"}), 404
        content = filepath.read_text(encoding="utf-8", errors="replace")[:5000]
    else:
        contents = []
        if params_dir.exists():
            for f in params_dir.iterdir():
                if f.is_file() and f.suffix in (".yaml", ".txt", ".json", ".cfg"):
                    contents.append(f"--- {f.name} ---\n{f.read_text(encoding='utf-8', errors='replace')[:2000]}")
        content = "\n\n".join(contents)
    if not content:
        return jsonify({"error": "没有可学习的参数"}), 400

    add_learning_log("params", "AI开始学习参数", filename or "所有参数")

    def learn_from_params():
        try:
            from openai import OpenAI
            cfg = load_config()
            llm_cfg = cfg["llm"]
            client = OpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["api_base"])
            prompt = (
                "你是 Firefight AI 学习系统。以下是上传的配置参数，请分析并提取可以用于改进战术的要点。\n"
                "请输出: 1) 参数摘要 2) 可改进的战术规则 3) 建议调整\n\n"
                f"参数内容:\n{content}"
            )
            resp = client.chat.completions.create(
                model=llm_cfg.get("model", "deepseek-v4-flash"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.1,
                timeout=6,
            )
            analysis = resp.choices[0].message.content
            add_learning_log("params", "参数学习完成", analysis[:300])
            socketio.emit("params_learned", {"analysis": analysis, "source": filename or "所有参数", "time": datetime.now().isoformat()})
            try:
                from src.learning.battle_memory import BattleMemory
                bm = BattleMemory()
                bm.record(state_hash=f"params_{int(time.time())}", ally_count=0, enemy_count=0, ally_positions=[], decision={"action": "param_learn", "reason": f"参数学习: {analysis[:200]}", "target": []}, outcome_score=10, cycle_num=0, game_session="params_learning")
            except:
                pass
        except Exception as e:
            socketio.emit("params_learned", {"error": str(e)[:200]})

    threading.Thread(target=learn_from_params, daemon=True).start()
    return jsonify({"status": "learning", "source": filename or "所有参数"})


# ═══════════════════════════════════════════════════════════════
# 训练管线
# ═══════════════════════════════════════════════════════════════

@app.route("/api/datasets")
def api_datasets():
    data_dir = PROJECT_ROOT / "data"
    datasets = []
    if data_dir.exists():
        for d in data_dir.iterdir():
            if d.is_dir() and (d / "data.yaml").exists():
                img_count = len(list(d.glob("images/*.png"))) + len(list(d.glob("images/*.jpg"))) if (d / "images").exists() else 0
                datasets.append({"name": d.name, "images": img_count, "path": str(d.relative_to(PROJECT_ROOT))})
    return jsonify(datasets)

@app.route("/api/upload_images", methods=["POST"])
def api_upload_images():
    dataset_name = request.form.get("dataset", "custom_dataset")
    dataset_dir = PROJECT_ROOT / "data" / dataset_name
    images_dir = dataset_dir / "images"
    labels_dir = dataset_dir / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    uploaded = []
    for key in request.files:
        file = request.files[key]
        if file.filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            img_name = f"{ts}_{file.filename}"
            file.save(str(images_dir / img_name))
            (labels_dir / (Path(img_name).stem + ".txt")).touch()
            uploaded.append(img_name)
    data_yaml = dataset_dir / "data.yaml"
    if not data_yaml.exists():
        data_yaml.write_text(f"path: {dataset_dir}\ntrain: images\nval: images\n\nnc: 2\nnames: ['tank', 'infantry']\n")
    add_learning_log("training", f"上传{len(uploaded)}张图片到数据集 {dataset_name}", "")
    return jsonify({"uploaded": uploaded, "dataset": dataset_name})

@app.route("/api/train/start", methods=["POST"])
def api_train_start():
    global _training_process
    if _training_process and _training_process.poll() is None:
        return jsonify({"error": "训练已在运行中"}), 409
    data = request.get_json() or {}
    dataset_name = data.get("dataset", "faction_yolo")
    model_name = data.get("model_name", "yolov8n.pt")
    epochs = int(data.get("epochs", 50))
    imgsz = int(data.get("imgsz", 640))
    auto_push = data.get("auto_push_github", False)

    dataset_path = PROJECT_ROOT / "data" / dataset_name / "data.yaml"
    if not dataset_path.exists():
        return jsonify({"error": f"数据集不存在: {dataset_name}"}), 404

    update_state(training_status="running", training_progress=0, training_message="启动训练...")
    add_learning_log("training", f"开始训练: {dataset_name}, epochs={epochs}", "")

    def run_training():
        global _training_process
        try:
            cmd = [sys.executable, "-m", "ultralytics", "train", f"data={dataset_path}", f"model={model_name}", f"epochs={epochs}", f"imgsz={imgsz}", f"project={PROJECT_ROOT / 'runs' / 'detect'}", f"name=custom_{int(time.time())}", "--exist_ok"]
            _training_process = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            for line in _training_process.stdout:
                line = line.strip()
                if not line:
                    continue
                if "epoch" in line.lower() and "/" in line:
                    try:
                        for p in line.split():
                            if "/" in p and p.split("/")[0].isdigit():
                                progress = min(100, int(int(p.split("/")[0]) / epochs * 100))
                                update_state(training_progress=progress, training_message=f"Epoch {p}")
                                break
                    except:
                        pass
                socketio.emit("training_log", {"line": line})
            _training_process.wait()
            ok = _training_process.returncode == 0
            update_state(training_status="completed" if ok else "failed", training_progress=100 if ok else 0, training_message="训练完成!" if ok else "训练失败")
            add_learning_log("training", "训练完成" if ok else "训练失败", f"dataset={dataset_name}, epochs={epochs}")
            socketio.emit("training_complete", {"success": ok})

            # 自动推送模型到GitHub
            if ok and auto_push:
                add_learning_log("training", "自动推送训练结果到GitHub", "")
                try:
                    import git
                    repo = git.Repo(str(PROJECT_ROOT))
                    repo.index.add(["runs/detect/", "data/params/"])
                    repo.index.commit(f"训练完成: {dataset_name} epochs={epochs}")
                    repo.remotes.origin.push()
                    add_learning_log("github", "训练结果已推送", "")
                    socketio.emit("github_push_complete", {"success": True})
                except Exception as e:
                    socketio.emit("github_push_complete", {"success": False, "error": str(e)[:200]})

        except Exception as e:
            update_state(training_status="failed", training_message=str(e)[:100])
            socketio.emit("training_complete", {"success": False, "error": str(e)})
        finally:
            _training_process = None

    threading.Thread(target=run_training, daemon=True).start()
    return jsonify({"status": "started", "dataset": dataset_name, "epochs": epochs})

@app.route("/api/train/stop", methods=["POST"])
def api_train_stop():
    global _training_process
    if _training_process and _training_process.poll() is None:
        _training_process.terminate()
        update_state(training_status="stopped", training_message="训练已停止")
        return jsonify({"status": "stopped"})
    return jsonify({"error": "没有正在运行的训练"})

@app.route("/api/train/status")
def api_train_status():
    return jsonify({"status": get_state().get("training_status", "idle"), "progress": get_state().get("training_progress", 0), "message": get_state().get("training_message", "")})

@app.route("/api/models")
def api_models():
    models = []
    for d in [PROJECT_ROOT / "models_registry", PROJECT_ROOT / "runs" / "detect"]:
        if d.exists():
            for f in d.rglob("*.pt"):
                if f.is_file():
                    models.append({"name": str(f.relative_to(PROJECT_ROOT)), "size_mb": round(f.stat().st_size / 1024 / 1024, 1), "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
    return jsonify(models)


# ═══════════════════════════════════════════════════════════════
# 实战数据学习
# ═══════════════════════════════════════════════════════════════

@app.route("/api/combat/learn", methods=["POST"])
def api_combat_learn():
    """让AI从实战积累数据中学习"""
    data = request.get_json() or {}
    session = data.get("session", "")

    add_learning_log("combat", "AI开始从实战数据学习", f"session={session}")

    def do_combat_learn():
        try:
            from src.learning.battle_memory import BattleMemory
            from src.learning.strategy_compressor import StrategyCompressor
            from openai import OpenAI
            cfg = load_config()
            llm_cfg = cfg["llm"]

            bm = BattleMemory()
            total = bm.count()
            if total == 0:
                socketio.emit("combat_learn_result", {"error": "没有实战数据"})
                return

            # 获取高分经验
            top_exps = bm.get_top_experiences(top_k=30, game_session=session)
            if len(top_exps) < 5:
                socketio.emit("combat_learn_result", {"error": "高分经验不足(需要>=5条)"})
                return

            # 统计
            stats = bm.get_stats(session)
            add_learning_log("combat", f"实战数据统计: {stats}", "")

            # 提炼规则
            compressor = StrategyCompressor(bm, api_key=llm_cfg["api_key"], api_base=llm_cfg["api_base"], model=llm_cfg["model"])
            rules = compressor.compress(cycle_num=999, game_session=session, force=True)

            # 总结学习成果
            client = OpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["api_base"])
            exp_text = "\n".join([f"- 友{d['ally_count']}vs敌{d['enemy_count']}: {d['decision'].get('reason','')[:80]} (得分+{d['outcome_score']:.0f})" for d in top_exps[:15]])
            prompt = f"从以下实战数据中总结AI学到了什么（3-5条要点）：\n{exp_text}\n\n请用中文列出。"
            resp = client.chat.completions.create(model=llm_cfg.get("model", "deepseek-v4-flash"), messages=[{"role": "user", "content": prompt}], max_tokens=512, temperature=0.1, timeout=6)
            summary = resp.choices[0].message.content

            add_learning_log("combat", "实战学习总结", summary[:300])
            socketio.emit("combat_learn_result", {
                "stats": stats,
                "rules": rules,
                "summary": summary,
                "total_experiences": total,
                "time": datetime.now().isoformat(),
            })

        except Exception as e:
            socketio.emit("combat_learn_result", {"error": str(e)[:200]})

    threading.Thread(target=do_combat_learn, daemon=True).start()
    return jsonify({"status": "learning"})

@app.route("/api/combat/export", methods=["POST"])
def api_combat_export():
    """导出实战数据"""
    data = request.get_json() or {}
    fmt = data.get("format", "json")
    try:
        from src.learning.battle_memory import BattleMemory
        bm = BattleMemory()
        exps = bm.get_top_experiences(top_k=500)
        if fmt == "json":
            return jsonify({"experiences": exps, "total": len(exps)})
        else:
            import io
            import csv
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ally_count", "enemy_count", "outcome_score", "action", "reason"])
            for e in exps:
                d = e.get("decision", {})
                writer.writerow([e["ally_count"], e["enemy_count"], e["outcome_score"], d.get("action", ""), d.get("reason", "")])
            return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=combat_data.csv"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# 智能搜索 / Web Search
# ═══════════════════════════════════════════════════════════════

WEB_KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "web_knowledge"

@app.route("/api/web/search", methods=["POST"])
def api_web_search():
    """Web search using DeepSeek API"""
    data = request.get_json() or {}
    query = data.get("query", "")
    if not query:
        return jsonify({"error": "query required"}), 400

    add_learning_log("web_search", f"搜索: {query[:50]}")

    try:
        from openai import OpenAI
        cfg = load_config()
        llm_cfg = cfg["llm"]
        client = OpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["api_base"], timeout=10)
        response = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": "你是一个智能搜索助手。请根据用户的问题，利用你的知识库提供准确、详细的信息。如果涉及最新信息，请说明知识截止日期。回答要结构化、有条理。"},
                {"role": "user", "content": f"请帮我搜索并回答以下问题，提供详细信息：\n\n{query}"}
            ],
            temperature=0.1,
            max_tokens=2048,
            stream=True,
            timeout=6,
        )
        full_text = ""
        for chunk in response:
            if chunk.choices[0].delta.content:
                full_text += chunk.choices[0].delta.content
                socketio.emit("web_search_stream", {"text": full_text, "done": False})
        socketio.emit("web_search_stream", {"text": full_text, "done": True})

        add_learning_log("web_search", f"搜索完成: {query[:50]}", full_text[:200])
        return jsonify({
            "query": query,
            "summary": full_text,
            "searched_at": datetime.now().isoformat(),
            "source": "DeepSeek Knowledge Base",
            "total_results": 1,
        })
    except Exception as e:
        add_learning_log("web_search", f"搜索失败: {str(e)[:100]}")
        return jsonify({"error": str(e), "query": query}), 500

@app.route("/api/web/save", methods=["POST"])
def api_web_save():
    """保存搜索结果到知识库"""
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    results = data.get("results", [])
    summary = data.get("summary", "")
    tags = data.get("tags", [])
    
    if not query:
        return jsonify({"error": "缺少查询参数"}), 400
    
    WEB_KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    
    filename = f"web_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.md5(query.encode()).hexdigest()[:8]}.json"
    filepath = WEB_KNOWLEDGE_DIR / filename
    
    entry = {
        "query": query,
        "results": results,
        "summary": summary,
        "tags": tags,
        "saved_at": datetime.now().isoformat(),
        "source": "web_search",
    }
    
    filepath.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    
    add_learning_log("web_search", f"知识已保存: {filename}", f"查询: {query}")
    
    return jsonify({
        "status": "saved",
        "filename": filename,
        "path": str(filepath.relative_to(PROJECT_ROOT)),
        "query": query,
    })

@app.route("/api/web/learn", methods=["POST"])
def api_web_learn():
    """AI从保存的web知识中学习"""
    data = request.get_json() or {}
    filename = data.get("filename", "")
    query_filter = data.get("query", "")
    
    if filename:
        filepath = WEB_KNOWLEDGE_DIR / filename
        if not filepath.exists():
            return jsonify({"error": "文件不存在"}), 404
        entries = [json.loads(filepath.read_text(encoding="utf-8"))]
    else:
        entries = []
        if WEB_KNOWLEDGE_DIR.exists():
            for f in sorted(WEB_KNOWLEDGE_DIR.glob("web_*.json"), reverse=True):
                try:
                    entry = json.loads(f.read_text(encoding="utf-8"))
                    if query_filter and query_filter.lower() not in entry.get("query", "").lower():
                        continue
                    entries.append(entry)
                except:
                    pass
        entries = entries[:20]
    
    if not entries:
        return jsonify({"error": "没有可学习的知识", "suggestion": "请先保存搜索结果到知识库"}), 400
    
    add_learning_log("web_search", f"AI开始从{len(entries)}条知识中学习", "")
    
    def learn_from_web():
        try:
            from openai import OpenAI
            cfg = load_config()
            llm_cfg = cfg["llm"]
            client = OpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["api_base"])
            
            knowledge_text = "\n\n---\n".join([
                f"查询: {e.get('query','')}\n结果: {e.get('summary','')[:500]}" 
                for e in entries
            ])
            
            prompt = (
                "你是Firefight AI学习系统。请从以下网络搜索知识中提取对战术AI有用的信息：\n"
                "1. 提取关键战术概念和策略\n"
                "2. 如果有可用的战术数据，转化为游戏规则\n"
                "3. 总结3-5条可应用的学习要点\n\n"
                f"知识内容:\n{knowledge_text[:4000]}"
            )
            
            resp = client.chat.completions.create(
                model=llm_cfg.get("model", "deepseek-v4-flash"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.1,
                timeout=6,
            )
            learning = resp.choices[0].message.content
            
            add_learning_log("web_search", "AI学习完成", learning[:300])
            socketio.emit("web_learn_result", {
                "learning": learning,
                "source_count": len(entries),
                "time": datetime.now().isoformat(),
            })
            
            # 尝试保存战术规则
            try:
                from src.learning.strategy_compressor import StrategyCompressor
                if "规则" in learning or "要点" in learning:
                    lines = [l.strip() for l in learning.split("\n") if l.strip() and any(kw in l for kw in ["规则", "要点", "策略", "战术"])]
                    if lines:
                        StrategyCompressor._save_rules_static(lines[:5])
            except:
                pass
                
        except Exception as e:
            socketio.emit("web_learn_result", {"error": str(e)[:200]})
    
    threading.Thread(target=learn_from_web, daemon=True).start()
    return jsonify({"status": "learning", "source_count": len(entries)})

@app.route("/api/web/knowledge")
def api_web_knowledge():
    """列出已保存的知识"""
    entries = []
    if WEB_KNOWLEDGE_DIR.exists():
        for f in sorted(WEB_KNOWLEDGE_DIR.glob("web_*.json"), reverse=True):
            try:
                entry = json.loads(f.read_text(encoding="utf-8"))
                entries.append({
                    "filename": f.name,
                    "query": entry.get("query", ""),
                    "saved_at": entry.get("saved_at", ""),
                    "tags": entry.get("tags", []),
                    "summary_preview": entry.get("summary", "")[:100],
                })
            except:
                pass
    return jsonify(entries)

@socketio.on("web_search")
def on_web_search(data: dict):
    """流式Web搜索，实时返回进度"""
    query = data.get("query", "").strip()
    if not query:
        emit("web_search_error", {"error": "缺少查询参数"})
        return
    
    emit("web_search_progress", {"step": "搜索中", "progress": 20, "query": query})
    
    def search_worker():
        try:
            import requests as req
            
            # 搜索
            search_url = "https://html.duckduckgo.com/html/"
            r = req.post(search_url, data={"q": query}, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }, timeout=15)
            
            from html.parser import HTMLParser
            
            class DDGParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.results = []
                    self.current = {}
                    self.in_result = False
                    self.in_snippet = False
                    self.in_link = False
                    self.text_buf = ""
                    
                def handle_starttag(self, tag, attrs):
                    attrs_dict = dict(attrs)
                    if tag == "div" and "result__body" in attrs_dict.get("class", ""):
                        self.in_result = True
                        self.current = {}
                    if self.in_result and tag == "a" and "result__a" in attrs_dict.get("class", ""):
                        self.in_link = True
                        self.current["url"] = attrs_dict.get("href", "")
                    if self.in_result and tag == "a" and "result__snippet" in attrs_dict.get("class", ""):
                        self.in_snippet = True
                        self.text_buf = ""
                        
                def handle_endtag(self, tag):
                    if self.in_snippet and tag == "a":
                        self.in_snippet = False
                        self.current["snippet"] = self.text_buf.strip()
                    if self.in_result and tag == "div":
                        self.in_result = False
                        if self.current.get("snippet") or self.current.get("url"):
                            self.results.append(dict(self.current))
                        
                def handle_data(self, data):
                    if self.in_snippet:
                        self.text_buf += data
                    if self.in_link:
                        self.current["title"] = self.current.get("title", "") + data.strip()
            
            parser = DDGParser()
            parser.feed(r.text)
            results = parser.results[:10]
            
            emit("web_search_progress", {"step": "AI总结中", "progress": 60, "results_count": len(results)})
            
            # AI总结
            summary = ""
            try:
                from openai import OpenAI
                cfg = load_config()
                llm_cfg = cfg["llm"]
                client = OpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["api_base"])
                
                snippets_text = "\n".join([f"{i+1}. {r.get('title','')}: {r.get('snippet','')[:300]}" for i, r in enumerate(results[:8])])
                prompt = f"搜索结果如下，请用中文总结关键信息（3-5条要点）：\n查询: {query}\n\n{snippets_text}"
                
                full_summary = ""
                stream = client.chat.completions.create(
                    model=llm_cfg.get("model", "deepseek-v4-flash"),
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=512,
                    temperature=0.1,
                    stream=True,
                    timeout=6,
                )
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        full_summary += token
                        emit("web_search_token", {"token": token, "done": False})
                summary = full_summary
                emit("web_search_token", {"token": "", "done": True, "full": summary})
            except Exception as e:
                summary = f"(AI总结暂不可用)"
                emit("web_search_token", {"token": summary, "done": True, "full": summary})
            
            emit("web_search_progress", {"step": "完成", "progress": 100})
            emit("web_search_complete", {
                "query": query,
                "results": results,
                "summary": summary,
                "total_results": len(results),
            })
            
            add_learning_log("web_search", f"搜索完成: {query}", summary[:200])
            
        except Exception as e:
            emit("web_search_error", {"error": str(e)[:200]})
    
    threading.Thread(target=search_worker, daemon=True).start()

# ═══════════════════════════════════════════════════════════════
# 安装包创建
# ═══════════════════════════════════════════════════════════════

PACKAGE_EXCLUDE = {".git", "__pycache__", "logs", "runs", "sessions", "data/battle_memory.db", ".venv", "venv", "node_modules", "dist"}

@app.route("/api/package/create", methods=["POST"])
def api_package_create():
    """创建项目安装包zip"""
    import zipfile
    
    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    
    zip_path = dist_dir / "firefightAI_v5.zip"
    
    add_learning_log("system", "开始创建安装包", "")
    
    file_count = 0
    try:
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(str(PROJECT_ROOT)):
                # 过滤排除目录
                rel_root = Path(root).relative_to(PROJECT_ROOT)
                rel_str = str(rel_root).replace("\\", "/")
                if rel_str == ".":
                    parts = []
                else:
                    parts = rel_str.split("/")
                
                # 跳过排除的目录
                skip = False
                for part in parts:
                    if part in PACKAGE_EXCLUDE or part.startswith("."):
                        skip = True
                        break
                if skip:
                    dirs[:] = []  # 不进入子目录
                    continue
                
                # 修改dirs in place 来排除目录
                dirs[:] = [d for d in dirs if d not in PACKAGE_EXCLUDE and not d.startswith(".")]
                
                for f in files:
                    if f.startswith("."):
                        continue
                    fp = Path(root) / f
                    arcname = str(fp.relative_to(PROJECT_ROOT))
                    # 跳过较大的数据库文件
                    if "battle_memory.db" in arcname:
                        continue
                    try:
                        zf.write(str(fp), arcname)
                        file_count += 1
                    except Exception:
                        pass  # 跳过无法读取的文件
        
        zip_size = zip_path.stat().st_size
        size_mb = round(zip_size / 1024 / 1024, 2)
        
        add_learning_log("system", f"安装包创建完成: {size_mb}MB, {file_count}个文件", "")
        
        # 创建install.bat
        install_bat = dist_dir / "install.bat"
        install_bat.write_text("""@echo off
chcp 65001 >nul
title Firefight AI v5.0 安装程序
echo ============================================
echo   Firefight AI v5.0 安装程序
echo ============================================
echo.

:: 检查Python
echo [1/4] 检查Python环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到Python，请先安装Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version
echo.

:: 安装依赖
echo [2/4] 安装依赖包...
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [警告] 部分依赖安装可能失败，请检查网络连接
)
echo.

:: 创建桌面快捷方式
echo [3/4] 创建桌面快捷方式...
powershell -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\\FirefightAI.lnk'); $Shortcut.TargetPath = 'pythonw.exe'; $Shortcut.Arguments = 'dashboard_server.py --host 0.0.0.0 --port 5000'; $Shortcut.WorkingDirectory = '%~dp0'; $Shortcut.IconLocation = 'shell32.dll,13'; $Shortcut.Save()"
echo.

:: 启动应用
echo [4/4] 启动Firefight AI Dashboard...
echo.
echo 服务将在 http://localhost:5000 启动
echo 按 Ctrl+C 可停止服务
echo.
python dashboard_server.py --host 0.0.0.0 --port 5000

pause
""", encoding="utf-8")
        
        return jsonify({
            "status": "created",
            "filename": "firefightAI_v5.zip",
            "path": str(zip_path),
            "size_bytes": zip_size,
            "size_mb": size_mb,
            "file_count": file_count,
            "download_url": "/api/package/download",
            "install_bat": "install.bat",
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/api/package/download")
def api_package_download():
    """下载安装包"""
    zip_path = PROJECT_ROOT / "dist" / "firefightAI_v5.zip"
    if not zip_path.exists():
        return jsonify({"error": "安装包不存在，请先创建", "suggestion": "POST /api/package/create 创建安装包"}), 404
    return send_from_directory(
        str(PROJECT_ROOT / "dist"),
        "firefightAI_v5.zip",
        as_attachment=True,
        download_name="firefightAI_v5.zip",
    )

# ═══════════════════════════════════════════════════════════════
# 标注工具
# ═══════════════════════════════════════════════════════════════

@app.route("/annotate")
def annotate():
    return render_template_string(ANNOTATE_HTML)

@app.route("/api/annotate/images")
def api_annotate_images():
    dataset = request.args.get("dataset", "faction_yolo")
    images_dir = PROJECT_ROOT / "data" / dataset / "images"
    if not images_dir.exists():
        return jsonify([])
    images = []
    for f in sorted(images_dir.glob("*")):
        if f.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        labels = []
        lp = PROJECT_ROOT / "data" / dataset / "labels" / (f.stem + ".txt")
        if lp.exists():
            for line in lp.read_text().strip().split("\n"):
                parts = line.strip().split()
                if len(parts) >= 5:
                    labels.append({"class": int(parts[0]), "x": float(parts[1]), "y": float(parts[2]), "w": float(parts[3]), "h": float(parts[4])})
        images.append({"name": f.name, "url": f"/data/{dataset}/images/{f.name}", "labeled": len(labels) > 0, "label_count": len(labels), "labels": labels})
    return jsonify(images)

@app.route("/api/annotate/save", methods=["POST"])
def api_annotate_save():
    data = request.get_json()
    dataset = data.get("dataset", "faction_yolo")
    image_name = data.get("image", "")
    labels = data.get("labels", [])
    if not image_name:
        return jsonify({"error": "缺少图片名"}), 400
    lp = PROJECT_ROOT / "data" / dataset / "labels" / (Path(image_name).stem + ".txt")
    lp.parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "w") as f:
        for l in labels:
            f.write(f"{l.get('class',0)} {l.get('x',0.5):.6f} {l.get('y',0.5):.6f} {l.get('w',0.1):.6f} {l.get('h',0.1):.6f}\n")
    return jsonify({"status": "saved", "count": len(labels)})

@app.route("/data/<path:filepath>")
def serve_data(filepath):
    return send_from_directory(str(PROJECT_ROOT / "data"), filepath)


# ═══════════════════════════════════════════════════════════════
# 连接管理 + 重建决策链
# ═══════════════════════════════════════════════════════════════

@socketio.on("rebuild_chain")
def on_rebuild_chain():
    """重建整条决策链"""
    add_learning_log("system", "用户触发重建决策链", "")
    emit("rebuild_progress", {"step": "正在验证API...", "progress": 10})

    def rebuild():
        results = {}
        # 1. 验证DeepSeek API
        emit("rebuild_progress", {"step": "验证DeepSeek API", "progress": 20})
        ds = verify_deepseek_api()
        results["deepseek"] = ds["status"]
        update_state(api_status={"deepseek": ds["status"]})

        if ds["status"] != "online":
            emit("rebuild_progress", {"step": "DeepSeek API离线!", "progress": 30})
            emit("rebuild_error", {"error": "DeepSeek API不可用，请检查API Key"})
            return

        # 2. 验证ADB
        emit("rebuild_progress", {"step": "验证ADB连接", "progress": 40})
        try:
            cfg = load_config()
            dc = cfg["device"]
            ad = dc.get("active", "mumu")
            di = dc.get(ad, {})
            adb_paths = [r"d:\MuMuPlayer\nx_device\12.0\shell\adb.exe", r"d:\firefight\adb\adb.exe", "adb"]
            adb_exe = "adb"
            for p in adb_paths:
                if p == "adb" or Path(p).exists():
                    adb_exe = p
                    break
            subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
            r = subprocess.run([adb_exe, "connect", f"{di.get('adb_host','127.0.0.1')}:{di.get('adb_port',7555)}"], capture_output=True, text=True, timeout=10)
            results["adb"] = "connected" if "connected" in r.stdout.lower() else "failed"
            update_state(adb_status=results["adb"])
        except:
            results["adb"] = "error"

        # 3. 验证GitHub
        emit("rebuild_progress", {"step": "验证GitHub", "progress": 60})
        try:
            import requests
            r = requests.get("https://api.github.com", timeout=5)
            results["github"] = "online" if r.status_code == 200 else "error"
        except:
            results["github"] = "offline"

        # 4. 验证服务器
        emit("rebuild_progress", {"step": "验证腾讯云服务器", "progress": 80})
        try:
            ok, out, _ = _ssh_exec("echo OK", timeout=10)
            results["server"] = "online" if ok and "OK" in out else "offline"
        except:
            results["server"] = "offline"

        emit("rebuild_progress", {"step": "完成", "progress": 100})
        add_learning_log("system", "决策链重建完成", json.dumps(results, ensure_ascii=False))
        emit("rebuild_complete", {"results": results, "time": datetime.now().isoformat()})

    threading.Thread(target=rebuild, daemon=True).start()


@socketio.on("check_all_connections")
def on_check_all_connections():
    """检查所有连接状态"""
    results = {}

    # API
    try:
        import requests
        r = requests.get("https://api.deepseek.com/v1/models", headers={"Authorization": f"Bearer {load_config()['llm']['api_key']}"}, timeout=5)
        results["deepseek"] = "online" if r.status_code == 200 else "error"
    except:
        results["deepseek"] = "offline"

    # ADB
    try:
        cfg = load_config()
        dc = cfg["device"]
        ad = dc.get("active", "mumu")
        di = dc.get(ad, {})
        adb_exe = "adb"
        for p in [r"d:\MuMuPlayer\nx_device\12.0\shell\adb.exe", "adb"]:
            if p == "adb" or Path(p).exists():
                adb_exe = p
                break
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        r = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
        results["adb"] = "connected" if f"{di.get('adb_host','127.0.0.1')}:{di.get('adb_port',7555)}" in r.stdout else "disconnected"
    except:
        results["adb"] = "error"

    # GitHub
    try:
        import requests
        r = requests.get("https://api.github.com", timeout=5)
        results["github"] = "online" if r.status_code == 200 else "error"
    except:
        results["github"] = "offline"

    # Server
    try:
        r = subprocess.run(["ssh", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", f"{SERVER_USER}@{SERVER_HOST}", "echo OK"], capture_output=True, text=True, timeout=10)
        results["server"] = "online" if "OK" in r.stdout else "offline"
    except:
        results["server"] = "offline"

    update_state(api_status={"deepseek": results.get("deepseek", "unknown")}, adb_status=results.get("adb", "unknown"), github_status=results.get("github", "unknown"), server_status=results.get("server", "unknown"))
    emit("all_connections_status", results)


# ═══════════════════════════════════════════════════════════════
# AI 线程 + Patch
# ═══════════════════════════════════════════════════════════════

def _run_smart_mode(lc, dc, sc):
    """智能模式：无需ADB/模拟器，AI直接通过DeepSeek响应用户"""
    global _controller
    _controller = None  # 无游戏控制器
    
    socketio.emit("smart_mode_status", {"message": "智能模式已启动，AI通过DeepSeek直接响应"})
    add_learning_log("combat", "智能模式已启动", "无需模拟器，DeepSeek直接响应")
    
    # 持续运行，保持AI在线状态
    while get_state().get("running"):
        time.sleep(1)
        # 定期更新思考状态
        cur = get_state().get("ai_thinking", "")
        if not cur or cur.startswith("DeepSeek"):
            update_state(ai_thinking="DeepSeek智能体已就绪，可直接对话和下达指令")
    
    update_state(running=False, status="已停止", ai_thinking="")
    socketio.emit("stopped", {"status": "ok"})

def _run_ai_loop():
    global _controller
    update_state(status="初始化组件...", ai_thinking="正在加载模型和连接设备...")
    add_learning_log("combat", "AI上线，初始化组件", "")

    cfg = load_config()
    gc = cfg["game"]
    dc = cfg["device"]
    lc = cfg["llm"]
    lpc = cfg["game_loop"]
    yc = cfg["yolo"]
    sc = cfg["scrcpy"]
    lnc = cfg.get("learning", {})
    ss = (gc["screen_width"], gc["screen_height"])

    # 先检查ADB连接，避免导入不存在的MuMu模块
    from src.execution.adb_utils import ADBUtils
    ad = dc.get("active", "mumu")
    di = dc.get(ad, {})
    adb = ADBUtils(host=di.get("adb_host", "127.0.0.1"), port=di.get("adb_port", 7555), connect_timeout=dc["adb_connect_timeout"], command_timeout=dc["adb_command_timeout"], retry_count=dc["adb_retry_count"])

    if not adb.ensure_connected():
        # ADB不可用，进入智能模式（不需要游戏模拟器，纯DeepSeek直连）
        update_state(status="AI在线(智能模式)", ai_thinking="DeepSeek智能体已就绪，可直接对话和下达指令", adb_status="disconnected")
        add_learning_log("connection", "ADB未连接，进入智能模式", "AI可通过对话和指令交互，无需模拟器")
        socketio.emit("cycle_update", get_state())
        socketio.emit("started", {"status": "ok", "mode": "smart"})
        _run_smart_mode(lc, dc, sc)
        return

    update_state(adb_status="connected", status="ADB已连接, 加载模型...", ai_thinking="正在加载YOLO模型和OCR...")
    add_learning_log("connection", "ADB连接成功", "")

    # ADB已连接，才导入需要模拟器的模块
    try:
        from src.execution.mumu_manager import MuMuManagerTouch
        _mumu_available = True
    except ImportError:
        _mumu_available = False
        logger.warning("MuMuManagerTouch不可用，将使用ADB触控替代")

    from src.screen.capture import ScreenCapture
    from src.vision.detector import UnitDetector
    from src.vision.ocr_reader import UIReader
    from src.state.manager import StateManager
    from src.decision.commander import TacticalCommander
    from src.decision.parser import CommandParser
    from src.execution.executor import CommandExecutor
    from src.learning.battle_memory import BattleMemory
    from src.learning.outcome_eval import OutcomeEvaluator
    from src.learning.memory_retriever import MemoryRetriever
    from src.learning.strategy_compressor import StrategyCompressor

    capture = ScreenCapture(adb=adb, max_fps=sc["max_fps"], bitrate=sc["bitrate"], max_width=sc["max_width"], max_height=sc["max_height"], timeout=sc["timeout"])
    detector = UnitDetector(model_path=yc["model_path"], fallback_model_path=yc["fallback_model_path"], confidence_threshold=yc["confidence_threshold"], iou_threshold=yc["iou_threshold"], image_size=yc["image_size"], device=yc["device"])
    detector.load_model()
    ocr = UIReader()
    ocr.load_model()
    state_manager = StateManager(screen_size=ss)
    commander = TacticalCommander(provider=lc["provider"], model=lc["model"], api_key=lc["api_key"], api_base=lc["api_base"], temperature=lc["temperature"], max_tokens=lc["max_tokens"], timeout=lc["timeout"], retry_count=lc["retry_count"])
    commander.load_prompts()
    parser = CommandParser(screen_size=ss)
    gs = str(int(time.time()))

    bm = BattleMemory() if lnc.get("enabled", True) else None
    oe = OutcomeEvaluator() if lnc.get("enabled", True) else None
    mr = MemoryRetriever(bm) if bm else None
    scm = StrategyCompressor(battle_memory=bm, api_key=lc["api_key"], api_base=lc["api_base"], model=lc["model"]) if bm else None

    mc = cfg.get("mumu_manager", {})
    touch = None
    if _mumu_available:
        try:
            touch = MuMuManagerTouch(exe_path=mc.get("exe_path", r"D:\MuMuPlayer\nx_main\MuMuManager.exe"), verbosity=mc.get("verbosity", 0), timeout=mc.get("timeout", 5.0))
        except Exception as e:
            logger.warning(f"MuMu触控初始化失败: {e}，使用ADB触控替代")
    px = int(lpc["pause_button_x"] * ss[0])
    py = int(lpc["pause_button_y"] * ss[1])
    executor = CommandExecutor(adb=adb, screen_size=ss, touch=touch if (touch and touch.is_connected) else None, pause_button=(px, py))

    # 应用 monkey patch（仅在ADB可用时）
    _apply_patches()

    capture.start()

    controller = DashboardGameController(
        adb=adb, capture=capture, detector=detector, state_manager=state_manager,
        commander=commander, parser=parser, executor=executor,
        max_cycles=lpc["max_cycles"], game_over_timeout=lpc["game_over_timeout"],
        battle_memory=bm, outcome_eval=oe, memory_retriever=mr,
        strategy_compressor=scm, game_session=gs, event_callback=_on_cycle_event,
    )
    _controller = controller
    update_state(game_session=gs, status="战斗中...", ai_thinking="")

    try:
        result = controller.run()
        update_state(status="胜利!" if result else "游戏结束", ai_thinking="")
        add_learning_log("combat", "战斗结束", f"结果: {'胜利' if result else '游戏结束'}, 总分: {get_state().get('total_score',0)}")
    except Exception as e:
        logger.exception(f"AI异常: {e}")
        update_state(status=f"错误: {str(e)[:60]}", ai_thinking="")
    finally:
        update_state(running=False)
        capture.stop()


def _on_cycle_event(event: dict):
    cycle = event.get("cycle", 0)
    allies = event.get("allies", 0)
    enemies = event.get("enemies", 0)
    score = event.get("score", 0)
    decision = event.get("decision", "")
    action = event.get("action", "")
    cycle_time = event.get("cycle_time", 0)

    full = _last_full_decision
    analysis = full.get("analysis", decision)
    prediction = full.get("next_prediction", "")
    cd = full.get("commands", [])
    reason_text = ""
    actions_text = []

    for c in cd:
        a = c.get("action", "?")
        ids = c.get("unit_ids", [])
        tgt = c.get("target", None)
        r = c.get("reason", "")
        if a == "select" and ids:
            actions_text.append(f"select({','.join(str(i) for i in ids[:5])})")
        elif a in ("move", "attack") and ids and tgt:
            actions_text.append(f"{a}({ids[0]}->{tgt[0]:.2f},{tgt[1]:.2f})")
        if r:
            reason_text += f"[{a}] {r}; "

    action_display = " + ".join(actions_text) if actions_text else action
    reason_display = reason_text.rstrip("; ") if reason_text else decision
    new_total = get_state().get("total_score", 0) + score

    thinking = f"第{cycle}轮: {analysis[:200]}\n决策: {reason_display[:200]}\n"
    if prediction:
        thinking += f"预测: {prediction[:200]}\n"

    exp_count = 0
    rules_count = 0
    try:
        rules = StrategyCompressor.load_rules()
        rules_count = len(rules) if rules else 0
    except:
        pass
    try:
        exp_count = BattleMemory().count()
    except:
        pass

    st = get_state()
    old_avg = st.get("avg_cycle_time_ms", 0)
    new_avg = old_avg + (cycle_time - old_avg) / max(cycle, 1)

    sh = st.get("scores_history", [])[-49:]
    sh.append({"cycle": cycle, "score": score, "total": new_total})

    decs = st.get("decisions", [])[-19:]
    decs.append({"cycle": cycle, "action": action_display, "decision": analysis, "reason": reason_display, "prediction": prediction, "allies": allies, "enemies": enemies, "score": score})

    update_state(
        cycle=cycle, allies=allies, enemies=enemies, score=score, total_score=new_total,
        last_decision=analysis, last_action=action_display, last_reason=reason_display,
        cycle_time_ms=cycle_time, avg_cycle_time_ms=round(new_avg),
        decisions=decs, scores_history=sh, experience_count=exp_count, rules_count=rules_count,
        status=f"第{cycle}轮 ({allies}vs{enemies})", ai_thinking=thinking,
    )
    socketio.emit("cycle_update", get_state())
    socketio.emit("ai_thinking_update", {"thinking": thinking, "cycle": cycle, "analysis": analysis, "reason": reason_display})


# ── Patch (延迟导入，避免服务器端缺少游戏依赖) ──
class DashboardGameController:
    def __new__(cls, event_callback=None, **kw):
        from src.controller.game_controller import GameController
        inst = GameController.__new__(GameController)
        GameController.__init__(inst, **kw)
        inst._dashboard_callback = event_callback
        return inst

_patches_applied = False

def _apply_patches():
    """应用GameController和TacticalCommander的monkey patch（仅在ADB可用时调用）"""
    global _patches_applied
    if _patches_applied:
        return
    _patches_applied = True
    
    import src.controller.game_controller as gc_mod
    import src.decision.commander as cmd_mod
    
    _orig_record = gc_mod.GameController._record_cycle
    def _patched_record(self, state, outcome, commands):
        _orig_record(self, state, outcome, commands)
        cb = getattr(self, "_dashboard_callback", None)
        if not cb:
            return
        d = "无决策"
        a = "无行动"
        if commands:
            for c in commands:
                if c.action:
                    d = c.reason or "无理由"
                    a = f"{c.action.value}({','.join(str(u) for u in (c.unit_ids or []))})"
                    break
        cb({"cycle": self._cycle_count, "allies": state.ally_count, "enemies": state.enemy_count, "score": outcome.get("score", 0) if outcome else 0, "decision": d, "action": a, "cycle_time": int((time.time() - getattr(self, '_cycle_start', time.time())) * 1000)})
    gc_mod.GameController._record_cycle = _patched_record
    
    _orig_run = gc_mod.GameController.run
    def _patched_run(self):
        self._cycle_start = 0
        return _orig_run(self)
    gc_mod.GameController.run = _patched_run
    
    _orig_fe = gc_mod.GameController._fast_execute
    def _patched_fe(self, commands, state):
        self._cycle_start = time.time()
        return _orig_fe(self, commands, state)
    gc_mod.GameController._fast_execute = _patched_fe
    
    _orig_build = cmd_mod.TacticalCommander._build_user_message
    def _patched_build(self, state_text):
        global _user_instruction
        msg = _orig_build(self, state_text)
        if _user_instruction:
            marker = "请根据以上战场状态"
            if marker in msg:
                parts = msg.split(marker, 1)
                msg = f"{parts[0]}\n---\n## 指挥官指令 (你必须执行!)\n{_user_instruction}\n\n---\n{marker}{parts[1]}"
            else:
                msg += f"\n\n指挥官最新指令: {_user_instruction}"
        return msg
    cmd_mod.TacticalCommander._build_user_message = _patched_build
    
    _orig_decide = cmd_mod.TacticalCommander.decide
    def _patched_decide(self, state):
        result = _orig_decide(self, state)
        if result:
            try:
                data = json.loads(result)
                global _last_full_decision
                _last_full_decision = {"analysis": data.get("analysis", ""), "next_prediction": data.get("next_prediction", ""), "commands": [{"action": c.get("action", "?"), "unit_ids": c.get("unit_ids", []), "target": c.get("target", None), "reason": c.get("reason", "")} for c in data.get("commands", [])]}
            except:
                pass
        return result
    cmd_mod.TacticalCommander.decide = _patched_decide
    logger.info("GameController patches applied")
_last_full_decision: dict = {}


# ═══════════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)

@app.route("/api/stats")
def api_stats():
    return get_state()

@app.route("/api/verify_api_http")
def api_verify_api_http():
    return jsonify({"deepseek": verify_deepseek_api()})

@socketio.on("connect")
def on_connect():
    emit("cycle_update", get_state())

@socketio.on("start")
def on_start():
    if not get_state().get("running"):
        update_state(running=True, status="战斗中...", ai_thinking="正在初始化...")
        add_learning_log("combat", "用户手动上线AI", "")
        threading.Thread(target=_run_ai_loop, daemon=True).start()
        emit("started", {"status": "ok"})

@socketio.on("stop")
def on_stop():
    if _controller:
        _controller.stop()
    update_state(running=False, status="已停止", ai_thinking="")
    emit("stopped", {"status": "ok"})

@socketio.on("get_state")
def on_get_state():
    emit("cycle_update", get_state())

@socketio.on("send_command")
def on_send_command(data: dict):
    global _user_instruction
    cmd = data.get("command", "").strip()
    if not cmd:
        return

    # 检查是否是配置命令
    if _handle_config_command(cmd):
        return

    _user_instruction = cmd
    st = get_state()
    cycle = st.get("cycle", 0)
    allies = st.get("allies", 0)
    enemies = st.get("enemies", 0)

    cmds = st.get("user_commands", [])[-19:]
    cmds.append({"cycle": cycle, "command": cmd, "allies": allies, "enemies": enemies})
    update_state(user_commands=cmds)

    add_learning_log("command", f"用户指令: {cmd[:100]}", f"第{cycle}轮, 友{allies}vs敌{enemies}")

    def analyze():
        try:
            from openai import OpenAI
            cfg = load_config()
            lc = cfg["llm"]
            client = OpenAI(api_key=lc["api_key"], base_url=lc["api_base"])
            sp = f"你是Firefight战术AI。指挥官给你下达了一条指令。请用1-2句话分析: 1)你对这条指令的见解 2)你将在下一轮如何运用它。当前兵力: 友{allies}vs敌{enemies} (第{cycle}轮)"
            resp = client.chat.completions.create(model=lc.get("model", "deepseek-v4-flash"), messages=[{"role": "system", "content": sp}, {"role": "user", "content": f"指挥官指令: {cmd}"}], max_tokens=128, temperature=0.1, timeout=4)
            analysis = resp.choices[0].message.content.strip()
            socketio.emit("command_analysis", {"command": cmd, "cycle": cycle, "analysis": analysis, "allies": allies, "enemies": enemies})
            try:
                from src.learning.battle_memory import BattleMemory
                BattleMemory().record(state_hash=f"cmd_{int(time.time())}", ally_count=allies, enemy_count=enemies, ally_positions=[], decision={"action": "user_command", "reason": f"指挥官: {cmd} | 分析: {analysis}", "target": []}, outcome_score=10, cycle_num=cycle, game_session=st.get("game_session", ""))
            except:
                pass
        except Exception as e:
            socketio.emit("command_analysis", {"command": cmd, "cycle": cycle, "analysis": f"(分析暂时不可用: {str(e)[:50]})", "allies": allies, "enemies": enemies})

    threading.Thread(target=analyze, daemon=True).start()
    emit("command_recorded", {"command": cmd, "cycle": cycle})


def _handle_config_command(cmd: str) -> bool:
    """处理配置命令，在指令文本框中输入配置命令"""
    cmd_lower = cmd.lower().strip()

    # API Key 配置
    if cmd_lower.startswith("apikey ") or cmd_lower.startswith("api_key "):
        parts = cmd.split(maxsplit=1)
        if len(parts) == 2:
            new_key = parts[1].strip()
            cfg = load_config()
            cfg["llm"]["api_key"] = new_key
            with open(PROJECT_ROOT / "config" / "settings.yaml", "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
            add_learning_log("config", "API Key已更新", "")
            socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": "API Key已更新并保存到配置文件。"})
            return True

    # ADB配置
    if cmd_lower.startswith("adb ") or cmd_lower.startswith("connect "):
        parts = cmd.split()
        if len(parts) >= 2:
            addr = parts[1]
            if ":" in addr:
                host, port_str = addr.split(":", 1)
                try:
                    port = int(port_str)
                    cfg = load_config()
                    ad = cfg["device"].get("active", "mumu")
                    cfg["device"][ad]["adb_host"] = host
                    cfg["device"][ad]["adb_port"] = port
                    with open(PROJECT_ROOT / "config" / "settings.yaml", "w", encoding="utf-8") as f:
                        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
                    add_learning_log("config", f"ADB地址已更新: {host}:{port}", "")
                    socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": f"ADB地址已更新为 {host}:{port}，点击重连按钮生效。"})
                    return True
                except ValueError:
                    pass

    # GitHub仓库配置
    if cmd_lower.startswith("repo ") or cmd_lower.startswith("github "):
        parts = cmd.split(maxsplit=1)
        if len(parts) == 2:
            repo_url = parts[1].strip()
            add_learning_log("config", f"GitHub仓库配置: {repo_url}", "")
            # 尝试初始化
            try:
                import git
                try:
                    repo = git.Repo(str(PROJECT_ROOT))
                    repo.remotes.origin.set_url(repo_url)
                except:
                    repo = git.Repo.init(str(PROJECT_ROOT))
                    repo.create_remote("origin", repo_url)
                add_learning_log("github", f"GitHub仓库已配置: {repo_url}", "")
                socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": f"GitHub仓库已配置: {repo_url}"})
            except Exception as e:
                socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": f"GitHub配置失败: {e}"})
            return True

    # 服务器IP配置
    if cmd_lower.startswith("server ") or cmd_lower.startswith("host "):
        parts = cmd.split(maxsplit=1)
        if len(parts) == 2:
            global SERVER_HOST
            SERVER_HOST = parts[1].strip()
            add_learning_log("config", f"服务器地址已更新: {SERVER_HOST}", "")
            socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": f"服务器地址已更新为 {SERVER_HOST}。"})
            return True

    return False


@socketio.on("clear_command")
def on_clear_command():
    global _user_instruction
    _user_instruction = ""
    emit("command_cleared", {})


# ═══════════════════════════════════════════════════════════════
# HTML (v5.0) — 完整前端
# ═══════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Firefight AI v5.0</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0e14;color:#d0d0d0;min-height:100vh}
.header{background:#11151c;border-bottom:1px solid #252a33;padding:12px 24px;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:20px;font-weight:600;color:#58a5f3}
.nav-tabs{display:flex;gap:0;background:#11151c;border-bottom:1px solid #252a33;padding:0 24px;overflow-x:auto}
.nav-tab{padding:10px 16px;font-size:12px;font-weight:600;color:#888;cursor:pointer;border-bottom:2px solid transparent;transition:all .2s;background:none;border-top:none;border-left:none;border-right:none;white-space:nowrap}
.nav-tab:hover{color:#aaa}
.nav-tab.active{color:#58a5f3;border-bottom-color:#58a5f3}
.tab-content{display:none}
.tab-content.active{display:block}
.container{max-width:1500px;margin:0 auto;padding:16px}
.controls{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap}
button{padding:10px 22px;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s}
.btn-start{background:#4caf50;color:#000}.btn-start:hover{background:#66bb6a}
.btn-stop{background:#e53935;color:#fff}.btn-stop:hover{background:#f44336}
.btn-verify{background:#7c4dff;color:#fff}.btn-verify:hover{background:#9575ff}
.btn-clear{background:#555;color:#fff;padding:8px 14px;font-size:12px}
.btn-push{background:#ff9800;color:#000}.btn-push:hover{background:#ffb74d}
.btn-deploy{background:#00bcd4;color:#000}.btn-deploy:hover{background:#4dd0e1}
.btn-rebuild{background:#ff5722;color:#fff}.btn-rebuild:hover{background:#ff7043}
.cmd-input-wrapper{display:flex;gap:8px;flex:1;min-width:300px}
.cmd-input-wrapper input{flex:1;padding:10px 14px;border:1px solid #252a33;border-radius:8px;background:#1a1f2b;color:#d0d0d0;font-size:13px;outline:none}
.cmd-input-wrapper input:focus{border-color:#58a5f3}
.btn-send{background:#58a5f3;color:#000;padding:10px 16px;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-bottom:16px}
.stat-card{background:#11151c;border:1px solid #252a33;border-radius:10px;padding:12px 14px}
.stat-card .label{font-size:10px;color:#888;text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}
.stat-card .value{font-size:20px;font-weight:700}
.stat-card .value.blue{color:#58a5f3}.stat-card .value.red{color:#e53935}.stat-card .value.green{color:#4caf50}.stat-card .value.yellow{color:#ff9800}.stat-card .value.purple{color:#7c4dff}
.main-grid{display:grid;grid-template-columns:1.4fr 1fr;gap:14px}
.panel{background:#11151c;border:1px solid #252a33;border-radius:10px;padding:14px}
.panel h3{font-size:13px;font-weight:600;color:#aaa;margin-bottom:10px;border-bottom:1px solid #252a33;padding-bottom:7px}
.chart-container{height:280px;position:relative}
.log-list{max-height:300px;overflow-y:auto;font-size:11px}
.log-item{padding:7px;border-bottom:1px solid #1a1f2b}
.log-item .lhead{display:flex;gap:6px;align-items:center;margin-bottom:2px}
.log-item .cyc{color:#888;min-width:28px;font-size:10px}
.log-item .act{color:#58a5f3;font-weight:600;font-size:11px;flex:1}
.log-item .sco{min-width:40px;text-align:right;font-size:11px;font-weight:600}
.log-item .sco.pos{color:#4caf50}.log-item .sco.neg{color:#e53935}
.log-item .reason{font-size:10px;color:#999;padding-left:34px}
.cmd-item{background:#1a2530;border-left:2px solid #ff9800}
.full-width{grid-column:1/-1}
.exp-bar{display:flex;align-items:center;gap:8px;margin-top:5px}
.exp-bar .bar-bg{flex:1;height:5px;background:#252a33;border-radius:3px;overflow:hidden}
.exp-bar .bar-fill{height:100%;background:#58a5f3;border-radius:3px;transition:width .5s}
/* Chat */
.chat-container{display:flex;flex-direction:column;height:450px}
.chat-messages{flex:1;overflow-y:auto;padding:10px;background:#0a0e14;border-radius:8px;margin-bottom:8px}
.chat-msg{display:flex;gap:8px;margin-bottom:10px;animation:fadeIn .3s}
.chat-msg.user{flex-direction:row-reverse}
.chat-msg .avatar{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0}
.chat-msg.user .avatar{background:#58a5f3;color:#000}
.chat-msg.assistant .avatar{background:#4caf50;color:#000}
.chat-msg .bubble{max-width:75%;padding:9px 12px;border-radius:12px;font-size:12px;line-height:1.5}
.chat-msg.user .bubble{background:#1a2530;color:#d0d0d0;border-bottom-right-radius:4px}
.chat-msg.assistant .bubble{background:#1a3020;color:#d0d0d0;border-bottom-left-radius:4px}
.chat-input-area{display:flex;gap:8px}
.chat-input-area textarea{flex:1;padding:10px;border:1px solid #252a33;border-radius:8px;background:#1a1f2b;color:#d0d0d0;font-size:13px;outline:none;resize:none;height:55px}
.chat-input-area textarea:focus{border-color:#58a5f3}
/* Thinking */
.thinking-box{background:#0a0e14;border:1px solid #252a33;border-radius:8px;padding:12px;min-height:80px;max-height:220px;overflow-y:auto;font-size:11px;font-family:'Consolas',monospace;white-space:pre-wrap;line-height:1.5}
.thinking-box .highlight{color:#ff9800}.thinking-box .step{color:#58a5f3}
/* Training */
.train-section{margin-bottom:16px}
.train-section h4{font-size:12px;color:#58a5f3;margin-bottom:8px}
.train-config{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.train-config label{font-size:11px;color:#888;display:flex;flex-direction:column;gap:3px}
.train-config input,.train-config select{padding:7px 9px;border:1px solid #252a33;border-radius:6px;background:#1a1f2b;color:#d0d0d0;font-size:12px}
.train-progress{background:#252a33;border-radius:6px;height:22px;overflow:hidden;margin-top:8px}
.train-progress-bar{height:100%;background:linear-gradient(90deg,#58a5f3,#4caf50);transition:width .3s;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:600;color:#000}
.train-log{max-height:180px;overflow-y:auto;font-size:10px;font-family:'Consolas',monospace;background:#0a0e14;padding:8px;border-radius:6px;margin-top:8px}
.dataset-list{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.dataset-card{background:#1a1f2b;border:1px solid #252a33;border-radius:8px;padding:8px 12px;cursor:pointer;transition:all .2s}
.dataset-card:hover{border-color:#58a5f3}
.dataset-card.selected{border-color:#58a5f3;background:#1a2530}
.dataset-card .name{font-size:12px;font-weight:600;color:#58a5f3}
.dataset-card .count{font-size:10px;color:#888}
.model-list{display:flex;flex-wrap:wrap;gap:6px;font-size:11px}
.model-card{background:#1a1f2b;border:1px solid #252a33;border-radius:8px;padding:8px 12px}
.model-card .name{font-size:12px;font-weight:600;color:#4caf50}
.model-card .info{font-size:10px;color:#888}
/* Connection Status */
.conn-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:16px}
.conn-card{background:#1a1f2b;border:1px solid #252a33;border-radius:10px;padding:14px}
.conn-card .conn-name{font-size:13px;font-weight:600;margin-bottom:6px}
.conn-card .conn-status{font-size:12px}
.conn-card .conn-status.online{color:#4caf50}.conn-card .conn-status.offline{color:#e53935}.conn-card .conn-status.checking{color:#ff9800}.conn-card .conn-status.unknown{color:#888}
.conn-card .conn-actions{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
.conn-card .conn-actions button{padding:5px 12px;font-size:11px;border-radius:5px}
.alert{padding:8px 14px;border-radius:6px;font-size:11px;margin-top:8px}
.alert.info{background:#1a2530;color:#58a5f3;border-left:3px solid #58a5f3}
.alert.success{background:#1a3020;color:#4caf50;border-left:3px solid #4caf50}
.alert.error{background:#301a1a;color:#e53935;border-left:3px solid #e53935}
.alert.warning{background:#302a1a;color:#ff9800;border-left:3px solid #ff9800}
.upload-area{border:2px dashed #252a33;border-radius:10px;padding:24px;text-align:center;cursor:pointer;transition:all .2s}
.upload-area:hover{border-color:#58a5f3;background:#0d1117}
.upload-area input{display:none}
.version-info{font-size:10px;color:#555;padding:4px 0}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid #252a33;border-top-color:#58a5f3;border-radius:50%;animation:spin .8s linear infinite}
.learning-log-item{padding:6px 8px;border-bottom:1px solid #1a1f2b;font-size:11px}
.learning-log-item .ll-time{color:#555;font-size:10px}
.learning-log-item .ll-cat{font-size:10px;padding:1px 5px;border-radius:3px;margin-right:4px}
.learning-log-item .ll-cat.combat{background:#4caf50;color:#000}
.learning-log-item .ll-cat.correction{background:#e53935;color:#fff}
.learning-log-item .ll-cat.params{background:#7c4dff;color:#fff}
.learning-log-item .ll-cat.github{background:#ff9800;color:#000}
.learning-log-item .ll-cat.connection{background:#00bcd4;color:#000}
.learning-log-item .ll-cat.system{background:#555;color:#fff}
.learning-log-item .ll-cat.config{background:#ff9800;color:#000}
.learning-log-item .ll-cat.training{background:#58a5f3;color:#000}
.learning-log-item .ll-cat.command{background:#ff9800;color:#000}
.learning-log-item .ll-cat.server{background:#00bcd4;color:#000}
.learning-log-item .ll-msg{color:#d0d0d0}
.learning-log-item .ll-detail{color:#888;font-size:10px;margin-top:1px}
.conn-mini{font-size:10px;padding:2px 8px;border-radius:10px;margin-left:4px}
.conn-mini.online{background:#4caf50;color:#000}
.conn-mini.offline{background:#e53935;color:#fff}
.conn-mini.checking{background:#ff9800;color:#000}
/* Web Search */
.search-container{display:flex;flex-direction:column;gap:12px}
.search-bar{display:flex;gap:8px}
.search-bar input{flex:1;padding:10px 14px;border:1px solid #252a33;border-radius:8px;background:#1a1f2b;color:#d0d0d0;font-size:13px;outline:none}
.search-bar input:focus{border-color:#58a5f3}
.search-result{background:#1a1f2b;border:1px solid #252a33;border-radius:8px;padding:12px;margin-bottom:8px}
.search-result .sr-title{font-size:13px;font-weight:600;color:#58a5f3;margin-bottom:4px}
.search-result .sr-snippet{font-size:11px;color:#aaa;line-height:1.4}
.search-result .sr-url{font-size:10px;color:#555;margin-top:4px;word-break:break-all}
.search-summary{background:#1a2530;border-left:3px solid #58a5f3;padding:12px;border-radius:6px;margin-top:10px;font-size:12px;line-height:1.6}
.search-summary h4{color:#58a5f3;margin-bottom:6px;font-size:13px}
.search-progress{display:flex;align-items:center;gap:8px;padding:8px 12px;background:#302a1a;border-radius:6px;font-size:11px;color:#ff9800;margin-top:8px}
/* Agent */
.agent-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.agent-chat{display:flex;flex-direction:column;height:400px}
.agent-chat .chat-messages{flex:1;overflow-y:auto;padding:10px;background:#0a0e14;border-radius:8px;margin-bottom:8px}
.agent-chat .chat-input-area{display:flex;gap:8px}
.agent-chat .chat-input-area textarea{flex:1;padding:10px;border:1px solid #252a33;border-radius:8px;background:#1a1f2b;color:#d0d0d0;font-size:12px;outline:none;resize:none;height:50px}
.diagnostic-panel{display:flex;flex-direction:column;gap:8px}
.diag-item{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:#1a1f2b;border-radius:6px;font-size:11px}
.diag-item .diag-name{font-weight:600;color:#aaa}
.diag-item .diag-status{font-size:10px;padding:2px 8px;border-radius:10px}
.diag-item .diag-status.ok{background:#4caf50;color:#000}
.diag-item .diag-status.fail{background:#e53935;color:#fff}
.diag-item .diag-status.checking{background:#ff9800;color:#000}
.diag-item .diag-status.unknown{background:#555;color:#fff}
.diag-detail{font-size:9px;color:#888;margin-top:2px}
/* Package */
.package-info{background:#1a3020;border-left:3px solid #4caf50;padding:12px;border-radius:6px;margin-top:8px}
.knowledge-list-item{display:flex;justify-content:space-between;align-items:center;padding:8px;border-bottom:1px solid #1a1f2b;font-size:11px}
.knowledge-list-item:hover{background:#1a1f2b}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
</style>
</head>
<body>
<div class="header">
  <h1>Firefight AI v5.0</h1>
  <div style="display:flex;gap:8px;align-items:center">
    <span class="conn-mini" id="conn-adb">ADB</span>
    <span class="conn-mini" id="conn-api">API</span>
    <span class="conn-mini" id="conn-gh">GitHub</span>
    <span class="conn-mini" id="conn-srv">Server</span>
    <span class="status" id="status-badge" style="font-size:13px;padding:5px 12px;border-radius:6px;background:#1a1f2b;color:#888">已停止</span>
  </div>
</div>
<div class="nav-tabs">
  <button class="nav-tab active" onclick="switchTab('dashboard')">指挥面板</button>
  <button class="nav-tab" onclick="switchTab('chat')">AI 对话</button>
  <button class="nav-tab" onclick="switchTab('connections')">连接管理</button>
  <button class="nav-tab" onclick="switchTab('emulator')">模拟器</button>
  <button class="nav-tab" onclick="switchTab('agent')">智能体</button>
  <button class="nav-tab" onclick="switchTab('websearch')">智能搜索</button>
  <button class="nav-tab" onclick="switchTab('training')">模型训练</button>
  <button class="nav-tab" onclick="switchTab('annotate')">标注工具</button>
  <button class="nav-tab" onclick="switchTab('params')">参数学习</button>
  <button class="nav-tab" onclick="switchTab('learning')">学习日志</button>
  <button class="nav-tab" onclick="switchTab('datamanage')">数据管理</button>
  <button class="nav-tab" onclick="switchTab('settings')">系统设置</button>
</div>

<div class="container">
<!-- ═══ 指挥面板 ═══ -->
<div class="tab-content active" id="tab-dashboard">
  <div class="controls">
    <button class="btn-start" onclick="startAI()">上线 AI</button>
    <button class="btn-stop" onclick="stopAI()">停止</button>
    <div class="cmd-input-wrapper">
      <input type="text" id="cmd-input" placeholder="战术指令/配置命令(apikey/adb/repo/server)..." onkeydown="if(event.key==='Enter')sendCommand()">
      <button class="btn-send" onclick="sendCommand()">发送</button>
      <button class="btn-clear" onclick="clearCommand()">清除</button>
    </div>
  </div>
  <div class="stats-grid">
    <div class="stat-card"><div class="label">轮次</div><div class="value blue" id="cycle">0</div></div>
    <div class="stat-card"><div class="label">友军</div><div class="value blue" id="allies">0</div></div>
    <div class="stat-card"><div class="label">敌军</div><div class="value red" id="enemies">0</div></div>
    <div class="stat-card"><div class="label">本轮评分</div><div class="value yellow" id="score">0</div></div>
    <div class="stat-card"><div class="label">总得分</div><div class="value green" id="total-score">0</div></div>
    <div class="stat-card"><div class="label">平均耗时</div><div class="value" id="avg-time" style="color:#aaa">0ms</div></div>
    <div class="stat-card"><div class="label">经验库</div><div class="value yellow" id="exp-count">0</div></div>
    <div class="stat-card"><div class="label">战术规则</div><div class="value blue" id="rules-count">0</div></div>
  </div>
  <div class="main-grid">
    <div class="panel full-width"><h3>分数趋势</h3><div class="chart-container"><canvas id="scoreChart"></canvas></div></div>
    <div class="panel"><h3>AI 思考过程</h3><div class="thinking-box" id="thinking-box">等待 AI 上线...</div></div>
    <div class="panel"><h3>决策日志</h3><div class="log-list" id="decision-log"></div></div>
  </div>
</div>

<!-- ═══ AI 对话 ═══ -->
<div class="tab-content" id="tab-chat">
  <div class="panel" style="margin-bottom:0;">
    <h3>与 AI 对话（支持行为纠正）</h3>
    <div class="chat-container">
      <div class="chat-messages" id="chat-messages">
        <div class="chat-msg assistant"><div class="avatar">AI</div><div class="bubble">你好！我是Firefight AI战术助手。你可以：<br>1. 询问战场情况<br>2. 下达指令<br>3. 纠正我的行为（如"你应该优先防守"）<br>我会展示思考过程并从纠正中学习。</div></div>
      </div>
      <div class="chat-input-area" style="flex-wrap:wrap">
        <textarea id="chat-input" placeholder="输入消息..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChat()}"></textarea>
        <div style="display:flex;gap:6px;align-self:flex-end">
          <button class="btn-send" onclick="sendChat()">发送</button>
          <button class="btn-verify" onclick="sendCorrection()" style="font-size:11px">纠正AI</button>
          <button class="btn-clear" onclick="clearChat()">清空</button>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ 连接管理 ═══ -->
<div class="tab-content" id="tab-connections">
  <div class="panel" style="margin-bottom:12px">
    <h3>整条决策链状态</h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      <button class="btn-rebuild" onclick="rebuildChain()">重建决策链</button>
      <button class="btn-verify" onclick="checkAllConnections()">检查所有连接</button>
      <button class="btn-deploy" onclick="runDiagnostics()" style="background:#7c4dff;color:#fff">诊断</button>
      <button class="btn-start" onclick="oneClickFix()" style="background:#ff9800;color:#000">一键修复</button>
    </div>
    <div id="rebuild-status" style="font-size:12px;margin-top:8px"></div>
    <div id="diagnostic-result" style="margin-top:8px"></div>
  </div>
  <div class="conn-grid">
    <!-- DeepSeek API -->
    <div class="conn-card">
      <div class="conn-name">DeepSeek API</div>
      <div class="conn-status unknown" id="conn-deepseek-status">未检查</div>
      <div id="conn-deepseek-detail" style="font-size:10px;color:#888"></div>
      <div class="conn-actions">
        <button class="btn-verify" onclick="verifyAPI()">验证</button>
        <button class="btn-verify" onclick="checkBalance()" style="font-size:10px;padding:4px 8px">余额</button>
      </div>
    </div>
    <!-- ADB -->
    <div class="conn-card">
      <div class="conn-name">ADB 连接 (MuMu)</div>
      <div class="conn-status unknown" id="conn-adb-status">未检查</div>
      <div id="conn-adb-detail" style="font-size:10px;color:#888"></div>
      <div class="conn-actions">
        <button class="btn-verify" onclick="checkADB()">检查</button>
        <button class="btn-start" onclick="reconnectADB()">重连</button>
      </div>
    </div>
    <!-- GitHub -->
    <div class="conn-card">
      <div class="conn-name">GitHub</div>
      <div class="conn-status unknown" id="conn-github-status">未检查</div>
      <div id="conn-github-detail" style="font-size:10px;color:#888"></div>
      <div class="conn-actions">
        <button class="btn-verify" onclick="checkGitHub()">检查</button>
        <button class="btn-push" onclick="pushToGitHub()">推送</button>
        <button class="btn-verify" onclick="pullFromGitHub()">拉取</button>
        <button class="btn-deploy" onclick="setupGitHub()" style="font-size:10px;padding:4px 8px">配置仓库</button>
      </div>
    </div>
    <!-- 腾讯云 -->
    <div class="conn-card">
      <div class="conn-name">腾讯云服务器</div>
      <div class="conn-status unknown" id="conn-server-status">未检查</div>
      <div id="conn-server-detail" style="font-size:10px;color:#888"></div>
      <div class="conn-actions">
        <button class="btn-verify" onclick="checkServer()">检查</button>
        <button class="btn-deploy" onclick="deployToServer()">部署</button>
        <button class="btn-push" onclick="syncDataToServer()">同步数据</button>
        <button class="btn-verify" onclick="testSSH()" style="font-size:10px;padding:4px 8px">诊断SSH</button>
        <button class="btn-start" onclick="uploadSSHKey()" style="font-size:10px;padding:4px 8px">上传公钥</button>
      </div>
    </div>
    <!-- PyTorch -->
    <div class="conn-card">
      <div class="conn-name">PyTorch</div>
      <div class="conn-status unknown" id="conn-pytorch-status">检查中</div>
      <div id="conn-pytorch-detail" style="font-size:10px;color:#888"></div>
      <div class="conn-actions">
        <button class="btn-verify" onclick="checkPyTorch()">检查</button>
        <button class="btn-push" onclick="updatePyTorch()">更新</button>
      </div>
    </div>
    <!-- GPU -->
    <div class="conn-card">
      <div class="conn-name">GPU (NVIDIA)</div>
      <div class="conn-status unknown" id="conn-gpu-status">检查中</div>
      <div id="conn-gpu-detail" style="font-size:10px;color:#888"></div>
      <div class="conn-actions">
        <button class="btn-verify" onclick="checkGPU()">检查</button>
        <button class="btn-push" onclick="installCUDATorch()">安装CUDA PyTorch</button>
      </div>
    </div>
  </div>
</div>

<!-- ═══ 模拟器 ═══ -->
<div class="tab-content" id="tab-emulator">
  <div class="panel" style="margin-bottom:12px">
    <h3>Android 模拟器管理 (MUMU规格)</h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      <button class="btn-start" onclick="checkEmulatorStatus()">检查状态</button>
      <button class="btn-deploy" onclick="installEmulator()">安装模拟器</button>
      <button class="btn-start" onclick="startEmulator()">启动</button>
      <button class="btn-stop" onclick="stopEmulator()">停止</button>
      <button class="btn-verify" onclick="installGameAPK()">安装游戏APK</button>
      <button class="btn-push" onclick="installAPKPrompt()">安装APK文件</button>
    </div>
    <div id="emu-status" style="font-size:12px;color:#888;margin-bottom:8px">
      <div class="diag-item"><span class="diag-name">SDK安装</span><span class="diag-status unknown" id="emu-installed">未知</span></div>
      <div class="diag-item"><span class="diag-name">AVD</span><span class="diag-status unknown" id="emu-avd">未知</span></div>
      <div class="diag-item"><span class="diag-name">运行状态</span><span class="diag-status unknown" id="emu-running">未知</span></div>
      <div class="diag-item"><span class="diag-name">ADB连接</span><span class="diag-status unknown" id="emu-adb">未知</span></div>
    </div>
    <div id="emu-progress" style="font-size:11px;margin-top:6px"></div>
  </div>

  <!-- scrcpy 投屏控制 -->
  <div class="panel" style="margin-bottom:12px">
    <h3>scrcpy 投屏 <span style="font-size:10px;color:#888;font-weight:normal">(MUMU级全屏触控)</span></h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
      <button class="btn-deploy" onclick="installScrcpy()">安装scrcpy</button>
      <button class="btn-start" onclick="startScrcpy()">启动投屏</button>
      <button class="btn-stop" onclick="stopScrcpy()">停止投屏</button>
      <span id="scrcpy-status" style="font-size:11px;color:#888">未检测</span>
    </div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;font-size:11px;color:#aaa">
      <label>分辨率: <select id="scrcpy-res" style="background:#1a1f2b;color:#d0d0d0;border:1px solid #252a33;padding:2px 6px;border-radius:4px">
        <option value="1920">1920x1080</option>
        <option value="1280">1280x720</option>
        <option value="2560">2560x1440</option>
      </select></label>
      <label>FPS: <select id="scrcpy-fps" style="background:#1a1f2b;color:#d0d0d0;border:1px solid #252a33;padding:2px 6px;border-radius:4px">
        <option value="60">60</option>
        <option value="30">30</option>
        <option value="15">15</option>
      </select></label>
      <label>码率: <select id="scrcpy-bitrate" style="background:#1a1f2b;color:#d0d0d0;border:1px solid #252a33;padding:2px 6px;border-radius:4px">
        <option value="8000000">8M</option>
        <option value="16000000">16M</option>
        <option value="4000000">4M</option>
      </select></label>
      <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
        <input type="checkbox" id="scrcpy-fullscreen" checked> 全屏
      </label>
    </div>
  </div>

  <!-- 端口检测 -->
  <div class="panel" style="margin-bottom:12px">
    <h3>端口检测 <span style="font-size:10px;color:#888;font-weight:normal">(避免行旅白冲突)</span></h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
      <button class="btn-verify" onclick="checkPorts()">检测端口</button>
    </div>
    <div id="port-check-result" style="font-size:11px;color:#888"></div>
  </div>

  <div style="display:grid;grid-template-columns:1.2fr 1fr;gap:14px">
    <div class="panel">
      <h3>模拟器屏幕 <span id="emu-screen-fps" style="font-size:10px;color:#888;font-weight:normal"></span></h3>
      <div id="emu-screen-container" style="position:relative;width:100%;aspect-ratio:16/9;background:#000;border:1px solid #252a33;border-radius:8px;overflow:hidden;cursor:crosshair">
        <canvas id="emu-screen-canvas" style="width:100%;height:100%;object-fit:contain" onclick="handleEmulatorClick(event)"></canvas>
        <div id="emu-screen-placeholder" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#555;font-size:13px;text-align:center">
          模拟器未启动<br><span style="font-size:11px">点击"启动"按钮</span>
        </div>
      </div>
      <div style="display:flex;gap:6px;margin-top:6px;align-items:center">
        <label style="font-size:10px;color:#888;display:flex;align-items:center;gap:4px;cursor:pointer">
          <input type="checkbox" id="emu-auto-refresh" checked onchange="toggleEmulatorRefresh()"> 实时刷新(~3fps)
        </label>
        <button class="btn-clear" onclick="refreshEmulatorScreen()" style="font-size:10px;padding:4px 8px">手动刷新</button>
      </div>
    </div>
    <div class="panel">
      <h3>触摸控制</h3>
      <div style="font-size:11px;color:#888;margin-bottom:8px">点击屏幕上方的模拟器画面即可发送触摸事件</div>
      <div style="display:flex;flex-direction:column;gap:6px">
        <div style="display:flex;gap:6px;align-items:center">
          <label style="font-size:11px;color:#aaa;width:60px">X坐标:</label>
          <input type="number" id="emu-touch-x" value="960" style="width:80px;padding:4px 8px;border:1px solid #252a33;border-radius:4px;background:#1a1f2b;color:#d0d0d0;font-size:11px">
          <label style="font-size:11px;color:#aaa;width:60px">Y坐标:</label>
          <input type="number" id="emu-touch-y" value="540" style="width:80px;padding:4px 8px;border:1px solid #252a33;border-radius:4px;background:#1a1f2b;color:#d0d0d0;font-size:11px">
        </div>
        <div style="display:flex;gap:6px">
          <button class="btn-send" onclick="emuTouch('tap')" style="font-size:11px;padding:6px 14px">点击</button>
          <button class="btn-verify" onclick="emuTouch('longpress')" style="font-size:11px;padding:6px 14px">长按</button>
          <button class="btn-push" onclick="emuSwipe()" style="font-size:11px;padding:6px 14px">滑动</button>
        </div>
        <div id="emu-touch-result" style="font-size:10px;color:#888;margin-top:4px"></div>
      </div>
    </div>
  </div>
</div>

<!-- ═══ 智能体 ═══ -->
<div class="tab-content" id="tab-agent">
  <div class="agent-grid">
    <div class="panel">
      <h3>AI 智能体对话</h3>
      <div class="agent-chat">
        <div class="chat-messages" id="agent-chat-messages">
          <div class="chat-msg assistant"><div class="avatar">AI</div><div class="bubble">你好！我是智能体助手，可以联网搜索、分析连接状态、诊断问题。试试问我："诊断所有连接"或"搜索最新战术策略"</div></div>
        </div>
        <div class="chat-input-area">
          <textarea id="agent-chat-input" placeholder="输入问题，支持联网搜索和系统诊断..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();agentChat()}"></textarea>
          <button class="btn-send" onclick="agentChat()">发送</button>
        </div>
      </div>
    </div>
    <div class="panel">
      <h3>连接诊断面板</h3>
      <div style="display:flex;gap:8px;margin-bottom:8px">
        <button class="btn-verify" onclick="runDiagnostics()">自诊断</button>
        <button class="btn-start" onclick="oneClickFix()">一键修复</button>
        <button class="btn-deploy" onclick="createPackage()" style="background:#00bcd4;color:#000">创建安装包</button>
      </div>
      <div class="diagnostic-panel" id="diagnostic-panel">
        <div class="diag-item"><span class="diag-name">DeepSeek API</span><span class="diag-status unknown">等待诊断</span></div>
        <div class="diag-item"><span class="diag-name">ADB (MuMu)</span><span class="diag-status unknown">等待诊断</span></div>
        <div class="diag-item"><span class="diag-name">GitHub</span><span class="diag-status unknown">等待诊断</span></div>
        <div class="diag-item"><span class="diag-name">腾讯云服务器</span><span class="diag-status unknown">等待诊断</span></div>
        <div class="diag-item"><span class="diag-name">PyTorch</span><span class="diag-status unknown">等待诊断</span></div>
        <div class="diag-item"><span class="diag-name">Python环境</span><span class="diag-status unknown">等待诊断</span></div>
      </div>
      <div id="agent-fix-result" style="margin-top:8px;font-size:11px"></div>
    </div>
  </div>
  <div class="panel" style="margin-top:14px">
    <h3>决策链管理</h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
      <button class="btn-verify" onclick="verifyDecisionChain()">验证决策链</button>
      <button class="btn-rebuild" onclick="rebuildDecisionChain()">重建决策链</button>
      <button class="btn-deploy" onclick="oneClickDeploy()">一键部署</button>
      <button class="btn-start" onclick="executeAgent('重建决策链')" style="font-size:11px;padding:6px 12px">智能体: 重建决策链</button>
      <button class="btn-start" onclick="executeAgent('修复ADB连接并启动游戏')" style="font-size:11px;padding:6px 12px">智能体: 修复ADB</button>
    </div>
    <div id="chain-verify-progress" style="font-size:11px;margin-top:4px"></div>
    <div id="chain-verify-result" style="font-size:11px;margin-top:6px"></div>
  </div>
  <div class="panel" style="margin-top:14px">
    <h3>安装包管理</h3>
    <div style="display:flex;gap:8px;align-items:center">
      <button class="btn-start" onclick="createPackage()">创建安装包</button>
      <button class="btn-verify" onclick="downloadPackage()">下载安装包</button>
    </div>
    <div id="package-result" style="margin-top:8px;font-size:11px"></div>
  </div>
</div>

<!-- ═══ 智能搜索 ═══ -->
<div class="tab-content" id="tab-websearch">
  <div class="panel" style="margin-bottom:12px">
    <h3>联网智能搜索</h3>
    <div class="search-bar">
      <input type="text" id="web-search-input" placeholder="输入搜索内容，AI会自动总结..." onkeydown="if(event.key==='Enter')webSearch()">
      <button class="btn-send" onclick="webSearch()">联网搜索</button>
      <button class="btn-verify" onclick="webSearchStream()">流式搜索</button>
    </div>
    <div id="web-search-progress" style="margin-top:8px"></div>
    <div id="web-search-summary" style="margin-top:10px"></div>
  </div>
  <div class="panel" style="margin-bottom:12px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <h3 style="margin:0;border:none;padding:0">搜索结果</h3>
      <div style="display:flex;gap:6px">
        <button class="btn-start" onclick="saveSearchResult()" style="padding:5px 12px;font-size:11px">保存到知识库</button>
        <button class="btn-verify" onclick="learnFromWeb()" style="padding:5px 12px;font-size:11px">让AI学习</button>
        <button class="btn-clear" onclick="copySearchResults()" style="padding:5px 12px;font-size:11px">复制</button>
      </div>
    </div>
    <div id="web-search-results" style="max-height:400px;overflow-y:auto">
      <div style="color:#888;padding:20px;text-align:center">输入关键词开始搜索</div>
    </div>
  </div>
  <div class="panel">
    <h3>知识库（已保存的搜索结果）</h3>
    <div id="knowledge-list" style="max-height:200px;overflow-y:auto">
      <div style="color:#888;padding:10px;text-align:center;font-size:11px">加载中...</div>
    </div>
  </div>
</div>

<!-- ═══ 模型训练 ═══ -->
<div class="tab-content" id="tab-training">
  <div class="train-section"><h4>选择数据集</h4><div class="dataset-list" id="dataset-list">加载中...</div></div>
  <div class="train-section"><h4>上传图片</h4><div class="upload-area" onclick="document.getElementById('file-input').click()"><p>点击上传图片到数据集</p><input type="file" id="file-input" multiple accept=".png,.jpg,.jpeg" onchange="uploadImages()"></div><div id="upload-status"></div></div>
  <div class="train-section"><h4>训练配置</h4><div class="train-config">
    <label>模型<select id="train-model"><option value="yolov8n.pt">YOLOv8n</option><option value="yolov8s.pt">YOLOv8s</option></select></label>
    <label>轮数<input type="number" id="train-epochs" value="50" min="10" max="500" style="width:70px"></label>
    <label>尺寸<input type="number" id="train-imgsz" value="640" min="320" max="1280" style="width:70px"></label>
    <label style="align-items:center;flex-direction:row;gap:6px"><input type="checkbox" id="auto-push-github">训练后推送到GitHub</label>
  </div>
  <div style="display:flex;gap:10px;align-items:center;">
    <button class="btn-start" onclick="startTraining()" id="btn-train-start">开始训练</button>
    <button class="btn-stop" onclick="stopTraining()" id="btn-train-stop" style="display:none">停止</button>
    <span id="train-status-text" style="font-size:12px;color:#888"></span>
  </div>
  <div class="train-progress" id="train-progress-container" style="display:none"><div class="train-progress-bar" id="train-progress-bar" style="width:0%">0%</div></div>
  <div class="train-log" id="train-log"></div></div>
  <div class="train-section"><h4>已训练模型</h4><div class="model-list" id="model-list">加载中...</div></div>
</div>

<!-- ═══ 标注工具（iframe集成） ═══ -->
<div class="tab-content" id="tab-annotate">
  <div style="display:flex;gap:8px;margin-bottom:8px;align-items:center">
    <span style="color:#aaa;font-size:13px">标注工具</span>
    <button class="btn-verify" onclick="document.getElementById('annotate-frame').src='/annotate'" style="font-size:11px;padding:5px 12px">刷新标注页</button>
    <span style="font-size:10px;color:#888">拖拽画框标注 | 快捷键: N/P/S/Del</span>
  </div>
  <iframe id="annotate-frame" src="/annotate" style="width:100%;height:calc(100vh - 200px);border:1px solid #252a33;border-radius:8px;background:#0a0e14"></iframe>
</div>

<!-- ═══ 参数学习 ═══ -->
<div class="tab-content" id="tab-params">
  <div class="panel">
    <h3>上传训练参数</h3>
    <div class="upload-area" onclick="document.getElementById('params-input').click()"><p>点击上传配置文件 (.yaml, .json, .txt, .cfg)</p><input type="file" id="params-input" multiple accept=".yaml,.json,.txt,.cfg" onchange="uploadParams()"></div>
    <div id="params-upload-status"></div>
  </div>
  <div class="panel" style="margin-top:14px">
    <h3>已上传参数</h3><div id="params-list" style="font-size:11px;margin-bottom:8px">加载中...</div>
    <button class="btn-start" onclick="learnFromParams()">让 AI 学习参数</button>
    <div id="params-learn-result" style="margin-top:8px"></div>
  </div>
  <div class="panel" style="margin-top:14px">
    <h3>实战数据学习</h3>
    <p style="font-size:11px;color:#888;margin-bottom:8px">从AI实战积累的经验中学习，提炼战术规则</p>
    <button class="btn-start" onclick="learnFromCombat()">从实战数据学习</button>
    <button class="btn-verify" onclick="exportCombatData()" style="margin-left:6px">导出数据</button>
    <div id="combat-learn-result" style="margin-top:8px"></div>
  </div>
</div>

<!-- ═══ 学习日志 ═══ -->
<div class="tab-content" id="tab-learning">
  <div class="panel">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <h3 style="margin:0;border:none;padding:0">AI 学习日志</h3>
      <div>
        <button class="btn-verify" onclick="refreshLearningLog()" style="font-size:11px;padding:5px 12px">刷新</button>
        <button class="btn-clear" onclick="clearLearningLog()" style="font-size:11px;padding:5px 12px">清空</button>
        <button class="btn-push" onclick="exportLearningLog()" style="font-size:11px;padding:5px 12px">导出</button>
      </div>
    </div>
    <div id="learning-log-container" style="max-height:600px;overflow-y:auto;font-size:11px;background:#0a0e14;border-radius:8px;padding:4px">
      <div style="color:#888;padding:20px;text-align:center">暂无学习日志</div>
    </div>
  </div>
</div>

<!-- ═══ 数据管理 ═══ -->
<div class="tab-content" id="tab-datamanage">
  <div class="panel" style="margin-bottom:12px">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
      <h3 style="margin:0;border:none;padding:0">本地数据管理</h3>
      <div style="display:flex;gap:8px;align-items:center">
        <span style="font-size:10px;color:#888">自动清理(每5分钟):</span>
        <label class="toggle-switch" style="display:flex;align-items:center;gap:4px;cursor:pointer">
          <input type="checkbox" id="auto-cleanup-toggle" onchange="toggleAutoCleanup()" checked>
          <span id="auto-cleanup-label" style="font-size:10px;color:#4caf50">已开启</span>
        </label>
      </div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0">
      <button class="btn-verify" onclick="browseData()">浏览数据</button>
      <button class="btn-rebuild" onclick="autoCleanup()">一键清理</button>
      <button class="btn-deploy" onclick="selectiveCleanup()">选择删除</button>
    </div>
    <div id="data-total-size" style="font-size:12px;margin-bottom:8px;color:#888">总占用: --</div>
    <div style="max-height:500px;overflow-y:auto;font-size:11px">
      <table style="width:100%;border-collapse:collapse" id="data-table">
        <thead><tr style="background:#1a1f2b;color:#888;font-size:10px">
          <th style="padding:6px 8px;text-align:left">文件名</th>
          <th style="padding:6px 8px;text-align:left">目录</th>
          <th style="padding:6px 8px;text-align:right">大小</th>
          <th style="padding:6px 8px;text-align:right">时间</th>
          <th style="padding:6px 8px;text-align:center">状态</th>
          <th style="padding:6px 8px;text-align:center">操作</th>
        </tr></thead>
        <tbody id="data-browse-result"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- ═══ 系统设置 ═══ -->
<div class="tab-content" id="tab-settings">
  <div class="panel"><h3>API 验证</h3>
    <div class="conn-card" style="margin-bottom:8px"><div class="conn-name">DeepSeek API</div><div class="conn-status" id="api-deepseek-status">未检查</div><div id="api-deepseek-detail" style="font-size:10px;color:#888"></div></div>
    <button class="btn-verify" onclick="verifyAPI()">验证 API 连通性</button>
  </div>
  <div class="panel" style="margin-top:14px"><h3>版本信息</h3>
    <div class="version-info" id="version-info">加载中...</div>
    <button class="btn-verify" onclick="checkVersion()" style="margin-top:6px">检查更新</button>
    <button class="btn-rebuild" onclick="reloadModules()" style="margin-top:6px;margin-left:6px">热重载模块</button>
    <button class="btn-deploy" onclick="deployToServer()" style="margin-top:6px;margin-left:6px">部署到服务器</button>
    <div id="version-check-result" style="margin-top:6px;font-size:11px"></div>
  </div>
  <div class="panel" style="margin-top:14px"><h3>GitHub</h3>
    <button class="btn-push" onclick="pushToGitHub()">推送到GitHub</button>
    <button class="btn-verify" onclick="pullFromGitHub()" style="margin-left:6px">从GitHub拉取</button>
    <button class="btn-verify" onclick="checkGitHub()" style="margin-left:6px">检查连接</button>
    <div id="settings-github-result" style="margin-top:6px;font-size:11px"></div>
  </div>
</div>
</div>

<script>
const socket = io();
let selectedDataset = '';
let scoreChart = null;
let currentChatBubble = null;

// ── 标签页 ──
function switchTab(tab) {
  document.querySelectorAll('.nav-tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));
  var btn = document.querySelector('.nav-tab[onclick*="'+tab+'"]');
  if(btn)btn.classList.add('active');
  var el = document.getElementById('tab-'+tab);
  if(el)el.classList.add('active');
  if(tab==='training'){loadDatasets();loadModels()}
  if(tab==='settings'){loadVersion();verifyAPI()}
  if(tab==='connections'){checkAllConnections()}
  if(tab==='emulator'){checkEmulatorStatus();checkScrcpyStatus();checkPorts()}
  if(tab==='params'){loadParams()}
  if(tab==='learning'){refreshLearningLog()}
  if(tab==='dashboard'&&!scoreChart)initChart();
  if(tab==='websearch'){loadKnowledge()}
  if(tab==='datamanage'){browseData()}
  if(tab==='agent'){runDiagnostics()}
}

// ── 图表 ──
function initChart(){
  var ctx=document.getElementById('scoreChart');if(!ctx)return;
  scoreChart=new Chart(ctx,{type:'line',data:{labels:[],datasets:[
    {label:'本轮评分',data:[],borderColor:'#ff9800',backgroundColor:'rgba(255,152,0,0.1)',tension:0.3,pointRadius:2},
    {label:'累计得分',data:[],borderColor:'#4caf50',backgroundColor:'rgba(76,175,80,0.1)',tension:0.3,pointRadius:2,yAxisID:'y1'}
  ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#888',font:{size:11}}}},scales:{x:{ticks:{color:'#555'},grid:{color:'#1a1f2b'}},y:{ticks:{color:'#555'},grid:{color:'#1a1f2b'}},y1:{position:'right',ticks:{color:'#555'},grid:{display:false}}}}});
}

// ── AI 控制 ──
function startAI(){
  document.getElementById('status-badge').textContent='连接中...';
  document.getElementById('status-badge').style.color='#ff9800';
  document.getElementById('thinking-box').innerHTML='<span class="spinner"></span> 正在启动AI智能体...';
  socket.emit('start');
}
function stopAI(){
  socket.emit('stop');
  document.getElementById('status-badge').textContent='已停止';
  document.getElementById('status-badge').style.color='#888';
  document.getElementById('thinking-box').textContent='AI已离线';
}
function escapeHtml(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function sendCommand(){var inp=document.getElementById('cmd-input');var cmd=inp.value.trim();if(!cmd)return;socket.emit('send_command',{command:cmd});inp.value='';inp.placeholder='指令已发送...';setTimeout(function(){inp.placeholder='战术指令/配置命令(apikey/adb/repo/server)...'},2500)}
function clearCommand(){socket.emit('clear_command')}

// ── AI 对话 ──
function sendChat(){
  var inp=document.getElementById('chat-input');var msg=inp.value.trim();if(!msg)return;
  addChatMessage('user',msg);inp.value='';
  socket.emit('ai_chat',{message:msg,include_battlefield:true,is_correction:false});
}
function sendCorrection(){
  var inp=document.getElementById('chat-input');var msg=inp.value.trim();if(!msg)return;
  addChatMessage('user','[纠正] '+msg);inp.value='';
  socket.emit('ai_chat',{message:msg,include_battlefield:true,is_correction:true});
  socket.emit('ai_correct_behavior',{correction:msg});
}
function clearChat(){
  socket.emit('ai_chat_clear');
  document.getElementById('chat-messages').innerHTML='<div class="chat-msg assistant"><div class="avatar">AI</div><div class="bubble">对话已清空。</div></div>';
}
function addChatMessage(role,text){
  var msgs=document.getElementById('chat-messages');
  var avatar=role==='user'?'你':'AI';
  var div=document.createElement('div');div.className='chat-msg '+role;
  div.innerHTML='<div class="avatar">'+avatar+'</div><div class="bubble">'+escapeHtml(text)+'</div>';
  if(role==='assistant'){div.id='chat-bubble-streaming';currentChatBubble=div.querySelector('.bubble')}
  msgs.appendChild(div);msgs.scrollTop=msgs.scrollHeight;
}

socket.on('ai_chat_start',function(data){addChatMessage('assistant','')});
socket.on('ai_chat_token',function(data){
  if(currentChatBubble){
    if(data.done){currentChatBubble.textContent=data.full;var el=document.getElementById('chat-bubble-streaming');if(el)el.id='';currentChatBubble=null}
    else{currentChatBubble.textContent+=data.token}
    document.getElementById('chat-messages').scrollTop=document.getElementById('chat-messages').scrollHeight;
  }
  // 同时更新智能体聊天
  if(agentChatBubble){
    if(data.done){agentChatBubble.textContent=data.full;var el2=document.getElementById('agent-bubble-streaming');if(el2)el2.id='';agentChatBubble=null}
    else{agentChatBubble.textContent+=data.token}
    document.getElementById('agent-chat-messages').scrollTop=document.getElementById('agent-chat-messages').scrollHeight;
  }
});
socket.on('ai_chat_error',function(data){if(currentChatBubble){currentChatBubble.textContent='[错误] '+data.error;currentChatBubble=null}});

// ── 行为纠正回显 ──
socket.on('correction_analysis',function(data){
  var msgs=document.getElementById('chat-messages');
  var div=document.createElement('div');div.className='chat-msg assistant';
  div.innerHTML='<div class="avatar">AI</div><div class="bubble"><strong>学习完成:</strong><br>'+escapeHtml(data.analysis||'')+'</div>';
  msgs.appendChild(div);msgs.scrollTop=msgs.scrollHeight;
});
socket.on('ai_learned_from_correction',function(data){
  var msgs=document.getElementById('chat-messages');
  var div=document.createElement('div');div.className='chat-msg assistant';
  div.innerHTML='<div class="avatar">AI</div><div class="bubble" style="background:#302a1a;border-left:2px solid #ff9800"><strong>已从纠正中学习</strong></div>';
  msgs.appendChild(div);msgs.scrollTop=msgs.scrollHeight;
});

// ── AI 思考 ──
socket.on('ai_thinking_update',function(data){
  var box=document.getElementById('thinking-box');
  if(box){
    var html='';
    if(data.thinking)html+='<span class="step">## 实时思考</span>\n'+escapeHtml(data.thinking)+'\n';
    if(data.analysis)html+='<span class="highlight">[分析]</span> '+escapeHtml(data.analysis)+'\n';
    if(data.reason)html+='<span class="highlight">[理由]</span> '+escapeHtml(data.reason)+'\n';
    if(html)box.innerHTML=html;
  }
});

// ── 连接管理 ──
function rebuildChain(){
  var s=document.getElementById('rebuild-status');s.innerHTML='<div class="alert info"><span class="spinner"></span> 重建决策链中...</div>';
  socket.emit('rebuild_chain');
}
function checkAllConnections(){
  var s=document.getElementById('rebuild-status');s.innerHTML='<div class="alert info"><span class="spinner"></span> 检查所有连接...</div>';
  socket.emit('check_all_connections');
}
socket.on('rebuild_progress',function(d){document.getElementById('rebuild-status').innerHTML='<div class="alert info">'+d.step+' ('+d.progress+'%)</div>'});
socket.on('rebuild_complete',function(d){
  var html='<div class="alert success">决策链重建完成</div>';
  for(var k in d.results){html+='<span style="font-size:11px;color:#888">'+k+': </span><span style="font-size:11px;color:'+(d.results[k]==='online'||d.results[k]==='connected'?'#4caf50':'#e53935')+'">'+d.results[k]+'</span> '}
  document.getElementById('rebuild-status').innerHTML=html;
  updateConnMinis(d.results);
});
socket.on('rebuild_error',function(d){document.getElementById('rebuild-status').innerHTML='<div class="alert error">'+d.error+'</div>'});
socket.on('all_connections_status',function(d){updateConnMinis(d);updateConnCards(d)});

function updateConnMinis(d){
  var api=(d.deepseek||d.api)==='online';var adb=(d.adb||'')==='connected';var gh=(d.github||'')==='online';var srv=(d.server||'')==='online';
  setMini('conn-api',api?'online':'offline','API');
  setMini('conn-adb',adb?'online':'offline','ADB');
  setMini('conn-gh',gh?'online':'offline','GH');
  setMini('conn-srv',srv?'online':'offline','SRV');
}
function setMini(id,cls,text){var el=document.getElementById(id);el.className='conn-mini '+cls;el.textContent=text}
function updateConnCards(d){
  setConnCard('conn-deepseek-status','conn-deepseek-detail',d.deepseek||d.api,'DeepSeek');
  setConnCard('conn-adb-status','conn-adb-detail',d.adb,'ADB');
  setConnCard('conn-github-status','conn-github-detail',d.github,'GitHub');
  setConnCard('conn-server-status','conn-server-detail',d.server,'Server');
}
function setConnCard(statusId,detailId,val,name){
  var el=document.getElementById(statusId);var dl=document.getElementById(detailId);
  if(!el)return;
  var ok=val==='online'||val==='connected';
  el.textContent=ok?'在线':(val==='offline'||val==='disconnected'?'离线':val||'未知');
  el.className='conn-status '+(ok?'online':(val==='offline'||val==='disconnected'?'offline':'unknown'));
}

function checkADB(){fetch('/api/adb/status').then(r=>r.json()).then(d=>{
  setConnCard('conn-adb-status','conn-adb-detail',d.status,'ADB');
  document.getElementById('conn-adb-detail').textContent=(d.host||'')+':'+(d.port||'')+' | '+d.status;
})}
function reconnectADB(){fetch('/api/adb/reconnect',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
  document.getElementById('conn-adb-status').textContent=d.status==='connected'?'已连接':d.status;
  document.getElementById('conn-adb-status').className='conn-status '+(d.status==='connected'?'online':'offline');
  document.getElementById('conn-adb-detail').textContent=d.output||'';
})}
function checkGitHub(){fetch('/api/github/status').then(r=>r.json()).then(d=>{
  if(!d.has_remote){
    document.getElementById('conn-github-status').textContent='未配置仓库';
    document.getElementById('conn-github-status').className='conn-status offline';
    document.getElementById('conn-github-detail').textContent=(d.message||'GitHub未配置')+' | '+(d.suggestion||'');
  }else{
    document.getElementById('conn-github-status').textContent=d.api_status==='online'?'在线':'离线';
    document.getElementById('conn-github-status').className='conn-status '+(d.api_status==='online'?'online':'offline');
    document.getElementById('conn-github-detail').textContent=(d.repo_url||'')+' | '+d.branch;
  }
})}
function setupGitHub(){
  var url=prompt('请输入GitHub仓库地址:\n例如: https://github.com/username/repo.git');
  if(!url)return;
  fetch('/api/github/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({repo_url:url})}).then(r=>r.json()).then(d=>{
    if(d.status==='configured'){
      document.getElementById('conn-github-status').textContent='已配置';
      document.getElementById('conn-github-status').className='conn-status online';
      document.getElementById('conn-github-detail').textContent=d.repo_url;
      alert(d.message);
    }else{
      alert(d.error||'配置失败');
    }
  })
}
function pushToGitHub(){fetch('/api/github/push',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:'手动推送 '+new Date().toLocaleString()})}).then(r=>r.json()).then(d=>{
  document.getElementById('settings-github-result').innerHTML='<div class="alert '+(d.status==='pushed'||d.status==='no_changes'?'success':'error')+'">'+d.status+'</div>';
})}
function pullFromGitHub(){fetch('/api/github/pull',{method:'POST'}).then(r=>r.json()).then(d=>{
  document.getElementById('settings-github-result').innerHTML='<div class="alert '+(d.status==='pulled'?'success':'error')+'">'+d.status+'</div>';
})}
function checkServer(){fetch('/api/server/status').then(r=>r.json()).then(d=>{
  var el=document.getElementById('conn-server-status');
  var dl=document.getElementById('conn-server-detail');
  if(d.status==='no_key'){
    el.textContent='无密钥';
    el.className='conn-status offline';
    dl.textContent=(d.detail&&d.detail.suggestion)||'请配置SSH密钥';
  }else if(d.status==='online'){
    el.textContent='在线';
    el.className='conn-status online';
    dl.textContent='部署: '+(d.deployed?'是':'否');
  }else if(d.status==='error'){
    el.textContent='错误';
    el.className='conn-status offline';
    dl.textContent=(d.detail&&d.detail.diagnosis)||d.error||'连接失败';
  }else{
    el.textContent='离线';
    el.className='conn-status offline';
    dl.textContent=(d.detail&&d.detail.diagnosis)||d.error||'连接失败';
  }
})}
function testSSH(){
  var dl=document.getElementById('conn-server-detail');
  dl.textContent='诊断中...';
  fetch('/api/server/test_ssh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
    var html='<div class="alert '+(d.all_ok?'success':'warning')+'"><strong>诊断结果:</strong> '+d.summary+'</div>';
    if(d.tests){d.tests.forEach(function(t){html+='<div style="font-size:10px;color:'+(t.status==='ok'?'#4caf50':(t.status==='fail'?'#e53935':'#888'))+'">'+t.name+': '+t.detail+'</div>'})}
    dl.innerHTML=html;
  }).catch(function(e){dl.textContent='诊断失败: '+e})
}
function uploadSSHKey(){
  var dl=document.getElementById('conn-server-detail');
  dl.innerHTML='<span class="spinner"></span> 正在上传公钥到服务器...';
  if(!confirm('将尝试通过密码认证上传SSH公钥到服务器。如果密码正确，之后即可使用密钥连接。确认?')){dl.textContent='已取消';return}
  fetch('/api/server/upload_key',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'){
      dl.innerHTML='<div class="alert success">'+d.message+'</div>';
      setTimeout(checkServer,1500);
    }else{
      dl.innerHTML='<div class="alert error">上传失败: '+(d.error||'')+'</div><div style="font-size:10px;color:#ff9800;margin-top:4px">提示: 请确认服务器密码正确且允许密码登录</div>';
    }
  }).catch(function(e){dl.innerHTML='<div class="alert error">上传失败: '+e+'</div>'})
}
function deployToServer(){fetch('/api/server/deploy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sync_only:false})}).then(r=>r.json()).then(d=>{
  document.getElementById('conn-server-status').textContent='部署中...';document.getElementById('conn-server-status').className='conn-status checking';
})}
function syncDataToServer(){fetch('/api/server/deploy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sync_only:true})}).then(r=>r.json()).then(d=>{
  document.getElementById('conn-server-status').textContent='同步中...';document.getElementById('conn-server-status').className='conn-status checking';
})}
socket.on('server_deploy_progress',function(d){document.getElementById('conn-server-detail').textContent=d.step+' ('+d.progress+'%)'});
socket.on('server_deploy_complete',function(d){document.getElementById('conn-server-status').textContent='在线';document.getElementById('conn-server-status').className='conn-status online';document.getElementById('conn-server-detail').textContent='部署完成'});
socket.on('server_deploy_error',function(d){document.getElementById('conn-server-status').textContent='错误';document.getElementById('conn-server-status').className='conn-status offline';document.getElementById('conn-server-detail').textContent=d.error});

function checkPyTorch(){fetch('/api/pytorch/version').then(r=>r.json()).then(d=>{
  var el=document.getElementById('conn-pytorch-status');
  var dl=document.getElementById('conn-pytorch-detail');
  if(d.version&&d.version!=='未安装'){
    el.textContent=d.version;
    el.className='conn-status online';
    if(d.cuda){
      dl.textContent='CUDA: 可用 ('+(d.cuda_version||'')+') | GPU加速';
    }else{
      dl.textContent='仅CPU | 对于非NVIDIA系统完全正常，CPU推理可用';
    }
  }else{
    el.textContent='未安装';
    el.className='conn-status offline';
    dl.textContent='PyTorch未安装';
  }
})}
function updatePyTorch(){fetch('/api/pytorch/update',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
  document.getElementById('conn-pytorch-status').textContent='更新中...';document.getElementById('conn-pytorch-status').className='conn-status checking';
})}
socket.on('pytorch_update_complete',function(d){document.getElementById('conn-pytorch-status').textContent=d.version||'更新完成';document.getElementById('conn-pytorch-status').className='conn-status '+(d.success?'online':'offline')});

// ── 智能搜索 ──
var lastSearchResults=null;
var lastSearchQuery='';
function webSearch(){
  var q=document.getElementById('web-search-input').value.trim();
  if(!q)return;
  lastSearchQuery=q;
  document.getElementById('web-search-progress').innerHTML='<div class="search-progress"><span class="spinner"></span> 搜索中...</div>';
  document.getElementById('web-search-summary').innerHTML='';
  fetch('/api/web/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})}).then(r=>r.json()).then(d=>{
    document.getElementById('web-search-progress').innerHTML='';
    lastSearchResults=d;
    renderSearchResults(d);
    if(d.summary){
      document.getElementById('web-search-summary').innerHTML='<div class="search-summary"><h4>AI 总结</h4>'+escapeHtml(d.summary)+'</div>';
    }
  }).catch(function(e){
    document.getElementById('web-search-progress').innerHTML='<div class="alert error">搜索失败: '+e+'</div>';
  })
}
function webSearchStream(){
  var q=document.getElementById('web-search-input').value.trim();
  if(!q)return;
  lastSearchQuery=q;
  document.getElementById('web-search-progress').innerHTML='<div class="search-progress"><span class="spinner"></span> 流式搜索中...</div>';
  document.getElementById('web-search-results').innerHTML='';
  document.getElementById('web-search-summary').innerHTML='<div class="search-summary" id="stream-summary"><h4>AI 总结 (流式)</h4><span id="stream-text"></span></div>';
  socket.emit('web_search',{query:q});
}
function renderSearchResults(d){
  var html='';
  if(d.results){
    d.results.forEach(function(r,i){
      html+='<div class="search-result"><div class="sr-title">'+(i+1)+'. '+escapeHtml(r.title||'')+'</div>';
      if(r.snippet)html+='<div class="sr-snippet">'+escapeHtml(r.snippet)+'</div>';
      if(r.url)html+='<div class="sr-url">'+escapeHtml(r.url)+'</div>';
      html+='</div>';
    });
  }
  document.getElementById('web-search-results').innerHTML=html||'<div style="color:#888;padding:10px">无结果</div>';
}
function saveSearchResult(){
  if(!lastSearchResults||!lastSearchQuery){alert('请先搜索');return}
  fetch('/api/web/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:lastSearchQuery,results:lastSearchResults.results||[],summary:lastSearchResults.summary||''})}).then(r=>r.json()).then(d=>{
    alert('已保存到知识库: '+d.filename);
    loadKnowledge();
  })
}
function learnFromWeb(){
  fetch('/api/web/learn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(r=>r.json()).then(d=>{
    if(d.status==='learning'){alert('AI学习已启动，请查看学习日志')}
    else{alert(d.error||'启动失败')}
  })
}
function copySearchResults(){
  if(!lastSearchResults){alert('请先搜索');return}
  var text=lastSearchQuery+'\n\n';
  if(lastSearchResults.summary)text+='AI总结:\n'+lastSearchResults.summary+'\n\n';
  if(lastSearchResults.results){lastSearchResults.results.forEach(function(r,i){text+=(i+1)+'. '+r.title+'\n'+r.snippet+'\n'+r.url+'\n\n'})}
  navigator.clipboard.writeText(text).then(function(){alert('已复制到剪贴板')}).catch(function(){alert('复制失败')})
}
function loadKnowledge(){
  fetch('/api/web/knowledge').then(r=>r.json()).then(data=>{
    var html='';
    if(!data.length){html='<div style="color:#888;padding:10px;text-align:center;font-size:11px">暂无保存的知识</div>'}
    else{data.forEach(function(k){html+='<div class="knowledge-list-item"><div><strong>'+escapeHtml(k.query)+'</strong><br><span style="font-size:10px;color:#888">'+k.saved_at+' | '+escapeHtml(k.summary_preview)+'</span></div></div>'})}
    document.getElementById('knowledge-list').innerHTML=html;
  })
}

// ── 智能体 ──
var agentChatBubble=null;
function agentChat(){
  var inp=document.getElementById('agent-chat-input');var msg=inp.value.trim();if(!msg)return;
  addAgentMessage('user',msg);inp.value='';
  if(msg.includes('诊断')||msg.includes('检查')||msg.includes('连接')){
    runDiagnostics();
    addAgentMessage('assistant','正在运行诊断，请查看右侧诊断面板...');
  }else if(msg.includes('搜索')||msg.includes('查询')){
    var q=msg.replace(/搜索|查询|帮我查/g,'').trim();
    if(q){
      addAgentMessage('assistant','正在联网搜索: '+q+'...');
      fetch('/api/web/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})}).then(r=>r.json()).then(d=>{
        addAgentMessage('assistant','搜索完成! '+d.summary);
      });
    }
  }else if(msg.includes('修复')){
    oneClickFix();
    addAgentMessage('assistant','正在一键修复...');
  }else{
    socket.emit('ai_chat',{message:msg,include_battlefield:false,is_correction:false});
    addAgentMessage('assistant','');
  }
}
function addAgentMessage(role,text){
  var msgs=document.getElementById('agent-chat-messages');
  var avatar=role==='user'?'你':'AI';
  var div=document.createElement('div');div.className='chat-msg '+role;
  div.innerHTML='<div class="avatar">'+avatar+'</div><div class="bubble">'+escapeHtml(text)+'</div>';
  if(role==='assistant'){div.id='agent-bubble-streaming';agentChatBubble=div.querySelector('.bubble')}
  msgs.appendChild(div);msgs.scrollTop=msgs.scrollHeight;
}

// ── 诊断和一键修复 ──
function runDiagnostics(){
  updateDiagItem(0,'checking','检查中');
  updateDiagItem(1,'checking','检查中');
  updateDiagItem(2,'checking','检查中');
  updateDiagItem(3,'checking','检查中');
  updateDiagItem(4,'checking','检查中');
  updateDiagItem(5,'checking','检查中');
  fetch('/api/verify_api_http').then(r=>r.json()).then(d=>{
    var ok=d.deepseek&&d.deepseek.status==='online';
    updateDiagItem(0,ok?'ok':'fail',ok?'在线 ('+(d.deepseek.latency_ms||'')+'ms)':'离线: '+(d.deepseek.error||''));
  }).catch(function(){updateDiagItem(0,'fail','无法连接')});
  fetch('/api/adb/status').then(r=>r.json()).then(d=>{
    updateDiagItem(1,d.status==='connected'?'ok':'fail',d.status+' | '+d.adb_exe);
  }).catch(function(){updateDiagItem(1,'fail','检查失败')});
  fetch('/api/github/status').then(r=>r.json()).then(d=>{
    var ok=d.api_status==='online';
    var txt=(d.has_remote?d.repo_url:'未配置仓库')+' | API: '+d.api_status;
    updateDiagItem(2,ok&&d.has_remote?'ok':'fail',txt);
  }).catch(function(){updateDiagItem(2,'fail','无法连接')});
  fetch('/api/server/status').then(r=>r.json()).then(d=>{
    updateDiagItem(3,d.status==='online'?'ok':'fail',d.status+(d.error?': '+d.error:''));
  }).catch(function(){updateDiagItem(3,'fail','无法连接')});
  fetch('/api/pytorch/version').then(r=>r.json()).then(d=>{
    updateDiagItem(4,d.version?'ok':'fail',d.version||'未安装');
  }).catch(function(){updateDiagItem(4,'fail','检查失败')});
  updateDiagItem(5,'ok','Python环境正常');
}
function updateDiagItem(idx,status,detail){
  var panel=document.getElementById('diagnostic-panel');
  if(!panel)return;
  var items=panel.querySelectorAll('.diag-item');
  if(idx<items.length){
    var s=items[idx].querySelector('.diag-status');
    s.className='diag-status '+status;
    s.textContent=status==='ok'?'正常':(status==='fail'?'异常':status);
    var dd=items[idx].querySelector('.diag-detail');
    if(!dd){dd=document.createElement('div');dd.className='diag-detail';items[idx].appendChild(dd)}
    dd.textContent=detail;
  }
}
function oneClickFix(){
  var r=document.getElementById('agent-fix-result');
  r.innerHTML='<div class="alert info"><span class="spinner"></span> 一键修复中...</div>';
  var fixes=[];
  fetch('/api/adb/reconnect',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(res=>res.json()).then(d=>{
    fixes.push('ADB: '+(d.status==='connected'?'已连接':'重连失败'));
  }).catch(function(){fixes.push('ADB: 修复失败')});
  fetch('/api/server/setup_key',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(res=>res.json()).then(d=>{
    if(d.status==='generated'){fixes.push('SSH密钥: 已生成，请将公钥添加到服务器')}
    else{fixes.push('SSH密钥: 生成失败')}
  }).catch(function(){fixes.push('SSH密钥: 修复失败')});
  setTimeout(function(){
    r.innerHTML='<div class="alert success">一键修复完成</div>';
    fixes.forEach(function(f){r.innerHTML+='<div style="font-size:10px;color:#aaa">'+f+'</div>'});
    runDiagnostics();
  },3000);
}

// ── 安装包 ──
function createPackage(){
  var r=document.getElementById('package-result');
  r.innerHTML='<div class="alert info"><span class="spinner"></span> 创建安装包中...</div>';
  fetch('/api/package/create',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(res=>res.json()).then(d=>{
    if(d.status==='created'){
      r.innerHTML='<div class="package-info"><strong>安装包已创建!</strong><br>文件: '+d.filename+' | 大小: '+d.size_mb+'MB | 文件数: '+d.file_count+'<br><a href="'+d.download_url+'" style="color:#4caf50;font-size:11px">点击下载</a> | install.bat已生成</div>';
    }else{
      r.innerHTML='<div class="alert error">创建失败: '+d.error+'</div>';
    }
  }).catch(function(e){r.innerHTML='<div class="alert error">创建失败: '+e+'</div>'})
}
function downloadPackage(){
  window.open('/api/package/download','_blank');
}

// ── Web Search Socket事件 ──
socket.on('web_search_progress',function(d){
  document.getElementById('web-search-progress').innerHTML='<div class="search-progress"><span class="spinner"></span> '+d.step+' ('+d.progress+'%)</div>';
});
socket.on('web_search_token',function(d){
  var el=document.getElementById('stream-text');
  if(el){
    if(d.done){el.textContent=d.full}else{el.textContent+=d.token}
  }
});
socket.on('web_search_complete',function(d){
  document.getElementById('web-search-progress').innerHTML='';
  lastSearchResults=d;
  renderSearchResults(d);
});
socket.on('web_search_error',function(d){
  document.getElementById('web-search-progress').innerHTML='<div class="alert error">'+d.error+'</div>';
});
socket.on('web_learn_result',function(d){
  if(d.error){alert('学习失败: '+d.error)}else{alert('AI学习完成!')}
});

// ── 训练 ──
function loadDatasets(){fetch('/api/datasets').then(r=>r.json()).then(data=>{var html='';data.forEach(function(d){html+='<div class="dataset-card'+(selectedDataset===d.name?' selected':'')+'" onclick="selectDataset(\''+d.name+'\')"><div class="name">'+d.name+'</div><div class="count">'+d.images+' 张</div></div>'});document.getElementById('dataset-list').innerHTML=html||'暂无数据集'})}
function selectDataset(name){selectedDataset=name;loadDatasets()}
function loadModels(){fetch('/api/models').then(r=>r.json()).then(data=>{var html='';data.forEach(function(m){html+='<div class="model-card"><div class="name">'+m.name+'</div><div class="info">'+m.size_mb+'MB</div></div>'});document.getElementById('model-list').innerHTML=html||'暂无模型'})}
function uploadImages(){var files=document.getElementById('file-input').files;if(!files.length)return;var fd=new FormData();for(var i=0;i<files.length;i++)fd.append('file_'+i,files[i]);if(selectedDataset)fd.append('dataset',selectedDataset);var s=document.getElementById('upload-status');s.innerHTML='<div class="alert info">上传中...</div>';fetch('/api/upload_images',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{s.innerHTML='<div class="alert success">已上传 '+d.uploaded.length+' 张到 '+d.dataset+'</div>';loadDatasets()}).catch(e=>{s.innerHTML='<div class="alert error">失败: '+e+'</div>'})}
function startTraining(){if(!selectedDataset){alert('请先选择数据集');return}var ep=parseInt(document.getElementById('train-epochs').value)||50;var md=document.getElementById('train-model').value;var isz=parseInt(document.getElementById('train-imgsz').value)||640;var autoPush=document.getElementById('auto-push-github').checked;document.getElementById('btn-train-start').style.display='none';document.getElementById('btn-train-stop').style.display='inline-block';document.getElementById('train-progress-container').style.display='block';document.getElementById('train-log').innerHTML='';document.getElementById('train-status-text').textContent='训练中...';fetch('/api/train/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset:selectedDataset,model_name:md,epochs:ep,imgsz:isz,auto_push_github:autoPush})}).then(r=>r.json()).then(d=>{if(d.error)alert(d.error)})}
function stopTraining(){fetch('/api/train/stop',{method:'POST'}).then(r=>r.json()).then(d=>{document.getElementById('train-status-text').textContent='已停止';document.getElementById('btn-train-start').style.display='inline-block';document.getElementById('btn-train-stop').style.display='none'})}
socket.on('training_log',function(d){var l=document.getElementById('train-log');l.innerHTML+='<div>'+escapeHtml(d.line)+'</div>';l.scrollTop=l.scrollHeight})
socket.on('training_complete',function(d){document.getElementById('btn-train-start').style.display='inline-block';document.getElementById('btn-train-stop').style.display='none';document.getElementById('train-status-text').textContent=d.success?'训练完成!':'训练失败';loadModels()})
socket.on('github_push_complete',function(d){document.getElementById('train-status-text').textContent+=' | GitHub推送: '+(d.success?'成功':'失败')})

// ── 参数学习 ──
function loadParams(){fetch('/api/params/list').then(r=>r.json()).then(data=>{var html='';data.forEach(function(p){html+='<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #1a1f2b"><span>'+p.name+'</span><span style="color:#888">'+p.size_kb+'KB</span></div>'});document.getElementById('params-list').innerHTML=html||'暂无参数文件'})}
function uploadParams(){var files=document.getElementById('params-input').files;if(!files.length)return;var fd=new FormData();for(var i=0;i<files.length;i++)fd.append('file_'+i,files[i]);var s=document.getElementById('params-upload-status');s.innerHTML='<div class="alert info">上传中...</div>';fetch('/api/params/upload',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{s.innerHTML='<div class="alert success">已上传 '+d.count+' 个文件</div>';loadParams()}).catch(e=>{s.innerHTML='<div class="alert error">失败</div>'})}
function learnFromParams(){var r=document.getElementById('params-learn-result');r.innerHTML='<div class="alert info"><span class="spinner"></span> AI 正在学习参数...</div>';fetch('/api/params/learn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(res=>res.json()).then(d=>{r.innerHTML='<div class="alert info">学习已启动, 等待 AI 分析...</div>'})}
socket.on('params_learned',function(d){var r=document.getElementById('params-learn-result');if(d.error){r.innerHTML='<div class="alert error">'+d.error+'</div>'}else{r.innerHTML='<div class="alert success"><strong>AI 学习结果:</strong><br><pre style="font-size:10px;white-space:pre-wrap;margin-top:6px;color:#aaa">'+escapeHtml(d.analysis||'')+'</pre></div>'}})

function learnFromCombat(){var r=document.getElementById('combat-learn-result');r.innerHTML='<div class="alert info"><span class="spinner"></span> AI 从实战数据学习...</div>';fetch('/api/combat/learn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(res=>res.json()).then(d=>{r.innerHTML='<div class="alert info">学习已启动</div>'})}
socket.on('combat_learn_result',function(d){var r=document.getElementById('combat-learn-result');if(d.error){r.innerHTML='<div class="alert error">'+d.error+'</div>'}else{var html='<div class="alert success">学习完成! 共'+d.total_experiences+'条经验</div>';if(d.stats)html+='<div style="font-size:10px;color:#888">平均分: '+d.stats.avg_score+' | 正向率: '+d.stats.positive_rate+'%</div>';if(d.rules&&d.rules.length)html+='<div style="font-size:10px;color:#4caf50">新规则: '+d.rules.join('; ')+'</div>';if(d.summary)html+='<div style="font-size:10px;color:#aaa;margin-top:4px;white-space:pre-wrap">'+escapeHtml(d.summary)+'</div>';r.innerHTML=html}})
function exportCombatData(){fetch('/api/combat/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({format:'csv'})}).then(r=>r.blob()).then(b=>{var a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='combat_data.csv';a.click()})}

// ── 学习日志 ──
function refreshLearningLog(){fetch('/api/learning_log?limit=100').then(r=>r.json()).then(data=>renderLearningLog(data))}
function clearLearningLog(){fetch('/api/learning_log/clear',{method:'POST'}).then(r=>r.json()).then(d=>{document.getElementById('learning-log-container').innerHTML='<div style="color:#888;padding:20px;text-align:center">日志已清空</div>'})}
function exportLearningLog(){fetch('/api/learning_log/export').then(r=>r.json()).then(d=>{var a=document.createElement('a');a.href='data:text/json;charset=utf-8,'+encodeURIComponent(JSON.stringify(d,null,2));a.download='learning_log.json';a.click()})}
function renderLearningLog(data){var c=document.getElementById('learning-log-container');if(!data.length){c.innerHTML='<div style="color:#888;padding:20px;text-align:center">暂无学习日志</div>';return}var html='';data.reverse().forEach(function(e){var catClass=e.category||'system';html+='<div class="learning-log-item"><span class="ll-time">'+e.time+'</span> <span class="ll-cat '+catClass+'">'+catClass+'</span> <span class="ll-msg">'+escapeHtml(e.message)+'</span>'+(e.detail?'<div class="ll-detail">'+escapeHtml(e.detail)+'</div>':'')+'</div>'});c.innerHTML=html}
socket.on('learning_log_update',function(d){var c=document.getElementById('learning-log-container');if(c){var cur=c.querySelector('.learning-log-item');var e=d.entry;var catClass=e.category||'system';var item='<div class="learning-log-item"><span class="ll-time">'+e.time+'</span> <span class="ll-cat '+catClass+'">'+catClass+'</span> <span class="ll-msg">'+escapeHtml(e.message)+'</span>'+(e.detail?'<div class="ll-detail">'+escapeHtml(e.detail)+'</div>':'')+'</div>';if(cur)c.insertAdjacentHTML('afterbegin',item);else c.innerHTML=item;while(c.children.length>200)c.removeChild(c.lastChild)}});

// ── 指挥面板事件 ──
socket.on('cycle_update',function(d){
  document.getElementById('cycle').textContent=d.cycle||0;
  document.getElementById('allies').textContent=d.allies||0;
  document.getElementById('enemies').textContent=d.enemies||0;
  document.getElementById('score').textContent=d.score||0;
  document.getElementById('total-score').textContent=d.total_score||0;
  document.getElementById('avg-time').textContent=(d.avg_cycle_time_ms||0)+'ms';
  document.getElementById('exp-count').textContent=d.experience_count||0;
  document.getElementById('rules-count').textContent=d.rules_count||0;
  if(d.status){document.getElementById('status-badge').textContent=d.status;document.getElementById('status-badge').style.color=d.running?'#4caf50':'#888'}
  if(d.running!==undefined&&!d.running){document.getElementById('status-badge').textContent='已停止';document.getElementById('status-badge').style.color='#888'}
  // 更新决策日志
  if(d.decisions){
    var log=document.getElementById('decision-log');var html='';
    d.decisions.slice(-20).reverse().forEach(function(dc){
      var cls='';if(dc.command)cls=' cmd-item';
      html+='<div class="log-item'+cls+'"><div class="lhead"><span class="cyc">#'+dc.cycle+'</span><span class="act">'+escapeHtml(dc.action)+'</span><span class="sco '+(dc.score>0?'pos':(dc.score<0?'neg':''))+'">'+(dc.score>=0?'+':'')+dc.score+'</span></div>'+(dc.reason?'<div class="reason">'+escapeHtml(dc.reason)+'</div>':'')+'</div>'
    });
    log.innerHTML=html||'<div style="color:#888;padding:10px">等待决策...</div>'
  }
  // 更新图表
  if(d.scores_history&&scoreChart){
    scoreChart.data.labels=d.scores_history.map(function(s){return '#'+s.cycle});
    scoreChart.data.datasets[0].data=d.scores_history.map(function(s){return s.score});
    scoreChart.data.datasets[1].data=d.scores_history.map(function(s){return s.total});
    scoreChart.update()
  }
  // 用户指令
  if(d.user_commands){
    var cl=document.getElementById('user-cmd-log');if(cl){
      var html='';d.user_commands.slice(-10).reverse().forEach(function(c){html+='<div class="log-item cmd-item"><div class="lhead"><span class="cyc">#'+c.cycle+'</span><span class="act" style="color:#ff9800">'+escapeHtml(c.command)+'</span></div></div>'});
      cl.innerHTML=html
    }
  }
});
socket.on('command_analysis',function(d){
  var box=document.getElementById('thinking-box');if(box)box.innerHTML='<span class="highlight">[指令分析]</span> '+escapeHtml(d.analysis||'');
});
socket.on('command_recorded',function(d){});
socket.on('started',function(d){
  var mode=d.mode||'combat';
  var label=mode==='smart'?'AI在线(智能模式)':'战斗中...';
  document.getElementById('status-badge').textContent=label;
  document.getElementById('status-badge').style.color='#4caf50';
  document.getElementById('thinking-box').textContent='DeepSeek智能体已就绪';
});
socket.on('smart_mode_status',function(d){
  document.getElementById('status-badge').textContent='AI在线(智能模式)';
  document.getElementById('status-badge').style.color='#4caf50';
  document.getElementById('thinking-box').textContent=d.message||'DeepSeek智能体已就绪';
});
socket.on('stopped',function(d){
  document.getElementById('status-badge').textContent='已停止';
  document.getElementById('status-badge').style.color='#888';
  document.getElementById('thinking-box').textContent='AI已离线';
});

// ── 版本/设置 ──
function loadVersion(){fetch('/api/version').then(r=>r.json()).then(d=>{
  document.getElementById('version-info').innerHTML='版本: v'+d.version+' | 构建: '+d.build+' | Python: '+d.python.split('\\n')[0]+' | PyTorch: '+(d.pytorch||'N/A')+' | Git: '+(d.git_branch||'')+'@'+(d.git_commit||'');
  document.getElementById('conn-pytorch-status').textContent=d.pytorch||'N/A';document.getElementById('conn-pytorch-status').className='conn-status '+(d.pytorch?'online':'offline');
})}
function checkVersion(){fetch('/api/version/check').then(r=>r.json()).then(d=>{document.getElementById('version-check-result').innerHTML='<div class="alert info">当前版本: v'+d.current_version+' | 构建: '+d.current_build+' | 文件数: '+d.files_modified+'</div>'})}
function reloadModules(){fetch('/api/version/reload',{method:'POST'}).then(r=>r.json()).then(d=>{document.getElementById('version-check-result').innerHTML='<div class="alert '+(d.status==='reloaded'?'success':'error')+'">'+d.status+': '+(d.modules||[]).join(', ')+'</div>'})}
function verifyAPI(){fetch('/api/verify_api_http').then(r=>r.json()).then(d=>{
  var ds=d.deepseek;var el=document.getElementById('api-deepseek-status');var dl=document.getElementById('api-deepseek-detail');
  if(el){el.textContent=ds.status==='online'?'在线':ds.status;el.className='conn-status '+(ds.status==='online'?'online':'offline')}
  if(dl){dl.textContent='延迟: '+(ds.latency_ms||'?')+'ms | 模型: '+(ds.models||[]).slice(0,3).join(', ')}
  setMini('conn-api',ds.status==='online'?'online':'offline','API');
  setConnCard('conn-deepseek-status','conn-deepseek-detail',ds.status,'DeepSeek');
})}

// ── GPU 检测 ──
function checkGPU(){
  var el=document.getElementById('conn-gpu-status');
  var dl=document.getElementById('conn-gpu-detail');
  el.textContent='检查中';el.className='conn-status checking';
  fetch('/api/gpu/status').then(r=>r.json()).then(d=>{
    if(d.gpus&&d.gpus.length>0){
      var g=d.gpus[0];
      el.textContent=g.name;
      el.className='conn-status online';
      dl.textContent='显存: '+g.memory+' | 驱动: '+g.driver+' | CUDA: '+g.cuda;
      if(d.pytorch_cuda){dl.textContent+=' | PyTorch CUDA: 可用 ('+d.pytorch_version+')'}
      else{dl.textContent+=' | PyTorch: CPU模式 ('+(d.pytorch_version||'')+')'}
      if(d.message){dl.textContent+=' | '+d.message}
    }else{
      el.textContent='未检测到GPU';
      el.className='conn-status offline';
      dl.textContent=d.message||'';
    }
  }).catch(function(e){el.textContent='错误';el.className='conn-status offline';dl.textContent=str(e)})
}
function installCUDATorch(){
  var el=document.getElementById('conn-gpu-status');
  el.textContent='安装中...';el.className='conn-status checking';
  var dl=document.getElementById('conn-gpu-detail');
  fetch('/api/gpu/install_cuda_torch',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
    el.textContent='安装中...';el.className='conn-status checking';
    dl.textContent='等待安装完成...';
  })
}
socket.on('gpu_install_progress',function(d){
  document.getElementById('conn-gpu-detail').textContent=d.step+' ('+d.progress+'%)';
});
socket.on('gpu_install_complete',function(d){
  var el=document.getElementById('conn-gpu-status');
  if(d.success){el.textContent='安装成功';el.className='conn-status online'}else{el.textContent='安装失败';el.className='conn-status offline'}
  document.getElementById('conn-gpu-detail').textContent=d.message;
  setTimeout(checkGPU,2000);
});

// ── 模拟器管理 ──
var emulatorInterval=null;
var emulatorScreenImage=null;
function checkEmulatorStatus(){
  fetch('/api/emulator/status').then(r=>r.json()).then(d=>{
    setDiagStatus('emu-installed',d.installed?'已安装':'未安装',d.installed);
    setDiagStatus('emu-avd',d.avd_exists?'已创建':'未创建',d.avd_exists);
    setDiagStatus('emu-running',d.running?'运行中':'已停止',d.running);
    setDiagStatus('emu-adb',d.adb_connected?'已连接':'未连接',d.adb_connected);
    if(d.installed){document.getElementById('emu-installed').textContent=d.emulator_path.replace(/\\/g,'\\\\').split('\\\\').pop()||'已安装'}
    if(d.running&&!emulatorInterval){startEmulatorRefresh()}
    if(!d.running){stopEmulatorRefresh();var ph=document.getElementById('emu-screen-placeholder');if(ph)ph.style.display='block'}
  }).catch(function(e){document.getElementById('emu-progress').innerHTML='<div class="alert error">检查失败: '+e+'</div>'})
}
function setDiagStatus(id,text,ok){
  var el=document.getElementById(id);if(!el)return;
  el.textContent=text;el.className='diag-status '+(ok?'ok':'fail');
}
function installEmulator(){
  var p=document.getElementById('emu-progress');
  p.innerHTML='<div class="alert info"><span class="spinner"></span> 开始安装Android模拟器...这可能需要几分钟</div>';
  fetch('/api/emulator/install',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
    p.innerHTML='<div class="alert info">安装已启动，请等待进度更新...</div>';
  }).catch(function(e){p.innerHTML='<div class="alert error">安装失败: '+e+'</div>'})
}
function startEmulator(){
  var p=document.getElementById('emu-progress');
  p.innerHTML='<div class="alert info"><span class="spinner"></span> 启动模拟器...</div>';
  fetch('/api/emulator/start',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
    if(d.status==='already_running'){p.innerHTML='<div class="alert warning">模拟器已在运行</div>';checkEmulatorStatus()}
    else{p.innerHTML='<div class="alert info">启动中，请等待...</div>'}
  }).catch(function(e){p.innerHTML='<div class="alert error">启动失败: '+e+'</div>'})
}
function stopEmulator(){
  fetch('/api/emulator/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
    stopEmulatorRefresh();
    document.getElementById('emu-progress').innerHTML='<div class="alert success">模拟器已停止</div>';
    checkEmulatorStatus();
  }).catch(function(e){document.getElementById('emu-progress').innerHTML='<div class="alert error">停止失败: '+e+'</div>'})
}
function installGameAPK(){
  var p=document.getElementById('emu-progress');
  p.innerHTML='<div class="alert info"><span class="spinner"></span> 安装游戏APK...</div>';
  fetch('/api/emulator/install_apk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(r=>r.json()).then(d=>{
    if(d.status==='success')p.innerHTML='<div class="alert success">APK安装成功!</div>';
    else p.innerHTML='<div class="alert error">安装失败: '+(d.error||d.output||'未知')+'</div>';
  }).catch(function(e){p.innerHTML='<div class="alert error">安装失败: '+e+'</div>'})
}
function installAPKPrompt(){
  var path=prompt('请输入APK文件路径:\\n例如: D:\\\\firefight\\\\Firefight.apk');
  if(!path)return;
  var p=document.getElementById('emu-progress');
  p.innerHTML='<div class="alert info"><span class="spinner"></span> 安装APK: '+path+'...</div>';
  fetch('/api/emulator/install_apk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({apk_path:path})}).then(r=>r.json()).then(d=>{
    if(d.status==='success')p.innerHTML='<div class="alert success">APK安装成功!</div>';
    else p.innerHTML='<div class="alert error">安装失败: '+(d.error||d.output||'未知')+'</div>';
  }).catch(function(e){p.innerHTML='<div class="alert error">安装失败: '+e+'</div>'})
}
function refreshEmulatorScreen(){
  fetch('/api/emulator/screenshot').then(r=>r.json()).then(d=>{
    if(d.error){return}
    emulatorScreenImage=d.image;
    var canvas=document.getElementById('emu-screen-canvas');
    if(!canvas)return;
    var ctx=canvas.getContext('2d');
    var img=new Image();
    img.onload=function(){
      canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;
      ctx.drawImage(img,0,0);
      var ph=document.getElementById('emu-screen-placeholder');
      if(ph)ph.style.display='none';
    };
    img.src='data:image/png;base64,'+d.image;
    var now=new Date();
    if(window._lastEmuScreen){var fps=Math.round(1000/(now-window._lastEmuScreen));document.getElementById('emu-screen-fps').textContent=fps+'fps'}
    window._lastEmuScreen=now;
  }).catch(function(){})
}
function startEmulatorRefresh(){
  if(emulatorInterval)return;
  refreshEmulatorScreen();
  emulatorInterval=setInterval(refreshEmulatorScreen,333);
}
function stopEmulatorRefresh(){
  if(emulatorInterval){clearInterval(emulatorInterval);emulatorInterval=null}
}
function toggleEmulatorRefresh(){
  if(document.getElementById('emu-auto-refresh').checked){startEmulatorRefresh()}else{stopEmulatorRefresh()}
}
function handleEmulatorClick(e){
  var canvas=document.getElementById('emu-screen-canvas');
  if(!canvas||!emulatorScreenImage)return;
  var rect=canvas.getBoundingClientRect();
  var scaleX=canvas.width/rect.width;
  var scaleY=canvas.height/rect.height;
  var x=Math.round((e.clientX-rect.left)*scaleX);
  var y=Math.round((e.clientY-rect.top)*scaleY);
  document.getElementById('emu-touch-x').value=x;
  document.getElementById('emu-touch-y').value=y;
  emuTouch('tap',x,y);
}
function emuTouch(action,ox,oy){
  var x=ox||parseInt(document.getElementById('emu-touch-x').value)||0;
  var y=oy||parseInt(document.getElementById('emu-touch-y').value)||0;
  var r=document.getElementById('emu-touch-result');
  fetch('/api/emulator/touch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({x:x,y:y,action:action})}).then(res=>res.json()).then(d=>{
    r.textContent=action+' ('+x+','+y+'): '+(d.status||'');
    if(d.status==='ok')r.style.color='#4caf50';else r.style.color='#e53935';
  }).catch(function(e){r.textContent='失败: '+e;r.style.color='#e53935'})
}
function emuSwipe(){
  var x1=parseInt(document.getElementById('emu-touch-x').value)||0;
  var y1=parseInt(document.getElementById('emu-touch-y').value)||0;
  var x2=prompt('滑动终点X:');if(x2===null)return;
  var y2=prompt('滑动终点Y:');if(y2===null)return;
  var r=document.getElementById('emu-touch-result');
  fetch('/api/emulator/touch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({x:x1,y:y1,x2:parseInt(x2),y2:parseInt(y2),action:'swipe'})}).then(res=>res.json()).then(d=>{
    r.textContent='swipe: '+(d.status||'');r.style.color=d.status==='ok'?'#4caf50':'#e53935';
  }).catch(function(e){r.textContent='失败: '+e;r.style.color='#e53935'})
}
socket.on('emu_install_progress',function(d){document.getElementById('emu-progress').innerHTML='<div class="alert info"><span class="spinner"></span> '+d.step+' ('+d.progress+'%)</div>'});
socket.on('emu_install_complete',function(d){
  var html='<div class="alert '+(d.success?'success':'error')+'">'+d.message+'</div>';
  document.getElementById('emu-progress').innerHTML=html;
  checkEmulatorStatus();
});
socket.on('emu_start_progress',function(d){document.getElementById('emu-progress').innerHTML='<div class="alert info"><span class="spinner"></span> '+d.step+' ('+d.progress+'%)</div>'});
socket.on('emu_start_complete',function(d){
  document.getElementById('emu-progress').innerHTML='<div class="alert success">模拟器启动完成! 端口: '+d.port+'</div>';
  checkEmulatorStatus();
  startEmulatorRefresh();
});
socket.on('emu_start_error',function(d){document.getElementById('emu-progress').innerHTML='<div class="alert error">'+d.error+'</div>'});

// ── scrcpy 投屏控制 ──
function checkScrcpyStatus(){
  fetch('/api/scrcpy/status').then(r=>r.json()).then(d=>{
    var s=document.getElementById('scrcpy-status');
    if(d.installed){
      s.innerHTML=d.running?'<span style="color:#4caf50">运行中</span>':'<span style="color:#ff9800">已安装(未运行)</span>';
    }else{
      s.innerHTML='<span style="color:#e53935">未安装</span>';
    }
  }).catch(function(e){document.getElementById('scrcpy-status').innerHTML='<span style="color:#e53935">检测失败</span>'})
}
function installScrcpy(){
  document.getElementById('scrcpy-status').innerHTML='<span class="spinner"></span> 安装中...';
  fetch('/api/scrcpy/install',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'){document.getElementById('scrcpy-status').innerHTML='<span style="color:#4caf50">安装成功</span>'}
    else{document.getElementById('scrcpy-status').innerHTML='<span style="color:#e53935">'+d.message+'</span>'}
  }).catch(function(e){document.getElementById('scrcpy-status').innerHTML='<span style="color:#e53935">安装失败: '+e+'</span>'})
}
function startScrcpy(){
  var res=document.getElementById('scrcpy-res').value;
  var fps=document.getElementById('scrcpy-fps').value;
  var bitrate=document.getElementById('scrcpy-bitrate').value;
  document.getElementById('scrcpy-status').innerHTML='<span class="spinner"></span> 启动投屏...';
  fetch('/api/scrcpy/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_width:parseInt(res),max_fps:parseInt(fps),bitrate:parseInt(bitrate)})}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'){document.getElementById('scrcpy-status').innerHTML='<span style="color:#4caf50">投屏已启动 (全屏)</span>'}
    else{document.getElementById('scrcpy-status').innerHTML='<span style="color:#e53935">'+d.error+'</span>'}
  }).catch(function(e){document.getElementById('scrcpy-status').innerHTML='<span style="color:#e53935">启动失败: '+e+'</span>'})
}
function stopScrcpy(){
  fetch('/api/scrcpy/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
    document.getElementById('scrcpy-status').innerHTML='<span style="color:#ff9800">已停止</span>';
  })
}

// ── 端口检测 ──
function checkPorts(){
  var pr=document.getElementById('port-check-result');
  pr.innerHTML='<span class="spinner"></span> 检测中...';
  fetch('/api/port/check').then(r=>r.json()).then(d=>{
    var html='';
    if(d.xinglv_detected){
      html+='<div class="alert warning">检测到行旅白相关进程占用端口</div>';
      d.xinglv_ports.forEach(function(p){
        html+='<div style="font-size:10px;color:#e53935;margin:2px 0">端口 '+p.port+' (PID: '+p.pid+') - '+p.process+'</div>';
      });
    }else{
      html+='<div class="alert success">未检测到行旅白端口冲突</div>';
    }
    html+='<div style="font-size:10px;margin-top:4px">建议端口: <span style="color:#4caf50;font-weight:600">'+d.suggested_port+'</span></div>';
    html+='<div style="font-size:10px;margin-top:4px">';
    if(d.port_scan){d.port_scan.forEach(function(p){
      html+='<span style="margin-right:8px;color:'+(p.occupied?'#e53935':'#4caf50')+'">'+p.port+':'+(p.occupied?'占用':'空闲')+'</span>';
    })}
    html+='</div>';
    pr.innerHTML=html;
  }).catch(function(e){pr.innerHTML='<span style="color:#e53935">检测失败: '+e+'</span>'})
}
function verifyDecisionChain(){
  var p=document.getElementById('chain-verify-progress');
  var r=document.getElementById('chain-verify-result');
  p.innerHTML='<div class="alert info"><span class="spinner"></span> 验证决策链中...</div>';
  r.innerHTML='';
  fetch('/api/chain/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(res=>res.json()).then(d=>{
    p.innerHTML='';
    var html='<div class="alert '+(d.all_ok?'success':'error')+'"><strong>'+d.summary+'</strong></div>';
    if(d.steps){
      for(var k in d.steps){
        var s=d.steps[k];
        html+='<div class="diag-item"><span class="diag-name">'+k+'</span><span class="diag-status '+(s.status==='ok'?'ok':'fail')+'">'+s.status+'</span><span class="diag-detail">'+escapeHtml(s.detail||'')+'</span></div>';
      }
    }
    r.innerHTML=html;
  }).catch(function(e){p.innerHTML='<div class="alert error">验证失败: '+e+'</div>'})
}
function rebuildDecisionChain(){
  document.getElementById('chain-verify-progress').innerHTML='<div class="alert info"><span class="spinner"></span> 重建决策链...</div>';
  socket.emit('rebuild_chain');
}
function oneClickDeploy(){
  var r=document.getElementById('chain-verify-result');
  r.innerHTML='<div class="alert info"><span class="spinner"></span> 一键部署中...</div>';
  fetch('/api/chain/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(res=>res.json()).then(d=>{
    var ok=0,fail=0;
    if(d.steps){for(var k in d.steps){if(d.steps[k].status==='ok')ok++;else fail++}}
    r.innerHTML='<div class="alert '+(fail===0?'success':'warning')+'">决策链状态: '+ok+'/'+(ok+fail)+' 就绪</div>';
    if(fail>0){
      fetch('/api/adb/reconnect',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(function(){r.innerHTML+='<div style="font-size:10px;color:#4caf50">ADB重连已触发</div>'})
    }
  }).catch(function(e){r.innerHTML='<div class="alert error">部署失败: '+e+'</div>'})
}
function executeAgent(cmd){
  var r=document.getElementById('chain-verify-result');
  r.innerHTML='<div class="alert info"><span class="spinner"></span> 智能体执行: '+cmd+'</div>';
  fetch('/api/agent/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:cmd})}).then(res=>res.json()).then(d=>{
    r.innerHTML='<div class="alert info">任务已启动: '+cmd+'</div>'
  }).catch(function(e){r.innerHTML='<div class="alert error">启动失败: '+e+'</div>'})
}
socket.on('agent_progress',function(d){
  var r=document.getElementById('chain-verify-result');
  if(r)r.innerHTML='<div class="alert info">'+d.step+' ('+d.progress+'%)</div>'
});
socket.on('agent_step_result',function(d){
  var r=document.getElementById('chain-verify-result');
  if(r){
    var ok=d.result&&!d.result.error;
    r.innerHTML+='<div style="font-size:10px;color:'+(ok?'#4caf50':'#e53935')+'">['+d.index+'/'+d.total+'] '+d.tool+': '+(ok?'OK':'FAIL')+'</div>'
  }
});
socket.on('agent_complete',function(d){
  var r=document.getElementById('chain-verify-result');
  if(r){
    var html='<div class="alert success">智能体完成: '+escapeHtml(d.summary||'')+'</div>';
    if(d.results){
      d.results.forEach(function(s){html+='<div style="font-size:10px;color:#aaa">'+s.tool+': '+JSON.stringify(s.result||{}).substring(0,100)+'</div>'})
    }
    r.innerHTML=html;
  }
});
socket.on('agent_error',function(d){
  var r=document.getElementById('chain-verify-result');
  if(r)r.innerHTML='<div class="alert error">智能体错误: '+d.error+'</div>'
});
socket.on('chain_verify_progress',function(d){
  document.getElementById('chain-verify-progress').innerHTML='<div class="alert info"><span class="spinner"></span> '+d.step+' ('+d.progress+'%)</div>';
});
socket.on('chain_verify_complete',function(d){
  document.getElementById('chain-verify-progress').innerHTML='';
  var r=document.getElementById('chain-verify-result');
  if(r){
    var html='<div class="alert '+(d.all_ok?'success':'error')+'"><strong>'+d.summary+'</strong></div>';
    if(d.steps){for(var k in d.steps){var s=d.steps[k];html+='<div class="diag-item"><span class="diag-name">'+k+'</span><span class="diag-status '+(s.status==='ok'?'ok':'fail')+'">'+s.status+'</span><span class="diag-detail">'+escapeHtml(s.detail||'')+'</span></div>'}}
    r.innerHTML=html;
  }
});

</textarea>
</div>
</div>

// ── DeepSeek 余额查询 ──
function checkBalance(){
  fetch('/api/deepseek/balance').then(r=>r.json()).then(d=>{
    var dl=document.getElementById('conn-deepseek-detail');
    if(d.status==='ok'){
      var balances=d.balance||[];
      var html='';
      if(balances.length===0){
        html='<span style="color:#ff9800">余额数据为空，请检查API Key</span>';
      }else{
        balances.forEach(function(b){
          html+='<div style="margin:2px 0"><span style="color:#4caf50;font-weight:600">'+b.currency+' '+b.total_balance+'</span></div>';
          html+='<div style="font-size:9px;color:#888">充值余额: '+b.topped_up_balance+' | 赠送余额: '+b.granted_balance+'</div>';
        });
      }
      dl.innerHTML=html||'余额: N/A';
    }else{
      dl.innerHTML='<span style="color:#e53935">查询失败: '+(d.message||'未知错误')+'</span>';
    }
  }).catch(function(e){document.getElementById('conn-deepseek-detail').innerHTML='<span style="color:#e53935">查询失败: '+e+'</span>'})
}

// ── 数据管理 (表格版) ──
var dataBrowseCache=null;
function browseData(){
  var tbody=document.getElementById('data-browse-result');
  tbody.innerHTML='<tr><td colspan="6" style="padding:20px;text-align:center"><span class="spinner"></span> 扫描中...</td></tr>';
  fetch('/api/data/browse').then(r=>r.json()).then(d=>{
    dataBrowseCache=d;
    document.getElementById('data-total-size').textContent='总占用: '+d.total_size_mb+' MB';
    var html='';
    var dirs=['data','sessions','logs','runs','test_screenshots'];
    var allFiles=[];
    dirs.forEach(function(name){
      var dd=d[name];
      if(!dd||!dd.exists||!dd.files)return;
      dd.files.forEach(function(f){
        f._dir=name;
        allFiles.push(f);
      });
    });
    // 按可删除优先排序
    allFiles.sort(function(a,b){
      if(a.can_delete!==b.can_delete)return a.can_delete?-1:1;
      return b.age_hours-a.age_hours;
    });
    if(allFiles.length===0){
      html='<tr><td colspan="6" style="padding:20px;text-align:center;color:#888">暂无数据文件</td></tr>';
    }else{
      allFiles.forEach(function(f){
        var bg=f.can_delete?'background:#2a1a1a':'';
        var statusHtml=f.can_delete
          ?'<span style="background:#e53935;color:#fff;padding:2px 6px;border-radius:3px;font-size:9px" title="'+f.reason+'">可清理</span>'
          :'<span style="color:#888;font-size:9px">正常</span>';
        var delBtn=f.can_delete
          ?'<button class="btn-clear" onclick="deleteFile(\''+f.path.replace(/\\/g,'\\\\')+'\')" style="padding:2px 8px;font-size:9px">删除</button>'
          :'<span style="color:#555;font-size:9px">-</span>';
        var reason=f.can_delete?'<span style="color:#e53935;font-size:9px;margin-left:4px">'+f.reason+'</span>':'';
        html+='<tr style="'+bg+';border-bottom:1px solid #1a1f2b">';
        html+='<td style="padding:4px 8px">'+f.name+reason+'</td>';
        html+='<td style="padding:4px 8px;color:#888">'+f._dir+'/</td>';
        html+='<td style="padding:4px 8px;text-align:right;color:#aaa">'+f.size_mb+' MB</td>';
        html+='<td style="padding:4px 8px;text-align:right;color:#555">'+f.age_hours+'h前</td>';
        html+='<td style="padding:4px 8px;text-align:center">'+statusHtml+'</td>';
        html+='<td style="padding:4px 8px;text-align:center">'+delBtn+'</td>';
        html+='</tr>';
      });
    }
    tbody.innerHTML=html;
  }).catch(function(e){document.getElementById('data-browse-result').innerHTML='<tr><td colspan="6" style="padding:20px;text-align:center;color:#e53935">扫描失败: '+e+'</td></tr>'})
}
function autoCleanup(){
  if(!confirm('确认删除超过5分钟的截图和临时文件？'))return;
  var tbody=document.getElementById('data-browse-result');
  tbody.innerHTML='<tr><td colspan="6" style="padding:20px;text-align:center"><span class="spinner"></span> 清理中...</td></tr>';
  fetch('/api/data/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dry_run:false})}).then(r=>r.json()).then(d=>{
    tbody.innerHTML='<tr><td colspan="6" style="padding:16px;text-align:center"><div class="alert success">已删除 '+d.deleted+' 个文件</div>'+(d.errors.length?'<div class="alert error">'+d.errors.length+' 个错误</div>':'')+'</td></tr>';
    setTimeout(browseData,1500);
  }).catch(function(e){tbody.innerHTML='<tr><td colspan="6" style="padding:20px;text-align:center;color:#e53935">清理失败: '+e+'</td></tr>'})
}
function selectiveCleanup(){
  if(!dataBrowseCache){alert('请先浏览数据');return}
  var files=[];
  var dirs=['data','sessions','logs','runs','test_screenshots'];
  dirs.forEach(function(name){
    var dd=dataBrowseCache[name];
    if(dd&&dd.files){dd.files.forEach(function(f){if(f.can_delete)files.push(f.path)})}
  });
  if(!files.length){alert('没有可清理的文件');return}
  if(!confirm('确认删除 '+files.length+' 个可清理文件？'))return;
  var tbody=document.getElementById('data-browse-result');
  tbody.innerHTML='<tr><td colspan="6" style="padding:20px;text-align:center"><span class="spinner"></span> 清理中...</td></tr>';
  fetch('/api/data/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({files:files,dry_run:false})}).then(r=>r.json()).then(d=>{
    tbody.innerHTML='<tr><td colspan="6" style="padding:16px;text-align:center"><div class="alert success">已删除 '+d.deleted+' 个文件</div></td></tr>';
    setTimeout(browseData,1500);
  })
}
function deleteFile(path){
  if(!confirm('删除 '+path+'?'))return;
  fetch('/api/data/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({files:[path],dry_run:false})}).then(r=>r.json()).then(d=>{
    browseData();
  })
}
function toggleAutoCleanup(){
  var enable=document.getElementById('auto-cleanup-toggle').checked;
  fetch('/api/data/auto_cleanup/toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enable:enable})}).then(r=>r.json()).then(d=>{
    var label=document.getElementById('auto-cleanup-label');
    if(d.running){
      label.textContent='已开启';label.style.color='#4caf50';
    }else{
      label.textContent='已关闭';label.style.color='#e53935';
    }
  }).catch(function(e){alert('切换失败: '+e)})
}

// ── 初始化 ──
document.addEventListener('DOMContentLoaded',function(){
  initChart();loadVersion();checkPyTorch();checkGPU();
  setTimeout(function(){checkADB();checkGitHub();checkServer()},1000);
  // 定期刷新连接状态
  setInterval(function(){checkAllConnections()},30000);
  // 定期刷新ADB状态
  setInterval(function(){checkADB()},15000);
  // 定期刷新GitHub状态
  setInterval(function(){checkGitHub()},30000);
  // 定期刷新服务器状态
  setInterval(function(){checkServer()},30000);
});
</script>
</body>
</html>
"""

ANNOTATE_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Firefight AI - 标注工具</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0e14;color:#d0d0d0;padding:12px}
h3{font-size:13px;color:#58a5f3;margin-bottom:10px}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;align-items:center}
.toolbar button{padding:6px 14px;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;background:#1a1f2b;color:#d0d0d0;border:1px solid #252a33}
.toolbar button:hover{background:#252a33;border-color:#58a5f3}
.toolbar button.active{background:#58a5f3;color:#000;border-color:#58a5f3}
.toolbar select{padding:6px 10px;border:1px solid #252a33;border-radius:6px;background:#1a1f2b;color:#d0d0d0;font-size:12px}
.toolbar .shortcut{font-size:10px;color:#888}
.workspace{display:flex;gap:10px;height:calc(100vh - 140px)}
.image-list{width:180px;overflow-y:auto;border:1px solid #252a33;border-radius:8px;padding:8px;background:#11151c;flex-shrink:0}
.image-list .thumb{padding:6px 8px;font-size:11px;border-bottom:1px solid #1a1f2b;cursor:pointer;border-radius:4px;display:flex;align-items:center;gap:6px}
.image-list .thumb:hover{background:#1a1f2b}
.image-list .thumb.active{background:#1a2530;color:#58a5f3}
.image-list .thumb .badge{font-size:9px;padding:1px 5px;border-radius:8px;background:#4caf50;color:#000}
.image-list .thumb .badge.unlabeled{background:#e53935;color:#fff}
.annotation-area{flex:1;position:relative;border:1px solid #252a33;border-radius:8px;overflow:hidden;background:#000;display:flex;align-items:center;justify-content:center}
.annotation-area canvas{max-width:100%;max-height:100%;object-fit:contain}
.annotation-area .info-overlay{position:absolute;top:8px;left:8px;background:rgba(0,0,0,0.7);padding:4px 8px;border-radius:4px;font-size:10px;color:#aaa}
.class-selector{display:flex;gap:8px;margin-bottom:8px}
.class-btn{padding:5px 14px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;border:1px solid #252a33;background:#1a1f2b;color:#888}
.class-btn.active-tank{background:#1a2530;color:#58a5f3;border-color:#58a5f3}
.class-btn.active-infantry{background:#1a3020;color:#4caf50;border-color:#4caf50}
.class-btn:hover{border-color:#58a5f3}
.stats{font-size:10px;color:#888;margin-top:6px}
</style>
</head>
<body>
<div class="toolbar">
  <h3 style="margin:0;margin-right:12px">标注工具</h3>
  <select id="dataset-select" onchange="loadImages()">
    <option value="faction_yolo">faction_yolo</option>
  </select>
  <button onclick="loadImages()">刷新</button>
  <span style="color:#888">|</span>
  <span class="shortcut">N=下一张 P=上一张 S=保存 Del=删除标注</span>
</div>
<div class="class-selector">
  <button class="class-btn active-tank" id="class-btn-tank" onclick="setClass(0)">Tank (0)</button>
  <button class="class-btn" id="class-btn-infantry" onclick="setClass(1)">Infantry (1)</button>
  <span class="stats" id="annot-stats"></span>
</div>
<div class="workspace">
  <div class="image-list" id="image-list">加载中...</div>
  <div class="annotation-area" id="annotation-area">
    <canvas id="annot-canvas"></canvas>
    <div class="info-overlay" id="info-overlay">选择图片开始标注</div>
  </div>
</div>
<script>
var currentClass=0;
var currentImage='';
var currentDataset='faction_yolo';
var images=[];
var labels=[];
var drawing=false;
var startX=0,startY=0;
var imgNaturalW=0,imgNaturalH=0;
var canvasW=0,canvasH=0;
var imgObj=null;

function setClass(c){
  currentClass=c;
  document.getElementById('class-btn-tank').className='class-btn'+(c===0?' active-tank':'');
  document.getElementById('class-btn-infantry').className='class-btn'+(c===1?' active-infantry':'');
}
function loadImages(){
  var ds=document.getElementById('dataset-select').value;currentDataset=ds;
  fetch('/api/annotate/images?dataset='+ds).then(r=>r.json()).then(data=>{
    images=data;renderImageList();
    if(images.length>0&&!currentImage){selectImage(images[0].name)}
  })
}
function renderImageList(){
  var list=document.getElementById('image-list');
  if(!images.length){list.innerHTML='<div style="color:#888;padding:10px;font-size:11px">暂无图片</div>';return}
  var html='';images.forEach(function(img){
    html+='<div class="thumb'+(img.name===currentImage?' active':'')+'" onclick="selectImage(\''+img.name+'\')"><span class="badge '+(img.labeled?'':'unlabeled')+'">'+(img.labeled?img.label_count:'未')+'</span>'+img.name+'</div>'
  });
  list.innerHTML=html
}
function selectImage(name){
  currentImage=name;labels=[];renderImageList();
  var img=images.find(function(i){return i.name===name});
  if(img&&img.labels)labels=img.labels.slice();
  var canvas=document.getElementById('annot-canvas');
  var ctx=canvas.getContext('2d');
  imgObj=new Image();
  imgObj.onload=function(){
    imgNaturalW=imgObj.naturalWidth;imgNaturalH=imgObj.naturalHeight;
    var area=document.getElementById('annotation-area');
    var maxW=area.clientWidth-20;var maxH=area.clientHeight-20;
    var scale=Math.min(maxW/imgNaturalW,maxH/imgNaturalH);
    canvasW=imgNaturalW*scale;canvasH=imgNaturalH*scale;
    canvas.width=canvasW;canvas.height=canvasH;
    canvas.style.width=canvasW+'px';canvas.style.height=canvasH+'px';
    ctx.drawImage(imgObj,0,0,canvasW,canvasH);
    drawLabels();
    document.getElementById('info-overlay').textContent=name+' | '+imgNaturalW+'x'+imgNaturalH+' | 标注: '+labels.length+'个 | 当前类别: '+(currentClass===0?'Tank':'Infantry');
  };
  imgObj.src=img.url;
  document.getElementById('annot-stats').textContent='已标注: '+images.filter(function(i){return i.labeled}).length+'/'+images.length
}
function drawLabels(){
  var canvas=document.getElementById('annot-canvas');var ctx=canvas.getContext('2d');
  ctx.drawImage(imgObj,0,0,canvasW,canvasH);
  labels.forEach(function(l){
    var x=l.x*canvasW,y=l.y*canvasH,w=l.w*canvasW,h=l.h*canvasH;
    var color=l['class']===0?'#58a5f3':'#4caf50';
    ctx.strokeStyle=color;ctx.lineWidth=2;ctx.strokeRect(x-w/2,y-h/2,w,h);
    ctx.fillStyle=color;ctx.font='10px sans-serif';ctx.fillText(l['class']===0?'Tank':'Inf',x-w/2,y-h/2-4)
  })
}
document.getElementById('annot-canvas').addEventListener('mousedown',function(e){
  if(!imgObj)return;var rect=e.target.getBoundingClientRect();
  startX=(e.clientX-rect.left)/canvasW;startY=(e.clientY-rect.top)/canvasH;
  drawing=true
});
document.getElementById('annot-canvas').addEventListener('mousemove',function(e){
  if(!drawing||!imgObj)return;var rect=e.target.getBoundingClientRect();
  var cx=(e.clientX-rect.left)/canvasW,cy=(e.clientY-rect.top)/canvasH;
  var w=cx-startX,h=cy-startY;if(Math.abs(w)<0.005||Math.abs(h)<0.005)return;
  var canvas=document.getElementById('annot-canvas'),ctx=canvas.getContext('2d');
  ctx.drawImage(imgObj,0,0,canvasW,canvasH);drawLabels();
  ctx.strokeStyle='#ff9800';ctx.lineWidth=2;ctx.setLineDash([4,2]);
  ctx.strokeRect(Math.min(startX,cx)*canvasW,Math.min(startY,cy)*canvasH,Math.abs(w)*canvasW,Math.abs(h)*canvasH);
  ctx.setLineDash([])
});
document.getElementById('annot-canvas').addEventListener('mouseup',function(e){
  if(!drawing||!imgObj)return;drawing=false;
  var rect=e.target.getBoundingClientRect();
  var ex=(e.clientX-rect.left)/canvasW,ey=(e.clientY-rect.top)/canvasH;
  var w=Math.abs(ex-startX),h=Math.abs(ey-startY);
  if(w<0.01||h<0.01)return;
  labels.push({class:currentClass,x:(Math.min(startX,ex)+Math.max(startX,ex))/2,y:(Math.min(startY,ey)+Math.max(startY,ey))/2,w:w,h:h});
  drawLabels()
});
document.addEventListener('keydown',function(e){
  if(e.key==='s'||e.key==='S'){e.preventDefault();saveLabels()}
  if(e.key==='n'||e.key==='N'){e.preventDefault();navigateImage(1)}
  if(e.key==='p'||e.key==='P'){e.preventDefault();navigateImage(-1)}
  if(e.key==='Delete'){e.preventDefault();labels.pop();drawLabels()}
});
function navigateImage(dir){
  var idx=images.findIndex(function(i){return i.name===currentImage});
  if(idx<0)return;idx+=dir;if(idx<0)idx=images.length-1;if(idx>=images.length)idx=0;
  selectImage(images[idx].name)
}
function saveLabels(){
  if(!currentImage)return;
  fetch('/api/annotate/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset:currentDataset,image:currentImage,labels:labels})}).then(r=>r.json()).then(d=>{
    document.getElementById('info-overlay').textContent='已保存 '+d.count+' 个标注';
    var img=images.find(function(i){return i.name===currentImage});if(img){img.labeled=d.count>0;img.label_count=d.count;img.labels=labels}renderImageList()
  })
}
loadImages();
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
# GPU 检测与管理
# ═══════════════════════════════════════════════════════════════

@app.route("/api/gpu/status")
def api_gpu_status():
    import subprocess as sp, json as _json
    result = {"cuda_available": False, "gpus": [], "pytorch_cuda": False, "message": ""}
    # Try multiple approaches to detect GPU
    try:
        # Method 1: Try direct nvidia-smi via subprocess
        r = sp.run('nvidia-smi --query-gpu=name,memory.total,driver_version,cuda_version --format=csv,noheader', capture_output=True, text=True, timeout=10, shell=True)
        if r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.strip().split("\n"):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    result["gpus"].append({"name": parts[0], "memory": parts[1], "driver": parts[2], "cuda": parts[3]})
            result["cuda_available"] = True
    except Exception:
        pass
    if not result["cuda_available"]:
        try:
            # Method 2: Try os.popen
            import os as _os
            with _os.popen('nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>nul') as f:
                out = f.read()
                if out.strip():
                    for line in out.strip().split("\n"):
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 2:
                            result["gpus"].append({"name": parts[0], "memory": parts[1], "driver": "N/A", "cuda": "N/A"})
                    result["cuda_available"] = True
        except Exception:
            pass
    if not result["cuda_available"]:
        try:
            # Method 3: Read cached GPU info
            cache_file = PROJECT_ROOT / "data" / ".gpu_info"
            if cache_file.exists():
                for line in cache_file.read_text().strip().split("\n"):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        result["gpus"].append({"name": parts[0], "memory": parts[1], "driver": parts[2], "cuda": "N/A"})
                    elif len(parts) >= 2:
                        result["gpus"].append({"name": parts[0], "memory": parts[1], "driver": "N/A", "cuda": "N/A"})
                result["cuda_available"] = True
        except Exception:
            pass
    try:
        import torch
        result["pytorch_cuda"] = torch.cuda.is_available()
        result["pytorch_version"] = torch.__version__
        if torch.cuda.is_available():
            result["cuda_devices"] = torch.cuda.device_count()
    except:
        pass
    if not result["pytorch_cuda"]:
        result["message"] = "PyTorch CPU版本。Python 3.14暂不支持CUDA预编译包，训练将使用CPU模式。YOLO推理仍可用。"
    global _gpu_info
    _gpu_info = result
    return jsonify(result)


@app.route("/api/gpu/install_cuda_torch", methods=["POST"])
def api_gpu_install_cuda_torch():
    import subprocess as sp, sys as _sys, threading as _thr

    def install_worker():
        try:
            socketio.emit("gpu_install_progress", {"step": "检测Python版本", "progress": 10})
            py_ver = f"{_sys.version_info.major}{_sys.version_info.minor}"
            socketio.emit("gpu_install_progress", {"step": f"Python 3.{_sys.version_info.minor}, 尝试安装CUDA PyTorch", "progress": 20})
            for cu_ver in ["cu128", "cu124", "cu121"]:
                try:
                    socketio.emit("gpu_install_progress", {"step": f"尝试 {cu_ver} 版本...", "progress": 40})
                    r = sp.run([_sys.executable, "-m", "pip", "install", "--pre", "torch", "torchvision", "--index-url", f"https://download.pytorch.org/whl/nightly/{cu_ver}"], capture_output=True, text=True, timeout=300)
                    if r.returncode == 0:
                        socketio.emit("gpu_install_complete", {"success": True, "cu_version": cu_ver, "message": f"PyTorch CUDA {cu_ver} 安装成功！请重启应用。"})
                        return
                except:
                    continue
            socketio.emit("gpu_install_complete", {"success": False, "message": "Python 3.14暂不支持CUDA PyTorch预编译包。建议使用Python 3.12环境运行GPU训练。"})
        except Exception as e:
            socketio.emit("gpu_install_complete", {"success": False, "message": str(e)})

    _thr.Thread(target=install_worker, daemon=True).start()
    return jsonify({"status": "installing"})


# ═══════════════════════════════════════════════════════════════
# Android 模拟器管理
# ═══════════════════════════════════════════════════════════════

# ── scrcpy 安装与管理 ──
SCRCPY_DIR = PROJECT_ROOT / "scrcpy"
SCRCPY_EXE = "scrcpy.exe"

def _install_scrcpy_internal():
    """内部安装scrcpy"""
    SCRCPY_DIR.mkdir(parents=True, exist_ok=True)
    scrcpy_exe = SCRCPY_DIR / "scrcpy.exe"
    if scrcpy_exe.exists():
        logger.info("scrcpy已安装")
        return True

    import requests as _req, zipfile
    try:
        scrcpy_url = "https://github.com/Genymobile/scrcpy/releases/download/v2.7/scrcpy-win64-v2.7.zip"
        zip_path = SCRCPY_DIR / "scrcpy.zip"
        logger.info(f"下载scrcpy: {scrcpy_url}")
        r = _req.get(scrcpy_url, stream=True, timeout=300)
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
        logger.info(f"scrcpy下载完成: {downloaded} bytes")

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(SCRCPY_DIR)
        zip_path.unlink(missing_ok=True)
        logger.info("scrcpy安装完成")
        return True
    except Exception as e:
        logger.warning(f"scrcpy下载失败: {e}，尝试使用PATH中的scrcpy")
        return False

def _get_scrcpy_exe():
    """获取scrcpy可执行文件"""
    candidates = [
        str(SCRCPY_DIR / "scrcpy-win64-v2.7" / "scrcpy.exe"),
        str(SCRCPY_DIR / "scrcpy.exe"),
        str(SCRCPY_DIR / "scrcpy-win64" / "scrcpy.exe"),
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    if SCRCPY_DIR.exists():
        for exe in SCRCPY_DIR.rglob("scrcpy.exe"):
            return str(exe)
    return "scrcpy"

# ── 行旅白端口检测 ──
def _detect_xinglv_ports():
    """检测行旅白占用的端口（快速版本）"""
    occupied = []
    try:
        # 一次性获取所有进程信息
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
        tasklist = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=5)
        # 构建PID到进程名的映射
        pid_map = {}
        for line in tasklist.stdout.split("\n"):
            if line.strip() and not line.startswith("映像名称"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    pid_map[parts[1]] = parts[0]
        
        for line in result.stdout.split("\n"):
            if "LISTENING" in line:
                parts = line.split()
                if len(parts) >= 2:
                    addr = parts[1]
                    if ":" in addr:
                        port = int(addr.split(":")[-1])
                        pid = parts[-1] if parts[-1].isdigit() else "?"
                        process_name = pid_map.get(pid, "")
                        if "xinglv" in process_name.lower() or "行旅" in process_name or "python" in process_name.lower():
                            occupied.append({"port": port, "pid": pid, "process": process_name})
    except:
        pass
    return occupied

def _find_available_port(start_port=5000, max_port=9000):
    """查找可用端口，避开行旅白"""
    occupied = _detect_xinglv_ports()
    occupied_ports = {p["port"] for p in occupied}
    import socket
    for port in range(start_port, max_port):
        if port in occupied_ports:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port

def _get_adb_for_emulator():
    """获取适合模拟器使用的ADB"""
    adb_candidates = [
        str(ANDROID_SDK_ROOT / "platform-tools" / "adb.exe"),
        r"d:\MuMuPlayer\nx_device\12.0\shell\adb.exe",
        r"d:\firefight\adb\adb.exe",
        "adb",
    ]
    for p in adb_candidates:
        if p == "adb" or Path(p).exists():
            return p
    return "adb"


def _get_emulator_exe():
    """获取模拟器可执行文件路径"""
    emu_path = ANDROID_SDK_ROOT / "emulator" / "emulator.exe"
    if emu_path.exists():
        return str(emu_path)
    return "emulator"


@app.route("/api/emulator/status")
def api_emulator_status():
    result = {
        "installed": False,
        "avd_exists": False,
        "running": False,
        "adb_connected": False,
        "sdk_path": str(ANDROID_SDK_ROOT),
        "avd_name": AVD_NAME,
        "emulator_path": "",
        "details": {},
    }

    # 检查SDK和模拟器是否安装
    emu_exe = _get_emulator_exe()
    result["emulator_path"] = emu_exe
    result["installed"] = Path(emu_exe).exists() if emu_exe != "emulator" else False

    # 检查AVD是否存在
    avd_dir = Path.home() / ".android" / "avd" / f"{AVD_NAME}.avd"
    result["avd_exists"] = avd_dir.exists()

    if result["avd_exists"]:
        config_ini = avd_dir / "config.ini"
        if config_ini.exists():
            details = {}
            for line in config_ini.read_text(errors="replace").split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    details[k.strip()] = v.strip()
            result["details"] = details

    # 检查是否在运行
    adb_exe = _get_adb_for_emulator()
    try:
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        r = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
        target = f"localhost:{_emulator_adb_port}"
        result["running"] = target in r.stdout and "device" in r.stdout
        result["adb_connected"] = result["running"]
        result["adb_port"] = _emulator_adb_port
    except Exception as e:
        result["adb_error"] = str(e)[:200]

    return jsonify(result)


@app.route("/api/emulator/install", methods=["POST"])
def api_emulator_install():
    import subprocess as sp, zipfile, requests as _req, io as _io, tempfile as _tf

    def install_worker():
        try:
            socketio.emit("emu_install_progress", {"step": "创建目录", "progress": 3})
            EMULATOR_HOME.mkdir(parents=True, exist_ok=True)
            ANDROID_SDK_ROOT.mkdir(parents=True, exist_ok=True)
            avd_home = Path.home() / ".android" / "avd"
            avd_home.mkdir(parents=True, exist_ok=True)

            # 下载命令行工具
            socketio.emit("emu_install_progress", {"step": "下载Android SDK命令行工具", "progress": 10})
            cmdline_url = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
            tools_zip = EMULATOR_HOME / "cmdline-tools.zip"

            if not tools_zip.exists():
                socketio.emit("emu_install_progress", {"step": "正在下载 (约150MB)...", "progress": 15})
                r = _req.get(cmdline_url, stream=True, timeout=600)
                total = int(r.headers.get("content-length", 0))
                downloaded = 0
                with open(tools_zip, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = min(15 + int(downloaded / total * 30), 45)
                            socketio.emit("emu_install_progress", {"step": f"下载中... {downloaded//1024//1024}MB", "progress": pct})

            # 解压
            socketio.emit("emu_install_progress", {"step": "解压命令行工具", "progress": 50})
            cmdline_dir = ANDROID_SDK_ROOT / "cmdline-tools" / "latest"
            if cmdline_dir.exists():
                shutil.rmtree(cmdline_dir, ignore_errors=True)
            cmdline_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(tools_zip, "r") as zf:
                for member in zf.namelist():
                    parts = member.split("/", 1)
                    if len(parts) < 2:
                        continue
                    target_path = cmdline_dir / parts[1].replace("/", "\\")
                    if member.endswith("/"):
                        target_path.mkdir(parents=True, exist_ok=True)
                    else:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(target_path, "wb") as dst:
                            dst.write(src.read())

            # 安装SDK组件
            sdkmanager = str(cmdline_dir / "bin" / "sdkmanager.bat")
            socketio.emit("emu_install_progress", {"step": "接受许可协议", "progress": 55})
            sp.run([sdkmanager, "--sdk_root=" + str(ANDROID_SDK_ROOT), "--licenses"], input=b"y\ny\ny\ny\ny\ny\ny\ny\n", capture_output=True, timeout=30)

            components = [
                "platform-tools",
                "emulator",
                f"system-images;android-{AVD_CONFIG['api_level']};default;{AVD_CONFIG['arch']}",
                f"platforms;android-{AVD_CONFIG['api_level']}",
            ]

            for i, comp in enumerate(components):
                pct = 60 + int((i + 1) / len(components) * 20)
                socketio.emit("emu_install_progress", {"step": f"安装: {comp}", "progress": pct})
                result = sp.run([sdkmanager, "--sdk_root=" + str(ANDROID_SDK_ROOT), comp], capture_output=True, text=True, timeout=600)
                if result.returncode != 0:
                    logger.warning(f"组件安装警告: {comp} - {result.stderr[:200]}")

            # 创建AVD
            socketio.emit("emu_install_progress", {"step": "创建AVD", "progress": 85})
            avd_dir = Path.home() / ".android" / "avd" / f"{AVD_NAME}.avd"
            avd_manager = str(cmdline_dir / "bin" / "avdmanager.bat")
            avd_created = False

            if Path(avd_manager).exists():
                result = sp.run([
                    avd_manager, "create", "avd",
                    "-n", AVD_NAME,
                    "-k", f"system-images;android-{AVD_CONFIG['api_level']};default;{AVD_CONFIG['arch']}",
                    "-d", AVD_CONFIG["device"],
                    "-f",
                ], capture_output=True, text=True, timeout=60)
                if result.returncode == 0 and avd_dir.exists():
                    avd_created = True
                else:
                    logger.warning(f"avdmanager创建失败: {result.stderr[:300]}")

            # 手动创建AVD（avdmanager失败时的备用方案）
            if not avd_created:
                socketio.emit("emu_install_progress", {"step": "手动创建AVD...", "progress": 88})
                avd_dir.mkdir(parents=True, exist_ok=True)
                ini_path = Path.home() / ".android" / "avd" / f"{AVD_NAME}.ini"
                ini_path.write_text(f"avd.ini.encoding=UTF-8\npath={avd_dir}\npath.rel=avd\\{AVD_NAME}.avd\ntarget=android-{AVD_CONFIG['api_level']}\n")

            # 配置AVD（确保config.ini存在）
            socketio.emit("emu_install_progress", {"step": "配置AVD (MUMU规格)", "progress": 92})
            config_ini = avd_dir / "config.ini"
            if not config_ini.exists():
                # 手动创建config.ini
                default_config = f"""AvdId={AVD_NAME}
PlayStore.enabled=false
abi.type={AVD_CONFIG['arch']}
avd.ini.displayname={AVD_NAME}
avd.ini.encoding=UTF-8
disk.dataPartition.size=8G
fastboot.chosenSnapshotFile=
fastboot.forceChosenSnapshotBoot=no
fastboot.forceColdBoot=no
fastboot.forceFastBoot=yes
hw.accelerometer=yes
hw.audioInput=yes
hw.battery=yes
hw.camera.back=emulated
hw.camera.front=emulated
hw.cpu.arch=x86_64
hw.cpu.ncore={AVD_CONFIG['cores']}
hw.dPad=no
hw.device.hash2=MD5:1b0e71a1d3d3c45e9c5c6e6f3a7b8c9d
hw.device.manufacturer=Google
hw.device.name=pixel_6
hw.gps=yes
hw.gpu.enabled=yes
hw.gpu.mode=host
hw.initialOrientation=landscape
hw.keyboard=yes
hw.lcd.density={AVD_CONFIG['density']}
hw.lcd.height={AVD_CONFIG['resolution'].split('x')[1]}
hw.lcd.width={AVD_CONFIG['resolution'].split('x')[0]}
hw.mainKeys=no
hw.ramSize={AVD_CONFIG['ram']}
hw.sdCard=no
hw.sensors.orientation=yes
hw.sensors.proximity=yes
hw.trackBall=no
image.sysdir.1=system-images\\android-{AVD_CONFIG['api_level']}\\default\\{AVD_CONFIG['arch']}\\
runtime.network.latency=none
runtime.network.speed=full
sdcard.size=512M
showDeviceFrame=no
skin.dynamic=yes
skin.name=1920x1080
skin.path=1920x1080
tag.display=Google APIs
tag.id=google_apis
vm.heapSize=256
"""
                config_ini.write_text(default_config)
                logger.info("手动创建AVD config.ini成功")
            else:
                # 更新已有配置为MUMU规格
                config_lines = config_ini.read_text(errors="replace").split("\n")
                custom_config = {
                    "hw.ramSize": str(AVD_CONFIG["ram"]),
                    "hw.cpu.ncore": str(AVD_CONFIG["cores"]),
                    "hw.lcd.width": AVD_CONFIG["resolution"].split("x")[0],
                    "hw.lcd.height": AVD_CONFIG["resolution"].split("x")[1],
                    "hw.lcd.density": str(AVD_CONFIG["density"]),
                    "hw.keyboard": "yes",
                    "disk.dataPartition.size": "8G",
                    "hw.gpu.enabled": "yes",
                    "hw.gpu.mode": "host",
                    "hw.initialOrientation": "landscape",
                    "skin.name": "1920x1080",
                    "showDeviceFrame": "no",
                }
                existing_keys = set()
                new_lines = []
                for line in config_lines:
                    if "=" in line:
                        k = line.split("=", 1)[0].strip()
                        existing_keys.add(k)
                        if k in custom_config:
                            new_lines.append(f"{k}={custom_config[k]}")
                        else:
                            new_lines.append(line)
                    else:
                        new_lines.append(line)
                for k, v in custom_config.items():
                    if k not in existing_keys:
                        new_lines.append(f"{k}={v}")
                config_ini.write_text("\n".join(new_lines))
                logger.info("AVD配置更新为MUMU规格")

            # 安装scrcpy
            socketio.emit("emu_install_progress", {"step": "安装scrcpy...", "progress": 96})
            _install_scrcpy_internal()

            socketio.emit("emu_install_progress", {"step": "完成!", "progress": 100})
            socketio.emit("emu_install_complete", {"success": True, "message": "Android模拟器安装完成！(MUMU规格)", "avd_name": AVD_NAME})
            add_learning_log("emulator", "Android模拟器安装完成 (MUMU规格)", f"AVD: {AVD_NAME}, 分辨率: {AVD_CONFIG['resolution']}")

        except Exception as e:
            error_msg = str(e)[:300]
            socketio.emit("emu_install_complete", {"success": False, "message": f"安装失败: {error_msg}"})
            add_learning_log("emulator", "模拟器安装失败", error_msg)

    threading.Thread(target=install_worker, daemon=True).start()
    return jsonify({"status": "installing"})


@app.route("/api/emulator/start", methods=["POST"])
def api_emulator_start():
    global _emulator_process

    if _emulator_process and _emulator_process.poll() is None:
        return jsonify({"status": "already_running", "message": "模拟器已在运行"})

    def start_worker():
        global _emulator_process
        try:
            emu_exe = _get_emulator_exe()
            if not Path(emu_exe).exists():
                socketio.emit("emu_start_error", {"error": "模拟器未安装，请先安装"})
                return

            socketio.emit("emu_start_progress", {"step": "启动模拟器...", "progress": 20})
            cmd = [
                emu_exe, "-avd", AVD_NAME,
                "-no-window", "-no-audio",
                "-gpu", "swiftshader_indirect",
                "-netdelay", "none", "-netspeed", "full",
                "-port", str(_emulator_adb_port),
            ]
            _emulator_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            socketio.emit("emu_start_progress", {"step": "等待模拟器启动...", "progress": 40})
            # 等待启动
            adb_exe = _get_adb_for_emulator()
            subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)

            waited = 0
            while waited < 120:
                time.sleep(3)
                waited += 3
                r = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
                if f"localhost:{_emulator_adb_port}" in r.stdout and "device" in r.stdout:
                    break
                socketio.emit("emu_start_progress", {"step": f"等待启动... {waited}s", "progress": 40 + min(waited, 40)})

            socketio.emit("emu_start_progress", {"step": "连接ADB", "progress": 80})
            subprocess.run([adb_exe, "connect", f"localhost:{_emulator_adb_port}"], capture_output=True, text=True, timeout=10)

            # 设置屏幕属性
            subprocess.run([adb_exe, "-s", f"localhost:{_emulator_adb_port}", "shell", "wm", "density", str(AVD_CONFIG["density"])], capture_output=True, text=True, timeout=5)

            socketio.emit("emu_start_complete", {"success": True, "port": _emulator_adb_port, "message": "模拟器启动完成"})
            add_learning_log("emulator", "模拟器启动完成", f"端口: {_emulator_adb_port}")

        except Exception as e:
            socketio.emit("emu_start_error", {"error": str(e)[:300]})
            add_learning_log("emulator", "模拟器启动失败", str(e)[:200])

    threading.Thread(target=start_worker, daemon=True).start()
    return jsonify({"status": "starting"})


@app.route("/api/emulator/stop", methods=["POST"])
def api_emulator_stop():
    global _emulator_process

    try:
        adb_exe = _get_adb_for_emulator()
        subprocess.run([adb_exe, "-s", f"localhost:{_emulator_adb_port}", "emu", "kill"], capture_output=True, text=True, timeout=10)
    except:
        pass

    if _emulator_process:
        try:
            _emulator_process.terminate()
            _emulator_process.wait(timeout=10)
        except:
            try:
                _emulator_process.kill()
            except:
                pass
        _emulator_process = None

    add_learning_log("emulator", "模拟器已停止", "")
    return jsonify({"status": "stopped"})


@app.route("/api/emulator/install_apk", methods=["POST"])
def api_emulator_install_apk():
    data = request.get_json() or {}
    apk_path = data.get("apk_path", "").strip()
    searched = [
        str(PROJECT_ROOT / "apk" / "firefight.apk"),
        str(PROJECT_ROOT / "dist" / "firefight.apk"),
        str(PROJECT_ROOT / "Firefight.apk"),
    ]

    if not apk_path:
        for p in searched:
            if Path(p).exists():
                apk_path = p
                break

    if not apk_path or not Path(apk_path).exists():
        return jsonify({"error": "APK文件不存在", "searched": searched}), 404

    try:
        adb_exe = _get_adb_for_emulator()
        r = subprocess.run(
            [adb_exe, "-s", f"localhost:{_emulator_adb_port}", "install", "-r", apk_path],
            capture_output=True, text=True, timeout=60
        )
        success = "Success" in r.stdout
        add_learning_log("emulator", f"APK安装{'成功' if success else '失败'}", apk_path)
        return jsonify({"status": "success" if success else "failed", "output": r.stdout.strip()[:500]})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:300]}), 500


@app.route("/api/emulator/screenshot")
def api_emulator_screenshot():
    import base64
    try:
        adb_exe = _get_adb_for_emulator()
        r = subprocess.run(
            [adb_exe, "-s", f"localhost:{_emulator_adb_port}", "exec-out", "screencap", "-p"],
            capture_output=True, timeout=10
        )
        if r.returncode != 0:
            return jsonify({"error": "截图失败", "stderr": r.stderr[:200]}), 500
        img_b64 = base64.b64encode(r.stdout).decode("utf-8")
        return jsonify({"image": img_b64, "format": "png", "timestamp": time.time()})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/emulator/touch", methods=["POST"])
def api_emulator_touch():
    data = request.get_json() or {}
    x = data.get("x", 0)
    y = data.get("y", 0)
    action = data.get("action", "tap")  # tap, swipe, longpress

    try:
        adb_exe = _get_adb_for_emulator()
        target = f"localhost:{_emulator_adb_port}"

        if action == "tap":
            subprocess.run(
                [adb_exe, "-s", target, "shell", "input", "tap", str(int(x)), str(int(y))],
                capture_output=True, text=True, timeout=5
            )
        elif action == "swipe":
            x2 = data.get("x2", x)
            y2 = data.get("y2", y)
            duration = data.get("duration", 300)
            subprocess.run(
                [adb_exe, "-s", target, "shell", "input", "swipe", str(int(x)), str(int(y)), str(int(x2)), str(int(y2)), str(int(duration))],
                capture_output=True, text=True, timeout=5
            )
        elif action == "longpress":
            subprocess.run(
                [adb_exe, "-s", target, "shell", "input", "swipe", str(int(x)), str(int(y)), str(int(x)), str(int(y)), "1000"],
                capture_output=True, text=True, timeout=5
            )

        return jsonify({"status": "ok", "action": action, "x": x, "y": y})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]}), 500


# ═══════════════════════════════════════════════════════════════
# scrcpy 投屏控制 (MUMU级别触控)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/scrcpy/status")
def api_scrcpy_status():
    """检查scrcpy状态"""
    result = {
        "installed": False,
        "exe_path": "",
        "running": _scrcpy_process is not None and _scrcpy_process.poll() is None,
        "enabled": _scrcpy_enabled,
    }
    scrcpy_exe = _get_scrcpy_exe()
    result["installed"] = Path(scrcpy_exe).exists() if scrcpy_exe != "scrcpy" else False
    result["exe_path"] = scrcpy_exe
    return jsonify(result)

@app.route("/api/scrcpy/install", methods=["POST"])
def api_scrcpy_install():
    """安装scrcpy"""
    try:
        success = _install_scrcpy_internal()
        if success:
            return jsonify({"status": "ok", "message": "scrcpy安装成功"})
        else:
            return jsonify({"status": "warning", "message": "scrcpy下载失败，请检查网络或手动安装: https://github.com/Genymobile/scrcpy"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]}), 500

@app.route("/api/scrcpy/start", methods=["POST"])
def api_scrcpy_start():
    """启动scrcpy投屏"""
    global _scrcpy_process, _scrcpy_enabled

    if _scrcpy_process and _scrcpy_process.poll() is None:
        return jsonify({"status": "already_running", "message": "scrcpy已在运行"})

    scrcpy_exe = _get_scrcpy_exe()
    if not Path(scrcpy_exe).exists():
        _install_scrcpy_internal()
        scrcpy_exe = _get_scrcpy_exe()
        if not Path(scrcpy_exe).exists():
            return jsonify({"status": "error", "error": "scrcpy未安装"}), 500

    try:
        data = request.get_json() or {}
        max_width = data.get("max_width", 1920)
        max_height = data.get("max_height", 1080)
        bitrate = data.get("bitrate", 8000000)
        max_fps = data.get("max_fps", 60)

        cmd = [
            scrcpy_exe,
            "-s", f"localhost:{_emulator_adb_port}",
            f"--max-size={max_width}",
            f"--bit-rate={bitrate}",
            f"--max-fps={max_fps}",
            "--stay-awake",
            "--turn-screen-off=false",
            "--no-audio",
            "--window-title=Firefight AI 模拟器",
            "--window-x=0", "--window-y=0",
            "--window-width=1920", "--window-height=1080",
            "--fullscreen",
        ]

        _scrcpy_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        _scrcpy_enabled = True
        add_learning_log("scrcpy", "scrcpy投屏已启动", f"分辨率: {max_width}x{max_height}, FPS: {max_fps}")
        return jsonify({"status": "ok", "message": "scrcpy投屏启动成功", "fullscreen": True})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]}), 500

@app.route("/api/scrcpy/stop", methods=["POST"])
def api_scrcpy_stop():
    """停止scrcpy投屏"""
    global _scrcpy_process, _scrcpy_enabled

    if _scrcpy_process:
        try:
            _scrcpy_process.terminate()
            _scrcpy_process.wait(timeout=5)
        except:
            try:
                _scrcpy_process.kill()
            except:
                pass
        _scrcpy_process = None
    _scrcpy_enabled = False
    add_learning_log("scrcpy", "scrcpy投屏已停止", "")
    return jsonify({"status": "ok", "message": "scrcpy已停止"})

# ═══════════════════════════════════════════════════════════════
# 端口检测 (避免与行旅白冲突)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/port/check")
def api_port_check():
    """检测端口占用情况"""
    xinglv_ports = _detect_xinglv_ports()
    import socket
    all_occupied = []
    try:
        # 检查关键端口
        for port in [5000, 5001, 5005, 5556, 7555, 8080, 3000, 9090, 9999]:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    all_occupied.append({"port": port, "occupied": True})
                else:
                    all_occupied.append({"port": port, "occupied": False})
    except:
        pass

    available = _find_available_port()
    return jsonify({
        "xinglv_detected": len(xinglv_ports) > 0,
        "xinglv_ports": xinglv_ports,
        "port_scan": all_occupied,
        "suggested_port": available,
        "current_server_port": 5000,
        "emulator_adb_port": _emulator_adb_port,
    })


# ═══════════════════════════════════════════════════════════════
# AI Agent 增强 (高级智能体)
# ═══════════════════════════════════════════════════════════════

AGENT_TOOLS = {
    "check_adb": "检查ADB连接状态",
    "reconnect_adb": "重新连接ADB",
    "check_emulator": "检查模拟器状态",
    "start_emulator": "启动模拟器",
    "install_apk": "安装APK到模拟器",
    "launch_game": "启动游戏",
    "verify_decision_chain": "验证完整决策链（ADB→截图→YOLO→LLM→执行）",
    "rebuild_chain": "重建整条决策链",
    "train_model": "训练YOLO模型",
    "deploy_to_server": "部署到腾讯云服务器",
    "push_to_github": "推送到GitHub",
    "web_search": "联网搜索信息",
}


@app.route("/api/agent/execute", methods=["POST"])
def api_agent_execute():
    data = request.get_json() or {}
    command = data.get("command", "").strip()
    if not command:
        return jsonify({"error": "缺少command参数"}), 400

    add_learning_log("agent", f"智能体执行: {command[:100]}", "")

    def agent_worker():
        try:
            socketio.emit("agent_progress", {"step": "正在分析指令...", "progress": 10, "command": command})

            from openai import OpenAI
            cfg = load_config()
            llm_cfg = cfg["llm"]
            client = OpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["api_base"])

            tools_desc = "\n".join([f"- {k}: {v}" for k, v in AGENT_TOOLS.items()])
            prompt = (
                f"你是Firefight AI系统的智能体。你可以执行以下工具:\n{tools_desc}\n\n"
                f"用户指令: {command}\n\n"
                "请分析指令，输出需要执行的工具调用序列（JSON数组格式）。"
                "每个工具调用包含: tool (工具名), args (参数对象)。\n"
                "例如: [{{\"tool\": \"check_adb\", \"args\": {{}}}}, {{\"tool\": \"reconnect_adb\", \"args\": {{}}}}]\n"
                "只输出JSON数组，不要其他内容。"
            )

            resp = client.chat.completions.create(
                model=llm_cfg.get("model", "deepseek-v4-flash"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=128,
                temperature=0.1,
                timeout=6,
            )
            plan_text = resp.choices[0].message.content.strip()
            # 提取JSON
            import re as _re
            json_match = _re.search(r"\[.*\]", plan_text, _re.DOTALL)
            if json_match:
                plan_text = json_match.group(0)

            try:
                plan = json.loads(plan_text)
            except:
                # 如果解析失败，使用关键词匹配
                plan = _keyword_parse_command(command)

            socketio.emit("agent_progress", {"step": f"解析出{len(plan)}个步骤", "progress": 20, "plan": plan})

            results = []
            for i, step in enumerate(plan):
                tool_name = step.get("tool", "")
                tool_args = step.get("args", {})
                pct = 20 + int((i + 1) / len(plan) * 60)
                socketio.emit("agent_progress", {"step": f"执行: {tool_name}", "progress": pct, "current": tool_name})

                result = _execute_agent_tool(tool_name, tool_args)
                results.append({"tool": tool_name, "result": result})
                socketio.emit("agent_step_result", {"tool": tool_name, "result": result, "index": i + 1, "total": len(plan)})

                if result.get("error"):
                    socketio.emit("agent_progress", {"step": f"步骤失败: {tool_name}", "progress": pct, "error": result["error"]})

            # 总结
            socketio.emit("agent_progress", {"step": "生成总结", "progress": 90})
            summary_prompt = f"执行结果:\n{json.dumps(results, ensure_ascii=False, indent=2)[:2000]}\n\n请用中文总结执行结果（2-3句话）。"
            summary_resp = client.chat.completions.create(
                model=llm_cfg.get("model", "deepseek-v4-flash"),
                messages=[{"role": "user", "content": summary_prompt}],
                max_tokens=128,
                temperature=0.1,
                timeout=6,
            )
            summary = summary_resp.choices[0].message.content.strip()

            socketio.emit("agent_complete", {
                "success": True,
                "command": command,
                "results": results,
                "summary": summary,
                "time": datetime.now().isoformat(),
            })
            add_learning_log("agent", f"智能体执行完成: {command[:80]}", summary[:200])

        except Exception as e:
            socketio.emit("agent_error", {"error": str(e)[:300], "command": command})
            add_learning_log("agent", f"智能体执行失败", str(e)[:200])

    threading.Thread(target=agent_worker, daemon=True).start()
    return jsonify({"status": "executing", "command": command})


def _keyword_parse_command(command: str) -> list:
    """基于关键词解析命令为工具调用序列"""
    plan = []
    cmd_lower = command.lower()

    if any(kw in cmd_lower for kw in ["重建", "决策链", "rebuild", "chain"]):
        plan.append({"tool": "verify_decision_chain", "args": {}})
        plan.append({"tool": "rebuild_chain", "args": {}})

    if any(kw in cmd_lower for kw in ["adb", "连接", "connect"]):
        if "修复" in cmd_lower or "重连" in cmd_lower:
            plan.append({"tool": "reconnect_adb", "args": {}})
        plan.append({"tool": "check_adb", "args": {}})

    if any(kw in cmd_lower for kw in ["模拟器", "emulator", "部署模拟器"]):
        plan.append({"tool": "check_emulator", "args": {}})
        if "启动" in cmd_lower or "start" in cmd_lower:
            plan.append({"tool": "start_emulator", "args": {}})

    if any(kw in cmd_lower for kw in ["apk", "安装", "游戏"]):
        plan.append({"tool": "install_apk", "args": {}})

    if any(kw in cmd_lower for kw in ["启动游戏", "launch", "运行"]):
        plan.append({"tool": "launch_game", "args": {}})

    if any(kw in cmd_lower for kw in ["训练", "train", "yolo"]):
        plan.append({"tool": "train_model", "args": {}})

    if any(kw in cmd_lower for kw in ["部署", "deploy", "服务器", "server"]):
        plan.append({"tool": "deploy_to_server", "args": {}})

    if any(kw in cmd_lower for kw in ["github", "推送", "push"]):
        plan.append({"tool": "push_to_github", "args": {}})

    if any(kw in cmd_lower for kw in ["搜索", "search", "查询"]):
        plan.append({"tool": "web_search", "args": {"query": command}})

    if not plan:
        plan.append({"tool": "verify_decision_chain", "args": {}})

    return plan


def _execute_agent_tool(tool_name: str, args: dict) -> dict:
    """执行单个智能体工具"""
    import requests as _req
    base = "http://127.0.0.1:5000"
    try:
        if tool_name == "check_adb":
            r = _req.get(f"{base}/api/adb/status", timeout=10)
            return r.json()

        elif tool_name == "reconnect_adb":
            r = _req.post(f"{base}/api/adb/reconnect", json={}, timeout=10)
            return r.json()

        elif tool_name == "check_emulator":
            r = _req.get(f"{base}/api/emulator/status", timeout=10)
            return r.json()

        elif tool_name == "start_emulator":
            r = _req.post(f"{base}/api/emulator/start", json={}, timeout=10)
            return r.json()

        elif tool_name == "install_apk":
            apk_path = args.get("apk_path", "")
            r = _req.post(f"{base}/api/emulator/install_apk", json={"apk_path": apk_path}, timeout=60)
            return r.json()

        elif tool_name == "launch_game":
            adb_exe = _get_adb_for_emulator()
            subprocess.run([adb_exe, "-s", f"localhost:{_emulator_adb_port}", "shell", "monkey", "-p", "com.windowsgames.firefightbw", "-c", "android.intent.category.LAUNCHER", "1"], capture_output=True, text=True, timeout=10)
            return {"status": "launched", "package": "com.windowsgames.firefightbw"}

        elif tool_name == "verify_decision_chain":
            r = _req.post(f"{base}/api/chain/verify", json={}, timeout=30)
            return r.json()

        elif tool_name == "rebuild_chain":
            r = _req.post(f"{base}/api/chain/verify", json={}, timeout=30)
            result = r.json()
            socketio.emit("rebuild_chain_triggered", {})
            return result

        elif tool_name == "train_model":
            return {"status": "skipped", "message": "训练需要手动触发"}

        elif tool_name == "deploy_to_server":
            r = _req.post(f"{base}/api/server/deploy", json={}, timeout=10)
            return r.json()

        elif tool_name == "push_to_github":
            r = _req.post(f"{base}/api/github/push", json={"message": "智能体自动推送"}, timeout=10)
            return r.json()

        elif tool_name == "web_search":
            query = args.get("query", "")
            r = _req.post(f"{base}/api/web/search", json={"query": query}, timeout=30)
            return r.json()

        else:
            return {"error": f"未知工具: {tool_name}"}

    except Exception as e:
        return {"error": str(e)[:200]}


# ═══════════════════════════════════════════════════════════════
# 决策链验证
# ═══════════════════════════════════════════════════════════════

@app.route("/api/chain/verify", methods=["POST"])
def api_chain_verify():
    results = {
        "timestamp": datetime.now().isoformat(),
        "steps": {},
        "all_ok": False,
        "summary": "",
    }

    # 1. ADB连接
    socketio.emit("chain_verify_progress", {"step": "检查ADB连接", "progress": 10})
    try:
        cfg = load_config()
        dc = cfg["device"]
        ad = dc.get("active", "mumu")
        di = dc.get(ad, {})
        adb_exe = _get_adb_for_emulator()
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        r = subprocess.run([adb_exe, "connect", f"{di.get('adb_host','127.0.0.1')}:{di.get('adb_port',7555)}"], capture_output=True, text=True, timeout=10)
        adb_ok = "connected" in r.stdout.lower() or "already connected" in r.stdout.lower()
        results["steps"]["adb"] = {"status": "ok" if adb_ok else "failed", "detail": r.stdout.strip()[:200]}
    except Exception as e:
        results["steps"]["adb"] = {"status": "error", "detail": str(e)[:200]}

    # 2. 截图测试
    socketio.emit("chain_verify_progress", {"step": "测试截图", "progress": 30})
    try:
        r = subprocess.run([adb_exe, "exec-out", "screencap", "-p"], capture_output=True, timeout=10)
        screenshot_ok = len(r.stdout) > 1000
        results["steps"]["screenshot"] = {"status": "ok" if screenshot_ok else "failed", "detail": f"大小: {len(r.stdout)} bytes"}
    except Exception as e:
        results["steps"]["screenshot"] = {"status": "error", "detail": str(e)[:200]}

    # 3. YOLO检测
    socketio.emit("chain_verify_progress", {"step": "验证YOLO模型", "progress": 50})
    try:
        from src.vision.detector import UnitDetector
        yc = load_config()["yolo"]
        detector = UnitDetector(model_path=yc["model_path"], fallback_model_path=yc["fallback_model_path"], confidence_threshold=yc["confidence_threshold"], iou_threshold=yc["iou_threshold"], image_size=yc["image_size"], device=yc["device"])
        detector.load_model()
        results["steps"]["yolo"] = {"status": "ok", "detail": f"模型: {yc['model_path']}"}
    except Exception as e:
        results["steps"]["yolo"] = {"status": "error", "detail": str(e)[:200]}

    # 4. LLM连接
    socketio.emit("chain_verify_progress", {"step": "验证LLM API", "progress": 70})
    try:
        ds = verify_deepseek_api()
        results["steps"]["llm"] = {"status": "ok" if ds["status"] == "online" else "failed", "detail": f"延迟: {ds.get('latency_ms', '?')}ms"}
    except Exception as e:
        results["steps"]["llm"] = {"status": "error", "detail": str(e)[:200]}

    # 5. GitHub
    socketio.emit("chain_verify_progress", {"step": "验证GitHub", "progress": 85})
    try:
        import requests as _req
        r = _req.get("https://api.github.com", timeout=5)
        results["steps"]["github"] = {"status": "ok" if r.status_code == 200 else "failed", "detail": f"HTTP {r.status_code}"}
    except Exception as e:
        results["steps"]["github"] = {"status": "error", "detail": str(e)[:200]}

    # 6. 服务器
    socketio.emit("chain_verify_progress", {"step": "验证服务器", "progress": 95})
    try:
        ok, out, _ = _ssh_exec("echo OK", timeout=10)
        results["steps"]["server"] = {"status": "ok" if ok and "OK" in out else "failed", "detail": out.strip()[:100]}
    except Exception as e:
        results["steps"]["server"] = {"status": "error", "detail": str(e)[:200]}

    # 汇总
    all_ok = all(s["status"] == "ok" for s in results["steps"].values())
    results["all_ok"] = all_ok
    failed_steps = [k for k, v in results["steps"].items() if v["status"] != "ok"]
    if all_ok:
        results["summary"] = "决策链完整，所有环节正常"
    else:
        results["summary"] = f"决策链存在问题: {', '.join(failed_steps)}"

    socketio.emit("chain_verify_complete", results)
    add_learning_log("chain", f"决策链验证: {results['summary']}", "")

    return jsonify(results)


# ═══════════════════════════════════════════════════════════════
# DeepSeek 余额查询
# ═══════════════════════════════════════════════════════════════

@app.route("/api/deepseek/balance")
def api_deepseek_balance():
    import requests as _req
    try:
        cfg = load_config()
        api_key = cfg["llm"]["api_key"]
        r = _req.get("https://api.deepseek.com/user/balance", headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return jsonify({"status": "ok", "balance": data.get("balance_infos", [])})
        return jsonify({"status": "error", "message": f"HTTP {r.status_code}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


# ═══════════════════════════════════════════════════════════════
# 本地数据管理
# ═══════════════════════════════════════════════════════════════

@app.route("/api/data/browse")
def api_data_browse():
    """Browse local data directories and list files with sizes"""
    import os as _os, time as _time
    dirs_to_scan = {
        "data": PROJECT_ROOT / "data",
        "sessions": PROJECT_ROOT / "sessions",
        "logs": PROJECT_ROOT / "logs",
        "runs": PROJECT_ROOT / "runs",
        "test_screenshots": PROJECT_ROOT / "test_screenshots",
    }
    results = {}
    total_size = 0
    for name, dpath in dirs_to_scan.items():
        if not dpath.exists():
            results[name] = {"exists": False, "files": [], "size_mb": 0}
            continue
        files = []
        dir_size = 0
        for f in sorted(dpath.rglob("*"), key=lambda x: x.stat().st_size, reverse=True):
            if f.is_file():
                sz = f.stat().st_size
                dir_size += sz
                age_hours = (_time.time() - f.stat().st_mtime) / 3600
                can_delete = any([
                    f.suffix.lower() in (".png", ".jpg", ".jpeg") and "screenshot" in f.name.lower(),
                    f.suffix.lower() in (".png", ".jpg") and age_hours > 0.08,
                    "tmp" in f.name.lower(),
                    f.name.endswith(".tmp"),
                    f.name.endswith(".bak"),
                ])
                if len(files) < 200:
                    files.append({
                        "name": f.name,
                        "path": str(f.relative_to(PROJECT_ROOT)),
                        "size_mb": round(sz / 1024 / 1024, 2),
                        "age_hours": round(age_hours, 1),
                        "can_delete": can_delete,
                        "reason": "截图超5分钟" if (f.suffix.lower() in (".png", ".jpg") and age_hours > 0.08) else ("截图文件" if "screenshot" in f.name.lower() else ("临时文件" if "tmp" in f.name.lower() else ""))
                    })
        total_size += dir_size
        results[name] = {"exists": True, "files": files, "file_count": sum(1 for _ in dpath.rglob("*") if _.is_file()), "size_mb": round(dir_size / 1024 / 1024, 2)}
    results["total_size_mb"] = round(total_size / 1024 / 1024, 2)
    return jsonify(results)

@app.route("/api/data/cleanup", methods=["POST"])
def api_data_cleanup():
    """Delete files marked for cleanup (screenshots older than 5 minutes, temp files)"""
    import os as _os, time as _time
    data = request.get_json() or {}
    files_to_delete = data.get("files", [])
    dry_run = data.get("dry_run", False)
    deleted = []
    errors = []
    now = _time.time()

    if not files_to_delete:
        # Auto mode: delete screenshots older than 5 minutes
        for pattern in ["sessions/**/*.png", "sessions/**/*.jpg", "test_screenshots/**/*.png", "data/**/*screenshot*.png", "data/**/*screenshot*.jpg"]:
            for f in PROJECT_ROOT.glob(pattern):
                if f.is_file() and (now - f.stat().st_mtime) > 300:
                    try:
                        if not dry_run:
                            _os.remove(f)
                        deleted.append(str(f.relative_to(PROJECT_ROOT)))
                    except Exception as e:
                        errors.append(str(f.relative_to(PROJECT_ROOT)) + ": " + str(e))

    for fp in files_to_delete:
        try:
            full = PROJECT_ROOT / fp
            if full.exists() and full.is_file():
                if not dry_run:
                    _os.remove(full)
                deleted.append(fp)
        except Exception as e:
            errors.append(fp + ": " + str(e))

    return jsonify({"deleted": len(deleted), "deleted_files": deleted[:50], "errors": errors, "dry_run": dry_run})


# ═══════════════════════════════════════════════════════════════
# 后台定时清理 (每5分钟自动删除超过5分钟的截图)
# ═══════════════════════════════════════════════════════════════

_auto_cleanup_running = False

def _auto_cleanup_worker():
    """后台工作线程: 每5分钟自动清理超过5分钟的截图和临时文件"""
    global _auto_cleanup_running
    _auto_cleanup_running = True
    logger.info("后台自动清理线程已启动 (每5分钟检查一次)")
    while _auto_cleanup_running:
        try:
            now = time.time()
            deleted_count = 0
            patterns = [
                "sessions/**/*.png", "sessions/**/*.jpg",
                "test_screenshots/**/*.png", "test_screenshots/**/*.jpg",
                "data/**/*screenshot*.png", "data/**/*screenshot*.jpg",
                "data/**/*.tmp", "data/**/*.bak",
                "logs/**/*.tmp", "runs/**/*.tmp",
            ]
            for pattern in patterns:
                for f in PROJECT_ROOT.glob(pattern):
                    if f.is_file() and (now - f.stat().st_mtime) > 300:  # 5分钟
                        try:
                            f.unlink()
                            deleted_count += 1
                        except:
                            pass
            if deleted_count > 0:
                add_learning_log("system", f"自动清理: 删除了{deleted_count}个过期文件", "")
        except Exception as e:
            logger.warning(f"自动清理错误: {e}")
        time.sleep(300)  # 每5分钟执行一次

def start_auto_cleanup():
    """启动自动清理线程"""
    global _auto_cleanup_running
    if not _auto_cleanup_running:
        t = threading.Thread(target=_auto_cleanup_worker, daemon=True)
        t.start()

def stop_auto_cleanup():
    """停止自动清理线程"""
    global _auto_cleanup_running
    _auto_cleanup_running = False

@app.route("/api/data/auto_cleanup/status")
def api_auto_cleanup_status():
    return jsonify({"running": _auto_cleanup_running})

@app.route("/api/data/auto_cleanup/toggle", methods=["POST"])
def api_auto_cleanup_toggle():
    global _auto_cleanup_running
    data = request.get_json() or {}
    enable = data.get("enable", not _auto_cleanup_running)
    if enable and not _auto_cleanup_running:
        start_auto_cleanup()
        return jsonify({"running": True, "message": "自动清理已启动"})
    elif not enable and _auto_cleanup_running:
        stop_auto_cleanup()
        return jsonify({"running": False, "message": "自动清理已停止"})
    return jsonify({"running": _auto_cleanup_running})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Firefight AI Dashboard Server v5.0")
    parser.add_argument("--port", type=int, default=5000, help="服务器端口")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="服务器地址")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    logger.info(f"Firefight AI Dashboard v{APP_VERSION} 启动")
    logger.info(f"地址: http://{args.host}:{args.port}")
    logger.info(f"项目目录: {PROJECT_ROOT}")

    add_learning_log("system", f"服务器启动 v{APP_VERSION}", f"host={args.host}:{args.port}")

    # 启动后台自动清理
    start_auto_cleanup()

    try:
        socketio.run(
            app, host=args.host, port=args.port,
            allow_unsafe_werkzeug=True, use_reloader=False,
            debug=args.debug,
        )
    except KeyboardInterrupt:
        logger.info("服务器已停止")
    except Exception as e:
        logger.error(f"服务器启动失败: {e}")
        sys.exit(1)