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

    # 尝试检测ADB
    adb_paths = [
        r"d:\firefight\adb\adb.exe",
        r"C:\adb\platform-tools\platform-tools\adb.exe",
        r"d:\MuMuPlayer\nx_device\12.0\shell\adb.exe",
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

    adb_paths = [r"d:\firefight\adb\adb.exe", r"C:\adb\platform-tools\platform-tools\adb.exe", r"d:\MuMuPlayer\nx_device\12.0\shell\adb.exe", "adb"]
    adb_exe = "adb"
    for p in adb_paths:
        if p == "adb" or Path(p).exists():
            adb_exe = p
            break

    add_learning_log("connection", f"尝试重连ADB: {host}:{port}", "")
    try:
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
        status = "online" if r.status_code == 200 else "error"
        update_state(github_status=status)
    except:
        status = "offline"
        update_state(github_status=status)

    # 尝试获取本地git信息
    try:
        import git
        repo = git.Repo(str(PROJECT_ROOT))
        remote = repo.remotes.origin.url if repo.remotes else "未配置"
        branch = repo.active_branch.name
        dirty = repo.is_dirty()
    except:
        remote = "未初始化"
        branch = "N/A"
        dirty = False

    return jsonify({
        "api_status": status,
        "repo_url": remote,
        "branch": branch,
        "has_changes": dirty,
    })

@app.route("/api/github/push", methods=["POST"])
def api_github_push():
    """推送训练数据/参数到GitHub"""
    data = request.get_json() or {}
    paths = data.get("paths", ["data/params", "data/tactics_rules.yaml", "data/battle_memory.db"])
    commit_msg = data.get("message", f"AI训练更新 {datetime.now().strftime('%Y%m%d-%H%M')}")

    add_learning_log("github", "开始推送数据到GitHub", f"文件: {', '.join(paths)}")

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
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=str(PROJECT_ROOT), check=True, capture_output=True)
            r = subprocess.run(["git", "push"], cwd=str(PROJECT_ROOT), check=True, capture_output=True, text=True)
            add_learning_log("github", f"Git推送成功: {commit_msg}", r.stdout[:200])
            return jsonify({"status": "pushed", "message": commit_msg})
        except Exception as e:
            add_learning_log("github", f"Git推送失败", str(e)[:200])
            return jsonify({"status": "error", "error": str(e)}), 500
    except Exception as e:
        add_learning_log("github", f"推送失败", str(e)[:200])
        return jsonify({"status": "error", "error": str(e)}), 500

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
SERVER_USER = "root"
SSH_KEY_PATH = r"C:\Users\19853\Downloads\firefightAI.pem"
SERVER_DEPLOY_PATH = "/opt/firefightAI"

@app.route("/api/server/status")
def api_server_status():
    """检查腾讯云服务器连接状态"""
    try:
        r = subprocess.run([
            "ssh", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5", f"{SERVER_USER}@{SERVER_HOST}",
            "echo OK && python3 --version 2>/dev/null && ls /opt/firefightAI 2>/dev/null || echo no_deploy"
        ], capture_output=True, text=True, timeout=10)
        if "OK" in r.stdout:
            update_state(server_status="online")
            deployed = "no_deploy" not in r.stdout
            return jsonify({"status": "online", "deployed": deployed, "output": r.stdout.strip()})
        else:
            update_state(server_status="offline")
            return jsonify({"status": "offline", "error": r.stderr.strip()[:200]})
    except Exception as e:
        update_state(server_status="error")
        return jsonify({"status": "error", "error": str(e)[:200]})

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
            r = subprocess.run([
                "ssh", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10", f"{SERVER_USER}@{SERVER_HOST}", "echo OK"
            ], capture_output=True, text=True, timeout=15)
            if "OK" not in r.stdout:
                socketio.emit("server_deploy_error", {"error": f"SSH连接失败: {r.stderr}"})
                update_state(server_status="offline")
                return

            update_state(server_status="online")
            socketio.emit("server_deploy_progress", {"step": "创建目录", "progress": 20})

            # 2. 创建远程目录
            subprocess.run([
                "ssh", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
                f"{SERVER_USER}@{SERVER_HOST}",
                f"mkdir -p {SERVER_DEPLOY_PATH}/data/params {SERVER_DEPLOY_PATH}/config {SERVER_DEPLOY_PATH}/models"
            ], check=True, capture_output=True, timeout=10)

            # 3. 同步数据文件
            socketio.emit("server_deploy_progress", {"step": "同步数据文件", "progress": 40})
            data_dirs = ["data/params", "data/tactics_rules.yaml", "data/battle_memory.db"]
            for d in data_dirs:
                local = PROJECT_ROOT / d
                if local.exists():
                    subprocess.run([
                        "scp", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
                        "-r", str(local), f"{SERVER_USER}@{SERVER_HOST}:{SERVER_DEPLOY_PATH}/data/"
                    ], check=True, capture_output=True, timeout=30)

            if not sync_only:
                # 4. 同步项目文件
                socketio.emit("server_deploy_progress", {"step": "同步项目文件", "progress": 60})
                for item in ["dashboard_server.py", "desktop_app.py", "requirements.txt", "config/settings.yaml"]:
                    local = PROJECT_ROOT / item
                    if local.exists():
                        subprocess.run([
                            "scp", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
                            str(local), f"{SERVER_USER}@{SERVER_HOST}:{SERVER_DEPLOY_PATH}/"
                        ], check=True, capture_output=True, timeout=30)

                # 5. 同步src目录
                socketio.emit("server_deploy_progress", {"step": "同步源码", "progress": 80})
                subprocess.run([
                    "scp", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
                    "-r", str(PROJECT_ROOT / "src"), f"{SERVER_USER}@{SERVER_HOST}:{SERVER_DEPLOY_PATH}/"
                ], check=True, capture_output=True, timeout=60)

                # 6. 安装依赖并重启
                socketio.emit("server_deploy_progress", {"step": "安装依赖", "progress": 90})
                subprocess.run([
                    "ssh", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
                    f"{SERVER_USER}@{SERVER_HOST}",
                    f"cd {SERVER_DEPLOY_PATH} && pip3 install -r requirements.txt -q 2>&1"
                ], capture_output=True, timeout=120)

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
            r2 = req.post(f"{llm_cfg['api_base']}/chat/completions", headers={"Authorization": f"Bearer {llm_cfg['api_key']}", "Content-Type": "application/json"}, json={"model": llm_cfg.get("model", "deepseek-chat"), "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}, timeout=10)
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
                model=llm_cfg.get("model", "deepseek-chat"),
                messages=messages,
                max_tokens=800,
                temperature=0.7,
                stream=True,
                timeout=30,
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
                model=llm_cfg.get("model", "deepseek-chat"),
                messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.5,
                timeout=30,
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
                model=llm_cfg.get("model", "deepseek-chat"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.5,
                timeout=30,
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
            resp = client.chat.completions.create(model=llm_cfg.get("model", "deepseek-chat"), messages=[{"role": "user", "content": prompt}], max_tokens=400, temperature=0.3, timeout=15)
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
            r = subprocess.run(["ssh", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", f"{SERVER_USER}@{SERVER_HOST}", "echo OK"], capture_output=True, text=True, timeout=10)
            results["server"] = "online" if "OK" in r.stdout else "offline"
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

def _run_ai_loop():
    global _controller
    update_state(status="初始化组件...", ai_thinking="正在加载模型和连接设备...")
    add_learning_log("combat", "AI上线，初始化组件", "")

    from src.execution.adb_utils import ADBUtils
    from src.execution.mumu_manager import MuMuManagerTouch
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

    cfg = load_config()
    gc = cfg["game"]
    dc = cfg["device"]
    lc = cfg["llm"]
    lpc = cfg["game_loop"]
    yc = cfg["yolo"]
    sc = cfg["scrcpy"]
    lnc = cfg.get("learning", {})
    ss = (gc["screen_width"], gc["screen_height"])

    ad = dc.get("active", "mumu")
    di = dc.get(ad, {})
    adb = ADBUtils(host=di.get("adb_host", "127.0.0.1"), port=di.get("adb_port", 7555), connect_timeout=dc["adb_connect_timeout"], command_timeout=dc["adb_command_timeout"], retry_count=dc["adb_retry_count"])

    if not adb.ensure_connected():
        update_state(status="ADB连接失败", ai_thinking="", adb_status="disconnected")
        add_learning_log("connection", "ADB连接失败", f"{di.get('adb_host','127.0.0.1')}:{di.get('adb_port',7555)}")
        socketio.emit("cycle_update", get_state())
        return

    update_state(adb_status="connected", status="ADB已连接, 加载模型...", ai_thinking="正在加载YOLO模型和OCR...")
    add_learning_log("connection", "ADB连接成功", "")

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
    touch = MuMuManagerTouch(exe_path=mc.get("exe_path", r"D:\MuMuPlayer\nx_main\MuMuManager.exe"), verbosity=mc.get("verbosity", 0), timeout=mc.get("timeout", 5.0))
    px = int(lpc["pause_button_x"] * ss[0])
    py = int(lpc["pause_button_y"] * ss[1])
    executor = CommandExecutor(adb=adb, screen_size=ss, touch=touch if touch.is_connected else None, pause_button=(px, py))

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


# ── Patch ──
class DashboardGameController:
    def __new__(cls, event_callback=None, **kw):
        from src.controller.game_controller import GameController
        inst = GameController.__new__(GameController)
        GameController.__init__(inst, **kw)
        inst._dashboard_callback = event_callback
        return inst


import src.controller.game_controller as gc_mod

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

import src.decision.commander as cmd_mod

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
            resp = client.chat.completions.create(model=lc.get("model", "deepseek-chat"), messages=[{"role": "system", "content": sp}, {"role": "user", "content": f"指挥官指令: {cmd}"}], max_tokens=150, temperature=0.5, timeout=5)
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
  <button class="nav-tab" onclick="switchTab('training')">模型训练</button>
  <button class="nav-tab" onclick="switchTab('annotate')">标注工具</button>
  <button class="nav-tab" onclick="switchTab('params')">参数学习</button>
  <button class="nav-tab" onclick="switchTab('learning')">学习日志</button>
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
    <button class="btn-rebuild" onclick="rebuildChain()" style="margin-bottom:10px">重建决策链</button>
    <button class="btn-verify" onclick="checkAllConnections()" style="margin-bottom:10px;margin-left:6px">检查所有连接</button>
    <div id="rebuild-status" style="font-size:12px;margin-top:8px"></div>
  </div>
  <div class="conn-grid">
    <!-- DeepSeek API -->
    <div class="conn-card">
      <div class="conn-name">DeepSeek API</div>
      <div class="conn-status unknown" id="conn-deepseek-status">未检查</div>
      <div id="conn-deepseek-detail" style="font-size:10px;color:#888"></div>
      <div class="conn-actions">
        <button class="btn-verify" onclick="verifyAPI()">验证</button>
      </div>
    </div>
    <!-- ADB -->
    <div class="conn-card">
      <div class="conn-name">ADB 连接</div>
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
  if(tab==='params'){loadParams()}
  if(tab==='learning'){refreshLearningLog()}
  if(tab==='dashboard'&&!scoreChart)initChart();
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
function startAI(){socket.emit('start');document.getElementById('status-badge').textContent='连接中...';document.getElementById('status-badge').style.color='#ff9800'}
function stopAI(){socket.emit('stop');document.getElementById('status-badge').textContent='已停止';document.getElementById('status-badge').style.color='#888'}
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
  document.getElementById('conn-github-status').textContent=d.api_status==='online'?'在线':'离线';
  document.getElementById('conn-github-status').className='conn-status '+(d.api_status==='online'?'online':'offline');
  document.getElementById('conn-github-detail').textContent=(d.repo_url||'')+' | '+d.branch;
})}
function pushToGitHub(){fetch('/api/github/push',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:'手动推送 '+new Date().toLocaleString()})}).then(r=>r.json()).then(d=>{
  document.getElementById('settings-github-result').innerHTML='<div class="alert '+(d.status==='pushed'||d.status==='no_changes'?'success':'error')+'">'+d.status+'</div>';
})}
function pullFromGitHub(){fetch('/api/github/pull',{method:'POST'}).then(r=>r.json()).then(d=>{
  document.getElementById('settings-github-result').innerHTML='<div class="alert '+(d.status==='pulled'?'success':'error')+'">'+d.status+'</div>';
})}
function checkServer(){fetch('/api/server/status').then(r=>r.json()).then(d=>{
  document.getElementById('conn-server-status').textContent=d.status==='online'?'在线':d.status;
  document.getElementById('conn-server-status').className='conn-status '+(d.status==='online'?'online':'offline');
  document.getElementById('conn-server-detail').textContent='部署: '+(d.deployed?'是':'否');
})}
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
  document.getElementById('conn-pytorch-status').textContent=d.version||'未安装';
  document.getElementById('conn-pytorch-status').className='conn-status '+(d.version?'online':'offline');
  document.getElementById('conn-pytorch-detail').textContent='CUDA: '+(d.cuda?'是':'否');
})}
function updatePyTorch(){fetch('/api/pytorch/update',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
  document.getElementById('conn-pytorch-status').textContent='更新中...';document.getElementById('conn-pytorch-status').className='conn-status checking';
})}
socket.on('pytorch_update_complete',function(d){document.getElementById('conn-pytorch-status').textContent=d.version||'更新完成';document.getElementById('conn-pytorch-status').className='conn-status '+(d.success?'online':'offline')});

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
socket.on('started',function(d){document.getElementById('status-badge').textContent='战斗中...';document.getElementById('status-badge').style.color='#4caf50'});
socket.on('stopped',function(d){document.getElementById('status-badge').textContent='已停止';document.getElementById('status-badge').style.color='#888'});

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

// ── 初始化 ──
document.addEventListener('DOMContentLoaded',function(){
  initChart();loadVersion();checkPyTorch();
  // 检查ADB状态
  setTimeout(function(){checkADB();checkGitHub();checkServer()},1000);
  // 定期刷新连接状态
  setInterval(function(){checkAllConnections()},30000);
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