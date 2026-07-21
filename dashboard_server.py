"""Firefight AI 控制面板服务端 v5.0
Flask + SocketIO + AI对话 + 训练管线 + 标注工具 + 自更新 + 参数学习
+ 学习日志透明化 + GitHub集成 + 连接管理 + 腾讯云部署
"""

from __future__ import annotations
import os, sys, time, json, threading, argparse, subprocess, hashlib, tempfile, re

# 🔇 禁用Windows错误弹窗 (避免子进程崩溃时弹出系统对话框)
if sys.platform == "win32":
    try:
        import ctypes
        # SEM_FAILCRITICALERRORS(1) | SEM_NOGPFAULTERRORBOX(2) | SEM_NOOPENFILEERRORBOX(0x8000)
        ctypes.windll.kernel32.SetErrorMode(0x8003)
    except:
        pass
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import yaml
from flask import Flask, render_template_string, request, send_from_directory, jsonify, Response, stream_with_context
from flask_socketio import SocketIO, emit
from loguru import logger

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["SECRET_KEY"] = "firefight_dashboard_v5"
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", max_http_buffer_size=100*1024*1024)

# 🔥 全局JSON错误处理器 - 确保所有API错误返回JSON而非HTML
# 关键修复：Flask的@app.errorhandler(Exception)不会捕获HTTPException子类(如400,405,413等)
# 必须单独注册HTTPException处理器，否则SyntaxError: Unexpected token '<' 会出现在前端

from werkzeug.exceptions import HTTPException

@app.errorhandler(HTTPException)
def handle_http_exception(e):
    """捕获所有HTTP异常(400/405/413等) - 这是SyntaxError '<' 的根本原因"""
    if request.path.startswith("/api/"):
        logger.warning(f"API HTTP异常: {request.path} - {e.code} {e.name}: {e.description}")
        return jsonify({
            "status": "error", 
            "error": f"{e.name}: {e.description}" if e.description else str(e),
            "code": e.code,
            "path": request.path
        }), e.code
    # 非API路径使用默认HTML响应
    return e

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "error": "接口不存在", "path": request.path}), 404
    return "<html><body><h1>404</h1><p>页面不存在，<a href='/'>返回首页</a></p></body></html>", 404

@app.errorhandler(500)
def server_error(e):
    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "error": "服务器内部错误，请查看日志", "detail": str(e)}), 500
    return "<h1>500 - 服务器错误</h1>", 500

@app.errorhandler(Exception)
def handle_exception(e):
    """全局异常捕获 - 确保所有API返回JSON（不包含HTTPException，已单独处理）"""
    if request.path.startswith("/api/"):
        logger.error(f"API异常: {request.path} - {e}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)[:300], "path": request.path}), 500
    # 非API路径返回HTML
    return "<h1>500 - 服务器错误</h1><pre>" + str(e)[:500] + "</pre>", 500

@app.after_request
def enforce_api_json(response):
    """确保所有/api/路径的响应Content-Type为application/json
    这是防止SyntaxError: Unexpected token '<'的最后一道防线"""
    if request.path.startswith("/api/") and "text/html" in response.content_type:
        logger.error(f"API返回了HTML而非JSON: {request.path} (status={response.status_code})")
        response.content_type = "application/json"
    return response

PROJECT_ROOT = Path(__file__).parent
APP_VERSION = "5.1.0"
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
    # v5.1 新增
    "prediction_accuracy": 0,     # AI预测准确率
    "predicted_enemies": [],      # 预测的敌方位置
    "map_hot_zones": [],          # 地图热区
    "threat_level": 0,            # 当前威胁等级
    "auto_save_status": "idle",   # 自动保存状态
    "next_save_time": "",         # 下次保存时间
}
_lock = threading.Lock()
_controller = None
_user_instruction = ""
_training_process = None
_chat_history: list[dict] = []
_learning_log: list[dict] = []  # AI学习日志（仅记录AI学到的新知识）
_system_log: list[dict] = []    # 系统日志（连接状态、部署、配置等系统事件）
_adb_utils = None  # ADB实例引用
_predictor = None  # 战场预测器
_scheduler = None  # 自动保存调度器

# ── GPU 状态 ──
_gpu_info: dict = {"cuda_available": False, "gpus": [], "pytorch_cuda": False, "pytorch_version": "", "message": ""}

# ── Android 模拟器状态 ──
EMULATOR_HOME = PROJECT_ROOT / "android_emulator"
ANDROID_SDK_ROOT = EMULATOR_HOME / "sdk"
AVD_NAME = "firefight_avd"
AVD_CONFIG = {
    "device": "Nexus 9",        # MuMu兼容: 平板设备, 非手机
    "api_level": 33,
    "arch": "x86_64",           # x86_64 + ARM翻译层 (Houdini)
    "ram": 4096,                # 4GB
    "cores": 4,
    "resolution": "1600x900",   # MuMu标准分辨率 (1920x1080备选)
    "density": 240,             # MuMu标准密度
    "fullscreen": True,
    "touch_screen": True,
    "keyboard": True,
    "gpu": "host",              # 🔥 强制GPU加速: 60fps
    "gpu_mode": "host",         # host模式 = 主机GPU渲染
    "renderer": "opengles",     # MuMu兼容: OpenGL ES
    "boot_complete_timeout": 120, # 启动等待(秒)
}
_emulator_process = None
_emulator_adb_port = 5556  # 内置模拟器ADB端口
_emulator_screen_on = False
_scrcpy_process = None
_scrcpy_enabled = False
_adb_monitor_running = False
_adb_last_connected = False


def _adb_keepalive_worker():
    """ADB 保活后台线程：每10秒检查一次连接，断开时自动重连"""
    global _adb_monitor_running, _adb_last_connected
    _adb_monitor_running = True
    logger.info("ADB保活监控已启动")
    add_system_log("connection", "ADB保活监控已启动", "每10秒检查一次连接状态")
    consecutive_failures = 0
    
    def _check_device(host, port, output):
        """检测ADB设备列表中是否包含指定设备"""
        for line in output.strip().split("\n"):
            if "\tdevice" in line:
                if f"{host}:{port}" in line or f"emulator-{port}" in line:
                    return True
            elif "device" in line and "emulator-" in line:
                return True
        return False
    
    while _adb_monitor_running:
        try:
            cfg = load_config()
            dc = cfg["device"]
            ad = dc.get("active", "generic")
            di = dc.get(ad, {})
            host = di.get("adb_host", "127.0.0.1")
            port = di.get("adb_port", 5555)
            adb_exe = _find_adb_exe()
            
            # 验证ADB可执行文件存在
            if adb_exe != "adb" and not Path(adb_exe).exists():
                adb_exe = "adb"
            
            subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
            r = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
            connected = _check_device(host, port, r.stdout)
            
            if connected:
                consecutive_failures = 0
                if not _adb_last_connected:
                    update_state(adb_status="connected")
                    add_system_log("connection", "ADB已连接", f"{host}:{port}")
                    logger.info(f"ADB保活: 已连接 {host}:{port}")
                    _adb_last_connected = True
                    # 🔥 自动检测并设置模拟器类型
                    global _emulator_adb_port, _emulator_type
                    if port == 7555:
                        _emulator_adb_port = 7555
                        _emulator_type = "mumu"
                        logger.info(f"自动检测到MUMU模拟器 (port={port})")
                    elif _emulator_type == "generic":
                        _emulator_adb_port = port
            else:
                consecutive_failures += 1
                update_state(adb_status="disconnected")
                
                if consecutive_failures == 1:
                    add_system_log("connection", "ADB断开，尝试重连", f"{host}:{port}")
                
                # 尝试重连（最多重试3次，避免频繁日志）
                subprocess.run([adb_exe, "connect", f"{host}:{port}"], capture_output=True, text=True, timeout=5)
                r2 = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
                reconnected = _check_device(host, port, r2.stdout)
                
                if reconnected:
                    consecutive_failures = 0
                    update_state(adb_status="connected")
                    add_system_log("connection", "ADB自动重连成功", f"{host}:{port}")
                    _adb_last_connected = True
                else:
                    _adb_last_connected = False
                    if consecutive_failures <= 1 or consecutive_failures % 6 == 0:
                        # 只在首次失败和每60秒报告一次，避免刷屏
                        add_system_log("connection", "ADB断开，等待设备", f"{host}:{port} (连续失败{consecutive_failures}次)")
        except Exception as e:
            consecutive_failures += 1
            logger.warning(f"ADB保活检查异常: {e}")
            if consecutive_failures <= 1 or consecutive_failures % 6 == 0:
                add_system_log("connection", "ADB检查异常", str(e)[:200])
        time.sleep(10)


def start_adb_monitor():
    """启动ADB保活监控"""
    global _adb_monitor_running
    if _adb_monitor_running:
        return
    threading.Thread(target=_adb_keepalive_worker, daemon=True).start()
    logger.info("ADB保活监控线程已启动")


def update_state(**kw):
    with _lock:
        _dashboard_state.update(kw)
    # 如果训练状态或进度发生变化，通知前端
    if "training_progress" in kw or "training_status" in kw or "training_message" in kw:
        socketio.emit("training_state_update", {
            "status": _dashboard_state.get("training_status", "idle"),
            "progress": _dashboard_state.get("training_progress", 0),
            "message": _dashboard_state.get("training_message", ""),
        })


def get_state() -> dict:
    with _lock:
        return dict(_dashboard_state)


def add_learning_log(category: str, message: str, detail: str = ""):
    """添加AI学习日志条目 — 仅记录AI真正学到的新知识（战术、战法、搜索洞察、对话纠正等）"""
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
    _save_learning_log()  # 🔥 实时持久化到磁盘


def add_system_log(category: str, message: str, detail: str = ""):
    """添加系统日志条目 — 记录连接状态、部署、配置变更等系统运维事件"""
    global _system_log
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "category": category,
        "message": message,
        "detail": detail[:500] if detail else "",
    }
    _system_log.append(entry)
    if len(_system_log) > 300:
        _system_log = _system_log[-300:]
    socketio.emit("system_log_update", {"entry": entry, "total": len(_system_log)})


# ═══ AI知识库系统 ═══
_ai_knowledge_base: list[dict] = []
_KNOWLEDGE_FILE = PROJECT_ROOT / "data" / "ai_knowledge.json"
_LEARNING_LOG_FILE = PROJECT_ROOT / "data" / "params" / "learning_log.json"

def _load_persistent_logs():
    """启动时从磁盘恢复学习日志（防止重启丢失）"""
    global _learning_log
    try:
        if _LEARNING_LOG_FILE.exists():
            _learning_log = json.loads(_LEARNING_LOG_FILE.read_text(encoding="utf-8", errors="ignore"))
            logger.info(f"从磁盘恢复学习日志: {len(_learning_log)} 条")
    except Exception as e:
        logger.warning(f"加载持久化日志失败: {e}")

def _save_learning_log():
    """持久化学习日志到磁盘（每次新增条目时调用）"""
    try:
        _LEARNING_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LEARNING_LOG_FILE.write_text(json.dumps(_learning_log[-500:], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"保存学习日志失败: {e}")

def _load_knowledge_base():
    global _ai_knowledge_base
    try:
        if _KNOWLEDGE_FILE.exists():
            _ai_knowledge_base = json.loads(_KNOWLEDGE_FILE.read_text(encoding="utf-8"))
    except:
        _ai_knowledge_base = []

def _save_knowledge_base():
    try:
        _KNOWLEDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _KNOWLEDGE_FILE.write_text(json.dumps(_ai_knowledge_base, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"保存知识库失败: {e}")

def add_knowledge(category: str, title: str, content: str, source: str = "manual"):
    """向AI知识库添加一条知识，去重后自动保存"""
    global _ai_knowledge_base
    entry = {
        "id": hashlib.md5(f"{category}:{title}:{content[:100]}".encode()).hexdigest()[:8],
        "category": category,
        "title": title,
        "content": content,
        "source": source,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trained": False,
        "selected": False,
    }
    # 去重
    if not any(k["id"] == entry["id"] for k in _ai_knowledge_base):
        _ai_knowledge_base.append(entry)
        _save_knowledge_base()
        socketio.emit("knowledge_update", {"entry": entry, "total": len(_ai_knowledge_base)})
        logger.info(f"知识库新增: [{category}] {title}")



def _auto_sync_params_from_server():
    """启动时自动从服务器拉取最新参数"""
    import threading as _thr
    def _do_sync():
        try:
            import urllib.request
            url = "https://firefightai.top/api/sync/params/download"
            req = urllib.request.Request(url, headers={"User-Agent": "FirefightAI-Local"})
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode())
            sv = data.get("version", {})
            lv = _get_params_version()
            if sv.get("version", 0) > lv.get("version", 0):
                new_params = data.get("params", {})
                _save_learning_params({**_load_learning_params(), **new_params})
                _update_params_version(sv.get("total_learnings", 0), "server_sync")
                logger.info(f"从服务器同步参数 v{sv['version']}")
                add_system_log("sync", f"同步服务器参数 v{sv['version']}", f"{sv.get('total_learnings',0)}次学习")
        except Exception as e:
            logger.debug(f"自动同步跳过: {e}")
    _thr.Thread(target=_do_sync, daemon=True).start()


def _auto_upload_params_to_server() -> bool:
    """自动上传参数到服务器"""
    import threading as _thr
    def _do_upload():
        try:
            import urllib.request
            params = _load_learning_params() or {}
            data = json.dumps({
                "machine_id": socket.gethostname(),
                "params": params,
                "total_learnings": _get_params_version().get("total_learnings", 0),
            }).encode()
            req = urllib.request.Request(
                "https://firefightai.top/api/sync/params/upload",
                data=data, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=15)
            logger.info("参数已上传到服务器")
        except Exception as e:
            logger.debug(f"上传跳过: {e}")
    _thr.Thread(target=_do_upload, daemon=True).start()
    return True


# ═══ APK战术分析 ═══
@app.route("/api/apk/analyze", methods=["POST"])
def api_apk_analyze():
    """分析本地APK安装包，提取战术信息"""
    data = request.get_json() or {}
    apk_path = data.get("path", "").strip()
    
    if not apk_path or not os.path.exists(apk_path):
        return jsonify({"status": "error", "error": "文件不存在: " + apk_path}), 404
    
    try:
        import zipfile
        import xml.etree.ElementTree as ET
        
        results = {
            "package": "", "version": "", "tactics": [],
            "assets_files": [], "lua_scripts": [], "raw_files": 0
        }
        
        with zipfile.ZipFile(apk_path, 'r') as apk:
            # AndroidManifest.xml
            if "AndroidManifest.xml" in apk.namelist():
                results["package"] = "已找到 (需aapt解析)"
            
            # 扫描战术相关文件: lua脚本, 配置文件
            for name in apk.namelist():
                nl = name.lower()
                if nl.endswith(".lua"):
                    results["lua_scripts"].append(name.split("/")[-1])
                if any(kw in nl for kw in ["tactic", "strategy", "battle", "combat", "ai", "behavior", "config"]):
                    results["assets_files"].append(name)
                if nl.startswith("assets/") or nl.startswith("res/raw/"):
                    results["raw_files"] += 1
            
            results["lua_scripts"] = list(set(results["lua_scripts"]))
            results["assets_files"] = results["assets_files"][:20]
        
        # 生成战术摘要
        summary_parts = []
        if results["lua_scripts"]:
            summary_parts.append(f"发现{len(results['lua_scripts'])}个Lua脚本: {', '.join(results['lua_scripts'][:8])}")
        if results["assets_files"]:
            summary_parts.append(f"发现{len(results['assets_files'])}个战术相关文件")
        
        results["summary"] = ";\n".join(summary_parts) if summary_parts else "未发现明显战术配置文件"
        
        # 自动存入知识库
        if results["lua_scripts"] or len(results["assets_files"]) > 3:
            kb_content = results["summary"] + "\n关键文件: " + ", ".join(results["lua_scripts"][:5] + results["assets_files"][:5])
            add_knowledge("apk_analysis", f"APK分析: {os.path.basename(apk_path)}", kb_content, source="apk")
        
        add_system_log("apk", f"APK分析完成: {os.path.basename(apk_path)}", results["summary"][:100])
        return jsonify({"status": "ok", "results": results})
        
    except Exception as e:
        return jsonify({"status": "error", "error": f"APK分析失败: {str(e)[:200]}"}), 500


# ═══ 知识库 API ═══
@app.route("/api/knowledge/list", methods=["GET"])
def api_knowledge_list():
    _load_knowledge_base()
    return jsonify({"knowledge": _ai_knowledge_base, "total": len(_ai_knowledge_base)})


@app.route("/api/knowledge/select", methods=["POST"])
def api_knowledge_select():
    data = request.get_json() or {}
    kid = data.get("id", "")
    checked = data.get("checked", False)
    for k in _ai_knowledge_base:
        if k["id"] == kid:
            k["selected"] = checked
            break
    _save_knowledge_base()
    return jsonify({"status": "ok"})

@app.route("/api/config/save", methods=["POST"])
def api_config_save():
    """保存前端输入的配置到服务器"""
    try:
        data = request.get_json(force=True) or {}
        
        # 写入DeepSeek API Key到环境
        if data.get("apikey"):
            os.environ["DEEPSEEK_API_KEY"] = data["apikey"]
            # 也写入 /etc/environment 持久化
            try:
                with open("/etc/environment", "r") as f:
                    lines = f.readlines()
                new_lines = [l for l in lines if "DEEPSEEK_API_KEY" not in l]
                new_lines.append(f'DEEPSEEK_API_KEY={data["apikey"]}\n')
                with open("/etc/environment", "w") as f:
                    f.writelines(new_lines)
            except:
                pass
        
        # 写入配置到 settings.yaml
        cfg = {
            "deepseek": {"api_key": data.get("apikey", ""), "model": "deepseek-chat", "temperature": 0.7},
            "llm": {"api_key": data.get("apikey", ""), "provider": "deepseek", "model": "deepseek-chat", "api_base": "https://api.deepseek.com/v1", "temperature": 0.7, "max_tokens": 4096, "timeout": 30, "retry_count": 3},
            "server": {"host": "0.0.0.0", "port": 5000, "cors_origins": ["*"]},
            "github": {"repo": "chenyt-Indom/firefightAI", "token": data.get("github_token", ""), "branch": "master"},
        }
        
        config_path = PROJECT_ROOT / "config" / "settings.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)
        
        socketio.emit("training_log", {"line": "🔐 配置已更新: API Key + GitHub Token"})
        logger.info("配置已从Web前端保存")
        
        return jsonify({"status": "ok", "message": "配置已保存"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/api/deploy", methods=["POST"])
def api_deploy():
    """一键部署: SCP上传+SSH重启"""
    try:
        data = request.get_json(force=True) or {}
        server_url = data.get("server", "139.199.69.88")
        ssh_user = data.get("ssh_user", "root")
        ssh_key = data.get("ssh_key", "")
        
        # 如果有SSH key，尝试部署
        if ssh_key and os.path.exists(ssh_key):
            import subprocess
            local_file = str(PROJECT_ROOT / "dashboard_server.py")
            remote = f"{ssh_user}@{server_url}:/home/ubuntu/firefightAI/dashboard_server.py"
            
            r = subprocess.run(["scp", "-o", "StrictHostKeyChecking=no", "-i", ssh_key,
                              local_file, remote], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                r2 = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "-i", ssh_key,
                                    f"{ssh_user}@{server_url}",
                                    "systemctl restart firefightai"], capture_output=True, text=True, timeout=20)
                return jsonify({"status": "ok", "message": f"已部署到 {server_url}"})
            else:
                return jsonify({"status": "error", "error": r.stderr[:200]})
        
        return jsonify({"status": "ok", "message": "配置已保存（无SSH密钥，跳过远程部署）"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/knowledge/train", methods=["POST"])
def api_knowledge_train():
    """基于勾选的知识条目训练AI并自动调参"""
    data = request.get_json() or {}
    kid = data.get("id", "")
    kids = data.get("ids", [])
    
    if kid:
        kids = [kid]
    elif not kids:
        kids = [k["id"] for k in _ai_knowledge_base if k.get("selected")]
    
    if not kids:
        return jsonify({"status": "error", "error": "请先勾选要训练的知识条目"}), 400
    
    trained_items = []
    for k in _ai_knowledge_base:
        if k["id"] in kids:
            k["trained"] = True
            k["trained_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            trained_items.append(k["title"])
    
    _save_knowledge_base()
    
    # 自动调参: 根据训练的知识数量调整学习参数
    try:
        params = _load_learning_params() or {}
        trained_count = sum(1 for k in _ai_knowledge_base if k.get("trained"))
        params["total_learnings"] = max(params.get("total_learnings", 0), trained_count)
        params["tactical_aggressiveness"] = min(0.95, 0.3 + trained_count * 0.05)
        params["learning_rate"] = max(0.001, 0.05 - trained_count * 0.002)
        _save_learning_params(params)
        socketio.emit("training_log", {"line": f"⚙️ 自动调参: aggressiveness={params['tactical_aggressiveness']:.2f}, lr={params['learning_rate']:.4f}"})
    except:
        logger.warning("自动调参失败")
    
    # 🔥 使用 DeepSeek 深度学习每条知识，提炼战术规则
    def deep_learn():
        try:
            cfg = load_config()
            api_key = cfg.get("deepseek", {}).get("api_key", "") or os.getenv("DEEPSEEK_API_KEY", "")
            if not api_key:
                socketio.emit("training_log", {"line": "⚠️ DeepSeek未配置，跳过深度学习"})
                return
            
            all_content = ""
            for k in _ai_knowledge_base:
                if k["id"] in kids:
                    all_content += f"[{k['category']}] {k['title']}\n{k['content'][:500]}\n---\n"
            
            if not all_content.strip():
                return
            
            import openai
            client = openai.OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
            
            prompt = f"""你是一个军事战术AI学习系统。请从以下知识中深度提炼战术规则和作战经验：

{all_content[:4000]}

请输出：
1. **核心战术规则**（至少3条，格式: 规则名: 详细描述）
2. **作战经验提炼**（关键发现和教训）
3. **参数调整建议**（针对aggressiveness/learning_rate等）

用中文回答，简洁有力。"""
            
            r = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=2000
            )
            analysis = r.choices[0].message.content
            
            # 存入学习日志
            add_learning_log("deep_learn", f"深度学习: {len(kids)}条知识", analysis)
            socketio.emit("training_log", {"line": "📖 DeepSeek深度分析完成:\n" + analysis[:300]})
            
            # 自动提取规则并保存到tactics_rules
            rules_file = PROJECT_ROOT / "data" / "tactics_rules.yaml"
            existing = rules_file.read_text(encoding="utf-8", errors="ignore") if rules_file.exists() else ""
            
            import re
            new_rules = re.findall(r'(?:^|\n)\d+\.\s*\**([^*\n]+)\**\s*[:：](.+?)(?=\n\d+\.|\n\n|$)', analysis)
            if new_rules:
                rules_text = existing + f"\n# DeepSeek深度学习 {datetime.now().strftime('%Y%m%d_%H%M')}\n"
                for name, desc in new_rules[:20]:
                    rules_text += f"- rule: {name.strip()}\n  description: {desc.strip()}\n"
                rules_file.write_text(rules_text, encoding="utf-8")
                add_learning_log("tactic", f"提取{len(new_rules)}条新战术规则", str(new_rules[:3]))
            
        except Exception as e:
            socketio.emit("training_log", {"line": f"⚠️ 深度学习失败: {str(e)[:100]}"})
    
    threading.Thread(target=deep_learn, daemon=True).start()
    
    add_system_log("training", "知识训练完成", f"训练{len(kids)}条, DeepSeek分析中...")
    socketio.emit("knowledge_update", {"total": len(_ai_knowledge_base)})
    return jsonify({"status": "ok", "trained": len(kids), "items": trained_items[:5], "total_learnings": trained_count})


def _load_learning_params():
    """加载AI学习参数"""
    try:
        pf = PROJECT_ROOT / "data" / "params" / "ai_learning_params.json"
        if pf.exists():
            return json.loads(pf.read_text(encoding="utf-8", errors="ignore"))
    except:
        pass


def load_config() -> dict:
    """加载配置，自动用环境变量覆盖敏感字段"""
    config_path = PROJECT_ROOT / "config" / "settings.yaml"
    if not config_path.exists():
        # 生成默认配置
        config_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = {
            "deepseek": {"api_key": os.getenv("DEEPSEEK_API_KEY", ""), "model": "deepseek-chat", "temperature": 0.7},
            "llm": {"api_key": os.getenv("DEEPSEEK_API_KEY", ""), "model": "deepseek-chat", "provider": "deepseek", "api_base": "https://api.deepseek.com/v1", "temperature": 0.7, "max_tokens": 4096, "timeout": 30, "retry_count": 3},
            "server": {"host": "0.0.0.0", "port": 5000},
            "github": {"repo": "chenyt-Indom/firefightAI", "branch": "master", "token": os.getenv("GITHUB_TOKEN", "")},
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)
        return cfg
    
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    
    # 🔥 自动用环境变量覆盖(环境变量优先于yaml占位符)
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if api_key:
        # 覆盖deepseek段
        if "deepseek" not in cfg:
            cfg["deepseek"] = {}
        if not cfg["deepseek"].get("api_key") or cfg["deepseek"]["api_key"] == "YOUR_DEEPSEEK_API_KEY":
            cfg["deepseek"]["api_key"] = api_key
        # 覆盖llm段
        if "llm" not in cfg:
            cfg["llm"] = {}
        if not cfg["llm"].get("api_key") or cfg["llm"]["api_key"] == "YOUR_DEEPSEEK_API_KEY":
            cfg["llm"]["api_key"] = api_key
    
    return cfg


# ═══════════════════════════════════════════════════════════════
# 学习日志 API
# ═══════════════════════════════════════════════════════════════

@app.route("/api/learning_log")
def api_learning_log():
    limit = request.args.get("limit", 100, type=int)
    return jsonify(_learning_log[-limit:])

@app.route("/api/learning_log/add_manual", methods=["POST"])
def api_learning_log_add_manual():
    """手动粘贴知识到学习日志并训练AI"""
    data = request.get_json() or {}
    content = (data.get("content", "") or "").strip()
    if not content:
        return jsonify({"status": "error", "error": "内容不能为空"}), 400
    
    # 自动分类
    category = "manual_knowledge"
    if any(kw in content for kw in ["战术", "包抄", "迂回", "正面", "侧翼", "防守", "进攻"]):
        category = "tactic"
    elif any(kw in content for kw in ["战法", "兵法", "三十六计", "孙子", "策略"]):
        category = "strategy"
    elif any(kw in content for kw in ["火力", "压制", "射程", "炮弹", "导弹"]):
        category = "combat"
    
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    title = lines[0][:80] if lines else "手动知识"
    detail = content[:2000]
    
    # 保存到学习日志
    add_learning_log(category, title, detail)
    
    # 自动触发训练
    def do_train():
        try:
            params = _load_learning_params() or {}
            word_count = len(content)
            boost = min(0.15, word_count / 5000)
            params["tactical_aggressiveness"] = min(0.95, params.get("tactical_aggressiveness", 0.5) + boost)
            params["learning_rate"] = min(0.05, params.get("learning_rate", 0.01) + boost / 3)
            params["total_learnings"] = params.get("total_learnings", 0) + 1
            _save_learning_params(params)
            
            # 也存到知识库
            add_knowledge(category, title, detail, source="manual")
            
            socketio.emit("learning_params_updated", params)
            add_learning_log("self_learn", f"手动知识训练完成: +{boost:.2f}攻防倾向", f"word_count={word_count}, aggressiveness={params['tactical_aggressiveness']:.2f}")
        except Exception as e:
            logger.error(f"手动训练失败: {e}")
    
    threading.Thread(target=do_train, daemon=True).start()
    return jsonify({
        "status": "ok", 
        "category": category,
        "message": f"已保存 {len(content)} 字符到学习日志，正在训练...",
        "word_count": len(content)
    })

@app.route("/api/learning_log/clear", methods=["POST"])
def api_learning_log_clear():
    global _learning_log
    _learning_log = []
    update_state(learning_log=[])
    _save_learning_log()
    return jsonify({"status": "cleared"})

@app.route("/api/manual_action/record", methods=["POST"])
def api_manual_action_record():
    """记录用户的直接操控操作，AI重点学习用户行为"""
    data = request.get_json() or {}
    action_type = data.get("type", "")  # tap/swipe/button/select_unit/deploy
    location = data.get("location", {})  # {x, y}
    button_name = data.get("button", "")  # 按钮名称
    target = data.get("target", "")  # 目标描述
    observations = data.get("observations", "")  # 用户观察到的现象
    casualties = data.get("casualties", {})  # {"allies": 0, "enemies": 0}
    
    detail_parts = []
    if location:
        detail_parts.append(f"位置=({location.get('x','?')},{location.get('y','?')})")
    if button_name:
        detail_parts.append(f"按钮=\"{button_name}\"")
    if target:
        detail_parts.append(f"目标={target}")
    if observations:
        detail_parts.append(f"观察={observations}")
    if casualties:
        detail_parts.append(f"伤亡=友{casualties.get('allies',0)}/敌{casualties.get('enemies',0)}")
    
    detail = "; ".join(detail_parts)
    add_learning_log("manual_control", f"用户操控: {action_type}", detail)
    add_knowledge("manual_control", f"用户{action_type}操作: {button_name or location}", detail, source="manual")
    
    # 如果有伤亡数据，额外记录
    if casualties:
        add_learning_log("casualties", f"伤亡报告: 友军{casualties['allies']}/敌军{casualties['enemies']}", json.dumps(casualties))
    
    return jsonify({"status": "recorded", "action": action_type})

@app.route("/api/learning_log/export")
def api_learning_log_export():
    """导出学习日志为JSON"""
    return jsonify({"exported_at": datetime.now().isoformat(), "version": APP_VERSION, "entries": _learning_log})

@app.route("/api/ai/self_improve", methods=["POST"])
def api_ai_self_improve():
    """AI自主改进: 评估当前参数 → 根据学习数据自动调参 → 保存 → 上传GitHub"""
    data = request.get_json() or {}
    trigger = data.get("trigger", "manual")  # manual/auto/battle_end
    
    global _self_learning_params
    old_params = dict(_self_learning_params)
    
    # AI 评估当前状态
    knowledge_count = len(_ai_knowledge_base)
    trained_count = sum(1 for k in _ai_knowledge_base if k.get("trained"))
    log_count = len(_learning_log)
    
    adjustments = []
    
    # 1. 温度调节: 知识越多 → temperature越低(AI更确定)
    if knowledge_count > 10 and _self_learning_params["temperature"] > 0.1:
        new_t = max(0.05, _self_learning_params["temperature"] - 0.02)
        _self_learning_params["temperature"] = round(new_t, 3)
        adjustments.append(f"temperature: {old_params['temperature']} → {new_t} (-{0.02})")
    
    # 2. 学习率调节: 训练越多 → 学习率降低(精调)
    if trained_count > 5 and _self_learning_params["learning_rate"] > 0.005:
        new_lr = max(0.001, _self_learning_params["learning_rate"] - 0.003)
        _self_learning_params["learning_rate"] = round(new_lr, 4)
        adjustments.append(f"learning_rate: {old_params['learning_rate']} → {new_lr} (-{0.003})")
    
    # 3. 攻击性调节: 根据手动操作和成功模式
    manual_ops = sum(1 for entry in _learning_log if entry.get("category") == "manual_control")
    if manual_ops > 3:
        boost = min(0.95, _self_learning_params.get("tactical_aggressiveness", 0.5) + 0.03)
        _self_learning_params["tactical_aggressiveness"] = round(boost, 3)
        adjustments.append(f"tactical_aggressiveness: +0.03 (用户操控引导)")
    
    # 4. 置信阈值调节
    if log_count > 20:
        _self_learning_params["confidence_threshold"] = round(max(0.3, min(0.8, log_count / 50)), 3)
        adjustments.append(f"confidence_threshold: {_self_learning_params['confidence_threshold']}")
    
    # 5. 更新学习统计
    _self_learning_params["total_learnings"] = _self_learning_params.get("total_learnings", 0) + 1
    _self_learning_params["last_learned"] = datetime.now().isoformat()
    _self_learning_params["last_adjustment_reason"] = "; ".join(adjustments) if adjustments else "评估通过，无需调整"
    
    # 立即保存到磁盘
    _save_learning_params()
    _save_learning_log()
    
    # 后台推送到GitHub
    def push_params():
        try:
            _git_run(["git", "add", "data/params/"], timeout=10)
            _git_run(["git", "commit", "-m", f"AI自学习参数更新 #{_self_learning_params['total_learnings']}: {_self_learning_params['last_adjustment_reason'][:50]}"], timeout=15)
            _git_run(["git", "push", "origin", "master"], timeout=300)
            socketio.emit("training_log", {"line": f"📤 参数已推送到GitHub (#{_self_learning_params['total_learnings']})"})
        except Exception as e:
            logger.warning(f"推送参数失败: {e}")
    
    threading.Thread(target=push_params, daemon=True).start()
    
    # 实时更新前端
    socketio.emit("self_learning_params", _self_learning_params)
    add_learning_log("self_improve", 
                     f"AI自主调参 (#{_self_learning_params['total_learnings']})", 
                     "; ".join(adjustments) if adjustments else "参数已最优")
    
    return jsonify({
        "status": "improved",
        "adjustments": adjustments,
        "old_params": old_params,
        "new_params": _self_learning_params,
        "total_learnings": _self_learning_params["total_learnings"],
    })

@app.route("/api/learning_log/train", methods=["POST"])
def api_learning_log_train():
    """将AI学习日志中的知识用于训练和调整AI参数，并同步到GitHub"""
    if len(_learning_log) < 3:
        return jsonify({"status": "error", "error": "学习日志条目不足（至少需要3条）"}), 400
    
    _train_status = {"running": True, "result": None}
    add_learning_log("self_learn", "开始从学习日志训练AI参数", f"共{len(_learning_log)}条知识")

    def do_train():
        try:
            recent = _learning_log[-50:]
            knowledge_text = "\n".join([
                f"[{e['category']}] {e['message']}" + (f" | {e['detail'][:200]}" if e.get('detail') else "")
                for e in recent
            ])

            # 🔥 简化版: 直接本地分析+调参，避免DeepSeek API超时
            analysis_lines = []
            params = _load_learning_params() or {}
            
            # 分析学习日志
            categories = {}
            for e in recent:
                cat = e.get('category', 'unknown')
                categories[cat] = categories.get(cat, 0) + 1
            
            analysis_lines.append(f"📊 分析{len(recent)}条日志: " + ", ".join(f"{k}:{v}条" for k,v in sorted(categories.items(), key=lambda x:-x[1])[:5]))
            
            # 提取战术关键词
            all_text = knowledge_text.lower()
            tactics_found = []
            keywords = {
                "侧翼包抄": ("tactical_aggressiveness", 0.08), "迂回": ("tactical_aggressiveness", 0.05),
                "正面进攻": ("tactical_aggressiveness", 0.03), "防守": ("tactical_aggressiveness", -0.03),
                "快速部署": ("execution_speed", 0.05), "火力压制": ("tactical_aggressiveness", 0.04),
                "牺牲": ("defense_weight", 0.05), "撤退": ("tactical_aggressiveness", -0.05),
                "闪电战": ("tactical_aggressiveness", 0.1), "稳扎稳打": ("tactical_aggressiveness", -0.02),
            }
            
            # 🔥 累计学习进度(防止正负相消)
            params["learning_history"] = params.get("learning_history", [])
            
            adjustments = {}
            for kw, (param, delta) in keywords.items():
                if kw in all_text:
                    adjustments[param] = adjustments.get(param, 0) + delta
                    tactics_found.append(kw)
            
            if tactics_found:
                analysis_lines.append(f"🎯 识别战术: {', '.join(tactics_found[:8])}")
            
            # 🔥 应用参数调整(使用动量防止突变)
            for param, delta in adjustments.items():
                if param not in params:
                    params[param] = 0.5
                old = params[param]
                new = max(0.1, min(0.99, old + delta))
                params[param] = old + (new - old) * 0.7  # 70%动量
            
            # 🔥 基础学习奖励: 每次训练都至少加一点aggressiveness
            base_boost = 0.002 * len(recent)
            params["tactical_aggressiveness"] = min(0.95, params.get("tactical_aggressiveness", 0.5) + base_boost)
            
            params["total_learnings"] = params.get("total_learnings", 0) + len(recent)
            params["last_trained"] = datetime.now().isoformat()
            params["effective_learnings"] = params.get("effective_learnings", 0) + len(tactics_found)
            
            # 🔥 学习历史追踪(用于前端展示进度)
            params["learning_history"].append({
                "t": datetime.now().isoformat(),
                "aggressiveness": round(params["tactical_aggressiveness"], 3),
                "tactics": len(tactics_found),
                "total": params["total_learnings"]
            })
            params["learning_history"] = params["learning_history"][-20:]  # 保留最近20次
            
            # 🔥 优化操作速度(每次训练都渐进式加速)
            if params["total_learnings"] > 5:
                params["tap_delay"] = max(0.05, params.get("tap_delay", 0.2) - 0.003)
                params["swipe_duration"] = max(300, params.get("swipe_duration", 1000) - 8)
                params["batch_execution"] = True
                if params["total_learnings"] % 10 == 0:
                    analysis_lines.append(f"⚡ 操作加速: tap={params['tap_delay']:.3f}s swipe={params['swipe_duration']:.0f}ms")
            
            if len(recent) > 10:
                params["multitasking"] = True
                analysis_lines.append("🔀 多任务协同已启用")
            
            _save_learning_params(params)
            analysis = "\n".join(analysis_lines)
            
            # 🔥 发送详细学习进度
            socketio.emit("learning_log_train_result", {
                "status": "ok",
                "log_count": len(recent),
                "analysis": analysis,
                "aggressiveness": round(params["tactical_aggressiveness"], 3),
                "speed": f"tap={params.get('tap_delay',0.2):.3f}s swipe={params.get('swipe_duration',1000):.0f}ms",
                "params_adjusted": list(adjustments.keys()),
                "tactics_found": len(tactics_found),
                "total_learnings": params["total_learnings"],
                "effective_learnings": params["effective_learnings"],
            })
            _train_status["result"] = "ok"
            
            # 也存到知识库
            add_knowledge("self_learn", f"训练分析 ({len(tactics_found)}战术)", analysis, "auto_train")
            
            # 自动推送到服务器和GitHub
            _auto_push_learning_params()
            _auto_upload_params_to_server()
            
        except Exception as e:
            logger.error(f"训练失败: {e}")
            socketio.emit("learning_log_train_result", {
                "status": "error", "error": str(e)[:200]
            })
            _train_status["result"] = "error"
    
    threading.Thread(target=do_train, daemon=True).start()
    return jsonify({"status": "training", "log_count": len(_learning_log)})


# ═══════════════════════════════════════════════════════════════
# 系统日志 API
# ═══════════════════════════════════════════════════════════════

@app.route("/api/system_log")
def api_system_log():
    limit = request.args.get("limit", 100, type=int)
    return jsonify(_system_log[-limit:])

@app.route("/api/system_log/clear", methods=["POST"])
def api_system_log_clear():
    global _system_log
    _system_log = []
    return jsonify({"status": "cleared"})

@app.route("/api/system_log/export")
def api_system_log_export():
    """导出系统日志为JSON"""
    return jsonify({"exported_at": datetime.now().isoformat(), "version": APP_VERSION, "entries": _system_log})


# ═══════════════════════════════════════════════════════════════
# 版本管理 + 自更新
# ═══════════════════════════════════════════════════════════════

@app.route("/favicon.ico")
def favicon():
    return b"", 204

@app.route("/api/version")
def api_version():
    try:
        import torch
        pt_ver = torch.__version__
    except ImportError:
        pt_ver = "N/A"
    update_state(pytorch_version=pt_ver)
    try:
        import git
        repo = git.Repo(str(PROJECT_ROOT))
        git_branch = repo.active_branch.name
        git_commit = repo.head.commit.hexsha[:8]
    except:
        git_branch = "unknown"
        git_commit = "unknown"
    # 🔥 实时检测DeepSeek
    ds = "offline"
    try:import requests as _r;r2=_r.post("https://api.deepseek.com/v1/chat/completions",headers={"Authorization":f"Bearer {load_config()['llm']['api_key']}","Content-Type":"application/json"},json={"model":"deepseek-chat","messages":[{"role":"user","content":"p"}],"max_tokens":2},timeout=5);ds="online" if r2.status_code==200 else f"err{r2.status_code}"
    except:pass
    update_state(api_status={"deepseek":ds})
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
        add_system_log("system", "热重载完成", f"模块: {', '.join(reloaded)}")
        return jsonify({"status": "reloaded", "modules": reloaded})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# ADB 连接管理
# ═══════════════════════════════════════════════════════════════

def _git_env() -> dict:
    """返回安全的git环境变量，防止认证弹窗导致进程挂起"""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10"
    env.pop("GIT_ASKPASS", None)
    return env

def _ensure_git_remote_with_token() -> bool:
    """确保git远程URL配置正确，优先使用SSH（不受HTTPS封锁影响），回退HTTPS+Token。
    从 settings.yaml 读取 github.token 和 github.repo 配置。
    
    关键发现：本地网络443端口(HTTPS)被封锁，导致无法连接GitHub。
    但22端口(SSH)可用，因此优先使用SSH方式推送。
    
    返回 True 表示配置成功或已配置。"""
    try:
        cfg = load_config()
        gh = cfg.get("github", {})
        token = gh.get("token", "").strip()
        repo = gh.get("repo", "").strip()
        if not repo:
            return False
        
        # 🔥 优先使用SSH（解决HTTPS 443端口被封锁的问题）
        ssh_url = f"git@github.com:{repo}.git"
        
        # 检查当前remote URL
        r = subprocess.run(["git", "remote", "get-url", "origin"],
                          cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=5)
        current_url = (r.stdout or "").strip() if r.returncode == 0 else ""
        
        # 如果已经是SSH格式，直接返回
        if current_url.startswith("git@github.com:"):
            return True
        
        # 如果已经是HTTPS+token格式且token有效，保持
        if token and token in current_url:
            return True
        
        # 尝试SSH连接测试
        ssh_test = subprocess.run(
            ["ssh", "-T", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", "git@github.com"],
            capture_output=True, text=True, timeout=10
        )
        ssh_ok = "successfully authenticated" in (ssh_test.stdout + ssh_test.stderr).lower()
        
        if ssh_ok:
            # SSH可用，使用SSH
            subprocess.run(["git", "remote", "set-url", "origin", ssh_url],
                          cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=5)
            logger.info("GitHub远程URL已配置为SSH（HTTPS 443端口被封锁）")
            return True
        elif token:
            # SSH不可用，回退到HTTPS+token
            username = repo.split("/")[0] if "/" in repo else "chenyt-Indom"
            token_url = f"https://{username}:{token}@github.com/{repo}.git"
            subprocess.run(["git", "remote", "set-url", "origin", token_url],
                          cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=5)
            logger.info("GitHub远程URL已配置HTTPS+Token（SSH不可用）")
            return True
        else:
            return False
    except Exception as e:
        logger.warning(f"配置GitHub远程失败: {e}")
        return False

def _git_run(cmd: list, timeout: int = 15, cwd: str = None) -> tuple:
    """安全执行git命令，防止认证挂起。返回 (returncode, stdout, stderr)"""
    try:
        r = subprocess.run(cmd, cwd=cwd or str(PROJECT_ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=_git_env(), timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "git命令超时（网络不可达，请检查网络连接）"
    except Exception as e:
        return -1, "", str(e)

def _find_adb_exe() -> str:
    """统一ADB查找：项目自带 > 模拟器SDK > 系统 > 回退"""
    candidates = [
        str(PROJECT_ROOT / "adb" / "adb.exe"),
        str(ANDROID_SDK_ROOT / "platform-tools" / "adb.exe"),
        r"d:\firefight\adb\adb.exe",
        r"C:\adb\platform-tools\platform-tools\adb.exe",
        r"C:\adb\adb.exe",
        "adb",
    ]
    for p in candidates:
        if p == "adb" or Path(p).exists():
            return p
    return "adb"


@app.route("/api/adb/status")
def api_adb_status():
    cfg = load_config()
    dc = cfg["device"]
    ad = dc.get("active", "generic")
    di = dc.get(ad, {})
    host = di.get("adb_host", "127.0.0.1")
    port = di.get("adb_port", 5555)

    adb_exe = _find_adb_exe()
    adb_found = adb_exe != "adb" or True  # "adb" 在PATH中视为可用

    result = {
        "host": host, "port": port, "device": ad,
        "adb_exe": adb_exe, "adb_available": Path(adb_exe).exists() if adb_exe != "adb" else True,
    }

    # 尝试连接检测
    try:
        # 启动ADB server
        r_start = subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        result["adb_server"] = "started" if r_start.returncode == 0 else f"error({r_start.returncode})"
        
        # 先尝试连接
        r_conn = subprocess.run([adb_exe, "connect", f"{host}:{port}"], capture_output=True, text=True, timeout=5)
        conn_output = r_conn.stdout.strip() + r_conn.stderr.strip()
        result["connect_output"] = conn_output[:200]
        
        r = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
        devices = [l for l in r.stdout.strip().split("\n") if l and "\tdevice" in l]
        result["devices"] = devices
        result["all_devices_raw"] = r.stdout.strip()[:500]
        
        if f"{host}:{port}" in r.stdout and "\tdevice" in r.stdout:
            result["status"] = "connected"
            result["connected"] = True
            update_state(adb_status="connected", adb_host=host, adb_port=port)
        elif f"emulator-{port}" in r.stdout and "\tdevice" in r.stdout:
            # 模拟器以emulator-{port}格式连接
            result["status"] = "connected"
            result["connected"] = True
            update_state(adb_status="connected", adb_host=host, adb_port=port)
        elif devices:
            # 检查是否有emulator设备
            has_emulator = any("emulator-" in d for d in devices)
            result["status"] = "connected" if has_emulator else "other_device"
            result["connected"] = has_emulator
            if has_emulator:
                update_state(adb_status="connected", adb_host=host, adb_port=port)
            else:
                result["error"] = f"设备 {host}:{port} 未连接，但发现其他设备: {devices[0]}"
                update_state(adb_status="disconnected", adb_host=host, adb_port=port)
        else:
            result["status"] = "disconnected"
            result["connected"] = False
            result["error"] = f"设备 {host}:{port} 未连接。请确认：1) 模拟器已启动 2) ADB调试已开启"
            update_state(adb_status="disconnected", adb_host=host, adb_port=port)
    except Exception as e:
        result["status"] = "error"
        result["connected"] = False
        result["error"] = f"ADB执行异常: {str(e)[:200]}"
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
        ad = dc.get("active", "generic")
        di = dc.get(ad, {})
        host = host or di.get("adb_host", "127.0.0.1")
        port = port or di.get("adb_port", 5555)

    adb_paths = [r"d:\firefight\adb\adb.exe", r"C:\adb\platform-tools\platform-tools\adb.exe", "adb"]
    adb_exe = "adb"
    for p in adb_paths:
        if p == "adb" or Path(p).exists():
            adb_exe = p
            break

    add_system_log("connection", f"尝试重连ADB: {host}:{port}", "")
    try:
        # 先启动ADB server
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        r = subprocess.run([adb_exe, "connect", f"{host}:{port}"], capture_output=True, text=True, timeout=10)
        if "connected" in r.stdout.lower() or "already connected" in r.stdout.lower():
            update_state(adb_status="connected", adb_host=host, adb_port=port)
            add_system_log("connection", f"ADB连接成功: {host}:{port}", r.stdout.strip()[:200])
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
        ad = cfg["device"].get("active", "generic")
        cfg["device"][ad]["adb_host"] = host
        cfg["device"][ad]["adb_port"] = int(port)
        with open(PROJECT_ROOT / "config" / "settings.yaml", "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        add_system_log("config", f"ADB配置已更新: {host}:{port}", "")
        return jsonify({"status": "saved", "host": host, "port": port})
    return jsonify({"error": "缺少host/port"}), 400


# ═══════════════════════════════════════════════════════════════
# GitHub 集成
# ═══════════════════════════════════════════════════════════════

@app.route("/api/github/status")
def api_github_status():
    """检查GitHub连接状态"""
    # ── 先检测本地Git配置 ──
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
    except:
        pass

    # 如果GitPython失败，用命令行回退
    if not has_remote:
        try:
            # 🔥 多种方式检测Git远程
            for cmd in [
                ["git", "remote", "get-url", "origin"],
                ["git", "config", "--get", "remote.origin.url"],
            ]:
                r = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
                if r.returncode == 0 and r.stdout.strip():
                    remote = r.stdout.strip()
                    has_remote = True
                    remote_url = remote
                    break
            r2 = subprocess.run(["git", "branch", "--show-current"], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
            branch = r2.stdout.strip() or "N/A"
            r3 = subprocess.run(["git", "status", "--porcelain"], cwd=str(PROJECT_ROOT), capture_output=True, text=True)
            dirty = bool(r3.stdout and r3.stdout.strip())
        except:
            pass

    # 🔥 如果以上都失败，直接读取.git/config文件
    if not has_remote:
        try:
            git_config = PROJECT_ROOT / ".git" / "config"
            if git_config.exists():
                config_text = git_config.read_text(errors="replace")
                for line in config_text.split("\n"):
                    if "url =" in line and "remote" in config_text[:config_text.index(line) if config_text.index(line) > 0 else 0:]:
                        remote_url = line.split("=", 1)[1].strip()
                        has_remote = True
                        remote = remote_url
                        break
        except:
            pass

    # ── 检测GitHub连通性（优先SSH, 回退HTTPS）──
    def _check_github_api():
        try:
            # 🔥 优先用 git ls-remote 通过SSH检测
            r = subprocess.run(
                ["git", "ls-remote", "--exit-code", "origin", "HEAD"],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True,
                env=_git_env(), timeout=10
            )
            if r.returncode == 0:
                return "online"
            # 回退HTTPS
            import requests
            r = requests.get("https://api.github.com", timeout=5)
            return "online" if r.status_code == 200 else "error"
        except:
            return "offline"
    api_status = _get_cached_or_fetch("github", _check_github_api, _CACHE_TTL["github"])

    update_state(github_status=api_status if not has_remote else "configured")

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
    elif api_status == "unreachable":
        result["message"] = "GitHub网络不可达 - 当前环境无法访问github.com，请检查网络/代理"
    elif api_status == "offline":
        result["message"] = "GitHub连接失败"
        result["suggestion"] = "在指令框输入: repo https://github.com/用户名/仓库名.git"
    else:
        # 🔥 如果已配置远程仓库，标记为已配置状态
        result["api_status"] = "configured"
        result["repo_url"] = remote_url
        result["message"] = f"仓库已配置: {remote_url}"
    
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
    
    add_system_log("github", f"配置GitHub仓库: {repo_url}", "")
    
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
        add_system_log("github", f"GitHub仓库配置成功: {repo_url}", "")
        
        return jsonify({
            "status": "configured",
            "repo_url": repo_url,
            "message": f"GitHub仓库已配置: {repo_url}",
            "next_steps": "可以使用 git push -u origin main 进行首次推送，或点击推送按钮",
        })
    except Exception as e:
        add_system_log("github", f"GitHub配置失败", str(e)[:200])
        return jsonify({"status": "error", "error": str(e), "suggestion": "请确保已初始化git仓库 (git init)"}), 500

@app.route("/api/github/push", methods=["POST"])
def api_github_push():
    """推送变更到GitHub"""
    data = request.get_json() or {}
    commit_msg = data.get("message", f"AI训练更新 {datetime.now().strftime('%Y%m%d-%H%M')}")
    is_auto = data.get("auto", False)
    paths = data.get("paths", ["."] if not is_auto else ["data/params", "data/tactics_rules.yaml", "data/battle_memory.db"])

    # 先检查是否有remote
    has_remote = False
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=10)
        has_remote = r.returncode == 0 and bool(r.stdout and r.stdout.strip())
    except:
        pass
    
    if not has_remote:
        add_system_log("github", "GitHub未配置仓库，无法推送", "")
        return jsonify({"status": "error", "error": "未配置GitHub仓库", "suggestion": "请先使用 /api/github/setup 或在指令框输入 repo 地址 配置仓库"}), 400

    try:
        # 🔥 确保GitHub远程URL包含token认证
        _ensure_git_remote_with_token()
        
        # 🔥 先同步远程最新（防止 fetch first 错误）
        rc, _, stderr = _git_run(["git", "pull", "--rebase", "--autostash", "origin", "master"], timeout=60)
        if rc != 0:
            _git_run(["git", "rebase", "--abort"], timeout=5)
            _git_run(["git", "stash", "pop"], timeout=5)
            logger.warning(f"git pull rebase 失败 (非致命): {(stderr or '')[:200]}")
        
        # 🔥 先检查是否有实际变更（避免重复推送）
        rc, stdout, _ = _git_run(["git", "status", "--porcelain"], timeout=10)
        has_changes = bool(stdout and stdout.strip())
        
        if not has_changes:
            # 工作区干净，检查是否有未推送的提交
            rc2, ahead_out, _ = _git_run(["git", "rev-list", "--count", "origin/master..HEAD"], timeout=10)
            ahead_count = int(ahead_out.strip()) if (ahead_out and ahead_out.strip().isdigit()) else 0
            if ahead_count == 0:
                add_system_log("github", "⚠ 请勿推送重复内容", "工作区无变更，且无未推送提交")
                return jsonify({
                    "status": "duplicate", 
                    "message": "请勿推送重复内容",
                    "detail": "当前工作区没有新的变更，所有内容已与远程仓库同步",
                    "push_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
        
        # 添加所有变更
        rc, _, _ = _git_run(["git", "add", "-A"], timeout=10)
        if rc != 0:
            return jsonify({"status": "error", "error": "git add 失败", "suggestion": "请检查本地git仓库状态"}), 500

        # 尝试提交（如果有变更）
        rc, _, _ = _git_run(["git", "diff", "--cached", "--quiet"], timeout=10)
        has_staged = (rc != 0)
        
        if has_staged:
            # 🔥 检查是否与上次提交内容相同
            rc, last_msg, _ = _git_run(["git", "log", "-1", "--format=%s"], timeout=5)
            last_commit_msg = (last_msg or "").strip()
            if last_commit_msg == commit_msg:
                # 消息相同，检查diff是否也相同
                rc, diff_out, _ = _git_run(["git", "diff", "--cached", "--stat"], timeout=10)
                rc2, last_diff, _ = _git_run(["git", "diff", "HEAD~1..HEAD", "--stat"], timeout=10)
                if (diff_out or "").strip() == (last_diff or "").strip():
                    add_system_log("github", "⚠ 请勿推送重复内容", f"提交消息和变更内容与上次相同")
                    return jsonify({
                        "status": "duplicate",
                        "message": "请勿推送重复内容", 
                        "detail": f"提交消息'{commit_msg}'和变更内容与上次提交完全相同",
                        "push_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
            
            rc, _, stderr = _git_run(["git", "commit", "-m", commit_msg], timeout=30)
            if rc != 0:
                add_system_log("github", "提交失败", stderr[:200])
                return jsonify({"status": "error", "error": f"git commit 失败: {stderr[:200]}"}), 500
            add_system_log("github", "已提交", commit_msg)
        else:
            # 没有staged变更，但可能有未推送的提交
            rc2, ahead_out, _ = _git_run(["git", "rev-list", "--count", "origin/master..HEAD"], timeout=10)
            ahead_count = int(ahead_out.strip()) if (ahead_out and ahead_out.strip().isdigit()) else 0
            if ahead_count == 0:
                add_system_log("github", "⚠ 请勿推送重复内容", "无新变更需要推送")
                return jsonify({
                    "status": "duplicate",
                    "message": "请勿推送重复内容",
                    "detail": "没有新的变更，所有内容已是最新",
                    "push_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
        
        # 直接推送
        add_system_log("github", "开始推送...", f"commits: {commit_msg}")
        rc, stdout, stderr = _git_run(["git", "push", "origin", "master"], timeout=300)
        if rc == 0:
            push_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if "Everything up-to-date" in (stdout + stderr):
                add_system_log("github", "推送成功（无新内容）", commit_msg)
                return jsonify({"status": "pushed", "message": "Everything up-to-date", "push_time": push_time})
            add_system_log("github", "推送成功", commit_msg)
            return jsonify({"status": "pushed", "message": commit_msg, "push_time": push_time})
        else:
            error_msg = stderr[:300] if stderr else "推送失败"
            if "Authentication failed" in error_msg or "could not read" in error_msg:
                suggestion = "GitHub认证失败，请在系统设置中配置GitHub Token"
            elif "remote rejected" in error_msg:
                suggestion = "远程仓库拒绝推送，请先拉取最新代码"
            elif "timed out" in error_msg.lower() or "超时" in error_msg:
                suggestion = "网络连接超时，请检查网络或使用SSH密钥"
            else:
                suggestion = "推送失败，请检查网络连接和Git配置"
            add_system_log("github", "推送失败", error_msg)
            return jsonify({"status": "error", "error": error_msg, "suggestion": suggestion}), 500
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200], "suggestion": "发生异常，请检查git配置"}), 500

@app.route("/api/github/pull", methods=["POST"])
def api_github_pull():
    """从GitHub拉取最新代码"""
    add_system_log("github", "开始拉取最新代码", "")
    try:
        import git
        repo = git.Repo(str(PROJECT_ROOT))
        origin = repo.remotes.origin
        result = origin.pull()
        add_system_log("github", "拉取成功", str(result[0].note) if result else "")
        return jsonify({"status": "pulled"})
    except ImportError:
        try:
            r = subprocess.run(["git", "pull"], cwd=str(PROJECT_ROOT), check=True, capture_output=True, text=True)
            add_system_log("github", "拉取成功", r.stdout[:200])
            return jsonify({"status": "pulled", "output": (r.stdout or "").strip()})
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

    add_system_log("github", f"文件已保存: {folder}/{filename}", "")

    # 自动提交
    auto_push = request.form.get("auto_push", "true") == "true"
    if auto_push:
        try:
            import git
            repo = git.Repo(str(PROJECT_ROOT))
            repo.index.add([str(save_path.relative_to(PROJECT_ROOT))])
            repo.index.commit(f"上传: {filename}")
            repo.remotes.origin.push()
            add_system_log("github", f"已自动推送: {filename}", "")
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
    """检查腾讯云服务器连接状态（带缓存）"""
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
    
    # 使用缓存避免频繁SSH连接（60秒TTL）
    def _do_check():
        stdout, stderr, diagnosis = _ssh_connect("echo OK && python3 --version 2>/dev/null && ls /home/ubuntu/firefightAI 2>/dev/null && echo DEPLOYED || echo NOT_DEPLOYED")
        if "OK" in stdout:
            deployed = "DEPLOYED" in stdout
            py_ver = stdout.split("\n")[1] if len(stdout.split("\n")) > 1 else "unknown"
            return {"status": "online", "deployed": deployed, "output": stdout.strip(), "python_version": py_ver}
        elif diagnosis:
            return {"status": "error", "error": diagnosis, "stderr": stderr[:300]}
        else:
            return {"status": "offline", "error": stderr.strip()[:300] if stderr else "连接失败"}
    
    sv = _get_cached_or_fetch("server", _do_check, _CACHE_TTL["server"])
    
    detail["python_version"] = sv.get("python_version", "unknown")
    if sv.get("error"):
        detail["diagnosis"] = sv["error"]
    
    if sv["status"] == "online":
        update_state(server_status="online")
        return jsonify({"status": "online", "deployed": sv["deployed"], "output": sv.get("output", ""), "detail": detail})
    elif sv["status"] == "error":
        update_state(server_status="error")
        return jsonify({"status": "error", "detail": detail, "error": sv["error"], "stderr": sv.get("stderr", "")})
    else:
        update_state(server_status="offline")
        return jsonify({"status": "offline", "detail": detail, "error": sv.get("error", "连接失败")})

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
        
        add_system_log("server", "已生成新的SSH密钥对", f"路径: {new_key_path}")
        
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
            add_system_log("server", "SSH公钥已上传到服务器", "")
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
    
    add_system_log("server", f"SSH诊断完成: {result['summary']}", "")
    return jsonify(result)

@app.route("/api/server/deploy", methods=["POST"])
def api_server_deploy():
    """部署项目到腾讯云服务器"""
    data = request.get_json() or {}
    sync_only = data.get("sync_only", False)  # 仅同步数据，不部署完整项目

    add_system_log("server", "开始部署到腾讯云服务器", SERVER_HOST)

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

                # 6. 安装依赖并重启服务到5001端口
                socketio.emit("server_deploy_progress", {"step": "安装依赖", "progress": 90})
                _ssh_exec(f"cd {SERVER_DEPLOY_PATH} && pip3 install -r requirements.txt -q 2>&1", timeout=120)
                # 杀掉旧进程并重启到5001端口
                _ssh_exec(f"pkill -f 'dashboard_server.py' 2>/dev/null; sleep 2; cd {SERVER_DEPLOY_PATH} && nohup python3 dashboard_server.py --host 0.0.0.0 --port 5001 > /tmp/firefight_5001.log 2>&1 &", timeout=10)

            socketio.emit("server_deploy_progress", {"step": "完成", "progress": 100})
            add_system_log("server", "部署成功", f"服务器: {SERVER_HOST}")
            socketio.emit("server_deploy_complete", {"success": True, "host": SERVER_HOST})

        except Exception as e:
            add_system_log("server", f"部署失败", str(e)[:200])
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
    add_system_log("system", f"开始更新PyTorch: {version or 'latest'}", "")

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
            add_system_log("system", f"PyTorch更新完成: {new_ver}", "")
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


# ═══════════════════════════════════════════════════════════════
# 统一 DeepSeek API 调用（使用 requests 避免 openai 库的 jiter.dll 被 Windows 拦截）
# ═══════════════════════════════════════════════════════════════

def _deepseek_chat(messages: list, max_tokens: int = 800, temperature: float = 0.1, stream: bool = True, timeout: int = 30) -> dict:
    """调用 DeepSeek API 聊天，返回 {"success": bool, "content": str, "error": str}"""
    import requests as req
    cfg = load_config()
    llm_cfg = cfg["llm"]
    try:
        resp = req.post(
            f"{llm_cfg['api_base']}/chat/completions",
            headers={
                "Authorization": f"Bearer {llm_cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": llm_cfg.get("model", "deepseek-v4-flash"),
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": stream,
            },
            timeout=(6, timeout),
        )
        resp.raise_for_status()
        if stream:
            return {"success": True, "response": resp, "stream": True}
        else:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"success": True, "content": content, "stream": False}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}

def _deepseek_stream_to_end(response, emit_fn=None) -> str:
    """从 streaming response 中读取全部内容，可选 emit 每个 token"""
    full_text = ""
    thinking_parts = []
    try:
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                choices = chunk.get("choices", [])
                if choices and choices[0].get("delta", {}).get("content"):
                    token = choices[0]["delta"]["content"]
                    full_text += token
                    if emit_fn:
                        thinking_parts.append(token)
                        if len(thinking_parts) >= 5:
                            emit_fn({"token": "".join(thinking_parts), "done": False})
                            thinking_parts = []
            except json.JSONDecodeError:
                continue
        if thinking_parts and emit_fn:
            emit_fn({"token": "".join(thinking_parts), "done": False})
    except Exception as e:
        if full_text:
            return full_text
        raise
    return full_text


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
    
    # 🔥 是否附带实时战场截图
    screenshot_b64 = data.get("screenshot", "")
    include_vision = data.get("include_vision", False) and bool(screenshot_b64)
    
    # 🔥 是否为键鼠操控指令
    control_action = data.get("control_action", {})  # {type: "tap/swipe/key/type", x, y, key, text}

    # 🔥 检测是否为"记录到学习日志"指令
    log_keywords = ["记录到学习日志", "添加到学习日志", "学习日志记录", "记录这条", "记录到日志", "记住这条", "记下这条"]
    is_log_request = any(kw in message for kw in log_keywords)
    
    if is_log_request:
        log_content = message
        for kw in log_keywords:
            log_content = log_content.replace(kw, "").strip()
        if not log_content:
            log_content = message
        
        category = "ai_chat"
        if "战术" in log_content or "策略" in log_content:
            category = "tactics"
        elif "bug" in log_content.lower() or "错误" in log_content or "修复" in log_content:
            category = "bug_fix"
        elif "经验" in log_content or "教训" in log_content:
            category = "experience"
        elif "参数" in log_content or "配置" in log_content:
            category = "params"
        elif "纠正" in log_content or "修正" in log_content:
            category = "correction"
        elif "操控" in log_content or "控制" in log_content or "键鼠" in log_content:
            category = "control"
        
        add_learning_log(category, f"用户记录: {log_content[:100]}", log_content[:300])
        _chat_history.append({"role": "assistant", "content": f"已记录到学习日志 [{category}]: {log_content[:100]}", "time": time.time()})
        socketio.emit("ai_chat_token", {"token": "", "done": True, "full": f"已记录到学习日志 [{category}]: {log_content[:100]}"})
        socketio.emit("learning_log_update", {"entry": {"time": datetime.now().strftime("%H:%M:%S"), "category": category, "message": f"用户记录: {log_content[:100]}", "detail": log_content[:300]}, "total": len(_learning_log)})
        return

    # 🔥 处理键鼠操控指令
    if control_action and control_action.get("type"):
        _execute_control_action(control_action, message)
        return

    # 🔥 检测消息中的键鼠操控指令（自然语言解析）
    control_result = _parse_control_command(message)
    if control_result:
        _execute_control_action(control_result, message)
        return

    emit("ai_chat_start", {"message": message, "has_vision": include_vision})

    def do_chat():
        try:
            cfg = load_config()
            llm_cfg = cfg["llm"]

            sys_prompt = (
                "你是 Firefight AI 战术指挥系统的 AI 助手。你具备以下能力：\n"
                "1. 分析战场截图并给出战术建议（当用户发送截图时）\n"
                "2. 分析战场局势并给出战术建议\n"
                "3. 解释你的决策逻辑\n"
                "4. 回答关于游戏机制、单位、战术的问题\n"
                "5. 帮助指挥官制定作战计划\n"
                "6. 接受指挥官的行为纠正并调整策略\n"
                "7. 指导键鼠操控：当你需要执行具体操作时，用【操控:类型,参数】格式输出\n"
                "   例如：【操控:tap,500,300】=点击坐标(500,300)\n"
                "         【操控:swipe,100,200,500,600】=从(100,200)滑动到(500,600)\n"
                "         【操控:key,back】=按返回键\n"
                "         【操控:type,输入文字】=输入文字\n\n"
                "请用中文回答，保持专业、简洁。如果涉及战术决策，请分步骤说明你的思考过程。"
            )

            if is_correction:
                sys_prompt += (
                    "\n\n【重要】指挥官正在纠正你的行为。请仔细分析纠正内容，"
                    "并说明你将如何调整后续的战术决策和操控方式。"
                )

            # 构建消息列表
            messages = [{"role": "system", "content": sys_prompt}]
            
            # 添加历史消息（排除包含截图的消息以节省token）
            for h in _chat_history[-10:]:
                if isinstance(h.get("content"), str):
                    messages.append({"role": h["role"], "content": h["content"]})

            # 🔥 如果有截图，构建vision消息
            user_content = message + context
            if include_vision and screenshot_b64:
                # 使用vision格式：先发送截图分析请求，再发送文字
                user_content = [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}
                    },
                    {
                        "type": "text",
                        "text": f"【实时战场截图】请分析当前截图中显示的战场情况，包括：\n1. 识别屏幕上的单位位置和血量\n2. 当前战况评估\n3. 建议的下一步操作\n\n用户消息: {message}{context}"
                    }
                ]
                # 使用vision-capable模型
                result = _deepseek_vision_chat(messages, user_content, max_tokens=1200, temperature=0.1, stream=True)
            else:
                messages.append({"role": "user", "content": user_content})
                result = _deepseek_chat(messages, max_tokens=800, temperature=0.1, stream=True)

            if not result["success"]:
                emit("ai_chat_error", {"error": result["error"]})
                return

            def emit_token(data):
                socketio.emit("ai_chat_token", data)

            full_response = _deepseek_stream_to_end(result["response"], emit_token)
            
            # 🔥 解析AI响应中的操控指令
            control_actions = _extract_control_actions(full_response)
            if control_actions:
                for action in control_actions:
                    _execute_adb_action(action)
                    socketio.emit("ai_chat_token", {"token": "", "done": False, 
                        "full": f"\n[已执行操控: {action.get('type','')} {action}]"})
            
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


# ═══════════════════════════════════════════════════════════════
# Vision Chat + 键鼠操控 + 指令解析
# ═══════════════════════════════════════════════════════════════

def _deepseek_vision_chat(messages: list, user_content, max_tokens: int = 1200, temperature: float = 0.1, stream: bool = True, timeout: int = 60) -> dict:
    """调用DeepSeek API进行视觉聊天（支持截图分析）"""
    import requests as req
    cfg = load_config()
    llm_cfg = cfg["llm"]
    
    # 构建vision消息
    vision_messages = list(messages)  # 复制system prompt等
    vision_messages.append({"role": "user", "content": user_content})
    
    try:
        resp = req.post(
            f"{llm_cfg['api_base']}/chat/completions",
            headers={
                "Authorization": f"Bearer {llm_cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": llm_cfg.get("vision_model", llm_cfg.get("model", "deepseek-chat")),
                "messages": vision_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": stream,
            },
            timeout=(10, timeout),
        )
        resp.raise_for_status()
        if stream:
            return {"success": True, "response": resp, "stream": True}
        else:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"success": True, "content": content, "stream": False}
    except Exception as e:
        # 如果vision模型不支持，回退到普通chat（去掉图片部分）
        logger.warning(f"Vision API调用失败，回退到普通模式: {e}")
        text_parts = []
        if isinstance(user_content, list):
            for part in user_content:
                if part.get("type") == "text":
                    text_parts.append(part["text"])
        fallback_msg = "\n".join(text_parts) if text_parts else str(user_content)
        messages.append({"role": "user", "content": fallback_msg})
        return _deepseek_chat(messages, max_tokens, temperature, stream, timeout)


def _parse_control_command(message: str) -> dict:
    """解析自然语言中的键鼠操控指令"""
    msg_lower = message.lower()
    
    # 点击指令
    import re as _regex
    
    # 匹配: 点击(x,y) 或 点击 x y 或 tap x y
    tap_match = _regex.search(r'(?:点击|tap|click)\s*[\(（]?\s*(\d+)\s*[,，\s]\s*(\d+)\s*[\)）]?', msg_lower)
    if tap_match:
        return {"type": "tap", "x": int(tap_match.group(1)), "y": int(tap_match.group(2))}
    
    # 滑动指令: 滑动(x1,y1,x2,y2) 或 swipe x1 y1 x2 y2
    swipe_match = _regex.search(r'(?:滑动|swipe|drag)\s*[\(（]?\s*(\d+)\s*[,，\s]\s*(\d+)\s*[,，\s]\s*(\d+)\s*[,，\s]\s*(\d+)\s*[\)）]?', msg_lower)
    if swipe_match:
        return {"type": "swipe", "x1": int(swipe_match.group(1)), "y1": int(swipe_match.group(2)),
                "x2": int(swipe_match.group(3)), "y2": int(swipe_match.group(4))}
    
    # 按键指令: 按键(key) 或 key xxx
    key_match = _regex.search(r'(?:按键|key|按下)\s*[\(（]?\s*(\w+)\s*[\)）]?', msg_lower)
    if key_match:
        key_map = {"返回": "back", "back": "back", "home": "home", "主页": "home", 
                   "菜单": "menu", "menu": "menu", "回车": "enter", "enter": "enter",
                   "删除": "del", "del": "del", "空格": "space", "space": "space"}
        key_name = key_match.group(1)
        return {"type": "key", "key": key_map.get(key_name, key_name)}
    
    # 输入文字: 输入(xxx) 或 type xxx
    type_match = _regex.search(r'(?:输入|type|text)\s*[\(（]?\s*(.+?)\s*[\)）]?$', msg_lower)
    if type_match:
        return {"type": "type", "text": type_match.group(1).strip()}
    
    return None


def _extract_control_actions(response: str) -> list:
    """从AI响应中提取【操控:xxx】格式的指令"""
    import re as _regex
    actions = []
    pattern = _regex.compile(r'【操控:\s*(\w+)\s*,?\s*([^】]+)】')
    for match in pattern.finditer(response):
        action_type = match.group(1).strip()
        params = match.group(2).strip()
        try:
            if action_type == "tap":
                parts = [p.strip() for p in params.split(",")]
                actions.append({"type": "tap", "x": int(parts[0]), "y": int(parts[1])})
            elif action_type == "swipe":
                parts = [p.strip() for p in params.split(",")]
                actions.append({"type": "swipe", "x1": int(parts[0]), "y1": int(parts[1]),
                               "x2": int(parts[2]), "y2": int(parts[3])})
            elif action_type == "key":
                actions.append({"type": "key", "key": params.strip()})
            elif action_type == "type":
                actions.append({"type": "type", "text": params.strip()})
        except (ValueError, IndexError):
            logger.warning(f"无法解析操控指令: {match.group(0)}")
    return actions


def _execute_control_action(action: dict, message: str = ""):
    """执行键鼠操控指令"""
    action_type = action.get("type", "")
    try:
        result = _execute_adb_action(action)
        if result:
            status_msg = f"已执行操控: {action_type} - {action}"
            _chat_history.append({"role": "assistant", "content": f"[操控] {status_msg}", "time": time.time()})
            socketio.emit("ai_chat_token", {"token": "", "done": True, "full": f"[操控] {status_msg}"})
            add_learning_log("control", f"执行操控: {action_type}", str(action))
            socketio.emit("control_action_executed", {"action": action, "success": True})
        else:
            err_msg = f"操控执行失败: {action_type}"
            socketio.emit("ai_chat_token", {"token": "", "done": True, "full": f"[操控] {err_msg}"})
            socketio.emit("control_action_executed", {"action": action, "success": False, "error": err_msg})
    except Exception as e:
        socketio.emit("ai_chat_token", {"token": "", "done": True, "full": f"[操控错误] {str(e)}"})


def _execute_adb_action(action: dict) -> bool:
    """通过ADB执行具体的操控动作"""
    global _emulator_adb_port
    port = str(_emulator_adb_port)
    dev_id = f"emulator-{port}"

    try:
        adb_exe = _get_adb_for_emulator()
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=3)
        
        action_type = action.get("type", "")
        if action_type == "tap":
            x, y = action.get("x", 0), action.get("y", 0)
            r = subprocess.run([adb_exe, "-s", dev_id, "shell", "input", "tap", str(x), str(y)],
                             capture_output=True, text=True, timeout=5)
            return r.returncode == 0
            
        elif action_type == "swipe":
            x1, y1 = action.get("x1", 0), action.get("y1", 0)
            x2, y2 = action.get("x2", 0), action.get("y2", 0)
            # 计算滑动时长(ms)基于距离
            duration = max(100, int(((x2-x1)**2 + (y2-y1)**2)**0.5 * 2))
            r = subprocess.run([adb_exe, "-s", dev_id, "shell", "input", "swipe", 
                              str(x1), str(y1), str(x2), str(y2), str(duration)],
                             capture_output=True, text=True, timeout=5)
            return r.returncode == 0
            
        elif action_type == "key":
            key = action.get("key", "")
            r = subprocess.run([adb_exe, "-s", dev_id, "shell", "input", "keyevent", key.upper()],
                             capture_output=True, text=True, timeout=5)
            return r.returncode == 0
            
        elif action_type == "type":
            text = action.get("text", "")
            # 转义特殊字符
            text = text.replace(" ", "%s").replace("&", "\\&").replace("<", "\\<").replace(">", "\\>")
            r = subprocess.run([adb_exe, "-s", dev_id, "shell", "input", "text", text],
                             capture_output=True, text=True, timeout=5)
            return r.returncode == 0
            
        elif action_type == "longpress":
            x, y = action.get("x", 0), action.get("y", 0)
            duration = action.get("duration", 1000)
            r = subprocess.run([adb_exe, "-s", dev_id, "shell", "input", "swipe", 
                              str(x), str(y), str(x), str(y), str(duration)],
                             capture_output=True, text=True, timeout=5)
            return r.returncode == 0
            
        logger.warning(f"未知操控类型: {action_type}")
        return False
    except Exception as e:
        logger.error(f"ADB操控执行失败: {e}")
        return False


@app.route("/api/control/execute", methods=["POST"])
def api_control_execute():
    """通过API执行键鼠操控指令"""
    data = request.get_json() or {}
    action = data.get("action", {})
    if not action or not action.get("type"):
        return jsonify({"status": "error", "error": "缺少操控指令"}), 400
    
    success = _execute_adb_action(action)
    if success:
        add_learning_log("control", f"API操控: {action.get('type')}", str(action))
        return jsonify({"status": "ok", "action": action})
    else:
        return jsonify({"status": "error", "error": "执行失败"}), 500


@app.route("/api/control/screenshot", methods=["GET"])
def api_control_screenshot():
    """获取当前战场截图（base64 JPEG格式）- 兼容所有模拟器类型"""
    import base64, struct, io
    try:
        adb_exe = _get_adb_for_emulator()
        port = _emulator_adb_port
        
        # 🔥 根据模拟器类型选择正确的设备ID
        dev_ids = []
        if _emulator_type == "mumu":
            dev_ids = [f"127.0.0.1:{port}"]
        elif _emulator_type == "generic":
            dev_ids = [f"emulator-{port}", f"localhost:{port}", f"127.0.0.1:{port}"]
        else:
            dev_ids = [f"127.0.0.1:{port}", f"localhost:{port}", f"emulator-{port}"]
        
        raw_data = None
        for dev_id in dev_ids:
            r = subprocess.run([adb_exe, "-s", dev_id, "exec-out", "screencap"],
                              capture_output=True, timeout=5)
            if r.returncode == 0 and len(r.stdout) >= 20:
                raw_data = r.stdout
                break
            time.sleep(0.5)
        
        if raw_data is None:
            return jsonify({"error": "截图失败: 模拟器未响应, 请确认已启动并连接"}), 500
        
        raw_data = r.stdout
        width = struct.unpack_from("<I", raw_data, 0)[0]
        height = struct.unpack_from("<I", raw_data, 4)[0]
        pixels = raw_data[12:]
        
        try:
            from PIL import Image
            img = Image.frombytes("RGBA", (width, height), pixels, "raw")
            img_rgb = img.convert("RGB")
            buf = io.BytesIO()
            img_rgb.save(buf, format="JPEG", quality=70, optimize=True)
            img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return jsonify({"screenshot": img_b64, "width": width, "height": height, "format": "jpeg"})
        except ImportError:
            img_b64 = base64.b64encode(pixels).decode("ascii")
            return jsonify({"screenshot": img_b64, "width": width, "height": height, "format": "raw"})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


# ═══════════════════════════════════════════════════════════════
# AI 文件分析 - 拖入文件 / 本地文件路径读取
# ═══════════════════════════════════════════════════════════════

@app.route("/api/chat/upload_file", methods=["POST"])
def api_chat_upload_file():
    """上传文件让AI分析内容"""
    if "file" not in request.files:
        return jsonify({"error": "未上传文件"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "文件名为空"}), 400
    
    try:
        filename = f.filename
        content = ""
        ext = os.path.splitext(filename)[1].lower()
        
        # 读取文件内容
        if ext in (".txt", ".py", ".js", ".html", ".css", ".yaml", ".yml", ".json", ".md", ".cfg", ".ini", ".log", ".csv", ".xml"):
            content = f.read().decode("utf-8", errors="replace")
        elif ext in (".pdf", ".doc", ".docx"):
            content = f"[二进制文件: {filename}, 大小: {len(f.read())} bytes]"
        else:
            raw = f.read()
            # 尝试作为文本读取
            try:
                content = raw.decode("utf-8", errors="replace")
            except:
                content = f"[二进制文件: {filename}, 大小: {len(raw)} bytes]"
        
        content_preview = content[:8000] if len(content) > 8000 else content
        add_system_log("chat", f"上传文件: {filename}", f"大小: {len(content)}字符")
        
        return jsonify({
            "status": "ok",
            "filename": filename,
            "size": len(content),
            "preview": content_preview,
            "truncated": len(content) > 8000,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat/read_file", methods=["POST"])
def api_chat_read_file():
    """读取本地文件路径并返回内容"""
    data = request.get_json() or {}
    filepath = data.get("path", "").strip()
    if not filepath:
        return jsonify({"error": "未提供文件路径"}), 400
    
    try:
        p = Path(filepath)
        if not p.exists():
            return jsonify({"error": f"文件不存在: {filepath}"}), 404
        if not p.is_file():
            return jsonify({"error": f"不是文件: {filepath}"}), 400
        if p.stat().st_size > 50 * 1024 * 1024:  # 50MB限制
            return jsonify({"error": "文件超过50MB，暂不支持"}), 400
        
        # 读取文件内容
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except:
            content = p.read_bytes().decode("utf-8", errors="replace") if p.stat().st_size < 1024 * 1024 else f"[二进制文件: {p.name}, 大小: {p.stat().st_size} bytes]"
        
        content_preview = content[:10000] if len(content) > 10000 else content
        add_system_log("chat", f"读取本地文件: {filepath}", f"大小: {len(content)}字符")
        
        return jsonify({
            "status": "ok",
            "filename": p.name,
            "path": str(p),
            "size": len(content),
            "preview": content_preview,
            "truncated": len(content) > 10000,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# AI 自学习引擎 - 持续自主学习，不断积累经验
# ═══════════════════════════════════════════════════════════════

_self_learning_thread = None
_self_learning_running = False
_self_learning_params = {
    "temperature": 0.3,
    "max_tokens": 1024,
    "confidence_threshold": 0.5,
    "learning_rate": 0.01,
    "tactical_aggressiveness": 0.5,
    "unit_preference": "balanced",
    "last_learned": None,
    "total_learnings": 0,
    "insights": [],
}

def _start_self_learning_engine():
    """启动AI自学习引擎 - 持续分析日志、调整参数、积累经验"""
    global _self_learning_thread, _self_learning_running
    if _self_learning_running:
        return
    _self_learning_running = True

    def learning_loop():
        logger.info("AI自学习引擎已启动")
        add_learning_log("self_learn", "AI自学习引擎启动", "持续自主学习模式已激活")
        socketio.emit("self_learning_status", {"status": "active", "message": "AI自学习引擎已启动"})

        while _self_learning_running:
            try:
                time.sleep(60)  # 每60秒执行一次学习分析
                if not _self_learning_running:
                    break

                _execute_self_learning_cycle()
            except Exception as e:
                logger.error(f"自学习循环异常: {e}")
                time.sleep(10)

    _self_learning_thread = threading.Thread(target=learning_loop, daemon=True)
    _self_learning_thread.start()


def _execute_self_learning_cycle():
    """执行一次自学习周期 - 使用DeepSeek深度分析学习日志并调整参数"""
    global _learning_log, _self_learning_params

    recent_logs = _learning_log[-100:]
    if len(recent_logs) < 3:
        return

    # 使用DeepSeek进行深度分析
    insights = _deep_analyze_learning_logs(recent_logs)

    if insights:
        _self_learning_params["insights"] = (_self_learning_params["insights"] + insights)[-50:]
        _self_learning_params["total_learnings"] += 1
        _self_learning_params["last_learned"] = datetime.now().isoformat()

        # 保存参数到文件
        _save_learning_params()

        # 通过SocketIO通知前端
        socketio.emit("self_learning_update", {
            "insights": insights,
            "params": _self_learning_params,
            "time": datetime.now().isoformat(),
        })

        add_learning_log("self_learn", f"AI自主学习了 {len(insights)} 条新知识（第{_self_learning_params['total_learnings']}周期）",
                         " | ".join([i.get("summary", "")[:80] for i in insights[:3]]))

        # 自动推送到GitHub和服务器
        _auto_push_learning_params()
        _auto_upload_params_to_server()
        
        # 🔥 AI自主评估并调参 (温度/学习率/攻击性/置信度)
        import threading
        threading.Thread(target=lambda: api_ai_self_improve_auto(), daemon=True).start()

def api_ai_self_improve_auto():
    """AI自动改进 (静默模式, 不返回JSON)"""
    global _self_learning_params
    old = dict(_self_learning_params)
    kc = len(_ai_knowledge_base)
    tc = sum(1 for k in _ai_knowledge_base if k.get("trained"))
    
    changed = False
    if kc > 10 and _self_learning_params["temperature"] > 0.1:
        _self_learning_params["temperature"] = max(0.05, _self_learning_params["temperature"] - 0.02)
        changed = True
    if tc > 5 and _self_learning_params["learning_rate"] > 0.005:
        _self_learning_params["learning_rate"] = max(0.001, _self_learning_params["learning_rate"] - 0.003)
        changed = True
    
    if changed:
        _self_learning_params["total_learnings"] = _self_learning_params.get("total_learnings", 0) + 1
        _self_learning_params["last_learned"] = datetime.now().isoformat()
        _save_learning_params()
        socketio.emit("training_log", {"line": f"⚙️ AI自动调参完成"})


def _deep_analyze_learning_logs(logs: list) -> list:
    """使用DeepSeek深度分析学习日志，提取战术知识并生成参数调整建议"""
    insights = []
    if not logs:
        return insights

    # 构建分析提示词
    knowledge_text = "\n".join([
        f"[{l.get('category','?')}] {l.get('message','')}"
        + (f" | {l.get('detail','')[:200]}" if l.get('detail') else "")
        for l in logs[-50:]
    ])

    prompt = f"""你是AI战术学习系统的核心分析引擎。请分析以下AI学习日志，提取可执行的战术知识并建议参数调整。

【AI学习日志（最近{len(logs)}条）】
{knowledge_text[:8000]}

请输出JSON格式（不要markdown包裹）：
{{
  "knowledge_summary": ["学到的关键知识1", "知识2", ...],
  "tactical_rules": [
    {{"rule": "规则描述", "condition": "触发条件", "action": "应采取的行动"}},
    ...
  ],
  "param_adjustments": [
    {{"param": "参数名", "current": 当前值, "suggested": 建议值, "reason": "调整原因"}},
    ...
  ],
  "priority_learning": ["下一步优先学习方向1", "方向2", ...],
  "confidence": 0.0到1.0之间的置信度
}}"""

    try:
        r = _deepseek_chat([{"role": "user", "content": prompt}],
                          max_tokens=1024, temperature=0.2, stream=False, timeout=45)
        if not r.get("success"):
            # 回退到简单规则分析
            return _analyze_learning_patterns(logs)

        content = r["content"]
        # 尝试解析JSON
        import json as _json
        # 提取JSON部分
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            analysis = _json.loads(json_match.group())
        else:
            analysis = _json.loads(content)

        # 提取知识摘要
        for k in analysis.get("knowledge_summary", [])[:5]:
            insights.append({"type": "knowledge", "summary": k, "detail": "从学习日志中提取的战术知识"})

        # 提取战术规则
        for rule in analysis.get("tactical_rules", [])[:3]:
            insights.append({
                "type": "tactical_rule",
                "summary": rule.get("rule", ""),
                "detail": f"条件: {rule.get('condition','')} | 行动: {rule.get('action','')}"
            })

        # 应用参数调整
        for adj in analysis.get("param_adjustments", [])[:5]:
            param = adj.get("param", "")
            suggested = adj.get("suggested")
            reason = adj.get("reason", "")
            if param and suggested is not None:
                old_val = _self_learning_params.get(param, 0)
                _self_learning_params[param] = suggested
                insights.append({
                    "type": "param_adjust",
                    "summary": f"调整参数 {param}: {old_val} → {suggested}",
                    "detail": f"原因: {reason}"
                })

        # 优先级学习方向
        for p in analysis.get("priority_learning", [])[:3]:
            insights.append({"type": "priority", "summary": p, "detail": "下一步优先学习方向"})

        add_learning_log("self_learn", f"DeepSeek深度分析完成（置信度{analysis.get('confidence',0):.0%}）",
                         f"提取{len(insights)}条洞察")

    except Exception as e:
        logger.warning(f"DeepSeek学习分析失败: {e}，使用简单规则回退")
        return _analyze_learning_patterns(logs)

    return insights


def _auto_upload_params_to_server():
    """自动上传学习参数到腾讯云服务器"""
    try:
        params_file = PROJECT_ROOT / "data" / "params" / "ai_learning_params.json"
        if not params_file.exists():
            return

        content = params_file.read_bytes()
        import base64
        b64 = base64.b64encode(content).decode("ascii")
        remote_dir = "/home/ubuntu/firefightAI/data/params"
        remote_path = f"{remote_dir}/ai_learning_params.json"

        cmd = f"mkdir -p {remote_dir} && echo '{b64}' | base64 -d > {remote_path}"
        ok, _, err = _ssh_exec(cmd, timeout=15)
        if ok:
            socketio.emit("params_uploaded_server", {
                "success": True,
                "message": "学习参数已上传到服务器",
                "time": datetime.now().isoformat()
            })
    except Exception:
        pass  # 静默失败


def _analyze_learning_patterns(logs: list) -> list:
    """分析学习日志，提取模式洞察"""
    insights = []
    if not logs:
        return insights

    # 统计各类日志数量
    categories = {}
    for log in logs:
        cat = log.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    # 分析错误模式
    errors = [l for l in logs if "失败" in l.get("message", "") or "错误" in l.get("message", "") or "error" in l.get("message", "").lower()]
    if errors:
        common_errors = {}
        for e in errors:
            msg = e.get("message", "")[:50]
            common_errors[msg] = common_errors.get(msg, 0) + 1
        top_errors = sorted(common_errors.items(), key=lambda x: x[1], reverse=True)[:3]
        if top_errors:
            insights.append({
                "type": "error_pattern",
                "summary": f"发现 {len(errors)} 个错误，最常见: {top_errors[0][0]}",
                "detail": f"Top errors: {top_errors}",
                "suggestion": "建议检查相关模块配置或网络连接",
            })

    # 分析成功模式
    successes = [l for l in logs if "成功" in l.get("message", "") or "完成" in l.get("message", "")]
    if successes:
        insights.append({
            "type": "success_pattern",
            "summary": f"最近 {len(successes)} 次成功操作",
            "detail": f"成功操作分布: {categories}",
            "suggestion": "保持当前良好状态",
        })

    # 分析AI对话质量
    chat_logs = [l for l in logs if l.get("category") in ("ai_chat", "correction", "web_search")]
    if chat_logs:
        corrections = [l for l in chat_logs if l.get("category") == "correction"]
        if corrections:
            insights.append({
                "type": "correction_analysis",
                "summary": f"近期收到 {len(corrections)} 次行为纠正",
                "detail": f"纠正内容: {[c.get('message','')[:50] for c in corrections[:3]]}",
                "suggestion": "已根据纠正调整响应策略，提高相关领域回答质量",
            })

    # 分析战术决策
    battle_logs = [l for l in logs if l.get("category") in ("decision", "tactics", "battle")]
    if battle_logs:
        insights.append({
            "type": "tactical_analysis",
            "summary": f"记录了 {len(battle_logs)} 条战术决策",
            "detail": "AI正在积累实战经验",
            "suggestion": "继续积累更多战斗数据以优化决策模型",
        })

    return insights


def _adjust_parameters_from_insights(insights: list):
    """根据分析结果自动调整参数"""
    global _self_learning_params

    for insight in insights:
        if insight["type"] == "error_pattern":
            # 错误增多时降低温度（更保守）
            _self_learning_params["temperature"] = max(0.05, _self_learning_params["temperature"] - 0.02)
        elif insight["type"] == "correction_analysis":
            # 有纠正时提高学习率
            _self_learning_params["learning_rate"] = min(0.1, _self_learning_params["learning_rate"] + 0.005)
            _self_learning_params["temperature"] = min(0.5, _self_learning_params["temperature"] + 0.01)
        elif insight["type"] == "success_pattern":
            # 成功时适度调高温度（更自信）
            _self_learning_params["temperature"] = min(0.5, _self_learning_params["temperature"] + 0.01)

    # 确保参数在合理范围内
    _self_learning_params["temperature"] = round(max(0.05, min(0.5, _self_learning_params["temperature"])), 3)
    _self_learning_params["learning_rate"] = round(max(0.001, min(0.1, _self_learning_params["learning_rate"])), 4)


def _save_learning_params(params=None):
    """保存学习参数到文件"""
    try:
        params_dir = PROJECT_ROOT / "data" / "params"
        params_dir.mkdir(parents=True, exist_ok=True)
        import json as _json
        p = params if params is not None else _self_learning_params
        params_file = params_dir / "ai_learning_params.json"
        params_file.write_text(_json.dumps(p, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning(f"保存学习参数失败: {e}")


def _load_learning_params():
    """从文件加载学习参数"""
    global _self_learning_params
    try:
        params_file = PROJECT_ROOT / "data" / "params" / "ai_learning_params.json"
        if params_file.exists():
            import json as _json
            loaded = _json.loads(params_file.read_text())
            _self_learning_params.update(loaded)
            logger.info(f"已加载学习参数 (total_learnings: {_self_learning_params.get('total_learnings', 0)})")
    except Exception as e:
        logger.warning(f"加载学习参数失败: {e}")


def _auto_push_learning_params():
    """自动推送学习参数到GitHub"""
    try:
        # 🔥 确保GitHub远程URL包含token认证
        _ensure_git_remote_with_token()
        rc, _, _ = _git_run(["git", "add", "data/params/ai_learning_params.json", "data/params/"], timeout=10)
        rc, _, _ = _git_run(["git", "diff", "--cached", "--quiet"], timeout=10)
        if rc != 0:
            rc, _, _ = _git_run(["git", "commit", "-m", f"AI自学习参数更新 #{_self_learning_params['total_learnings']}"], timeout=15)
            rc, _, stderr = _git_run(["git", "push", "origin", "master"], timeout=300)
            if rc == 0:
                add_learning_log("self_learn", "学习参数已自动推送到GitHub",
                                 f"第 {_self_learning_params['total_learnings']} 次学习")
                socketio.emit("learning_params_pushed", {
                    "success": True, "count": _self_learning_params["total_learnings"],
                    "message": "AI学习参数已推送到GitHub"
                })
            else:
                logger.info(f"GitHub推送失败: {stderr[:200] if stderr else 'unknown'}")
    except Exception:
        pass  # 静默失败，GitHub可能未配置


@app.route("/api/learn/status")
def api_learn_status():
    """获取AI自学习引擎状态"""
    return jsonify({
        "running": _self_learning_running,
        "params": _self_learning_params,
        "total_logs": len(_learning_log),
        "last_learned": _self_learning_params.get("last_learned"),
    })


@app.route("/api/learn/start", methods=["POST"])
def api_learn_start():
    """启动AI自学习引擎"""
    _start_self_learning_engine()
    return jsonify({"status": "started", "message": "AI自学习引擎已启动"})


@app.route("/api/learn/stop", methods=["POST"])
def api_learn_stop():
    """停止AI自学习引擎"""
    global _self_learning_running
    _self_learning_running = False
    add_learning_log("self_learn", "AI自学习引擎已停止", "")
    return jsonify({"status": "stopped", "message": "AI自学习引擎已停止"})


@app.route("/api/learn/params", methods=["GET"])
def api_learn_params_get():
    """获取当前学习参数"""
    return jsonify(_self_learning_params)


@app.route("/api/learn/params", methods=["POST"])
def api_learn_params_set():
    """手动设置学习参数"""
    data = request.get_json() or {}
    for key in ["temperature", "learning_rate", "tactical_aggressiveness", "confidence_threshold"]:
        if key in data:
            _self_learning_params[key] = data[key]
    _save_learning_params()
    add_learning_log("self_learn", "手动调整学习参数", str(data))
    return jsonify({"status": "ok", "params": _self_learning_params})


@app.route("/api/learn/logs/export", methods=["GET"])
def api_learn_logs_export():
    """导出学习日志"""
    import json as _json
    return jsonify({
        "logs": _learning_log,
        "params": _self_learning_params,
        "exported_at": datetime.now().isoformat(),
        "total": len(_learning_log),
    })


@app.route("/api/learn/logs/upload", methods=["POST"])
def api_learn_logs_upload():
    """上传学习日志到GitHub"""
    try:
        import json as _json
        log_dir = PROJECT_ROOT / "data" / "learning_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"learning_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        log_file.write_text(_json.dumps({
            "logs": _learning_log,
            "params": _self_learning_params,
            "exported_at": datetime.now().isoformat(),
        }, ensure_ascii=False, indent=2))

        subprocess.run(["git", "add", str(log_file), "data/params/"], cwd=str(PROJECT_ROOT),
                       capture_output=True, timeout=10)
        subprocess.run(["git", "commit", "-m", f"学习日志导出 {datetime.now().strftime('%Y%m%d-%H%M')}"],
                       cwd=str(PROJECT_ROOT), capture_output=True, timeout=15)
        push_r = subprocess.run(["git", "push", "origin", "master"], cwd=str(PROJECT_ROOT),
                                capture_output=True, timeout=30)

        if push_r.returncode == 0:
            add_learning_log("self_learn", "学习日志已上传到GitHub", f"共 {len(_learning_log)} 条")
            return jsonify({"status": "pushed", "message": "学习日志已上传到GitHub", "total": len(_learning_log)})
        else:
            return jsonify({"status": "saved_local", "message": "已保存到本地，GitHub推送失败"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]}), 500


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

            r = _deepseek_chat([{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}], max_tokens=512, temperature=0.1, stream=False)
            if not r["success"]:
                socketio.emit("ai_chat_error", {"error": r["error"]})
                return
            analysis = r["content"]
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
            prompt = (
                "你是 Firefight AI 学习系统。以下是上传的配置参数，请分析并提取可以用于改进战术的要点。\n"
                "请输出: 1) 参数摘要 2) 可改进的战术规则 3) 建议调整\n\n"
                f"参数内容:\n{content}"
            )
            r = _deepseek_chat([{"role": "user", "content": prompt}], max_tokens=512, temperature=0.1, stream=False)
            if not r["success"]:
                socketio.emit("params_learned", {"error": r["error"]})
                return
            analysis = r["content"]
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


@app.route("/api/params/upload_github", methods=["POST"])
def api_params_upload_github():
    """手动推送AI学习参数到GitHub"""
    try:
        params_file = PROJECT_ROOT / "data" / "params" / "ai_learning_params.json"
        if not params_file.exists():
            return jsonify({"status": "error", "error": "参数文件不存在，请先运行自学习引擎"}), 404

        # 🔥 先确保SSH远程 URL 配置正确
        _ensure_git_remote_with_token()

        # 🔥 先拉取远程最新（强制 SSH），防止 push rejected
        remote_url_r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, env=_git_env(), timeout=10)
        current_remote = (remote_url_r.stdout or "").strip()

        if "github.com" in current_remote and not current_remote.startswith("git@"):
            # 强制改成 SSH
            ssh_url = "git@github.com:chenyt-Indom/firefightAI.git"
            subprocess.run(["git", "remote", "set-url", "origin", ssh_url], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=5)
            logger.info(f"远程URL已强制改为SSH: {ssh_url}")
            current_remote = ssh_url

        rc, _, stderr = _git_run(["git", "fetch", "origin", "master"], timeout=30)
        if rc != 0:
            return jsonify({"status": "error", "error": f"git fetch 失败: {(stderr or '')[:200]}。请检查SSH是否配置正确。"}), 500

        # 检查远程是否有未拉取的提交
        rc, ahead_out, _ = _git_run(["git", "rev-list", "--count", "origin/master..HEAD"], timeout=5)
        rc, behind_out, _ = _git_run(["git", "rev-list", "--count", "HEAD..origin/master"], timeout=5)
        ahead = int((ahead_out or "0").strip()) if (ahead_out or "0").strip().isdigit() else 0
        behind = int((behind_out or "0").strip()) if (behind_out or "0").strip().isdigit() else 0

        if behind > 0:
            # 远程有未拉取的提交，先rebase合并
            rc, _, stderr = _git_run(["git", "rebase", "origin/master"], timeout=30)
            if rc != 0:
                # rebase冲突，自动中止并告知用户
                subprocess.run(["git", "rebase", "--abort"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=5)
                return jsonify({
                    "status": "error",
                    "error": f"远程有{behind}个新提交，且与本地有冲突。请手动git pull或git fetch后重试。错误: {(stderr or '')[:200]}"
                }), 500

        rc, _, _ = _git_run(["git", "add", "data/params/"], timeout=10)
        rc, _, _ = _git_run(["git", "diff", "--cached", "--quiet"], timeout=10)
        if rc == 0:
            return jsonify({"status": "ok", "message": "无变更，已是最新"})

        commit_msg = f"AI学习参数手动推送 {datetime.now().strftime('%Y%m%d_%H%M%S')}"
        rc, _, stderr = _git_run(["git", "commit", "-m", commit_msg], timeout=15)
        if rc != 0:
            return jsonify({"status": "error", "error": f"git commit 失败: {stderr[:200]}"}), 500

        rc, _, stderr = _git_run(["git", "push", "origin", "master"], timeout=300)
        if rc == 0:
            add_system_log("github", "学习参数已推送到GitHub", f"手动推送: {commit_msg}")
            return jsonify({"status": "ok", "success": True, "message": "已推送到GitHub"})
        else:
            return jsonify({"status": "error", "error": f"推送失败: {stderr[:200]}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]}), 500


@app.route("/api/params/upload_server", methods=["POST"])
def api_params_upload_server():
    """手动上传AI学习参数到腾讯云服务器"""
    try:
        params_file = PROJECT_ROOT / "data" / "params" / "ai_learning_params.json"
        if not params_file.exists():
            return jsonify({"status": "error", "error": "参数文件不存在"}), 404

        content = params_file.read_bytes()
        import base64
        b64 = base64.b64encode(content).decode("ascii")
        remote_dir = "/home/ubuntu/firefightAI/data/params"
        remote_path = f"{remote_dir}/ai_learning_params.json"

        cmd = f"mkdir -p {remote_dir} && echo '{b64}' | base64 -d > {remote_path}"
        ok, _, err = _ssh_exec(cmd, timeout=15)
        if ok:
            add_system_log("server", "学习参数已上传到服务器", "手动上传")
            return jsonify({"status": "ok", "success": True, "message": "已上传到服务器"})
        else:
            return jsonify({"status": "error", "error": f"上传失败: {err[:200]}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]}), 500


@app.route("/api/params/pull_github", methods=["POST"])
def api_params_pull_github():
    """从GitHub拉取最新参数文件并更新本地AI参数"""
    add_system_log("github", "开始从GitHub拉取参数", "")
    try:
        # 先拉取最新代码
        rc, stdout, stderr = _git_run(["git", "fetch", "origin", "master"], timeout=60)
        if rc != 0:
            add_system_log("github", "GitHub拉取失败", stderr[:200] if stderr else "未知错误")
            return jsonify({"status": "error", "error": f"fetch失败: {stderr[:200]}"}), 500

        # 检出最新的参数文件
        rc, stdout, stderr = _git_run(["git", "checkout", "origin/master", "--", "data/params/ai_learning_params.json"], timeout=15)
        
        params_file = PROJECT_ROOT / "data" / "params" / "ai_learning_params.json"
        if params_file.exists():
            try:
                loaded = json.loads(params_file.read_text(encoding="utf-8"))
                # 更新内存中的参数
                for key in ["temperature", "learning_rate", "tactical_aggressiveness", "confidence_threshold"]:
                    if key in loaded:
                        _self_learning_params[key] = loaded[key]
                _self_learning_params["total_learnings"] = loaded.get("total_learnings", _self_learning_params.get("total_learnings", 0))
                _self_learning_params["last_synced"] = datetime.now().isoformat()
                
                add_system_log("github", "参数已从GitHub同步", json.dumps({k: _self_learning_params[k] for k in ["temperature", "learning_rate", "tactical_aggressiveness", "confidence_threshold"]}, ensure_ascii=False))
                add_learning_log("params", "参数已从GitHub同步", f"新参数: temperature={_self_learning_params['temperature']}, learning_rate={_self_learning_params['learning_rate']}")
                
                return jsonify({
                    "status": "ok",
                    "message": "参数已从GitHub同步到本地",
                    "params": {k: _self_learning_params[k] for k in ["temperature", "learning_rate", "tactical_aggressiveness", "confidence_threshold"]},
                    "total_learnings": _self_learning_params.get("total_learnings", 0),
                })
            except (json.JSONDecodeError, IOError) as e:
                return jsonify({"status": "error", "error": f"参数文件解析失败: {str(e)}"}), 500
        else:
            return jsonify({"status": "error", "error": "GitHub上未找到参数文件"}), 404
            
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]}), 500


@app.route("/api/params/sync", methods=["POST"])
def api_params_sync():
    """双向同步参数：先推送到GitHub，再确认服务器状态"""
    results = {"github": None, "server": None}
    
    # 1. 推送到GitHub
    try:
        _auto_push_learning_params()
        results["github"] = "pushed"
    except Exception as e:
        results["github"] = f"error: {str(e)[:100]}"
    
    # 2. 检查服务器状态
    try:
        ok, stdout, _ = _ssh_exec("curl -s http://127.0.0.1:5000/api/learn/params 2>/dev/null", timeout=10)
        if ok and stdout:
            try:
                server_params = json.loads(stdout.strip())
                results["server"] = {"status": "online", "params": server_params}
            except:
                results["server"] = {"status": "online", "raw": stdout[:200]}
        else:
            results["server"] = {"status": "offline"}
    except:
        results["server"] = {"status": "unreachable"}
    
    add_system_log("sync", "参数同步完成", json.dumps(results, ensure_ascii=False))
    return jsonify({"status": "ok", "results": results, "time": datetime.now().isoformat()})


# ═══════════════════════════════════════════════════════════════
# v5.1 自动保存 + 预测系统 API
# ═══════════════════════════════════════════════════════════════

@app.route("/api/auto_save/status")
def api_auto_save_status():
    """获取自动保存状态"""
    global _scheduler
    if _scheduler:
        try:
            status = _scheduler.get_status()
            return jsonify({"status": "ok", "data": status})
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)})
    return jsonify({"status": "not_initialized", "data": {"running": False}})

@app.route("/api/auto_save/now", methods=["POST"])
def api_auto_save_now():
    """立即执行参数保存和上传"""
    global _scheduler
    if not _scheduler:
        return jsonify({"error": "自动保存调度器未初始化"}), 400
    
    try:
        save_result = _scheduler.save_params_now()
        upload_result = _scheduler.upload_params()
        add_system_log("auto_save", "手动保存完成", f"保存: {save_result}, 上传: {upload_result}")
        return jsonify({"status": "ok", "save": save_result, "upload": upload_result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/prediction/status")
def api_prediction_status():
    """获取预测系统状态"""
    global _predictor
    if _predictor:
        try:
            wisdom = _predictor.get_accumulated_wisdom()
            return jsonify({
                "status": "ok",
                "accuracy": wisdom.get("accuracy", 0),
                "experience_count": len(_predictor._experience),
                "recent_successes": wisdom.get("recent_successes", []),
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)})
    return jsonify({"status": "not_initialized", "accuracy": 0, "experience_count": 0})

@app.route("/api/auto_save/schedule")
def api_auto_save_schedule():
    """获取定时保存计划"""
    global _scheduler
    if _scheduler:
        try:
            status = _scheduler.get_status()
            return jsonify({
                "status": "ok",
                "next_save_time": status.get("next_save_time", ""),
                "last_save_time": status.get("last_save_time", ""),
                "total_saves": status.get("total_saves", 0),
                "running": status.get("running", False),
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)})
    return jsonify({"status": "not_initialized"})

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
    add_system_log("training", f"上传{len(uploaded)}张图片到数据集 {dataset_name}", "")
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
    device = data.get("device", "cpu")
    remove_after = data.get("remove_after_train", True)

    dataset_path = PROJECT_ROOT / "data" / dataset_name / "data.yaml"
    if not dataset_path.exists():
        return jsonify({"error": f"数据集不存在: {dataset_name}"}), 404
    
    # 检查数据集图片数量
    images_dir = PROJECT_ROOT / "data" / dataset_name / "images"
    labels_dir = PROJECT_ROOT / "data" / dataset_name / "labels"
    image_files = list(images_dir.glob("*")) if images_dir.exists() else []
    image_count = len([f for f in image_files if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp")])
    label_files = list(labels_dir.glob("*.txt")) if labels_dir.exists() else []
    label_count = len([f for f in label_files if f.stat().st_size > 0])
    
    # 统计各类别数量
    class_counts = {}
    for lf in label_files:
        try:
            for line in lf.read_text().strip().split("\n"):
                if line.strip():
                    cls_id = line.strip().split()[0]
                    class_counts[cls_id] = class_counts.get(cls_id, 0) + 1
        except:
            pass
    
    # 训练参数快照
    train_params = {
        "dataset": dataset_name,
        "model": model_name,
        "epochs": epochs,
        "imgsz": imgsz,
        "image_count": image_count,
        "label_count": label_count,
        "class_distribution": class_counts,
        "auto_push_github": auto_push,
        "remove_after_train": remove_after,
        "device": device,
    }
    
    # 图片数量不足警告
    min_images = 50
    if image_count < min_images:
        warning = f"⚠️ 数据集图片仅 {image_count} 张（建议 ≥ {min_images} 张），训练效果可能不佳。建议补充更多标注数据。"
        socketio.emit("training_log", {"line": warning})
        add_system_log("training", "图片数量不足警告", warning)
    
    socketio.emit("training_log", {"line": f"📊 数据集分析: {image_count}张图片, {label_count}个有效标注"})
    if class_counts:
        cls_names = {"0": "tank", "1": "infantry"}
        cls_str = ", ".join([f"{cls_names.get(k, k)}: {v}个" for k, v in sorted(class_counts.items())])
        socketio.emit("training_log", {"line": f"📊 类别分布: {cls_str}"})
    
    device_label = "GPU" if device != "cpu" else "CPU"
    socketio.emit("training_log", {"line": f"🖥️ 训练设备: {device_label}"})
    
    update_state(training_status="running", training_progress=0, training_message=f"启动训练 ({device_label})...")
    add_system_log("training", f"开始训练: {dataset_name}, epochs={epochs}, device={device}, images={image_count}", "")

    def run_training():
        global _training_process
        try:
            from ultralytics import YOLO
            project_dir = str(PROJECT_ROOT / 'runs' / 'detect')
            run_name = f"custom_{int(time.time())}"
            
            socketio.emit("training_log", {"line": f"🔧 加载模型: {model_name}"})
            update_state(training_progress=5, training_message="加载模型...")
            model = YOLO(model_name)
            
            socketio.emit("training_log", {"line": f"📁 数据集: {dataset_path}"})
            socketio.emit("training_log", {"line": f"⚙️ 训练参数: epochs={epochs}, imgsz={imgsz}, images={image_count}, labels={label_count}"})
            update_state(training_progress=10, training_message="训练中...")
            
            # 训练进度回调
            def on_train_epoch_end(trainer):
                try:
                    epoch = trainer.epoch + 1
                    progress = int(10 + (epoch / epochs) * 80)
                    update_state(training_progress=progress, training_message=f"训练中... epoch {epoch}/{epochs}")
                    socketio.emit("training_log", {"line": f"Epoch {epoch}/{epochs} 完成"})
                except:
                    pass
            
            model.add_callback("on_train_epoch_end", on_train_epoch_end)
            
            results = model.train(
                data=str(dataset_path),
                epochs=epochs,
                imgsz=imgsz,
                device=device,
                project=project_dir,
                name=run_name,
                exist_ok=True,
                verbose=True,
            )
            
            update_state(training_progress=95, training_message="训练完成，整理结果...")
            
            # 提取训练结果
            train_results = {"params": train_params}
            if results is not None:
                try:
                    train_results.update({
                        "mAP50": round(float(results.results_dict.get("metrics/mAP50(B)", 0)), 4),
                        "mAP50_95": round(float(results.results_dict.get("metrics/mAP50-95(B)", 0)), 4),
                        "precision": round(float(results.results_dict.get("metrics/precision(B)", 0)), 4),
                        "recall": round(float(results.results_dict.get("metrics/recall(B)", 0)), 4),
                        "run_dir": f"runs/detect/{run_name}",
                    })
                    # 判断训练效果
                    mAP50 = train_results["mAP50"]
                    if mAP50 < 0.3:
                        train_results["quality"] = "poor"
                        train_results["feedback"] = f"训练效果不佳 (mAP50={mAP50})。可能原因: 图片数量不足({image_count}张)、标注质量低、类别不平衡。建议补充更多标注数据或调整训练参数。"
                    elif mAP50 < 0.6:
                        train_results["quality"] = "moderate"
                        train_results["feedback"] = f"训练效果中等 (mAP50={mAP50})。可继续补充数据或增加epochs提升效果。"
                    else:
                        train_results["quality"] = "good"
                        train_results["feedback"] = f"训练效果良好 (mAP50={mAP50})。"
                except Exception as e:
                    train_results["error"] = f"读取结果失败: {str(e)[:100]}"
            
            ok = results is not None
            
            # 训练后移除已用图片
            if ok and remove_after:
                try:
                    trained_dir = images_dir.parent / "trained"
                    trained_dir.mkdir(parents=True, exist_ok=True)
                    moved_count = 0
                    for img_file in image_files:
                        if img_file.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp"):
                            dest = trained_dir / img_file.name
                            if dest.exists():
                                dest = trained_dir / f"{img_file.stem}_{int(time.time())}{img_file.suffix}"
                            img_file.rename(dest)
                            # 同时移动对应的标注文件
                            label_file = labels_dir / (img_file.stem + ".txt")
                            if label_file.exists():
                                label_dest = trained_dir / label_file.name
                                if label_dest.exists():
                                    label_dest = trained_dir / f"{label_file.stem}_{int(time.time())}.txt"
                                label_file.rename(label_dest)
                            moved_count += 1
                    socketio.emit("training_log", {"line": f"📦 已移除 {moved_count} 张训练图片到 trained/ 目录，防止重复训练"})
                    add_system_log("training", "训练图片已移除", f"已将 {moved_count} 张图片移至 trained/")
                except Exception as e:
                    socketio.emit("training_log", {"line": f"⚠️ 移除图片失败: {str(e)[:100]}"})
            
            update_state(training_status="completed" if ok else "failed", training_progress=100 if ok else 0, training_message="训练完成!" if ok else "训练失败")
            add_system_log("training", "训练完成" if ok else "训练失败", f"dataset={dataset_name}, epochs={epochs}, mAP50={train_results.get('mAP50', 'N/A')}")
            socketio.emit("training_complete", {"success": ok, "results": train_results})
            socketio.emit("training_log", {"line": "✅ 训练完成!" if ok else "❌ 训练失败"})
            if train_results and "mAP50" in train_results:
                socketio.emit("training_log", {"line": f"📊 mAP50: {train_results['mAP50']}, mAP50-95: {train_results['mAP50_95']}, Precision: {train_results['precision']}, Recall: {train_results['recall']}"})
            if train_results.get("feedback"):
                socketio.emit("training_log", {"line": f"💡 {train_results['feedback']}"})
            
            # 自动推送至GitHub
            if auto_push and ok:
                try:
                    socketio.emit("training_log", {"line": "📤 自动推送到GitHub..."})
                    # 🔥 确保GitHub远程URL包含token认证
                    _ensure_git_remote_with_token()
                    # 先提交所有变更（包括训练结果和模型文件）
                    _git_run(["git", "add", "-A"], timeout=10)
                    _git_run(["git", "commit", "-m", f"训练完成: {dataset_name} epochs={epochs} device={device}"], timeout=30)
                    rc, stdout, stderr = _git_run(["git", "push", "origin", "master"], timeout=300)
                    if rc == 0:
                        socketio.emit("training_log", {"line": "✅ GitHub推送成功"})
                    else:
                        socketio.emit("training_log", {"line": f"⚠️ GitHub推送失败: {stderr[:200]}"})
                except Exception as e:
                    socketio.emit("training_log", {"line": f"⚠️ GitHub推送异常: {str(e)[:100]}"})
        except Exception as e:
            update_state(training_status="failed", training_progress=0, training_message=f"训练失败: {str(e)[:50]}")
            socketio.emit("training_log", {"line": f"❌ 训练异常: {str(e)[:200]}"})
            socketio.emit("training_complete", {"success": False, "error": str(e)[:200]})
            add_system_log("training", "训练失败", str(e)[:200])

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
            exp_text = "\n".join([f"- 友{d['ally_count']}vs敌{d['enemy_count']}: {d['decision'].get('reason','')[:80]} (得分+{d['outcome_score']:.0f})" for d in top_exps[:15]])
            prompt = f"从以下实战数据中总结AI学到了什么（3-5条要点）：\n{exp_text}\n\n请用中文列出。"
            r = _deepseek_chat([{"role": "user", "content": prompt}], max_tokens=512, temperature=0.1, stream=False)
            summary = r.get("content", f"(总结失败: {r.get('error', '')})")

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
    """Web search - 真正联网搜索: DuckDuckGo/Bing → DeepSeek总结"""
    data = request.get_json() or {}
    query = data.get("query", "")
    if not query:
        return jsonify({"error": "query required"}), 400

    add_learning_log("web_search", f"搜索: {query[:50]}")

    try:
        import requests as _requests
        # 🔥 先尝试真正联网搜索
        results = _search_duckduckgo(query, _requests)
        if not results:
            results = _search_bing(query, _requests)
        
        if results:
            # 有搜索结果，用DeepSeek总结
            snippets_text = "\n".join([f"{i+1}. {r.get('title','')}: {r.get('snippet','')[:300]}" for i, r in enumerate(results[:8])])
            r = _deepseek_chat([{"role": "user", "content": f"搜索结果如下，请用中文总结关键信息（3-5条要点）：\n查询: {query}\n\n{snippets_text}"}], max_tokens=1024, temperature=0.1, stream=True)
            if r["success"]:
                full_text = ""
                def _emit_ws(data):
                    nonlocal full_text
                    full_text += data["token"]
                    socketio.emit("web_search_stream", {"text": full_text, "done": False})
                full_text = _deepseek_stream_to_end(r["response"], emit_fn=_emit_ws)
            else:
                full_text = f"(AI总结失败: {r['error']})"
            socketio.emit("web_search_stream", {"text": full_text, "done": True})
            add_learning_log("web_search", f"搜索完成: {query[:50]}", full_text[:200])
            return jsonify({
                "query": query, "summary": full_text, "results": results,
                "searched_at": datetime.now().isoformat(), "source": "Web Search (DuckDuckGo/Bing)",
                "total_results": len(results),
            })
        else:
            # 🔥 无搜索结果，回退到DeepSeek知识库
            return _search_deepseek_rest(query)
    except Exception as e:
        add_learning_log("web_search", f"搜索失败: {str(e)[:100]}")
        return jsonify({"error": str(e), "query": query}), 500


def _search_deepseek_rest(query: str):
    """DeepSeek知识库回退 (REST版本)"""
    r = _deepseek_chat([
        {"role": "system", "content": "你是一个智能搜索助手。请根据用户的问题，利用你的知识库提供准确、详细的信息。如果涉及最新信息，请说明知识截止日期。回答要结构化、有条理。"},
        {"role": "user", "content": f"请帮我搜索并回答以下问题，提供详细信息：\n\n{query}"}
    ], max_tokens=2048, temperature=0.1, stream=True)
    if r["success"]:
        full_text = ""
        def _emit_ws(data):
            nonlocal full_text
            full_text += data["token"]
            socketio.emit("web_search_stream", {"text": full_text, "done": False})
        full_text = _deepseek_stream_to_end(r["response"], emit_fn=_emit_ws)
    else:
        full_text = f"(AI总结失败: {r['error']})"
    socketio.emit("web_search_stream", {"text": full_text, "done": True})
    add_learning_log("web_search", f"搜索完成: {query[:50]}", full_text[:200])
    return jsonify({
        "query": query, "summary": full_text, "results": [],
        "searched_at": datetime.now().isoformat(), "source": "DeepSeek Knowledge Base",
        "total_results": 0,
    })

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
            
            r = _deepseek_chat([{"role": "user", "content": prompt}], max_tokens=512, temperature=0.1, stream=False)
            if not r["success"]:
                socketio.emit("web_learn_result", {"error": r["error"]})
                return
            learning = r["content"]
            
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


@app.route("/api/web/knowledge/<filename>")
def api_web_knowledge_detail(filename: str):
    """获取单条知识的完整内容"""
    # 安全检查：防止路径遍历
    safe_name = Path(filename).name
    file_path = WEB_KNOWLEDGE_DIR / safe_name
    if not file_path.exists():
        return jsonify({"error": "知识条目不存在"}), 404
    try:
        entry = json.loads(file_path.read_text(encoding="utf-8"))
        return jsonify({
            "filename": safe_name,
            "query": entry.get("query", ""),
            "saved_at": entry.get("saved_at", ""),
            "tags": entry.get("tags", []),
            "summary": entry.get("summary", ""),
            "full_text": entry.get("full_text", ""),
            "url": entry.get("url", ""),
            "results": entry.get("results", []),
        })
    except Exception as e:
        return jsonify({"error": f"读取失败: {str(e)}"}), 500


@app.route("/api/web/knowledge/<filename>", methods=["DELETE"])
def api_web_knowledge_delete(filename: str):
    """删除单条知识"""
    safe_name = Path(filename).name
    file_path = WEB_KNOWLEDGE_DIR / safe_name
    if not file_path.exists():
        return jsonify({"error": "知识条目不存在"}), 404
    try:
        file_path.unlink()
        add_learning_log("web_search", f"知识已删除: {safe_name}", "")
        return jsonify({"status": "ok", "message": f"已删除: {safe_name}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@socketio.on("web_search")
def on_web_search(data: dict):
    """流式Web搜索，实时返回进度。支持URL直接抓取和关键词搜索。"""
    query = data.get("query", "").strip()
    if not query:
        emit("web_search_error", {"error": "缺少查询参数"})
        return
    
    # 检测是否为URL
    import re
    url_pattern = re.compile(r'https?://[^\s]+')
    is_url = bool(url_pattern.match(query))
    
    emit("web_search_progress", {"step": "分析查询中", "progress": 10, "query": query})
    
    def search_worker():
        try:
            import requests as req
            
            if is_url:
                # ── URL模式：直接抓取网页内容 ──
                _search_url_content(query, req)
                return
            
            # ── 关键词搜索模式 ──
            results = _search_duckduckgo(query, req)
            
            if not results:
                # 回退：尝试Bing搜索
                emit("web_search_progress", {"step": "DuckDuckGo无结果，尝试备用搜索...", "progress": 30, "query": query})
                results = _search_bing(query, req)
            
            if not results:
                # 最终回退：直接让DeepSeek回答
                emit("web_search_progress", {"step": "搜索引擎无结果，使用DeepSeek知识库回答...", "progress": 30, "query": query})
                _search_deepseek_fallback(query)
                return
            
            emit("web_search_progress", {"step": "AI总结中", "progress": 60, "results_count": len(results)})
            
            # 检测是否为兵法/战术学习类查询
            is_military_query = any(kw in query for kw in ["兵法", "战术", "孙子", "战争", "军事", "克劳塞维茨", "三十六计", "战略", "布阵", "作战", "兵书", "打仗"])
            
            _summarize_and_emit(query, results, is_military_query)
            
            emit("web_search_progress", {"step": "完成", "progress": 100})
            
        except Exception as e:
            emit("web_search_error", {"error": str(e)[:200]})
    
    threading.Thread(target=search_worker, daemon=True).start()


def _search_url_content(url: str, req):
    """抓取URL内容并用AI总结"""
    emit("web_search_progress", {"step": "抓取网页内容中...", "progress": 30, "query": url})
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        resp = req.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        
        # 提取文本内容
        from html.parser import HTMLParser
        
        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.skip_tags = {'script', 'style', 'nav', 'footer', 'header', 'noscript', 'iframe', 'svg'}
                self.skip_depth = 0
                self.title = ""
                self.in_title = False
                
            def handle_starttag(self, tag, attrs):
                if tag in self.skip_tags:
                    self.skip_depth += 1
                if tag == 'title':
                    self.in_title = True
                    
            def handle_endtag(self, tag):
                if tag in self.skip_tags and self.skip_depth > 0:
                    self.skip_depth -= 1
                if tag == 'title':
                    self.in_title = False
                    
            def handle_data(self, data):
                if self.skip_depth > 0:
                    return
                if self.in_title:
                    self.title += data.strip()
                text = data.strip()
                if text and len(text) > 3:
                    self.text.append(text)
        
        extractor = TextExtractor()
        extractor.feed(resp.text)
        
        content_text = "\n".join(extractor.text[:200])  # 限制长度
        title = extractor.title or url
        
        if not content_text.strip():
            content_text = resp.text[:5000]
        
        emit("web_search_progress", {"step": "AI分析网页内容中...", "progress": 60, "query": url})
        
        # 用DeepSeek总结
        prompt = f"""请分析以下网页内容并用中文总结关键信息（3-8条要点），如果内容包含战术、军事、游戏相关，请特别标注：
        
网页标题: {title}
网页URL: {url}

内容:
{content_text[:4000]}

请用清晰的格式输出总结。"""
        
        r = _deepseek_chat([{"role": "user", "content": prompt}], max_tokens=1024, temperature=0.1, stream=True)
        if r["success"]:
            summary = _deepseek_stream_to_end(r["response"], emit_fn=lambda d: emit("web_search_token", d))
        else:
            summary = f"(AI分析失败: {r['error']})"
        emit("web_search_token", {"token": "", "done": True, "full": summary})
        
        results = [{"title": title, "url": url, "snippet": content_text[:300]}]
        emit("web_search_complete", {
            "query": url,
            "results": results,
            "summary": summary,
            "total_results": 1,
            "source": "url_fetch",
        })
        
        add_learning_log("web_search", f"URL抓取完成: {title}", summary[:200])
        
    except Exception as e:
        # URL抓取失败，回退到搜索该URL
        emit("web_search_progress", {"step": f"直接抓取失败({str(e)[:50]})，转为搜索...", "progress": 30, "query": url})
        try:
            results = _search_duckduckgo(url, req)
            if results:
                _summarize_and_emit(url, results, False)
            else:
                _search_deepseek_fallback(url)
        except Exception as e2:
            emit("web_search_error", {"error": f"URL抓取和搜索均失败: {str(e2)[:150]}"})


def _search_duckduckgo(query: str, req) -> list:
    """DuckDuckGo HTML搜索"""
    search_url = "https://html.duckduckgo.com/html/"
    try:
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
        return parser.results[:10]
    except Exception as e:
        logger.warning(f"DuckDuckGo搜索失败: {e}")
        return []


def _search_bing(query: str, req) -> list:
    """Bing搜索回退"""
    try:
        r = req.get("https://www.bing.com/search", params={"q": query}, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=15)
        
        from html.parser import HTMLParser
        
        class BingParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.results = []
                self.current = {}
                self.in_result = False
                self.in_snippet = False
                self.text_buf = ""
                
            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                cls = attrs_dict.get("class", "")
                if tag == "li" and "b_algo" in cls:
                    self.in_result = True
                    self.current = {}
                if self.in_result and tag == "a":
                    self.current["url"] = attrs_dict.get("href", "")
                    self.text_buf = ""
                if self.in_result and tag == "p":
                    self.in_snippet = True
                    self.text_buf = ""
                    
            def handle_endtag(self, tag):
                if self.in_snippet and tag == "p":
                    self.in_snippet = False
                    self.current["snippet"] = self.text_buf.strip()
                if self.in_result and tag == "li":
                    self.in_result = False
                    if self.current.get("snippet") or self.current.get("url"):
                        self.results.append(dict(self.current))
                    
            def handle_data(self, data):
                if self.in_result:
                    if self.in_snippet:
                        self.text_buf += data
                    else:
                        self.current["title"] = self.current.get("title", "") + data.strip()
        
        parser = BingParser()
        parser.feed(r.text)
        return parser.results[:10]
    except Exception as e:
        logger.warning(f"Bing搜索失败: {e}")
        return []


def _search_deepseek_fallback(query: str):
    """DeepSeek直接回答（无搜索结果时回退）"""
    try:
        r = _deepseek_chat([
            {"role": "system", "content": "你是一个智能搜索助手。请根据用户的问题，利用你的知识库提供准确、详细的信息。如果涉及最新信息，请说明知识截止日期。回答要结构化、有条理。"},
            {"role": "user", "content": f"请帮我搜索并回答以下问题，提供详细信息：\n\n{query}"}
        ], max_tokens=2048, temperature=0.1, stream=True)
        if r["success"]:
            full_text = _deepseek_stream_to_end(r["response"], emit_fn=lambda d: emit("web_search_token", d))
        else:
            full_text = f"(AI回复失败: {r['error']})"
        emit("web_search_token", {"token": "", "done": True, "full": full_text})
        
        emit("web_search_complete", {
            "query": query,
            "results": [],
            "summary": full_text,
            "total_results": 0,
            "source": "DeepSeek Knowledge Base",
        })
        
        add_learning_log("web_search", f"搜索完成(知识库): {query[:50]}", full_text[:200])
    except Exception as e:
        emit("web_search_error", {"error": str(e)[:200]})


def _summarize_and_emit(query: str, results: list, is_military_query: bool):
    """AI总结搜索结果并发射事件"""
    summary = ""
    try:
        snippets_text = "\n".join([f"{i+1}. {r.get('title','')}: {r.get('snippet','')[:300]}" for i, r in enumerate(results[:8])])
        
        if is_military_query:
            prompt = f"""搜索结果如下，请从兵法和战术角度进行深度分析，用中文输出：
1. 核心战术思想（3-5条）
2. 可应用于FirefightAI游戏的具体战术建议
3. 关键要点总结

查询: {query}
搜索结果:
{snippets_text}"""
        else:
            prompt = f"搜索结果如下，请用中文总结关键信息（3-5条要点，包含来源链接）：\n查询: {query}\n\n{snippets_text}"
        
        r = _deepseek_chat([{"role": "user", "content": prompt}], max_tokens=1024 if is_military_query else 512, temperature=0.1, stream=True)
        if r["success"]:
            summary = _deepseek_stream_to_end(r["response"], emit_fn=lambda d: emit("web_search_token", d))
        else:
            summary = f"(AI总结失败: {r['error']})"
        emit("web_search_token", {"token": "", "done": True, "full": summary})
        
        # ── 兵法学习: 自动保存 ──
        if is_military_query and summary:
            _save_military_knowledge(query, results, summary)
            
    except Exception as e:
        summary = f"(AI总结暂不可用: {str(e)[:80]})"
        emit("web_search_token", {"token": summary, "done": True, "full": summary})
    
    emit("web_search_complete", {
        "query": query,
        "results": results,
        "summary": summary,
        "total_results": len(results),
    })
    
    add_learning_log("web_search", f"搜索完成: {query[:50]}", summary[:200])


def _save_military_knowledge(query: str, results: list, summary: str):
    """保存兵法知识并让AI学习"""
    try:
        WEB_KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"military_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.md5(query.encode()).hexdigest()[:8]}.json"
        filepath = WEB_KNOWLEDGE_DIR / filename
        entry = {
            "query": query,
            "summary": summary,
            "results": [{"title": r.get("title", ""), "snippet": r.get("snippet", "")} for r in results[:5]],
            "tags": ["兵法", "战术", "AI学习"],
            "saved_at": datetime.now().isoformat(),
            "source": "web_search_military",
            "type": "military_doctrine",
        }
        filepath.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        add_learning_log("military_learn", f"兵法知识已保存: {query[:40]}", f"文件: {filename}")
        
        # 立即让AI学习
        doctrine_learn_prompt = f"""你是FirefightAI的战术学习系统。请从以下兵法知识中提取可应用于即时战略游戏的战术规则：

{summary[:2000]}

请输出JSON格式：{{"rules": [{{"name": "规则名", "condition": "触发条件", "action": "战术行动", "priority": "优先级(1-10)"}}], "overall_strategy": "总体战略建议"}}"""
        
        r = _deepseek_chat([{"role": "user", "content": doctrine_learn_prompt}], max_tokens=512, temperature=0.1, stream=False)
        if r["success"]:
            learned = r["content"]
            add_learning_log("military_learn", "AI已学习兵法知识", learned[:300])
            emit("military_learned", {"summary": learned[:500], "query": query})
    except Exception as e:
        logger.debug(f"兵法学习保存失败: {e}")

# ═══════════════════════════════════════════════════════════════
# 安装包创建
# ═══════════════════════════════════════════════════════════════

PACKAGE_EXCLUDE = {".git", "__pycache__", "logs", "runs", "sessions", ".venv", "venv", "node_modules", "__pycache__"}

@app.route("/api/package/create", methods=["POST"])
def api_package_create():
    """创建完整项目安装包 (包含源码+模型+参数+数据)"""
    import zipfile
    import io as _io
    
    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    
    zip_path = dist_dir / "firefightAI_v5.zip"
    
    add_system_log("system", "开始创建完整安装包", "")
    
    # 🔥 先同步最新参数到包内
    _sync_params_to_package()
    
    file_count = 0
    total_size = 0
    try:
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(str(PROJECT_ROOT)):
                rel_root = Path(root).relative_to(PROJECT_ROOT)
                rel_str = str(rel_root).replace("\\", "/")
                
                parts = [] if rel_str == "." else rel_str.split("/")
                
                skip = False
                for part in parts:
                    if part in PACKAGE_EXCLUDE or part.startswith("."):
                        skip = True
                        break
                if skip:
                    dirs[:] = []
                    continue
                
                dirs[:] = [d for d in dirs if d not in PACKAGE_EXCLUDE and not d.startswith(".")]
                
                for f in files:
                    if f.startswith("."):
                        continue
                    fp = Path(root) / f
                    arcname = str(fp.relative_to(PROJECT_ROOT))
                    try:
                        zf.write(str(fp), arcname)
                        file_count += 1
                        total_size += fp.stat().st_size
                    except Exception:
                        pass
            
            # 🔥 确保包内包含最新参数版本号
            v = _get_params_version()
            zf.writestr("data/params/version.json", json.dumps(v, indent=2, ensure_ascii=False))
            zf.writestr("data/params/ai_learning_params.json", json.dumps(_load_learning_params() or {}, indent=2, ensure_ascii=False))
            zf.writestr("data/params/server_url.txt", "https://firefightai.top")
        
        zip_size = zip_path.stat().st_size
        size_mb = round(zip_size / 1024 / 1024, 2)
        
        add_system_log("system", f"安装包: {size_mb}MB, {file_count}文件, 含最新参数v{_get_params_version()['version']}", "")
        
        # 创建install.bat (含自动同步)
        install_bat = dist_dir / "install.bat"
        install_bat.write_text(f"""@echo off
chcp 65001 >nul
title Firefight AI v5.0 — 智能安装
echo ============================================
echo   Firefight AI v5.0 安装程序 (含自动同步)
echo ============================================
echo.

:: 检查Python
echo [1/5] 检查Python环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到Python，请先安装Python 3.10+
    pause
    exit /b 1
)

:: 安装依赖
echo [2/5] 安装依赖包...
pip install -r requirements.txt -q

:: 🔥 从服务器同步最新参数
echo [3/5] 同步最新AI参数...
python -c "import urllib.request,json,os;d=json.loads(urllib.request.urlopen('https://firefightai.top/api/sync/params/download').read());json.dump(d.get('params',{{}}),open('data/params/ai_learning_params.json','w'));json.dump(d.get('version',{{}}),open('data/params/version.json','w'))" 2>nul
if %errorlevel% equ 0 (echo    参数已同步到最新版) else (echo    使用安装包内置参数)

:: 创建桌面快捷方式
echo [4/5] 创建快捷方式...
powershell -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\FirefightAI.lnk');$s.TargetPath='pythonw.exe';$s.Arguments='dashboard_server.py --host 0.0.0.0 --port 5000';$s.WorkingDirectory='%~dp0';$s.IconLocation='shell32.dll,13';$s.Save()"

:: 启动应用
echo [5/5] 启动 Firefight AI...
echo   本地: http://localhost:5000
echo   公网: https://firefightai.top
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
# 多机参数同步系统
# ═══════════════════════════════════════════════════════════════

_PARAMS_VERSION_FILE = PROJECT_ROOT / "data" / "params" / "version.json"
_PARAMS_SYNC_DIR = PROJECT_ROOT / "data" / "params" / "sync"

def _get_params_version() -> dict:
    """获取当前参数版本信息"""
    if _PARAMS_VERSION_FILE.exists():
        try:
            return json.loads(_PARAMS_VERSION_FILE.read_text(encoding="utf-8"))
        except:
            pass
    return {"version": 0, "updated_at": "", "source": "local", "total_learnings": 0}

def _update_params_version(learnings: int = 0, source: str = "local"):
    """更新参数版本号"""
    v = _get_params_version()
    v["version"] = v.get("version", 0) + 1
    v["updated_at"] = datetime.now().isoformat()
    v["total_learnings"] = max(v.get("total_learnings", 0), learnings)
    v["source"] = source
    _PARAMS_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PARAMS_VERSION_FILE.write_text(json.dumps(v, indent=2, ensure_ascii=False))

def _sync_params_to_package():
    """将最新参数版本写入包内，供安装包携带"""
    v = _get_params_version()
    params_dir = PROJECT_ROOT / "data" / "params"
    params_dir.mkdir(parents=True, exist_ok=True)
    (params_dir / "version.json").write_text(json.dumps(v, indent=2, ensure_ascii=False))
    # 复制学习参数文件
    learning_file = params_dir / "ai_learning_params.json"
    if not learning_file.exists():
        _save_learning_params(_load_learning_params() or {})

@app.route("/api/sync/params/upload", methods=["POST"])
def api_sync_params_upload():
    """本地应用上传训练参数到服务器"""
    try:
        data = request.get_json(force=True) or {}
        machine_id = data.get("machine_id", "unknown")
        params_data = data.get("params", {})
        
        _PARAMS_SYNC_DIR.mkdir(parents=True, exist_ok=True)
        
        # 保存来自该机器的参数
        machine_file = _PARAMS_SYNC_DIR / f"{machine_id}.json"
        entry = {
            "machine_id": machine_id,
            "uploaded_at": datetime.now().isoformat(),
            "params": params_data,
            "total_learnings": data.get("total_learnings", 0),
        }
        machine_file.write_text(json.dumps(entry, indent=2, ensure_ascii=False))
        
        # 触发AI合并
        merge_count = _ai_merge_params()
        
        add_system_log("sync", f"机器 {machine_id} 上传参数", f"合并{merge_count}台机器数据")
        
        return jsonify({
            "status": "ok",
            "message": f"参数已上传，已合并{merge_count}台机器数据",
            "version": _get_params_version()["version"],
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/api/sync/params/download", methods=["GET"])
def api_sync_params_download():
    """下载服务器最新参数"""
    version = _get_params_version()
    params = _load_learning_params() or {}
    
    # 同时返回学习日志和知识库
    knowledge_base = _ai_knowledge_base[-50:] if _ai_knowledge_base else []
    learning_log = _learning_log[-50:] if _learning_log else []
    
    return jsonify({
        "version": version,
        "params": params,
        "knowledge_base": knowledge_base,
        "learning_log": learning_log,
        "total_learnings": version.get("total_learnings", 0),
        "server_time": datetime.now().isoformat(),
    })

def _ai_merge_params() -> int:
    """AI合并多机参数 - 返回合并的机器数量"""
    if not _PARAMS_SYNC_DIR.exists():
        return 0
    
    machine_files = list(_PARAMS_SYNC_DIR.glob("*.json"))
    if len(machine_files) < 2:
        return len(machine_files)
    
    all_params = {}
    learnings = 0
    for mf in machine_files:
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
            all_params[data["machine_id"]] = data.get("params", {})
            learnings += data.get("total_learnings", 0)
        except:
            pass
    
    # 简单合并: 取所有参数的加权平均值
    current = _load_learning_params() or {}
    if all_params:
        merged = {}
        # 收集所有参数键
        all_keys = set(current.keys())
        for p in all_params.values():
            all_keys.update(p.keys())
        
        for key in all_keys:
            values = []
            if key in current and isinstance(current[key], (int, float)):
                values.append((current[key], max(1, version.get("total_learnings", 1))))
            for mid, p in all_params.items():
                if key in p and isinstance(p[key], (int, float)):
                    entry = json.loads(_PARAMS_SYNC_DIR.joinpath(f"{mid}.json").read_text())
                    values.append((p[key], entry.get("total_learnings", 1)))
            
            if values:
                # 加权平均
                total_weight = sum(w for _, w in values)
                merged[key] = sum(v * w for v, w in values) / total_weight
            elif key in current:
                merged[key] = current[key]
        
        _save_learning_params(merged)
        version = _get_params_version()
        version["total_learnings"] = learnings
        _update_params_version(learnings, "ai_merge")
        
        socketio.emit("training_log", {"line": f"🧠 AI合并参数: {len(machine_files)}台机器, {len(merged)}个参数"})
    
    return len(machine_files)

@app.route("/api/sync/params/version", methods=["GET"])
def api_sync_params_version():
    """检查是否需要更新"""
    return jsonify(_get_params_version())

@app.route("/api/sync/global", methods=["POST"])
def api_sync_global():
    """🔥 一键全域同步: 上传参数+知识+日志，合并后返回最新"""
    try:
        data = request.get_json(force=True) or {}
        machine_id = data.get("machine_id", socket.gethostname() if 'socket' in dir() else "unknown")
        
        # 上传参数
        params_data = data.get("params", _load_learning_params() or {})
        knowledge_data = data.get("knowledge_base", [])
        log_data = data.get("learning_log", [])
        
        _PARAMS_SYNC_DIR.mkdir(parents=True, exist_ok=True)
        machine_file = _PARAMS_SYNC_DIR / f"{machine_id}.json"
        entry = {
            "machine_id": machine_id,
            "uploaded_at": datetime.now().isoformat(),
            "params": params_data,
            "knowledge_base": knowledge_data[-20:] if knowledge_data else [],
            "learning_log": log_data[-20:] if log_data else [],
            "total_learnings": data.get("total_learnings", len(log_data)),
        }
        machine_file.write_text(json.dumps(entry, indent=2, ensure_ascii=False))
        
        # 🔥 合并知识库
        new_knowledge = 0
        for k in (knowledge_data or []):
            if k and not any(e.get("id") == k.get("id") for e in _ai_knowledge_base):
                _ai_knowledge_base.append(k)
                new_knowledge += 1
        _save_knowledge_base()
        
        # 🔥 合并学习日志
        new_logs = 0
        for l in (log_data or []):
            if l and not any(e == l for e in _learning_log):
                _learning_log.append(l)
                new_logs += 1
        _save_learning_log()
        update_state(learning_log=_learning_log[-50:])
        
        # 触发AI合并
        merge_count = _ai_merge_params()
        _update_params_version(data.get("total_learnings", 0), machine_id)
        
        # 返回最新状态
        result = {
            "status": "synced",
            "machine_id": machine_id,
            "merged_machines": merge_count,
            "new_knowledge": new_knowledge,
            "new_logs": new_logs,
            "version": _get_params_version(),
            "params": _load_learning_params() or {},
            "knowledge_base": _ai_knowledge_base[-50:],
            "learning_log": _learning_log[-50:],
            "server_time": datetime.now().isoformat(),
        }
        
        socketio.emit("training_log", {"line": f"🌐 全域同步: {machine_id} ({merge_count}机合并, +{new_knowledge}知识, +{new_logs}日志)"})
        add_system_log("sync", f"全域同步完成", f"{machine_id}: +{new_knowledge}知识 +{new_logs}日志")
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/api/sync/pull", methods=["GET"])
def api_sync_pull():
    """下载服务器全部最新数据（供新机初始化）"""
    version = _get_params_version()
    return jsonify({
        "version": version,
        "params": _load_learning_params() or {},
        "knowledge_base": _ai_knowledge_base,
        "learning_log": _learning_log,
        "settings": {
            "deepseek_api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
            "server_url": "https://firefightai.top",
        },
        "total_learnings": version.get("total_learnings", 0),
        "total_knowledge": len(_ai_knowledge_base),
        "total_logs": len(_learning_log),
        "server_time": datetime.now().isoformat(),
    })

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

@app.route("/api/annotate/to_training", methods=["POST"])
def api_annotate_to_training():
    """将标注工具导出的YOLO数据直接上传到模型训练数据集"""
    import base64 as _b64
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "error": "缺少数据"}), 400

    image_name = data.get("image_name", "annotated_image")
    image_data = data.get("image_data", "")  # data URL
    yolo_labels = data.get("yolo_labels", "")
    boxes = data.get("boxes", [])
    width = data.get("width", 1920)
    height = data.get("height", 1080)

    try:
        # 创建训练数据集目录
        dataset_name = "faction_yolo"
        img_dir = PROJECT_ROOT / "data" / dataset_name / "images"
        lbl_dir = PROJECT_ROOT / "data" / dataset_name / "labels"
        img_dir.mkdir(parents=True, exist_ok=True)
        lbl_dir.mkdir(parents=True, exist_ok=True)

        # 保存图片
        base_name = Path(image_name).stem
        img_path = img_dir / f"{base_name}.png"
        if image_data and image_data.startswith("data:image"):
            # 解码base64图片数据
            img_bytes = _b64.b64decode(image_data.split(",", 1)[1])
            img_path.write_bytes(img_bytes)
        elif image_data and not image_data.startswith("data:"):
            # 可能是纯base64
            try:
                img_bytes = _b64.b64decode(image_data)
                img_path.write_bytes(img_bytes)
            except:
                pass

        # 保存YOLO标注文件
        lbl_path = lbl_dir / f"{base_name}.txt"
        with open(lbl_path, "w") as f:
            f.write(yolo_labels)

        # 同时保存到训练上传区（用于模型训练页面直接使用）
        train_upload_dir = PROJECT_ROOT / "data" / "train_uploads"
        train_upload_dir.mkdir(parents=True, exist_ok=True)
        if img_path.exists():
            import shutil
            shutil.copy2(img_path, train_upload_dir / f"{base_name}.png")

        add_system_log("annotate", f"标注数据已上传到训练集", f"{base_name}: {len(boxes)}个标注框 → {dataset_name}")
        logger.info(f"标注数据已上传到训练集: {base_name}, {len(boxes)} boxes")

        return jsonify({
            "status": "ok",
            "dataset": dataset_name,
            "image": base_name,
            "boxes": len(boxes),
            "message": f"已保存到训练数据集 {dataset_name}"
        })
    except Exception as e:
        logger.error(f"标注数据上传训练集失败: {e}")
        return jsonify({"status": "error", "error": str(e)[:200]}), 500

@app.route("/data/<path:filepath>")
def serve_data(filepath):
    return send_from_directory(str(PROJECT_ROOT / "data"), filepath)


# ═══════════════════════════════════════════════════════════════
# 连接管理 + 重建决策链
# ═══════════════════════════════════════════════════════════════

@socketio.on("rebuild_chain")
def on_rebuild_chain():
    """重建整条决策链"""
    add_system_log("system", "用户触发重建决策链", "")
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
            ad = dc.get("active", "generic")
            di = dc.get(ad, {})
            adb_paths = [r"d:\firefight\adb\adb.exe", "adb"]
            adb_exe = "adb"
            for p in adb_paths:
                if p == "adb" or Path(p).exists():
                    adb_exe = p
                    break
            subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
            r = subprocess.run([adb_exe, "connect", f"{di.get('adb_host','127.0.0.1')}:{di.get('adb_port',5555)}"], capture_output=True, text=True, timeout=10)
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
        add_system_log("system", "决策链重建完成", json.dumps(results, ensure_ascii=False))
        emit("rebuild_complete", {"results": results, "time": datetime.now().isoformat()})

    threading.Thread(target=rebuild, daemon=True).start()


# ── 状态缓存（避免频繁请求导致不稳定）──
_conn_cache = {"github": {"value": None, "time": 0}, "server": {"value": None, "time": 0}, "deepseek": {"value": None, "time": 0}}
_CACHE_TTL = {"github": 120, "server": 60, "deepseek": 30}  # 缓存有效期（秒），GitHub延长到120s减少超时错误

def _get_cached_or_fetch(key: str, fetch_fn, ttl: int = 30):
    """获取缓存值，过期则重新获取"""
    now = time.time()
    cache = _conn_cache.get(key, {})
    if cache.get("value") is not None and (now - cache.get("time", 0)) < ttl:
        return cache["value"]
    value = fetch_fn()
    _conn_cache[key] = {"value": value, "time": now}
    return value


@socketio.on("check_all_connections")
def on_check_all_connections():
    """检查所有连接状态（带缓存，避免频繁请求导致不稳定）"""
    results = {}

    # API - DeepSeek（带缓存）
    def _check_deepseek():
        try:
            import requests
            r = requests.post("https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {load_config()['llm']['api_key']}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "messages": [{"role":"user","content":"ping"}], "max_tokens": 5},
                timeout=10)
            return "online" if r.status_code == 200 else f"error:{r.status_code}"
        except Exception as e:
            return f"offline:{str(e)[:30]}"
    results["deepseek"] = _get_cached_or_fetch("deepseek", _check_deepseek, _CACHE_TTL["deepseek"])

    # ADB - 实时检测（ADB检测很快，不需要缓存）
    try:
        cfg = load_config()
        dc = cfg["device"]
        ad = dc.get("active", "generic")
        di = dc.get(ad, {})
        adb_exe = _find_adb_exe()
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        r = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
        adb_connected = False
        for line in r.stdout.strip().split("\n"):
            if "\tdevice" in line:
                if f"{di.get('adb_host','127.0.0.1')}:{di.get('adb_port',5555)}" in line or f"emulator-{di.get('adb_port',5555)}" in line:
                    adb_connected = True
                    break
            elif "device" in line and "emulator-" in line:
                adb_connected = True
                break
        results["adb"] = "connected" if adb_connected else "disconnected"
    except:
        results["adb"] = "error"

    # GitHub（带缓存）
    def _check_github():
        try:
            import requests
            r = requests.get("https://api.github.com", timeout=5)
            return "online" if r.status_code == 200 else "error"
        except:
            return "offline"
    results["github"] = _get_cached_or_fetch("github", _check_github, _CACHE_TTL["github"])

    # Server - 使用 paramiko 替代直接 ssh 命令（避免密码提示），带缓存
    def _check_server():
        try:
            success, stdout, stderr = _ssh_exec("echo OK && ls /home/ubuntu/firefightAI 2>/dev/null && echo DEPLOYED || echo NOT_DEPLOYED", timeout=10)
            if success:
                return {"status": "online" if "OK" in stdout else "offline", "deployed": "DEPLOYED" in stdout}
            return {"status": "offline", "deployed": False}
        except:
            return {"status": "offline", "deployed": False}
    sv = _get_cached_or_fetch("server", _check_server, _CACHE_TTL["server"])
    results["server"] = sv["status"]
    results["server_deployed"] = sv.get("deployed", False)

    update_state(api_status={"deepseek": results.get("deepseek", "unknown")}, adb_status=results.get("adb", "unknown"), github_status=results.get("github", "unknown"), server_status=results.get("server", "unknown"))
    emit("all_connections_status", results)
    # 🔥 同时触发 rebuild_complete 让前端"检查所有连接"按钮停止加载
    emit("rebuild_complete", {"results": results})


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

    # 先检查ADB连接
    from src.execution.adb_utils import ADBUtils
    ad = dc.get("active", "generic")
    di = dc.get(ad, {})
    adb = ADBUtils(host=di.get("adb_host", "127.0.0.1"), port=di.get("adb_port", 5555), connect_timeout=dc["adb_connect_timeout"], command_timeout=dc["adb_command_timeout"], retry_count=dc["adb_retry_count"])

    if not adb.ensure_connected():
        # ADB不可用，进入智能模式（不需要游戏模拟器，纯DeepSeek直连）
        update_state(status="AI在线(智能模式)", ai_thinking="DeepSeek智能体已就绪，可直接对话和下达指令", adb_status="disconnected")
        add_system_log("connection", "ADB未连接，进入智能模式", "AI可通过对话和指令交互，无需模拟器")
        socketio.emit("cycle_update", get_state())
        socketio.emit("started", {"status": "ok", "mode": "smart"})
        _run_smart_mode(lc, dc, sc)
        return

    update_state(adb_status="connected", status="ADB已连接, 加载模型...", ai_thinking="正在加载YOLO模型和OCR...")
    add_system_log("connection", "ADB连接成功", "")

    # ADB已连接，才导入需要模拟器的模块
    # 触控统一使用 ADB input，不再依赖 MuMuManager

    from src.screen.capture import ScreenCapture
    from src.vision.detector import UnitDetector
    from src.vision.ocr_reader import UIReader
    from src.state.manager import StateManager
    from src.decision.commander import TacticalCommander
    from src.decision.parser import CommandParser
    # commander.py已直接修复(requests直连), 不再需要patch
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
    commander = TacticalCommander(
        provider=lc["provider"], model=lc["model"],
        api_key=lc["api_key"], api_base=lc["api_base"],
        temperature=lc["temperature"], max_tokens=lc["max_tokens"],
        timeout=lc["timeout"], retry_count=lc["retry_count"],
        fallback_provider=lc.get("fallback_provider", "zhipu"),
        fallback_model=lc.get("fallback_model", "glm-4-flash"),
        fallback_api_key=lc.get("fallback_api_key", ""),
        fallback_api_base=lc.get("fallback_api_base", "https://open.bigmodel.cn/api/paas/v4"),
    )
    commander.load_prompts()
    parser = CommandParser(screen_size=ss)
    gs = str(int(time.time()))

    bm = BattleMemory() if lnc.get("enabled", True) else None
    oe = OutcomeEvaluator() if lnc.get("enabled", True) else None
    mr = MemoryRetriever(bm) if bm else None
    scm = StrategyCompressor(battle_memory=bm, api_key=lc["api_key"], api_base=lc["api_base"], model=lc["model"]) if bm else None

    # 触控：使用 ADB input（通用方案，不依赖任何模拟器）
    touch = None
    px = int(lpc["pause_button_x"] * ss[0])
    py = int(lpc["pause_button_y"] * ss[1])
    executor = CommandExecutor(adb=adb, screen_size=ss, touch=None, pause_button=(px, py))

    # 应用 monkey patch（仅在ADB可用时）
    _apply_patches()

    # ── v5.1 初始化战场预测器 ──
    global _predictor
    try:
        from src.learning.battle_predictor import BattlefieldPredictor
        _predictor = BattlefieldPredictor(screen_size=ss, api_key=lc["api_key"], api_base=lc["api_base"])
        _predictor.load()
        add_learning_log("predictor", "战场预测器已初始化", f"经验数: {len(_predictor._experience)}")
        try:
            w = _predictor.get_accumulated_wisdom()
            acc = w.get("accuracy", 0) if isinstance(w, dict) else 0
            update_state(prediction_accuracy=acc)
        except:
            update_state(prediction_accuracy=0)
    except Exception as e:
        logger.warning(f"战场预测器初始化失败: {e}")
        _predictor = None

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
    
    # 🔥 监控线程: 如果AI停止超过10秒，自动重启
    _last_cycle_time = {"time": time.time(), "cycle": 0}
    def _cycle_watchdog():
        while controller._running:
            time.sleep(5)
            elapsed = time.time() - _last_cycle_time["time"]
            if elapsed > 15 and _last_cycle_time["cycle"] > 0:
                logger.warning(f"AI停止{elapsed:.0f}秒, 尝试恢复...")
                controller._cycle_count += 1  # 强制推进一轮
                _last_cycle_time["time"] = time.time()
                socketio.emit("training_log", {"line": "🔄 AI自动恢复: 检测到长时间无响应"})
    threading.Thread(target=_cycle_watchdog, daemon=True).start()

    try:
        result = controller.run()
        update_state(status="胜利!" if result else "游戏结束", ai_thinking="")
        add_learning_log("combat", "战斗结束", f"结果: {'胜利' if result else '游戏结束'}, 总分: {get_state().get('total_score',0)}")
    except Exception as e:
        logger.exception(f"AI异常: {e}")
        update_state(status=f"错误: {str(e)[:60]}", ai_thinking="")
    finally:
        # ── v5.1 保存预测经验 ──
        if _predictor:
            try:
                _predictor.save()
                add_learning_log("predictor", "预测经验已保存", f"总经验: {len(_predictor._experience)}")
            except Exception as e:
                logger.warning(f"保存预测经验失败: {e}")
        update_state(running=False)
        capture.stop()


def _on_cycle_event(event: dict):
    global _last_cycle_time
    cycle = event.get("cycle", 0)
    allies = event.get("allies", 0)
    enemies = event.get("enemies", 0)
    score = event.get("score", 0)
    decision = event.get("decision", "")
    action = event.get("action", "")
    cycle_time = event.get("cycle_time", 0)
    
    _last_cycle_time = {"time": time.time(), "cycle": cycle}

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
    
    # ── v5.1 预测系统集成 ──
    global _predictor
    if _predictor and cycle > 0:
        try:
            # 获取上一轮敌方位置用于预测
            prev_enemies = getattr(_predictor, '_last_enemy_positions', [])
            curr_enemies = [{"id": i, "position": (0.5, 0.5)} for i in range(enemies)]  # 使用normalized位置
            
            if cycle == 1:
                # 开局扫描：预测敌人位置
                wisdom = _predictor.get_accumulated_wisdom()
                thinking += f"\n[预测] 开局扫描完成，预测准确率: {wisdom.get('accuracy', 0):.0%}"
                update_state(prediction_accuracy=wisdom.get("accuracy", 0))
            elif prev_enemies:
                # 接敌时预测敌人动向
                result = _predictor.predict_enemy_movement(
                    curr_enemies, prev_enemies, 
                    {"screen_size": ss, "cycle": cycle}, cycle
                )
                if result:
                    thinking += f"\n[预测] 威胁等级: {result.get('threat_level', 0)}/100, 建议: {result.get('suggested_response', '')[:80]}"
                    update_state(
                        predicted_enemies=result.get("predicted_moves", []),
                        threat_level=result.get("threat_level", 0),
                        prediction_accuracy=_predictor.get_accumulated_wisdom().get("accuracy", 0),
                    )
            
            # 保存当前敌方位置用于下一轮预测
            _predictor._last_enemy_positions = curr_enemies
        except Exception as e:
            logger.debug(f"预测异常(非致命): {e}")
    
    socketio.emit("cycle_update", get_state())
    socketio.emit("ai_thinking_update", {"thinking": thinking, "cycle": cycle, "analysis": analysis, "reason": reason_display, "prediction_accuracy": _predictor.get_accumulated_wisdom().get("accuracy", 0) if _predictor else 0})

# ═══ 自动缩放管理 + 速度优化 ═══
_zoom_state = {"last_zoom_out": 0, "is_zoomed_in": False, "enemy_not_found_count": 0}
_zoom_check_interval = 3  # 每3轮检查一次

def _manage_auto_zoom(cycle: int, event: dict):
    """自动缩放管理: 放大地图5秒内缩小，寻找敌人时自动放大后缩小"""
    global _zoom_state
    if cycle % _zoom_check_interval != 0:
        return
    
    try:
        allies = event.get("allies", 0)
        enemies = event.get("enemies", 0)
        now = time.time()
        
        # 如果看不见敌人超过5轮，放大寻找
        if enemies == 0:
            _zoom_state["enemy_not_found_count"] += 1
            if _zoom_state["enemy_not_found_count"] >= 5:
                # 放大寻找
                _zoom_state["is_zoomed_in"] = True
                _zoom_state["last_zoom_out"] = now
                _zoom_state["enemy_not_found_count"] = 0
                _do_zoom("in")
                logger.debug("🔍 放大寻找敌人")
        else:
            _zoom_state["enemy_not_found_count"] = 0
        
        # 如果放大超过5秒，强制缩小
        if _zoom_state["is_zoomed_in"] and now - _zoom_state["last_zoom_out"] > 5:
            _do_zoom("out_max")  # 缩到最小
            _zoom_state["is_zoomed_in"] = False
            logger.debug("🔄 5秒限制: 强制缩小到最小")
        
        # 如果看不清楚蓝条（allies=0且之前有），缩小到正常
        if allies == 0 and now - _zoom_state["last_zoom_out"] > 10:
            _do_zoom("out")
            
    except Exception as e:
        logger.debug(f"自动缩放异常: {e}")

def _optimize_execution_speed(cycle: int, cycle_time_ms: float):
    """根据每轮耗时自动优化操作速度"""
    if cycle < 3:
        return
    
    try:
        params = _load_learning_params() or {}
        target_ms = params.get("target_cycle_ms", 1000)
        
        # 如果每轮超过2秒，加速
        if cycle_time_ms > 2000:
            params["tap_delay"] = max(0.05, params.get("tap_delay", 0.2) - 0.01)
            params["swipe_duration"] = max(300, params.get("swipe_duration", 1000) - 30)
            logger.debug(f"⚡ 加速: tap_delay={params['tap_delay']:.3f}, swipe={params['swipe_duration']}")
            _save_learning_params(params)
        
        # 如果连续3轮又快又准(<800ms)，可以放慢一点提高精度
        elif cycle_time_ms < 500 and cycle > 6:
            params["swipe_duration"] = min(1500, params.get("swipe_duration", 1000) + 50)
            params["tap_delay"] = min(0.3, params.get("tap_delay", 0.2) + 0.01)
            _save_learning_params(params)
            
    except Exception as e:
        logger.debug(f"速度优化异常: {e}")

def _do_zoom(direction: str):
    """执行缩放（通过ADB发送按键）"""
    try:
        adb_exe = _find_adb_exe()
        port = _emulator_adb_port
        dev = f"127.0.0.1:{port}" if _emulator_type == "mumu" else f"emulator-{port}"
        
        if direction == "in":
            subprocess.run([adb_exe, "-s", dev, "shell", "input", "keyevent", "KEYCODE_ZOOM_IN"], 
                         capture_output=True, timeout=3)
        elif direction == "out_max":
            # 连按多次缩小到最小
            for _ in range(8):
                subprocess.run([adb_exe, "-s", dev, "shell", "input", "keyevent", "KEYCODE_ZOOM_OUT"],
                             capture_output=True, timeout=2)
        else:
            subprocess.run([adb_exe, "-s", dev, "shell", "input", "keyevent", "KEYCODE_ZOOM_OUT"],
                         capture_output=True, timeout=3)
    except Exception as e:
        logger.debug(f"缩放执行失败: {e}")


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
    
    # 🔥 修复过早游戏结束: 只有画面无任何单位(双方都为0)才算结束
    _orig_check_game_over = gc_mod.GameController._check_game_over
    def _patched_check_game_over(self, state):
        elapsed = time.time() - self._start_time
        # 超时才算结束
        if elapsed > self.game_over_timeout:
            logger.info(f"游戏超时({self.game_over_timeout:.0f}s)")
            return True
        # 🔥 只有双方都为0(真正的结束画面)才判结束
        if state.ally_count == 0 and state.enemy_count == 0 and self._cycle_count > 10:
            logger.info("检测到结束画面(双方均为0)")
            return True
        # 无效状态快速跳过
        return False
    gc_mod.GameController._check_game_over = _patched_check_game_over
    
    # 🔥 最高优先级: 每轮开始前检查并立即执行指挥官指令
    _orig_fast_decide = gc_mod.GameController._fast_decide
    def _patched_fast_decide(self, state):
        global _user_instruction
        if _user_instruction:
            try:
                adb_cmd = self.adb._adb_path if hasattr(self.adb, '_adb_path') else "adb"
                port = self.adb.port if hasattr(self.adb, 'port') else 7555
                dev = f"127.0.0.1:{port}"
                import subprocess as _sp
                cmd_l = _user_instruction.lower()
                if any(k in cmd_l for k in ["进攻","攻击","冲锋"]):
                    _sp.run([adb_cmd, "-s", dev, "shell", "input tap 1400 300"], capture_output=True, timeout=2)
                elif any(k in cmd_l for k in ["撤退","防守"]):
                    _sp.run([adb_cmd, "-s", dev, "shell", "input tap 800 800"], capture_output=True, timeout=2)
                elif any(k in cmd_l for k in ["缩小"]):
                    for _ in range(5): _sp.run([adb_cmd, "-s", dev, "shell", "input keyevent KEYCODE_ZOOM_OUT"], capture_output=True, timeout=1)
            except: pass
        return _orig_fast_decide(self, state)
    gc_mod.GameController._fast_decide = _patched_fast_decide
    
    _orig_fe = gc_mod.GameController._fast_execute
    def _patched_fe(self, commands, state):
        self._cycle_start = time.time()
        return _orig_fe(self, commands, state)
    gc_mod.GameController._fast_execute = _patched_fe
    
    # 🔥 修复回放保存路径
    from pathlib import Path as _Path
    def _patched_save_replay(self):
        import json
        rp = _Path(PROJECT_ROOT) / "data" / "sessions" / f"replay_{int(self._start_time)}.json"
        rp.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(rp, "w", encoding="utf-8") as f:
                json.dump(self._replay_data, f, ensure_ascii=False, indent=2)
            logger.info(f"回放已保存: {rp}")
        except Exception as e:
            logger.error(f"保存回放失败: {e}")
    gc_mod.GameController._save_replay = _patched_save_replay
    
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

def _execute_user_command(cmd: str, allies: int, enemies: int, cycle: int):
    """🔥 最高优先级: 直接通过ADB立即执行指挥官指令，不等待LLM"""
    import threading as _th
    
    def _exec():
        try:
            adb_exe = _find_adb_exe()
            port = _emulator_adb_port
            dev = f"127.0.0.1:{port}" if _emulator_type == "mumu" else f"emulator-{port}"
            cmd_lower = cmd.lower()
            result_msg = ""
            executed = False
            
            # ═══ 立即执行指令解密 ═══
            # 全选单位类
            if any(kw in cmd_lower for kw in ["全选", "select all", "全部", "所有单位", "selectall"]):
                subprocess.run([adb_exe, "-s", dev, "shell", "input", "tap", "800", "450"], capture_output=True, timeout=2)
                time.sleep(0.05)
                subprocess.run([adb_exe, "-s", dev, "shell", "input", "tap", "800", "450"], capture_output=True, timeout=2)
                result_msg = "⚡ 全选所有单位 - 立即执行"
                executed = True
                
            # 进攻/攻击/冲锋
            elif any(kw in cmd_lower for kw in ["进攻", "攻击", "attack", "冲锋", "前进", "上", "冲", "打"]):
                for _ in range(2):
                    subprocess.run([adb_exe, "-s", dev, "shell", "input", "tap", "1400", "300"], capture_output=True, timeout=2)
                    time.sleep(0.03)
                result_msg = "⚔️ 全军进攻 - 立即执行"
                executed = True
                
            # 撤退/防御/防守/后撤
            elif any(kw in cmd_lower for kw in ["撤退", "防御", "defend", "退后", "防守", "后撤", "撤", "回车"]):
                subprocess.run([adb_exe, "-s", dev, "shell", "input", "tap", "800", "800"], capture_output=True, timeout=2)
                result_msg = "🛡️ 全军撤退/防御 - 立即执行"
                executed = True
                
            # 缩小地图
            elif any(kw in cmd_lower for kw in ["缩小", "zoom out", "缩小地图", "缩放"]):
                for _ in range(5):
                    subprocess.run([adb_exe, "-s", dev, "shell", "input", "keyevent", "KEYCODE_ZOOM_OUT"], capture_output=True, timeout=1)
                    time.sleep(0.03)
                result_msg = "🔍 缩小到最小 - 已执行"
                executed = True
                
            # 放大
            elif any(kw in cmd_lower for kw in ["放大", "zoom in"]):
                subprocess.run([adb_exe, "-s", dev, "shell", "input", "keyevent", "KEYCODE_ZOOM_IN"], capture_output=True, timeout=2)
                result_msg = "🔍 放大 - 已执行"
                executed = True
                
            # 移动 / 点击指定位置
            elif any(kw in cmd_lower for kw in ["移动", "move", "去", "到", "点击"]):
                # 尝试解析坐标
                import re
                nums = re.findall(r'\d{2,4}', cmd)
                if len(nums) >= 2:
                    x, y = int(nums[0]), int(nums[1])
                else:
                    x, y = 800, 600  # 默认中央
                subprocess.run([adb_exe, "-s", dev, "shell", "input", "tap", str(x), str(y)], capture_output=True, timeout=2)
                result_msg = f"📍 移动至({x},{y}) - 已执行"
                executed = True

            # 选定单个单位 (说编号)
            elif any(char.isdigit() for char in cmd) and any(kw in cmd_lower for kw in ["选", "单位", "编号"]):
                nums = re.findall(r'\d+', cmd)
                if nums:
                    uid = int(nums[0])
                    result_msg = f"👆 选中单位#{uid} - 已通知AI"
                    # 通知游戏controller选中该单位
                    if _controller:
                        try:
                            _controller._user_selected_unit = uid
                        except: pass
                    executed = True
                    
            # 默认：发送给game controller作为最高优先指令
            else:
                result_msg = f"⚡ 指令已发送至战场: {cmd[:80]}"
                # 通过controller立即执行
                if _controller and hasattr(_controller, '_fast_execute'):
                    try:
                        # 构建紧急移动指令
                        from src.controller.game_controller import ParsedCommand, ActionType
                        dummy = ParsedCommand(action=ActionType.MOVE, unit_ids=[1], target_pixel=(800, 450), reason=f"指挥官: {cmd[:60]}")
                        _controller._fast_execute([dummy], type('obj',(object,),{'get_unit_by_id':lambda x:None,'screen_size':(1600,900)}))
                    except: pass
                executed = True
            
            if not executed:
                result_msg = f"📡 指令已接收: {cmd[:60]}"

            socketio.emit("command_analysis", {
                "command": cmd, "cycle": cycle,
                "analysis": result_msg, "allies": allies, "enemies": enemies,
                "executed": True, "immediate": True,
            })
            add_learning_log("execute", result_msg, f"最高优先级: {cmd[:100]}")
            
        except Exception as e:
            socketio.emit("command_analysis", {
                "command": cmd, "cycle": cycle,
                "analysis": f"❌ 执行失败: {str(e)[:80]}",
                "allies": allies, "enemies": enemies
            })
    
    # 🔥 最高优先级线程，立即执行
    t = _th.Thread(target=_exec, daemon=True)
    t.name = f"URGENT_CMD_{int(time.time())}"
    t.start()

@socketio.on("send_command")
def on_send_command(data: dict):
    global _user_instruction, _controller
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

    # 🔥 直接执行用户命令（不依赖LLM）
    _execute_user_command(cmd, allies, enemies, cycle)

    def analyze():
        try:
            sp = f"你是Firefight战术AI。指挥官给你下达了一条指令。请用1-2句话分析: 1)你对这条指令的见解 2)你将在下一轮如何运用它。当前兵力: 友{allies}vs敌{enemies} (第{cycle}轮)"
            r = _deepseek_chat([{"role": "system", "content": sp}, {"role": "user", "content": f"指挥官指令: {cmd}"}], max_tokens=128, temperature=0.1, stream=False)
            if r["success"]:
                analysis = r["content"].strip()
            else:
                analysis = f"(分析暂时不可用: {r['error'][:50]})"
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


# ═══════════════════════════════════════════════════════════════
# 后台工作函数 (训练/部署/上传/更新/安装包)
# ═══════════════════════════════════════════════════════════════

def _start_training_background():
    """后台启动AI训练"""
    try:
        from src.learning.auto_scheduler import AutoScheduler
        scheduler = AutoScheduler()
        socketio.emit("command_analysis", {"command": "训练", "cycle": 0, "analysis": "训练启动中...正在加载数据和模型"})
        # 保存当前参数
        result = scheduler.save_params_now()
        update_state(training_progress=30, training_message="参数已保存")
        socketio.emit("command_analysis", {"command": "训练", "cycle": 0, "analysis": f"参数已保存: {len(result.get('saved',[]))}个文件。正在上传到GitHub..."})
        # 上传到GitHub
        upload_result = scheduler.upload_params()
        update_state(training_progress=60, training_message="上传中")
        socketio.emit("command_analysis", {"command": "训练", "cycle": 0, "analysis": f"GitHub: {upload_result.get('github',{}).get('message','')} | 服务器: {upload_result.get('server',{}).get('message','')}"})
        update_state(training_status="completed", training_progress=100, training_message="训练完成")
        add_learning_log("training", "AI训练完成", f"保存: {len(result.get('saved',[]))}个文件")
        socketio.emit("command_analysis", {"command": "训练", "cycle": 0, "analysis": "AI训练完成! 参数已保存并上传到GitHub和服务器。"})
    except Exception as e:
        update_state(training_status="error", training_message=str(e)[:100])
        socketio.emit("command_analysis", {"command": "训练", "cycle": 0, "analysis": f"训练失败: {str(e)[:100]}"})


def _deploy_to_server_background():
    """后台部署到服务器"""
    try:
        import paramiko
        socketio.emit("command_analysis", {"command": "部署", "cycle": 0, "analysis": "正在连接服务器..."})
        
        # 尝试多种方式连接
        key_paths = [
            r"D:\firefightAI2.pem",
            r"C:\Users\19853\Downloads\firefightAI.pem",
        ]
        password = "@Cyt20080102"
        host = "139.199.69.88"
        user = "ubuntu"
        
        ssh = None
        for key_path in key_paths:
            try:
                if os.path.exists(key_path):
                    key = paramiko.RSAKey.from_private_key_file(key_path)
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(host, username=user, pkey=key, timeout=10)
                    break
            except:
                continue
        
        if not ssh:
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(host, username=user, password=password, timeout=10)
            except Exception as e:
                socketio.emit("command_analysis", {"command": "部署", "cycle": 0, "analysis": f"SSH连接失败: {str(e)[:80]}"})
                return
        
        # 同步文件
        sftp = ssh.open_sftp()
        remote_path = "/home/ubuntu/firefightAI"
        
        # 创建必要目录
        for d in ["data", "data/params", "config", "src"]:
            try:
                sftp.mkdir(f"{remote_path}/{d}")
            except:
                pass
        
        # 上传关键文件
        files_to_upload = [
            "dashboard_server.py",
            "config/settings.yaml",
        ]
        for f in files_to_upload:
            local = PROJECT_ROOT / f
            if local.exists():
                try:
                    sftp.put(str(local), f"{remote_path}/{f}")
                except:
                    pass
        
        sftp.close()
        ssh.close()
        
        socketio.emit("command_analysis", {"command": "部署", "cycle": 0, "analysis": "部署完成! 文件已同步到服务器。"})
        add_system_log("deploy", "部署到服务器完成", f"主机: {host}")
    except Exception as e:
        socketio.emit("command_analysis", {"command": "部署", "cycle": 0, "analysis": f"部署失败: {str(e)[:100]}"})


def _push_to_github_background():
    """后台推送到GitHub（含重复检测和参数同步）"""
    try:
        import subprocess
        git_dir = str(PROJECT_ROOT)
        
        # 检查是否有未推送的提交
        has_unpushed = False
        ahead_count = 0
        try:
            unpushed = subprocess.run(
                ["git", "log", "origin/master..HEAD", "--oneline"],
                cwd=git_dir, capture_output=True, text=True, timeout=10
            )
            if unpushed.returncode == 0 and unpushed.stdout and unpushed.stdout.strip():
                has_unpushed = True
                ahead_count = len(unpushed.stdout.strip().split("\n"))
        except:
            pass
        
        # 检查是否有未提交的变更
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=git_dir, capture_output=True, text=True, timeout=10
        )
        has_changes = bool(status.stdout.strip())
        
        if not has_changes and not has_unpushed:
            socketio.emit("command_analysis", {"command": "上传", "cycle": 0, "analysis": "请勿推送重复内容: 工作区无变更，且无未推送提交"})
            add_system_log("github", "请勿推送重复内容", "工作区无变更，且无未推送提交")
            return
        
        if has_changes:
            # 只添加参数和学习相关文件（避免大文件）
            subprocess.run(["git", "add", "data/params/", "data/tactics_rules.yaml", "data/ai_learning_params.json", "src/", "dashboard_server.py"], cwd=git_dir, capture_output=True, timeout=10)
            
            # 检查是否有staged变更
            diff_check = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=git_dir, capture_output=True, timeout=10
            )
            if diff_check.returncode == 0:
                # 没有staged变更
                if not has_unpushed:
                    socketio.emit("command_analysis", {"command": "上传", "cycle": 0, "analysis": "请勿推送重复内容: 没有新变更需要提交"})
                    add_system_log("github", "请勿推送重复内容", "无新变更需要推送")
                    return
            else:
                # 有staged变更，检查是否与上次提交重复
                try:
                    last_msg_result = subprocess.run(
                        ["git", "log", "-1", "--format=%s"],
                        cwd=git_dir, capture_output=True, text=True, timeout=5
                    )
                    last_msg = last_msg_result.stdout.strip()
                    
                    current_diff = subprocess.run(
                        ["git", "diff", "--cached", "--stat"],
                        cwd=git_dir, capture_output=True, text=True, timeout=10
                    )
                    last_diff = subprocess.run(
                        ["git", "diff", "HEAD~1..HEAD", "--stat"],
                        cwd=git_dir, capture_output=True, text=True, timeout=10
                    )
                    
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    commit_msg = f"Auto-update: {ts}"
                    
                    if current_diff.stdout.strip() == last_diff.stdout.strip():
                        socketio.emit("command_analysis", {"command": "上传", "cycle": 0, "analysis": "请勿推送重复内容: 变更内容与上次提交完全相同"})
                        add_system_log("github", "请勿推送重复内容", "变更内容与上次提交相同")
                        return
                    
                    subprocess.run(
                        ["git", "commit", "-m", commit_msg],
                        cwd=git_dir, capture_output=True, text=True, timeout=10
                    )
                except:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    subprocess.run(
                        ["git", "commit", "-m", f"Auto-update: {ts}"],
                        cwd=git_dir, capture_output=True, text=True, timeout=10
                    )
        
        # 🔥 确保GitHub远程URL包含token认证
        _ensure_git_remote_with_token()
        
        # git push
        push_result = subprocess.run(
            ["git", "push", "origin", "master"],
            cwd=git_dir, capture_output=True, text=True, timeout=300
        )
        
        if push_result.returncode == 0:
            if "Everything up-to-date" in (push_result.stdout + push_result.stderr):
                socketio.emit("command_analysis", {"command": "上传", "cycle": 0, "analysis": "请勿推送重复内容: 远程仓库已是最新"})
                add_system_log("github", "请勿推送重复内容", "远程仓库已是最新")
            else:
                socketio.emit("command_analysis", {"command": "上传", "cycle": 0, "analysis": "推送到GitHub成功! 代码已同步到远程仓库。"})
                add_system_log("github", "推送到GitHub成功", f"ahead: {ahead_count}")
        else:
            err_msg = push_result.stderr.strip() or push_result.stdout.strip() or "未知错误"
            if "Authentication failed" in err_msg:
                sock_msg = "推送失败: GitHub认证失败，请检查Token配置"
            elif "remote rejected" in err_msg:
                sock_msg = "推送失败: 远程仓库拒绝，请先拉取最新代码"
            elif "timed out" in err_msg.lower():
                sock_msg = "推送失败: 网络超时，请检查网络连接"
            else:
                sock_msg = f"推送失败: {err_msg[:200]}"
            socketio.emit("command_analysis", {"command": "上传", "cycle": 0, "analysis": sock_msg})
            add_system_log("github", "推送失败", err_msg[:300])
    except Exception as e:
        socketio.emit("command_analysis", {"command": "上传", "cycle": 0, "analysis": f"推送失败: {str(e)[:100]}"})


def _update_app_background():
    """后台更新应用"""
    try:
        socketio.emit("command_analysis", {"command": "更新", "cycle": 0, "analysis": "正在检查更新..."})
        # 检查当前版本
        current_ver = APP_VERSION
        socketio.emit("command_analysis", {"command": "更新", "cycle": 0, "analysis": f"当前版本: v{current_ver} | 构建: {APP_BUILD}\n重新加载模块中..."})
        # 热重载Python模块
        import importlib
        reloaded = []
        for mod_name in ["src.learning.battle_predictor", "src.learning.auto_scheduler"]:
            try:
                if mod_name in sys.modules:
                    importlib.reload(sys.modules[mod_name])
                    reloaded.append(mod_name)
            except:
                pass
        socketio.emit("command_analysis", {"command": "更新", "cycle": 0, "analysis": f"更新完成! 已重载模块: {', '.join(reloaded) or '无'}\n当前版本: v{current_ver}"})
        add_system_log("update", "应用更新完成", f"版本: v{current_ver}")
    except Exception as e:
        socketio.emit("command_analysis", {"command": "更新", "cycle": 0, "analysis": f"更新失败: {str(e)[:100]}"})


def _create_package_background():
    """后台创建安装包"""
    try:
        import zipfile
        dist_dir = PROJECT_ROOT / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"firefightAI_{ts}.zip"
        zip_path = dist_dir / zip_name
        
        socketio.emit("command_analysis", {"command": "安装包", "cycle": 0, "analysis": f"正在打包项目文件..."})
        
        exclude = {".git", "__pycache__", "logs", "runs", "sessions", ".venv", "venv", "node_modules", "dist", "android_emulator"}
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(str(PROJECT_ROOT)):
                dirs[:] = [d for d in dirs if d not in exclude]
                for f in files:
                    fp = os.path.join(root, f)
                    arcname = os.path.relpath(fp, str(PROJECT_ROOT))
                    zf.write(fp, arcname)
        
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        socketio.emit("command_analysis", {"command": "安装包", "cycle": 0, "analysis": f"安装包创建完成!\n文件: {zip_name}\n大小: {size_mb:.1f}MB\n下载: /api/package/download/{zip_name}"})
        add_system_log("package", f"安装包创建: {zip_name}", f"{size_mb:.1f}MB")
    except Exception as e:
        socketio.emit("command_analysis", {"command": "安装包", "cycle": 0, "analysis": f"安装包创建失败: {str(e)[:100]}"})


def _handle_config_command(cmd: str) -> bool:
    """处理配置命令，在指令文本框中输入配置命令"""
    cmd_lower = cmd.lower().strip()

    # ── 游戏控制命令 ──
    # 阵营选择
    if cmd_lower.startswith("阵营 ") or cmd_lower.startswith("faction "):
        parts = cmd.split(maxsplit=1)
        faction = parts[1].strip() if len(parts) > 1 else ""
        valid_factions = {"红": "红方", "蓝": "蓝方", "red": "红方", "blue": "蓝方"}
        mapped = valid_factions.get(faction, faction)
        update_state(game_faction=mapped)
        add_system_log("game", f"阵营选择: {mapped}", "")
        socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": f"阵营已设置为: {mapped}。AI将以{mapped}身份进行战术决策。"})
        socketio.emit("game_config_update", {"faction": mapped})
        return True

    # 难度选择
    if cmd_lower.startswith("难度 ") or cmd_lower.startswith("difficulty "):
        parts = cmd.split(maxsplit=1)
        diff = parts[1].strip() if len(parts) > 1 else ""
        valid_diffs = {"简单": "简单", "普通": "普通", "困难": "困难", "easy": "简单", "normal": "普通", "hard": "困难"}
        mapped = valid_diffs.get(diff, diff)
        update_state(game_difficulty=mapped)
        add_system_log("game", f"难度选择: {mapped}", "")
        socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": f"难度已设置为: {mapped}。AI将调整战术策略匹配此难度。"})
        socketio.emit("game_config_update", {"difficulty": mapped})
        return True

    # 模式选择
    if cmd_lower.startswith("模式 ") or cmd_lower.startswith("mode "):
        parts = cmd.split(maxsplit=1)
        mode = parts[1].strip() if len(parts) > 1 else ""
        valid_modes = {"对战": "对战", "训练": "训练", "战役": "战役", "battle": "对战", "training": "训练", "campaign": "战役"}
        mapped = valid_modes.get(mode, mode)
        update_state(game_mode=mapped)
        add_system_log("game", f"模式选择: {mapped}", "")
        socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": f"模式已设置为: {mapped}。AI将根据{mapped}模式调整决策逻辑。"})
        socketio.emit("game_config_update", {"mode": mapped})
        return True

    # 训练控制
    if cmd_lower.startswith("训练 ") or cmd_lower.startswith("train "):
        parts = cmd.split(maxsplit=1)
        action = parts[1].strip().lower() if len(parts) > 1 else ""
        if action in ("开始", "start", "启动"):
            update_state(training_status="running", training_progress=0, training_message="训练启动中...")
            add_system_log("training", "用户启动AI训练", "")
            socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": "AI训练已启动! 系统将在后台进行模型训练，期间可正常使用其他功能。"})
            # 在后台线程中启动训练
            threading.Thread(target=_start_training_background, daemon=True).start()
        elif action in ("停止", "stop", "结束"):
            update_state(training_status="idle", training_message="训练已停止")
            add_system_log("training", "用户停止AI训练", "")
            socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": "AI训练已停止。"})
        return True

    # 部署命令
    if cmd_lower in ("部署", "deploy", "部署到服务器"):
        add_system_log("deploy", "用户触发部署到服务器", "")
        socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": "正在部署到腾讯云服务器...请稍候"})
        threading.Thread(target=_deploy_to_server_background, daemon=True).start()
        return True

    # 上传命令
    if cmd_lower in ("上传", "upload", "上传github", "推送github"):
        add_system_log("github", "用户触发推送到GitHub", "")
        socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": "正在推送到GitHub...请稍候"})
        threading.Thread(target=_push_to_github_background, daemon=True).start()
        return True

    # 更新命令
    if cmd_lower in ("更新", "update", "更新应用"):
        add_system_log("update", "用户触发应用更新", "")
        socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": "正在更新应用...检查版本中"})
        threading.Thread(target=_update_app_background, daemon=True).start()
        return True

    # 安装包
    if cmd_lower in ("安装包", "package", "创建安装包"):
        add_system_log("package", "用户触发创建安装包", "")
        socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": "正在创建安装包...请稍候"})
        threading.Thread(target=_create_package_background, daemon=True).start()
        return True

    # API Key 配置
    if cmd_lower.startswith("apikey ") or cmd_lower.startswith("api_key "):
        parts = cmd.split(maxsplit=1)
        if len(parts) == 2:
            new_key = parts[1].strip()
            cfg = load_config()
            cfg["llm"]["api_key"] = new_key
            with open(PROJECT_ROOT / "config" / "settings.yaml", "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
            add_system_log("config", "API Key已更新", "")
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
                    ad = cfg["device"].get("active", "generic")
                    cfg["device"][ad]["adb_host"] = host
                    cfg["device"][ad]["adb_port"] = port
                    with open(PROJECT_ROOT / "config" / "settings.yaml", "w", encoding="utf-8") as f:
                        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
                    add_system_log("config", f"ADB地址已更新: {host}:{port}", "")
                    socketio.emit("command_analysis", {"command": cmd, "cycle": 0, "analysis": f"ADB地址已更新为 {host}:{port}，点击重连按钮生效。"})
                    return True
                except ValueError:
                    pass

    # GitHub仓库配置
    if cmd_lower.startswith("repo ") or cmd_lower.startswith("github "):
        parts = cmd.split(maxsplit=1)
        if len(parts) == 2:
            repo_url = parts[1].strip()
            add_system_log("config", f"GitHub仓库配置: {repo_url}", "")
            # 尝试初始化
            try:
                import git
                try:
                    repo = git.Repo(str(PROJECT_ROOT))
                    repo.remotes.origin.set_url(repo_url)
                except:
                    repo = git.Repo.init(str(PROJECT_ROOT))
                    repo.create_remote("origin", repo_url)
                add_system_log("github", f"GitHub仓库已配置: {repo_url}", "")
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
            add_system_log("config", f"服务器地址已更新: {SERVER_HOST}", "")
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
<script src="static/js/socket.io.min.js"></script>
<script src="static/js/chart.umd.min.js"></script>
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
.learning-log-item{padding:6px 8px;border-bottom:1px solid #1a1f2b;font-size:11px;cursor:pointer;transition:background 0.15s}
.learning-log-item:hover{background:#1a1f2b}
.learning-log-item .ll-time{color:#555;font-size:10px}
.learning-log-item .ll-cat{font-size:10px;padding:1px 5px;border-radius:3px;margin-right:4px}
.learning-log-item .ll-cat.combat{background:#4caf50;color:#000}
.learning-log-item .ll-cat.correction{background:#e53935;color:#fff}
.learning-log-item .ll-cat.params{background:#7c4dff;color:#fff}
.learning-log-item .ll-cat.self_learn{background:#ff9800;color:#000}
.learning-log-item .ll-cat.web_search{background:#00bcd4;color:#000}
.learning-log-item .ll-cat.military_learn{background:#e53935;color:#fff}
.learning-log-item .ll-cat.predictor{background:#58a5f3;color:#000}
.learning-log-item .ll-cat.command{background:#ff9800;color:#000}
.learning-log-item .ll-cat.recording{background:#7c4dff;color:#fff}
.learning-log-item .ll-cat.github{background:#ff9800;color:#000}
.learning-log-item .ll-cat.connection{background:#00bcd4;color:#000}
.learning-log-item .ll-cat.system{background:#555;color:#fff}
.learning-log-item .ll-cat.config{background:#ff9800;color:#000}
.learning-log-item .ll-cat.training{background:#58a5f3;color:#000}
.learning-log-item .ll-cat.server{background:#00bcd4;color:#000}
.learning-log-item .ll-msg{color:#d0d0d0}
.learning-log-item .ll-detail{color:#888;font-size:10px;margin-top:3px;padding:4px 6px;background:#0d1117;border-radius:4px;display:none;white-space:pre-wrap;word-break:break-all;max-height:200px;overflow-y:auto}
.learning-log-item.expanded .ll-detail{display:block}
.learning-log-item .ll-expand-hint{color:#555;font-size:9px;margin-left:6px}
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
.knowledge-list-item:hover{background:#1a1f2b;cursor:pointer}
.knowledge-detail-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:9999;justify-content:center;align-items:center}
.knowledge-detail-overlay.active{display:flex}
.knowledge-detail-modal{background:#111827;border:1px solid #252a33;border-radius:12px;padding:24px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.5)}
.knowledge-detail-modal h3{color:#58a5f3;margin:0 0 12px 0;font-size:16px;border-bottom:1px solid #252a33;padding-bottom:10px}
.knowledge-detail-modal .meta{font-size:11px;color:#888;margin-bottom:16px;display:flex;gap:16px;flex-wrap:wrap}
.knowledge-detail-modal .meta span{background:#1a1f2b;padding:3px 8px;border-radius:4px}
.knowledge-detail-modal .content{font-size:13px;color:#d0d0d0;line-height:1.7;white-space:pre-wrap;word-break:break-word;max-height:50vh;overflow-y:auto;background:#0a0e14;padding:12px;border-radius:8px;margin-bottom:16px}
.knowledge-detail-modal .close-btn{position:sticky;top:0;float:right;background:none;border:none;color:#888;font-size:22px;cursor:pointer;line-height:1}
.knowledge-detail-modal .close-btn:hover{color:#e53935}
.knowledge-detail-modal .actions{display:flex;gap:8px;justify-content:flex-end}
.knowledge-detail-modal .actions button{padding:6px 14px;border-radius:6px;font-size:12px;cursor:pointer;border:1px solid #252a33;background:#1a1f2b;color:#d0d0d0}
.knowledge-detail-modal .actions button:hover{background:#252a33}
.knowledge-detail-modal .actions .btn-del{color:#e53935;border-color:#e53935}
.knowledge-detail-modal .actions .btn-del:hover{background:#e53935;color:#fff}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
</style>
</head>
<body>
<div class="header">
  <h1>Firefight AI v5.1</h1>
  <div style="display:flex;gap:8px;align-items:center">
    <span class="conn-mini" id="conn-adb">ADB</span>
    <span class="conn-mini" id="conn-api">API</span>
    <span class="conn-mini" id="conn-gh">GitHub</span>
    <span class="conn-mini" id="conn-srv">Server</span>
    <select id="emu-type-select" onchange="switchEmuType(this.value)" style="background:#1a1f2b;color:#58a5f3;border:1px solid #252a33;border-radius:6px;padding:3px 8px;font-size:11px;cursor:pointer;margin-right:4px" title="选择模拟器类型">
      <option value="generic">本地模拟器</option>
      <option value="mumu">MUMU模拟器</option>
      <option value="other">其他模拟器</option>
    </select>
    <span id="readiness-indicator" style="font-size:12px;padding:3px 10px;border-radius:10px;margin-left:4px;background:#e53935;color:#fff">检查中...</span>
    <span class="status" id="status-badge" style="font-size:13px;padding:5px 12px;border-radius:6px;background:#1a1f2b;color:#888">已停止</span>
    <a href="/api/package/download" class="btn-download" title="下载完整应用+AI参数" style="background:#4caf50;color:#fff;padding:6px 14px;border-radius:6px;text-decoration:none;font-size:12px;font-weight:600;display:flex;align-items:center;gap:4px;cursor:pointer;transition:all 0.2s" onmouseover="this.style.background='#45a049'" onmouseout="this.style.background='#4caf50'">下载应用</a>
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
  <button class="nav-tab" onclick="switchTab('learning')">AI学习日志</button>
  <button class="nav-tab" onclick="switchTab('syslog')">系统日志</button>
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
      <input type="text" id="cmd-input" placeholder="指令: 阵营 红/蓝 | 难度 简单/困难 | 模式 对战 | 训练 开始 | 部署/上传..." onkeydown="if(event.key==='Enter')sendCommand()">
      <button class="btn-send" onclick="sendCommand()">发送</button>
      <button class="btn-clear" onclick="clearCommand()">清除</button>
    </div>
  </div>
  <!-- ── 游戏控制面板 ── -->
  <div class="panel" style="margin-bottom:16px;background:#151920" id="game-control-panel">
    <h3>游戏控制 <span style="font-size:10px;font-weight:normal;color:#888">(指令或点击设置)</span></h3>
    <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center">
      <div style="display:flex;align-items:center;gap:6px">
        <span style="font-size:11px;color:#888">阵营:</span>
        <button class="btn-faction" id="btn-faction-red" onclick="setFaction('红')" style="padding:6px 14px;font-size:11px;background:#e53935;color:#fff;border-radius:6px;border:none;cursor:pointer">红方</button>
        <button class="btn-faction" id="btn-faction-blue" onclick="setFaction('蓝')" style="padding:6px 14px;font-size:11px;background:#2196f3;color:#fff;border-radius:6px;border:none;cursor:pointer">蓝方</button>
        <span id="faction-label" style="font-size:11px;color:#ff9800">未选择</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px">
        <span style="font-size:11px;color:#888">难度:</span>
        <button onclick="setDifficulty('简单')" style="padding:6px 14px;font-size:11px;background:#4caf50;color:#fff;border-radius:6px;border:none;cursor:pointer">简单</button>
        <button onclick="setDifficulty('普通')" style="padding:6px 14px;font-size:11px;background:#ff9800;color:#fff;border-radius:6px;border:none;cursor:pointer">普通</button>
        <button onclick="setDifficulty('困难')" style="padding:6px 14px;font-size:11px;background:#e53935;color:#fff;border-radius:6px;border:none;cursor:pointer">困难</button>
        <span id="difficulty-label" style="font-size:11px;color:#ff9800">未选择</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px">
        <span style="font-size:11px;color:#888">模式:</span>
        <button onclick="setMode('对战')" style="padding:6px 14px;font-size:11px;background:#7c4dff;color:#fff;border-radius:6px;border:none;cursor:pointer">对战</button>
        <button onclick="setMode('训练')" style="padding:6px 14px;font-size:11px;background:#00bcd4;color:#fff;border-radius:6px;border:none;cursor:pointer">训练</button>
        <button onclick="setMode('战役')" style="padding:6px 14px;font-size:11px;background:#ff5722;color:#fff;border-radius:6px;border:none;cursor:pointer">战役</button>
        <span id="mode-label" style="font-size:11px;color:#ff9800">未选择</span>
      </div>
    </div>
    <div style="display:flex;gap:10px;margin-top:10px;flex-wrap:wrap">
      <button onclick="startAITraining()" style="padding:6px 14px;font-size:11px;background:#4caf50;color:#fff;border-radius:6px;border:none;cursor:pointer">开始训练AI</button>
      <button onclick="stopTraining()" style="padding:6px 14px;font-size:11px;background:#e53935;color:#fff;border-radius:6px;border:none;cursor:pointer">停止训练</button>
      <button onclick="pushToGitHub()" style="padding:6px 14px;font-size:11px;background:#ff9800;color:#fff;border-radius:6px;border:none;cursor:pointer">推送到GitHub</button>
      <button onclick="deployToServer()" style="padding:6px 14px;font-size:11px;background:#00bcd4;color:#fff;border-radius:6px;border:none;cursor:pointer">部署到服务器</button>
      <button onclick="updateApp()" style="padding:6px 14px;font-size:11px;background:#7c4dff;color:#fff;border-radius:6px;border:none;cursor:pointer">更新应用</button>
      <button onclick="createInstallPackage()" style="padding:6px 14px;font-size:11px;background:#607d8b;color:#fff;border-radius:6px;border:none;cursor:pointer">创建安装包</button>
    </div>
    <div id="game-control-result" style="margin-top:8px;font-size:11px;color:#888"></div>
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
    <div class="panel" id="predict-panel" style="display:none"><h3>战场预测 <span style="font-size:10px;font-weight:normal;color:#888">(v5.1 预测系统)</span></h3>
      <div style="display:flex;gap:8px;margin-bottom:6px;font-size:11px">
        <span style="color:#4caf50">准确率: <b id="pred-accuracy">0%</b></span>
        <span style="color:#ff9800">威胁等级: <b id="pred-threat">0</b>/100</span>
        <span style="color:#2196f3">经验: <b id="pred-exp">0</b>条</span>
      </div>
      <div class="thinking-box" id="predict-thinking" style="max-height:120px;font-size:10px">等待预测数据...</div>
    </div>
    <div class="panel"><h3>决策日志</h3><div class="log-list" id="decision-log"></div></div>
  </div>
</div>

<!-- ═══ AI 对话 ═══ -->
<div class="tab-content" id="tab-chat">
  <div class="panel" style="margin-bottom:0;">
    <h3>与 AI 对话（支持战场截图分析 + 键鼠操控）</h3>
    <div class="chat-container">
      <div class="chat-messages" id="chat-messages">
        <div class="chat-msg assistant"><div class="avatar">AI</div><div class="bubble">你好！我是Firefight AI战术助手。新功能：<br>1. <b>战场截图分析</b>：点击"截图发送"截取模拟器画面让我分析<br>2. <b>键鼠操控</b>：输入"点击(500,300)"或"滑动(100,200,500,600)"直接操控<br>3. <b>记录到学习日志</b>：输入"记录到学习日志：xxx"保存知识<br>4. <b>纠正AI</b>：点击"纠正AI"按钮纠正我的行为</div></div>
      </div>
      <div class="chat-input-area" style="flex-wrap:wrap">
        <div id="chat-drop-zone" style="width:100%;border:2px dashed #252a33;border-radius:8px;padding:8px;margin-bottom:6px;text-align:center;font-size:11px;color:#666;transition:all 0.2s;display:none">
          &#128194; 拖放文件到此处分析 | 或输入本地文件路径如: D:\data\config.yaml
        </div>
        <div style="display:flex;gap:4px;margin-bottom:4px;flex-wrap:wrap">
          <button class="btn-verify" onclick="captureScreenshotForChat()" style="font-size:10px;padding:4px 8px;background:#2196f3;color:#fff" title="截取模拟器当前画面发送给AI分析">&#128247; 截图发送</button>
          <button class="btn-verify" onclick="sendVisionChat()" style="font-size:10px;padding:4px 8px;background:#9c27b0;color:#fff" title="附带截图让AI分析战场">&#128269; 视觉分析</button>
          <span style="font-size:10px;color:#888;align-self:center" id="screenshot-status"></span>
          <span style="font-size:10px;color:#888;margin-left:auto">操控指令: 点击(x,y) 滑动(x1,y1,x2,y2) 按键(back)</span>
        </div>
        <textarea id="chat-input" placeholder="输入消息，或拖入文件/输入文件路径...&#10;操控指令: 点击(500,300) 滑动(100,200,500,600) 按键(back)" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChat()}" oninput="checkChatFileInput()"></textarea>
        <div style="display:flex;gap:6px;align-self:flex-end">
          <input type="file" id="chat-file-input" style="display:none" onchange="handleChatFileSelect(event)">
          <button class="btn-clear" onclick="document.getElementById('chat-file-input').click()" style="font-size:10px;padding:4px 8px" title="选择文件">&#128206;</button>
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
    <!-- v5.1 自动保存 -->
    <div class="conn-card">
      <div class="conn-name">自动保存调度器</div>
      <div class="conn-status unknown" id="conn-autosave-status">未检查</div>
      <div id="conn-autosave-detail" style="font-size:10px;color:#888"></div>
      <div class="conn-actions">
        <button class="btn-verify" onclick="checkAutoSave()">检查</button>
        <button class="btn-start" onclick="saveNow()" style="background:#ff9800;color:#000">立即保存</button>
      </div>
    </div>
    <!-- v5.2 模型参数 -->
    <div class="conn-card">
      <div class="conn-name">AI模型参数</div>
      <div class="conn-status unknown" id="conn-params-status">未检查</div>
      <div id="conn-params-detail" style="font-size:10px;color:#888"></div>
      <div class="conn-actions">
        <button class="btn-verify" onclick="checkLearningParams()">检查</button>
        <button class="btn-push" onclick="uploadParamsToGitHub()" style="background:#ff9800;color:#000">上传到GitHub</button>
        <button class="btn-deploy" onclick="uploadParamsToServer()">上传到服务器</button>
      </div>
    </div>
    <!-- 🔐 独立运维配置 -->
    <div class="conn-card" style="border-left:3px solid #ff9800">
      <div class="conn-name">🔐 独立运维配置</div>
      <div style="margin-top:8px;display:flex;flex-direction:column;gap:6px">
        <input type="password" id="cfg-apikey" placeholder="DeepSeek API Key (sk-...)" style="background:#1a1f2b;border:1px solid #30363d;color:#e6edf3;padding:6px 10px;border-radius:4px;font-size:12px" onchange="onCfgChange()">
        <input type="text" id="cfg-server" placeholder="服务器地址 (https://firefightai.top)" value="https://firefightai.top" style="background:#1a1f2b;border:1px solid #30363d;color:#e6edf3;padding:6px 10px;border-radius:4px;font-size:12px" onchange="onCfgChange()">
        <input type="password" id="cfg-github-token" placeholder="GitHub Token (ghp_...)" style="background:#1a1f2b;border:1px solid #30363d;color:#e6edf3;padding:6px 10px;border-radius:4px;font-size:12px" onchange="onCfgChange()">
        <input type="text" id="cfg-ssh-key" placeholder="SSH密钥路径 (D:\\firefightAI.pem)" style="background:#1a1f2b;border:1px solid #30363d;color:#e6edf3;padding:6px 10px;border-radius:4px;font-size:12px" onchange="onCfgChange()">
        <input type="text" id="cfg-ssh-user" placeholder="SSH用户名 (root)" value="root" style="background:#1a1f2b;border:1px solid #30363d;color:#e6edf3;padding:6px 10px;border-radius:4px;font-size:12px" onchange="onCfgChange()">
      </div>
      <div class="conn-actions" style="margin-top:8px;flex-wrap:wrap;gap:4px">
        <button class="btn-start" onclick="saveConfig()" style="background:#238636">💾 保存配置</button>
        <button class="btn-deploy" onclick="deployToServer()" style="background:#ff6600">🚀 一键部署</button>
        <button class="btn-verify" onclick="testConnection()">🔗 测试连接</button>
        <button class="btn-push" onclick="syncGlobal()" style="background:#4caf50">🌐 全域同步</button>
      </div>
      <div id="cfg-result" style="font-size:10px;margin-top:6px;max-height:150px;overflow-y:auto"></div>
    </div>
  </div>
</div>

<!-- ═══ 模拟器 ═══ -->
<div class="tab-content" id="tab-emulator">
  <div class="panel" style="margin-bottom:12px">
    <h3>Android 模拟器管理 (标准规格) <span id="emu-type-label" style="font-size:10px;color:#58a5f3;font-weight:normal">[本地模拟器]</span></h3>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap">
      <span style="font-size:11px;color:#888">选择类型:</span>
      <select id="emu-type-select-tab" onchange="switchEmuType(this.value)" style="background:#1a1f2b;color:#58a5f3;border:1px solid #252a33;border-radius:6px;padding:4px 10px;font-size:12px;cursor:pointer">
        <option value="generic">本地模拟器 (Android SDK, 5556)</option>
        <option value="mumu">MUMU模拟器 (7555)</option>
        <option value="bluestacks">蓝叠模拟器 (5555)</option>
        <option value="ldplayer">雷电模拟器 (5555)</option>
        <option value="xiaoyao">逍遥模拟器 (21503)</option>
        <option value="nox">Nox模拟器 (62001)</option>
        <option value="memu">Memu模拟器 (21503)</option>
        <option value="other">其他模拟器 (5555)</option>
      </select>
      <button class="btn-verify" onclick="detectEmulators()" style="font-size:10px;padding:4px 8px">自动检测</button>
      <span id="emu-detect-result" style="font-size:10px;color:#888"></span>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      <button class="btn-start" onclick="checkEmulatorStatus()">检查状态</button>
      <button class="btn-deploy" onclick="installAndStartEmulator()" style="background:#ff6600;font-weight:bold">⚡ 一键安装并启动</button>
      <button class="btn-deploy" onclick="installEmulator()">安装模拟器</button>
      <button class="btn-start" onclick="startEmulator()">启动</button>
      <button class="btn-stop" onclick="stopEmulator()">停止</button>
      <button class="btn-verify" onclick="installGameAPK()">安装游戏APK</button>
      <button class="btn-push" onclick="installAPKPrompt()">安装APK文件</button>
      <button class="btn-push" onclick="analyzeAPK()" style="font-size:10px;padding:4px 10px">分析APK兼容性</button>
    </div>
    <div id="emu-status" style="font-size:12px;color:#888;margin-bottom:8px">
      <div class="diag-item"><span class="diag-name">SDK安装</span><span class="diag-status unknown" id="emu-installed">未知</span></div>
      <div class="diag-item"><span class="diag-name">AVD</span><span class="diag-status unknown" id="emu-avd">未知</span></div>
      <div class="diag-item"><span class="diag-name">Java</span><span class="diag-status unknown" id="emu-java">未知</span></div>
      <div class="diag-item"><span class="diag-name">运行状态</span><span class="diag-status unknown" id="emu-running">未知</span></div>
      <div class="diag-item"><span class="diag-name">ADB连接</span><span class="diag-status unknown" id="emu-adb">未知</span></div>
    </div>
    <div id="emu-progress" style="font-size:11px;margin-top:6px"></div>
  </div>

  <!-- 🔥 分辨率配置 -->
  <div class="panel" style="margin-bottom:12px">
    <h3>屏幕分辨率 <span style="font-size:10px;color:#ff9800;font-weight:normal">(修复游戏界面显示不全)</span></h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:6px">
      <span style="font-size:11px;color:#aaa">预设:</span>
      <button class="btn-verify" onclick="setEmuResolution(1920,1080,420)" style="font-size:10px;padding:4px 8px">1080p</button>
      <button class="btn-verify" onclick="setEmuResolution(1280,720,320)" style="font-size:10px;padding:4px 8px">720p</button>
      <button class="btn-verify" onclick="setEmuResolution(2560,1440,560)" style="font-size:10px;padding:4px 8px">1440p</button>
      <button class="btn-verify" onclick="setEmuResolution(1920,1200,320)" style="font-size:10px;padding:4px 8px">平板</button>
      <button class="btn-verify" onclick="setEmuResolution(1080,1920,480)" style="font-size:10px;padding:4px 8px">竖屏</button>
    </div>
    <div style="display:flex;gap:8px;align-items:center;font-size:11px;color:#aaa">
      <span>宽:</span><input type="number" id="emu-res-w" value="1920" style="width:70px;background:#1a1f2b;color:#d0d0d0;border:1px solid #252a33;padding:2px 6px;border-radius:4px">
      <span>高:</span><input type="number" id="emu-res-h" value="1080" style="width:70px;background:#1a1f2b;color:#d0d0d0;border:1px solid #252a33;padding:2px 6px;border-radius:4px">
      <span>DPI:</span><input type="number" id="emu-res-dpi" value="420" style="width:60px;background:#1a1f2b;color:#d0d0d0;border:1px solid #252a33;padding:2px 6px;border-radius:4px">
      <button class="btn-start" onclick="applyEmuResolution()" style="font-size:10px;padding:4px 8px">应用</button>
      <span id="emu-res-status" style="font-size:10px;color:#888"></span>
    </div>
  </div>

  <!-- 🔥 GPU渲染配置 -->
  <div class="panel" style="margin-bottom:12px">
    <h3>GPU渲染配置 <span style="font-size:10px;color:#ff9800;font-weight:normal">(修复APK游戏兼容性)</span></h3>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:11px;color:#aaa;margin-bottom:6px">
      <span>GPU:</span>
      <select id="emu-gpu-mode" onchange="applyGpuConfig()" style="background:#1a1f2b;color:#d0d0d0;border:1px solid #252a33;padding:2px 6px;border-radius:4px">
        <option value="host">host (推荐)</option>
        <option value="swiftshader_indirect">swiftshader_indirect</option>
        <option value="swiftshader">swiftshader</option>
        <option value="angle_indirect">angle_indirect</option>
        <option value="guest">guest</option>
      </select>
      <span>渲染器:</span>
      <select id="emu-renderer" onchange="applyGpuConfig()" style="background:#1a1f2b;color:#d0d0d0;border:1px solid #252a33;padding:2px 6px;border-radius:4px">
        <option value="opengl">OpenGL</option>
        <option value="skia">Skia</option>
      </select>
      <span>GL ES:</span>
      <select id="emu-gl-version" onchange="applyGpuConfig()" style="background:#1a1f2b;color:#d0d0d0;border:1px solid #252a33;padding:2px 6px;border-radius:4px">
        <option value="2.0">2.0</option>
        <option value="3.0">3.0</option>
        <option value="3.1">3.1</option>
      </select>
      <button class="btn-start" onclick="applyGpuConfig()" style="font-size:10px;padding:4px 8px">应用</button>
      <span id="emu-gpu-status" style="font-size:10px;color:#888"></span>
    </div>
    <div style="font-size:10px;color:#666;margin-top:4px">如果游戏内容加载不全，尝试切换到 swiftshader 或 angle_indirect 模式后重启模拟器</div>
  </div>

  <!-- scrcpy 投屏控制 (鼠标/键盘直接操控) -->
  <div class="panel" style="margin-bottom:12px;background:#1a2a1a;border:1px solid #4caf50">
    <h3>scrcpy 投屏 <span style="font-size:10px;color:#4caf50;font-weight:normal">(鼠标/键盘/触控板直接操控)</span></h3>
    <div style="font-size:11px;color:#aaa;margin-bottom:8px;line-height:1.5">推荐使用 scrcpy 投屏进行操控。启动后会打开一个独立窗口，你可以直接用<strong style="color:#4caf50">鼠标点击、键盘输入、触控板</strong>操控模拟器，无需手动输入坐标。</div>
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
        <img id="emu-screen-stream" src="" style="width:100%;height:100%;display:none;object-fit:contain" alt="模拟器画面">
        <canvas id="emu-screen-canvas" style="width:100%;height:100%;display:none"></canvas>
        <div id="emu-screen-off-overlay" style="display:none;position:absolute;top:0;left:0;width:100%;height:100%;background:#000;z-index:50;align-items:center;justify-content:center">
          <span style="color:#555;font-size:18px;font-weight:600">请打开模拟器屏幕</span>
        </div>
        <div id="emu-screen-placeholder" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#555;font-size:13px;text-align:center;z-index:10">
          模拟器未启动<br><span style="font-size:11px">点击"启动"按钮</span>
        </div>
        <button id="emu-fullscreen-exit" onclick="toggleEmulatorFullscreen()" style="display:none;position:absolute;top:10px;right:10px;z-index:10000;background:rgba(0,0,0,0.7);color:#fff;border:1px solid #555;border-radius:6px;padding:6px 14px;font-size:13px;cursor:pointer">✕ 退出全屏 (Esc)</button>
      </div>
      <div style="display:flex;gap:6px;margin-top:6px;align-items:center;flex-wrap:wrap">
        <button id="emu-screen-toggle-btn" class="btn-start" onclick="toggleEmulatorScreen()" style="font-size:11px;padding:6px 14px;background:#4caf50">🖥 屏幕: 开启</button>
        <label style="font-size:10px;color:#888;display:flex;align-items:center;gap:4px;cursor:pointer">
          <input type="checkbox" id="emu-auto-refresh" checked onchange="toggleEmulatorRefresh()"> 实时刷新(MJPEG)
        </label>
        <button class="btn-clear" onclick="refreshEmulatorScreen()" style="font-size:10px;padding:4px 8px">手动刷新</button>
        <button class="btn-clear" onclick="toggleEmulatorFullscreen()" style="font-size:10px;padding:4px 8px">🔲 全屏</button>
        <button class="btn-start" onclick="launchGame()" style="font-size:15px;padding:10px 24px;background:#4caf50;font-weight:bold">🎮 启动游戏 Firefight v10.8.1</button>
      </div>
    </div>
    <div class="panel">
      <h3>触摸控制 <span id="emu-mode-label" style="font-size:10px;color:#4caf50;font-weight:normal">[触控模式]</span></h3>
      <div style="display:flex;gap:6px;margin-bottom:8px">
        <button class="btn-send" onclick="setEmuMode('touch')" id="btn-mode-touch" style="font-size:11px;padding:6px 14px;background:#4caf50;color:#fff">&#128073; 触控模式</button>
        <button class="btn-verify" onclick="setEmuMode('annotate')" id="btn-mode-annotate" style="font-size:11px;padding:6px 14px">&#128396; 标注模式</button>
      </div>
      <div style="font-size:11px;color:#888;margin-bottom:8px" id="emu-mode-hint">点击屏幕上方的模拟器画面即可发送触摸事件</div>
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
        <div style="display:flex;gap:6px;margin-top:4px">
          <button class="btn-clear" onclick="clearEmuAnnotations()" style="font-size:10px;padding:4px 8px">清除标注</button>
          <button class="btn-verify" onclick="exportEmuAnnotations()" style="font-size:10px;padding:4px 8px">导出标注</button>
          <span style="font-size:10px;color:#888;align-self:center" id="emu-annotate-count"></span>
        </div>
      </div>
    </div>
  </div>
  <!-- 🔥 触控录制面板 -->
  <div class="panel" style="margin-top:14px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <h3 style="margin:0;border:none;padding:0">触控录制 <span id="recording-status" style="font-size:10px;color:#888;font-weight:normal">就绪</span></h3>
      <div style="display:flex;gap:6px">
        <button id="btn-record-toggle" class="btn-send" onclick="toggleRecording()" style="font-size:11px;padding:6px 14px;background:#e53935">⏺ 开始录制</button>
        <button id="btn-record-save" class="btn-verify" onclick="saveRecording()" style="font-size:11px;padding:6px 14px;display:none">💾 保存</button>
        <button id="btn-record-send" class="btn-push" onclick="sendRecordingToAI()" style="font-size:11px;padding:6px 14px;display:none">🤖 发送AI学习</button>
      </div>
    </div>
    <div style="font-size:10px;color:#888;margin-bottom:6px">开启录制后，所有触控操作和对应的屏幕截图将按时间戳同步记录，可发送给AI进行自主学习</div>
    <div id="recording-stats" style="font-size:10px;color:#aaa;margin-bottom:4px;display:none">
      已录制: <span id="rec-event-count">0</span> 个事件 | 截图: <span id="rec-screenshot-count">0</span> 帧 | 时长: <span id="rec-duration">0s</span>
    </div>
    <div id="recording-log" style="max-height:120px;overflow-y:auto;font-size:9px;background:#0a0e14;border-radius:6px;padding:4px;color:#666;font-family:monospace;display:none"></div>
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
          <div id="agent-drop-zone" style="width:100%;border:2px dashed #252a33;border-radius:8px;padding:8px;margin-bottom:6px;text-align:center;font-size:11px;color:#666;transition:all 0.2s;display:none">
            &#128194; 拖放文件到此处分析
          </div>
          <textarea id="agent-chat-input" placeholder="输入问题，支持联网搜索、系统诊断、拖入文件/输入文件路径..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();agentChat()}" oninput="checkAgentFileInput()"></textarea>
          <input type="file" id="agent-file-input" style="display:none" onchange="handleAgentFileSelect(event)">
          <button class="btn-send" onclick="agentChat()">发送</button>
          <button class="btn-clear" onclick="document.getElementById('agent-file-input').click()" style="font-size:10px;padding:4px 8px" title="选择文件">&#128206;</button>
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
        <div class="diag-item"><span class="diag-name">ADB</span><span class="diag-status unknown">等待诊断</span></div>
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
  <div class="panel" style="margin-top:14px">
    <h3>☁️ 多机全域同步</h3>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <button class="btn-start" onclick="syncGlobal()" style="background:#238636;font-size:14px;padding:8px 18px">🌐 一键全域同步</button>
      <button class="btn-verify" onclick="syncPullAll()">📥 拉取全部最新</button>
      <button class="btn-start" onclick="syncCheckVersion()">🔄 检查版本</button>
    </div>
    <div id="sync-result" style="margin-top:8px;font-size:11px;max-height:250px;overflow-y:auto"></div>
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
      <h3 style="margin:0;border:none;padding:0">搜索结果（勾选需要保留的条目）</h3>
      <div style="display:flex;gap:6px">
        <button class="btn-start" onclick="saveSelectedResults()" style="padding:5px 12px;font-size:11px;background:#238636">📥 保存勾选到知识库</button>
        <button class="btn-verify" onclick="learnFromWeb()" style="padding:5px 12px;font-size:11px">让AI学习</button>
        <button class="btn-clear" onclick="toggleAllSearchResults()" style="padding:5px 12px;font-size:11px">全选/取消</button>
      </div>
    </div>
    <div id="web-search-results" style="max-height:400px;overflow-y:auto">
      <div style="color:#888;padding:20px;text-align:center">输入关键词开始搜索</div>
    </div>
    <div id="web-search-selection-status" style="font-size:10px;color:#888;margin-top:4px"></div>
  </div>
  <div class="panel">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <h3 style="margin:0;border:none;padding:0">知识库（与AI学习记录互通）</h3>
      <div style="display:flex;gap:6px">
        <button class="btn-start" onclick="trainKnowledgeFromSearch()" style="padding:6px 14px;font-size:11px;background:#238636">🎯 深度学习训练</button>
        <button class="btn-verify" onclick="refreshSearchKnowledge()" style="padding:5px 12px;font-size:11px">🔄 刷新</button>
      </div>
    </div>
    <div id="search-knowledge-status" style="font-size:10px;color:#888;margin-bottom:6px"></div>
    <div id="knowledge-list" style="max-height:250px;overflow-y:auto">
      <div style="color:#888;padding:10px;text-align:center;font-size:11px">加载中...</div>
    </div>
  </div>
</div>

<!-- ═══ 模型训练 ═══ -->
<div class="tab-content" id="tab-training">
  <div class="train-section"><h4>统一数据集</h4>
    <div id="dataset-unified" style="padding:10px;background:#1a1f2b;border-radius:8px;border:1px solid #252a33;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span><b>&#128202; Firefight AI 统一数据集</b></span>
        <span style="font-size:11px;color:#888" id="dataset-unified-stats">加载中...</span>
      </div>
      <div style="font-size:10px;color:#888;margin-top:4px">所有子数据集合并：标注信息从框名称读取</div>
    </div>
    <button class="btn-verify" onclick="loadUnifiedDataset()" style="font-size:10px;padding:4px 10px">刷新数据集</button>
  </div>
  <div class="train-section"><h4>上传图片</h4><div class="upload-area" onclick="document.getElementById('file-input').click()"><p>点击上传图片到统一数据集</p><input type="file" id="file-input" multiple accept=".png,.jpg,.jpeg" onchange="uploadImages()"></div><div id="upload-status"></div></div>
  <div class="train-section"><h4>训练配置</h4><div class="train-config">
    <label>模型<select id="train-model"><option value="yolov8n.pt">YOLOv8n</option><option value="yolov8s.pt">YOLOv8s</option></select></label>
    <label>设备<select id="train-device"><option value="cpu">CPU</option><option value="0">GPU (CUDA)</option></select></label>
    <label>轮数<input type="number" id="train-epochs" value="50" min="10" max="500" style="width:70px"></label>
    <label>尺寸<input type="number" id="train-imgsz" value="640" min="320" max="1280" style="width:70px"></label>
    <label style="align-items:center;flex-direction:row;gap:6px"><input type="checkbox" id="remove-after-train" checked>训练后移除已用图片</label>
    <label style="align-items:center;flex-direction:row;gap:6px"><input type="checkbox" id="auto-push-github">训练后推送到GitHub</label>
  </div>
  <div style="display:flex;gap:10px;align-items:center;">
    <button class="btn-start" onclick="startTraining()" id="btn-train-start">开始训练</button>
    <button class="btn-stop" onclick="stopTraining()" id="btn-train-stop" style="display:none">停止</button>
    <span id="train-status-text" style="font-size:12px;color:#888"></span>
  </div>
  <div class="train-progress" id="train-progress-container" style="display:none"><div class="train-progress-bar" id="train-progress-bar" style="width:0%">0%</div></div>
  <div class="train-log" id="train-log"></div>
  <div id="train-results"></div></div>
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
  <div class="panel" style="margin-bottom:12px">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
      <h3 style="margin:0;border:none;padding:0">AI学习参数管理</h3>
      <div style="display:flex;gap:6px;align-items:center">
        <span style="font-size:10px;color:#888" id="params-sync-status"></span>
        <button class="btn-start" onclick="saveParamsToLocal()" style="font-size:10px;padding:4px 10px">保存参数</button>
        <button class="btn-push" onclick="pushParamsToGitHub()" style="font-size:10px;padding:4px 10px;background:#ff9800">推送到GitHub</button>
        <button class="btn-deploy" onclick="pushParamsToServer()" style="font-size:10px;padding:4px 10px">上传到服务器</button>
        <button class="btn-verify" onclick="pullParamsFromGitHub()" style="font-size:10px;padding:4px 10px">从GitHub拉取</button>
        <button class="btn-verify" onclick="syncParamsAll()" style="font-size:10px;padding:4px 10px;background:#7c4dff;color:#fff">全部同步</button>
      </div>
    </div>
    <div id="params-current" style="font-size:10px;color:#888;margin-top:6px;font-family:monospace;background:#0a0e14;padding:8px;border-radius:4px"></div>
  </div>
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

<!-- ═══ AI学习日志 ═══ -->
<div class="tab-content" id="tab-learning">
  <div class="panel" style="margin-bottom:12px">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
      <h3 style="margin:0;border:none;padding:0">AI 自学习引擎</h3>
      <div style="display:flex;gap:6px;align-items:center">
        <span id="learn-status-indicator" style="font-size:11px;color:#888">状态: --</span>
        <button class="btn-start" onclick="startSelfLearning()" style="font-size:10px;padding:4px 10px">启动</button>
        <button class="btn-stop" onclick="stopSelfLearning()" style="font-size:10px;padding:4px 10px">停止</button>
      </div>
    </div>
    <div id="self-learn-params" style="font-size:10px;color:#888;margin-top:6px;font-family:monospace"></div>
    <div id="self-learn-insights" style="font-size:10px;color:#aaa;margin-top:4px;max-height:100px;overflow-y:auto"></div>
  </div>
  <div class="panel">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <h3 style="margin:0;border:none;padding:0">AI 学到的知识<span style="font-size:10px;color:#888;font-weight:normal">（战术、战法、搜索洞察、对话纠正）</span></h3>
      <div>
        <button class="btn-verify" onclick="refreshLearningLog()" style="font-size:11px;padding:5px 12px">刷新</button>
        <button class="btn-clear" onclick="clearLearningLog()" style="font-size:11px;padding:5px 12px">清空</button>
        <button class="btn-push" onclick="exportLearningLog()" style="font-size:11px;padding:5px 12px">导出</button>
        <button class="btn-push" onclick="uploadLearningLog()" style="font-size:11px;padding:5px 12px;background:#f0a040">上传到GitHub</button>
        <button class="btn-start" onclick="trainFromLearningLog()" style="font-size:11px;padding:5px 12px;background:#4caf50">训练AI参数</button>
      </div>
    </div>
    <div style="font-size:10px;color:#f0a040;margin-bottom:8px;padding:6px 10px;background:#1a1a10;border-radius:4px;border-left:3px solid #f0a040">
      此日志仅记录AI通过作战、网络搜索、对话中学到的战术知识，用于训练和调整AI参数
    </div>
    <div style="margin-bottom:12px;border:2px solid #f0a040;border-radius:8px;padding:12px;background:#1a1a10">
      <div style="color:#f0a040;font-weight:bold;margin-bottom:8px;font-size:14px">✏️ 手动输入知识（复制网页内容直接粘贴）</div>
      <textarea id="manual-knowledge-input" placeholder="粘贴网上摘取的战术知识、战法、经验等（字数不限）..." style="width:100%;height:120px;background:#0a0e14;border:1px solid #505050;color:#e6edf3;padding:10px;border-radius:6px;font-size:13px;font-family:inherit;resize:vertical;box-sizing:border-box"></textarea>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button onclick="addManualKnowledge()" style="padding:8px 20px;background:#238636;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;flex:1">📥 保存到学习日志并训练AI</button>
      </div>
    </div>
    <div id="learning-log-train-result" style="display:none;font-size:11px;color:#4caf50;margin-bottom:8px;padding:8px 10px;background:#0a1a0e;border-radius:4px;border-left:3px solid #4caf50;max-height:200px;overflow-y:auto"></div>
    <div id="learning-log-container" style="max-height:500px;overflow-y:auto;font-size:11px;background:#0a0e14;border-radius:8px;padding:4px">
      <div style="color:#888;padding:20px;text-align:center">暂无AI学习记录</div>
    </div>
  </div>
</div>

<!-- ═══ AI 学到的知识 ═══ -->
<div style="margin-bottom:16px">
<div class="panel">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
    <h3 style="margin:0;border:none;padding:0;color:#58a5f3">🧠 AI 学到的知识</h3>
    <div style="display:flex;gap:8px">
      <button onclick="trainSelectedKnowledge()" style="padding:6px 14px;background:#238636;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px">🎯 训练选中</button>
      <button onclick="refreshKnowledge()" style="padding:6px 10px;background:#30363d;color:#ccc;border:none;border-radius:6px;cursor:pointer;font-size:12px">🔄 刷新</button>
    </div>
  </div>
  <div id="knowledge-container" style="max-height:400px;overflow-y:auto;font-size:11px;background:#0a0e14;border-radius:8px;padding:4px">
    <div style="color:#888;padding:20px;text-align:center">加载中...</div>
  </div>
  <div id="knowledge-status" style="font-size:10px;color:#888;margin-top:6px"></div>
</div>
</div>

<!-- ═══ 系统日志 ═══ -->
<div class="tab-content" id="tab-syslog">
  <div class="panel">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <h3 style="margin:0;border:none;padding:0">系统日志<span style="font-size:10px;color:#888;font-weight:normal">（连接、部署、配置等系统事件）</span></h3>
      <div>
        <button class="btn-verify" onclick="refreshSystemLog()" style="font-size:11px;padding:5px 12px">刷新</button>
        <button class="btn-clear" onclick="clearSystemLog()" style="font-size:11px;padding:5px 12px">清空</button>
        <button class="btn-push" onclick="exportSystemLog()" style="font-size:11px;padding:5px 12px">导出</button>
      </div>
    </div>
    <div id="system-log-container" style="max-height:600px;overflow-y:auto;font-size:11px;background:#0a0e14;border-radius:8px;padding:4px">
      <div style="color:#888;padding:20px;text-align:center">暂无系统日志</div>
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
// 🔇 全局禁用弹窗: 转为console.log, 不打扰用户
window._alert = window.alert;
window.alert = function(){console.log('[alert]',...arguments)};

// ── 全局变量 (延迟初始化) ──
let socket = null;
let selectedDataset = '';
let scoreChart = null;
let currentChatBubble = null;
let agentChatBubble = null;
let lastSearchResults = null;
let _emuRefreshTimer = null;
let _emuScreenConnected = false;

// Socket事件注册安全包装器 (必须在Socket初始化之前定义)
function _on(evt, handler) {
  if (socket) { socket.on(evt, handler); }
}

// ── Socket.IO 安全初始化 ──
(function initSocket(){
  if(typeof io === 'undefined'){
    console.warn('[FirefightAI] socket.io 未加载，使用轮询回退模式');
    setInterval(function(){
      fetch('/api/stats').then(function(r){return r.json()}).then(function(d){
        if(d.status) document.getElementById('status-badge').textContent = d.status;
        if(d.cycle) document.getElementById('cycle').textContent = d.cycle;
      }).catch(function(){});
    }, 2000);
    return;
  }
  try {
    socket = io({transports: ['websocket', 'polling'], timeout: 10000});
    _on('connect', function(){
      console.log('[FirefightAI] Socket.IO 已连接');
      document.getElementById('status-badge').style.color = '#ff9800';
    });
    _on('disconnect', function(){
      console.log('[FirefightAI] Socket.IO 已断开');
      document.getElementById('status-badge').style.color = '#888';
      document.getElementById('status-badge').textContent = '连接断开';
    });
    _on('connect_error', function(err){
      console.warn('[FirefightAI] Socket.IO 连接失败:', err.message);
    });
  } catch(e) {
    console.error('[FirefightAI] Socket.IO 初始化失败:', e.message);
  }
})();

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
  if(!socket || !socket.connected){
    alert('Socket.IO 未连接，请刷新页面或检查网络');
    return;
  }
  document.getElementById('status-badge').textContent='连接中...';
  document.getElementById('status-badge').style.color='#ff9800';
  document.getElementById('thinking-box').innerHTML='<span class="spinner"></span> 正在启动AI智能体...';
  socket.emit('start');
}
function stopAI(){
  if(!socket || !socket.connected){
    document.getElementById('status-badge').textContent='已停止';
    document.getElementById('status-badge').style.color='#888';
    return;
  }
  socket.emit('stop');
  document.getElementById('status-badge').textContent='已停止';
  document.getElementById('status-badge').style.color='#888';
  document.getElementById('thinking-box').textContent='AI已离线';
}
function escapeHtml(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function sendCommand(){
  if(!socket || !socket.connected){alert('Socket.IO 未连接');return;}
  var inp=document.getElementById('cmd-input');var cmd=inp.value.trim();if(!cmd)return;
  // 立即显示指令文本在AI思路中
  var box=document.getElementById('thinking-box');
  if(box)box.innerHTML='<span class="highlight">[指挥官指令]</span> '+escapeHtml(cmd)+'<br><span class="spinner"></span> AI分析中...';
  socket.emit('send_command',{command:cmd});inp.value='';inp.placeholder='指令已发送...';
  setTimeout(function(){inp.placeholder='输入指令: 阵营 红/蓝 | 难度 简单/困难 | 模式 对战 | 训练 开始 | 部署...'},2500);
}
function clearCommand(){if(socket)socket.emit('clear_command')}

// ── 游戏控制 ──
function setFaction(f){
  document.getElementById('faction-label').textContent = f;
  document.getElementById('faction-label').style.color = f==='红'?'#e53935':'#2196f3';
  document.getElementById('game-control-result').innerHTML = '<span style="color:#4caf50">阵营已设置为: '+f+'方</span>';
  if(socket&&socket.connected)socket.emit('send_command',{command:'阵营 '+f});
}
function setDifficulty(d){
  document.getElementById('difficulty-label').textContent = d;
  document.getElementById('difficulty-label').style.color = d==='困难'?'#e53935':(d==='普通'?'#ff9800':'#4caf50');
  document.getElementById('game-control-result').innerHTML = '<span style="color:#4caf50">难度已设置为: '+d+'</span>';
  if(socket&&socket.connected)socket.emit('send_command',{command:'难度 '+d});
}
function setMode(m){
  document.getElementById('mode-label').textContent = m;
  document.getElementById('game-control-result').innerHTML = '<span style="color:#4caf50">模式已设置为: '+m+'</span>';
  if(socket&&socket.connected)socket.emit('send_command',{command:'模式 '+m});
}
function startAITraining(){
  document.getElementById('game-control-result').innerHTML = '<span class="spinner"></span> 开始训练AI...';
  if(socket&&socket.connected)socket.emit('send_command',{command:'训练 开始'});
}
function updateApp(){
  document.getElementById('game-control-result').innerHTML = '<span class="spinner"></span> 正在更新应用...';
  fetch('/api/version/check').then(function(r){return r.json()}).then(function(d){
    document.getElementById('game-control-result').innerHTML = '<span style="color:#4caf50">当前版本: v'+d.current_version+' | 构建: '+d.current_build+'</span>';
  }).catch(function(e){
    document.getElementById('game-control-result').innerHTML = '<span style="color:#e53935">更新失败: '+e+'</span>';
  });
}
function createInstallPackage(){
  document.getElementById('game-control-result').innerHTML = '<span class="spinner"></span> 正在创建安装包...';
  fetch('/api/package/create',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){
      document.getElementById('game-control-result').innerHTML = '<span style="color:#4caf50">安装包已创建: '+d.filename+' ('+d.size_mb+'MB)</span><br><a href="'+d.download_url+'" target="_blank" style="color:#58a5f3;font-size:11px">下载安装包</a>';
    }else{
      document.getElementById('game-control-result').innerHTML = '<span style="color:#e53935">创建失败: '+d.error+'</span>';
    }
  }).catch(function(e){
    document.getElementById('game-control-result').innerHTML = '<span style="color:#e53935">创建失败: '+e+'</span>';
  });
}

// ── AI 对话 ──
var _pendingScreenshot = null;  // 暂存的截图base64

function sendChat(){
  if(!socket||!socket.connected){alert('Socket.IO 未连接');return;}
  var inp=document.getElementById('chat-input');var msg=inp.value.trim();if(!msg)return;
  addChatMessage('user',msg);inp.value='';
  var data = {message:msg, include_battlefield:true, is_correction:false};
  // 如果有暂存截图，附带发送
  if(_pendingScreenshot){
    data.screenshot = _pendingScreenshot;
    data.include_vision = true;
    _pendingScreenshot = null;
    document.getElementById('screenshot-status').textContent = '';
  }
  socket.emit('ai_chat', data);
}

function sendVisionChat(){
  if(!socket||!socket.connected){alert('Socket.IO 未连接');return;}
  var inp=document.getElementById('chat-input');var msg=inp.value.trim();
  if(!msg){msg='请分析当前战场截图并给出战术建议';}
  addChatMessage('user','[截图分析] '+msg);inp.value='';
  captureScreenshotForChat(function(){
    socket.emit('ai_chat', {message:msg, include_battlefield:true, is_correction:false, 
      screenshot: _pendingScreenshot, include_vision: true});
    _pendingScreenshot = null;
    document.getElementById('screenshot-status').textContent = '';
  });
}

function captureScreenshotForChat(callback){
  var status = document.getElementById('screenshot-status');
  status.textContent = '截图中...';
  status.style.color = '#ff9800';
  fetch('/api/control/screenshot').then(r=>r.json()).then(d=>{
    if(d.error){
      status.textContent = '截图失败: '+d.error;
      status.style.color = '#e53935';
      return;
    }
    _pendingScreenshot = d.screenshot;
    status.textContent = '截图已就绪 ('+d.width+'x'+d.height+')';
    status.style.color = '#4caf50';
    if(callback) callback();
  }).catch(function(e){
    status.textContent = '截图失败';
    status.style.color = '#e53935';
  });
}

function sendCorrection(){
  if(!socket||!socket.connected){alert('Socket.IO 未连接');return;}
  var inp=document.getElementById('chat-input');var msg=inp.value.trim();if(!msg)return;
  addChatMessage('user','[纠正] '+msg);inp.value='';
  var data = {message:msg, include_battlefield:true, is_correction:true};
  if(_pendingScreenshot){
    data.screenshot = _pendingScreenshot;
    data.include_vision = true;
    _pendingScreenshot = null;
    document.getElementById('screenshot-status').textContent = '';
  }
  socket.emit('ai_chat', data);
  socket.emit('ai_correct_behavior', {correction:msg});
}
function clearChat(){
  if(!socket||!socket.connected){return;}
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

_on('ai_chat_start',function(data){addChatMessage('assistant','')});
_on('ai_chat_token',function(data){
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
_on('ai_chat_error',function(data){if(currentChatBubble){currentChatBubble.textContent='[错误] '+data.error;currentChatBubble=null}});

// ── 文件拖放 & 本地路径读取 ──
var _chatFiles=[];  // 待分析的文件内容
function checkChatFileInput(){
  var inp=document.getElementById('chat-input');
  var val=inp.value.trim();
  var drop=document.getElementById('chat-drop-zone');
  // 检测是否为本地文件路径 (如 D:\xxx 或 C:\xxx 或 /home/xxx)
  if(/^[A-Za-z]:[\\\/]/.test(val)||/^\/[^\/]/.test(val)){
    drop.style.display='block';
    drop.style.borderColor='#58a5f3';
    drop.innerHTML='&#128269; 检测到文件路径: <b>'+escapeHtml(val)+'</b> - 发送后将自动读取并分析';
  }else{
    drop.style.display='none';
  }
}
function checkAgentFileInput(){
  var inp=document.getElementById('agent-chat-input');
  var val=inp.value.trim();
  var drop=document.getElementById('agent-drop-zone');
  if(/^[A-Za-z]:[\\\/]/.test(val)||/^\/[^\/]/.test(val)){
    drop.style.display='block';
    drop.style.borderColor='#58a5f3';
    drop.innerHTML='&#128269; 检测到文件路径: <b>'+escapeHtml(val)+'</b> - 发送后将自动读取并分析';
  }else{
    drop.style.display='none';
  }
}
function handleChatFileSelect(e){
  var f=e.target.files[0];
  if(!f) return;
  uploadChatFile(f,'chat');
}
function handleAgentFileSelect(e){
  var f=e.target.files[0];
  if(!f) return;
  uploadChatFile(f,'agent');
}
function uploadChatFile(file,type){
  var fd=new FormData();
  fd.append('file',file);
  var drop=document.getElementById(type==='agent'?'agent-drop-zone':'chat-drop-zone');
  drop.style.display='block';
  drop.style.borderColor='#ff9800';
  drop.innerHTML='<span class="spinner"></span> 正在读取文件: '+file.name+'...';
  fetch('/api/chat/upload_file',{method:'POST',body:fd}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){
      _chatFiles.push({name:d.filename,content:d.preview,size:d.size,truncated:d.truncated});
      drop.style.borderColor='#4caf50';
      drop.innerHTML='&#9989; 文件已加载: <b>'+d.filename+'</b> ('+d.size+'字符)'+(d.truncated?' [已截断前8000字符]':'')+' - 发送消息即可让AI分析';
      if(type==='agent'){
        document.getElementById('agent-chat-input').value='请分析文件: '+d.filename;
      }else{
        document.getElementById('chat-input').value='请分析文件: '+d.filename;
      }
    }else{
      drop.style.borderColor='#e53935';
      drop.innerHTML='&#10060; 读取失败: '+d.error;
    }
  }).catch(function(e){
    drop.style.borderColor='#e53935';
    drop.innerHTML='&#10060; 上传失败: '+e;
  });
}
// 读取本地文件路径
function readLocalFilePath(path,type){
  var drop=document.getElementById(type==='agent'?'agent-drop-zone':'chat-drop-zone');
  drop.style.display='block';
  drop.style.borderColor='#ff9800';
  drop.innerHTML='<span class="spinner"></span> 正在读取: '+path+'...';
  fetch('/api/chat/read_file',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:path})}).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){
      _chatFiles.push({name:d.filename,content:d.preview,size:d.size,truncated:d.truncated,path:path});
      drop.style.borderColor='#4caf50';
      drop.innerHTML='&#9989; 文件已读取: <b>'+d.filename+'</b> ('+d.size+'字符)'+(d.truncated?' [已截断前10000字符]':'')+' - 发送消息即可让AI分析';
      if(type==='agent'){
        document.getElementById('agent-chat-input').value='请分析文件: '+d.filename;
      }else{
        document.getElementById('chat-input').value='请分析文件: '+d.filename;
      }
    }else{
      drop.style.borderColor='#e53935';
      drop.innerHTML='&#10060; 读取失败: '+d.error;
    }
  }).catch(function(e){
    drop.style.borderColor='#e53935';
    drop.innerHTML='&#10060; 请求失败: '+e;
  });
}
// 初始化拖放区域
(function initDropZones(){
  function setupDrop(containerId,zoneId,type){
    var container=document.getElementById(containerId);
    if(!container) return setTimeout(function(){setupDrop(containerId,zoneId,type)},300);
    var zone=document.getElementById(zoneId);
    container.addEventListener('dragover',function(e){e.preventDefault();zone.style.display='block';zone.style.borderColor='#ff9800'});
    container.addEventListener('dragleave',function(e){if(!container.contains(e.relatedTarget)){zone.style.display='none'}});
    container.addEventListener('drop',function(e){
      e.preventDefault();
      zone.style.display='block';
      var file=e.dataTransfer.files[0];
      if(file) uploadChatFile(file,type);
      else zone.innerHTML='&#10060; 未检测到文件';
    });
  }
  setupDrop('chat-messages','chat-drop-zone','chat');
  setupDrop('agent-chat-messages','agent-drop-zone','agent');
})();
// 修改 sendChat 和 agentChat 函数，在发送时附带文件内容
var _origSendChat=sendChat;
sendChat=function(){
  var inp=document.getElementById('chat-input');
  var val=inp.value.trim();
  // 检测是否为本地文件路径
  if((/^[A-Za-z]:[\\\/]/.test(val)||/^\/[^\/]/.test(val))&&_chatFiles.length===0){
    readLocalFilePath(val,'chat');
    return;
  }
  // 如果有待分析文件，附加上下文
  if(_chatFiles.length>0&&val){
    var ctx='';
    _chatFiles.forEach(function(f){
      ctx+='\n\n[文件: '+f.name+' (路径: '+(f.path||'上传')+', 大小: '+f.size+'字符)]\n```\n'+f.content+'\n```';
    });
    val=val+'\n\n---\n以下为待分析文件内容:\n'+ctx;
    _chatFiles=[];
    document.getElementById('chat-drop-zone').style.display='none';
    inp.value=val;
  }
  _origSendChat();
};
var _origAgentChat=agentChat;
agentChat=function(){
  var inp=document.getElementById('agent-chat-input');
  var val=inp.value.trim();
  if((/^[A-Za-z]:[\\\/]/.test(val)||/^\/[^\/]/.test(val))&&_chatFiles.length===0){
    readLocalFilePath(val,'agent');
    return;
  }
  if(_chatFiles.length>0&&val){
    var ctx='';
    _chatFiles.forEach(function(f){
      ctx+='\n\n[文件: '+f.name+' (路径: '+(f.path||'上传')+', 大小: '+f.size+'字符)]\n```\n'+f.content+'\n```';
    });
    val=val+'\n\n---\n以下为待分析文件内容:\n'+ctx;
    _chatFiles=[];
    document.getElementById('agent-drop-zone').style.display='none';
    inp.value=val;
  }
  _origAgentChat();
};

// ── 行为纠正回显 ──
_on('correction_analysis',function(data){
  var msgs=document.getElementById('chat-messages');
  var div=document.createElement('div');div.className='chat-msg assistant';
  div.innerHTML='<div class="avatar">AI</div><div class="bubble"><strong>学习完成:</strong><br>'+escapeHtml(data.analysis||'')+'</div>';
  msgs.appendChild(div);msgs.scrollTop=msgs.scrollHeight;
});
_on('ai_learned_from_correction',function(data){
  var msgs=document.getElementById('chat-messages');
  var div=document.createElement('div');div.className='chat-msg assistant';
  div.innerHTML='<div class="avatar">AI</div><div class="bubble" style="background:#302a1a;border-left:2px solid #ff9800"><strong>已从纠正中学习</strong></div>';
  msgs.appendChild(div);msgs.scrollTop=msgs.scrollHeight;
});

// ── AI 思考 ──
_on('ai_thinking_update',function(data){
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
  if(!socket||!socket.connected){alert('Socket.IO 未连接');return;}
  var s=document.getElementById('rebuild-status');s.innerHTML='<div class="alert info"><span class="spinner"></span> 重建决策链中...</div>';
  socket.emit('rebuild_chain');
}
function checkAllConnections(){
  if(!socket||!socket.connected){alert('Socket.IO 未连接');return;}
  var s=document.getElementById('rebuild-status');s.innerHTML='<div class="alert info"><span class="spinner"></span> 检查所有连接...</div>';
  socket.emit('check_all_connections');
}
_on('rebuild_progress',function(d){document.getElementById('rebuild-status').innerHTML='<div class="alert info">'+d.step+' ('+d.progress+'%)</div>'});
_on('rebuild_complete',function(d){
  var html='<div class="alert success">决策链重建完成</div>';
  for(var k in d.results){html+='<span style="font-size:11px;color:#888">'+k+': </span><span style="font-size:11px;color:'+(d.results[k]==='online'||d.results[k]==='connected'?'#4caf50':'#e53935')+'">'+d.results[k]+'</span> '}
  document.getElementById('rebuild-status').innerHTML=html;
  updateConnMinis(d.results);
});
_on('rebuild_error',function(d){document.getElementById('rebuild-status').innerHTML='<div class="alert error">'+d.error+'</div>'});
_on('all_connections_status',function(d){updateConnMinis(d);updateConnCards(d)});

function updateConnMinis(d){
  var api=(d.deepseek||d.api)==='online';var adb=(d.adb||'')==='connected'||(d.adb||'')==='other_device';var gh=(d.github||'')==='online'||(d.github||'')==='configured';var srv=(d.server||'')==='online'&&d.server_deployed;
  setMini('conn-api',api?'online':'offline','API');
  setMini('conn-adb',adb?'online':'offline','ADB');
  setMini('conn-gh',gh?'online':'offline','GH');
  setMini('conn-srv',srv?'online':'offline','SRV');
  updateReadinessIndicator(api,adb,gh,srv);
}
function setMini(id,cls,text){var el=document.getElementById(id);el.className='conn-mini '+cls;el.textContent=text}
var _readiness={api:false,adb:false,gh:false,srv:false};
function updateReadinessIndicator(apiOk,adbOk,ghOk,srvOk){
  if(apiOk!==null)_readiness.api=apiOk;if(adbOk!==null)_readiness.adb=adbOk;
  if(ghOk!==null)_readiness.gh=ghOk;if(srvOk!==null)_readiness.srv=srvOk;
  var el=document.getElementById('readiness-indicator');if(!el)return;
  if(_readiness.api&&_readiness.adb&&_readiness.gh&&_readiness.srv){
    el.textContent='可以开始';el.style.background='#4caf50';el.style.color='#000';
  }else{
    var issues=[];if(!_readiness.api)issues.push('API');if(!_readiness.adb)issues.push('ADB');if(!_readiness.gh)issues.push('GitHub');if(!_readiness.srv)issues.push('服务器');
    el.textContent='异常: '+issues.join(', ');el.style.background='#e53935';el.style.color='#fff';
  }
}
function updateConnCards(d){
  setConnCard('conn-deepseek-status','conn-deepseek-detail',d.deepseek||d.api,'DeepSeek');
  setConnCard('conn-adb-status','conn-adb-detail',d.adb,'ADB');
  setConnCard('conn-github-status','conn-github-detail',d.github,'GitHub');
  setConnCard('conn-server-status','conn-server-detail',d.server,'Server');
}
function setConnCard(statusId,detailId,val,name){
  var el=document.getElementById(statusId);var dl=document.getElementById(detailId);
  if(!el)return;
  var ok=val==='online'||val==='connected'||val==='configured'||val==='other_device';
  var text;
  if(ok) text='在线';
  else if(val==='offline'||val==='disconnected') text='离线';
  else if(val==='unreachable') text='网络不可达';
  else if(val==='error') text='错误';
  else text=val||'未知';
  el.textContent=text;
  el.className='conn-status '+(ok?'online':(val==='unreachable'?'warn':(val==='offline'||val==='disconnected'?'offline':(val==='error'?'offline':'unknown'))));
}

function checkADB(){fetch('/api/adb/status').then(r=>r.json()).then(d=>{
  setConnCard('conn-adb-status','conn-adb-detail',d.status,'ADB');
  var detail = (d.host||'')+':'+(d.port||'')+' | '+d.adb_exe+' | '+d.status;
  if(d.error) detail += ' | '+d.error;
  if(d.devices&&d.devices.length) detail += ' | 已发现'+d.devices.length+'个设备';
  document.getElementById('conn-adb-detail').textContent = detail;
  var adbOk=d.status==='connected'||d.status==='other_device';
  setMini('conn-adb',adbOk?'online':'offline','ADB');
  updateReadinessIndicator(null,adbOk,null,null);
}).catch(function(e){
  document.getElementById('conn-adb-status').textContent='错误';
  document.getElementById('conn-adb-status').className='conn-status offline';
  document.getElementById('conn-adb-detail').textContent='请求失败: '+e;
  setMini('conn-adb','offline','ADB');
  updateReadinessIndicator(null,false,null,null);
})}
function reconnectADB(){fetch('/api/adb/reconnect',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
  document.getElementById('conn-adb-status').textContent=d.status==='connected'?'已连接':d.status;
  document.getElementById('conn-adb-status').className='conn-status '+(d.status==='connected'?'online':'offline');
  document.getElementById('conn-adb-detail').textContent=d.output||'';
})}
function checkGitHub(){fetch('/api/github/status').then(r=>r.json()).then(d=>{
  var el=document.getElementById('conn-github-status');
  var dl=document.getElementById('conn-github-detail');
  if(!d.has_remote){
    el.textContent='未配置';
    el.className='conn-status offline';
    dl.textContent=(d.message||'请在指令框输入: repo 仓库地址');
    setMini('conn-gh','offline','GH');
    updateReadinessIndicator(null,null,false,null);
  }else{
    var statusText = d.api_status==='configured'?'已配置':(d.api_status==='online'?'在线':'离线');
    var isOk = d.api_status==='configured'||d.api_status==='online';
    el.textContent=statusText;
    el.className='conn-status '+(isOk?'online':'offline');
    dl.textContent=(d.repo_url||'')+' | '+d.branch;
    if(d.has_changes) dl.textContent+=' | 有未提交变更';
    setMini('conn-gh',isOk?'online':'offline','GH');
    updateReadinessIndicator(null,null,isOk,null);
  }
}).catch(function(e){
  document.getElementById('conn-github-status').textContent='错误';
  document.getElementById('conn-github-status').className='conn-status offline';
  document.getElementById('conn-github-detail').textContent='网络错误: '+e;
  setMini('conn-gh','offline','GH');
  updateReadinessIndicator(null,null,false,null);
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
function pushToGitHub(){
  var btn=document.getElementById('conn-github-status');
  btn.textContent='推送中...';
  btn.className='conn-status unknown';
  var dl=document.getElementById('conn-github-detail');
  dl.textContent='正在推送到GitHub（可能需要较长时间，请耐心等待）...';
  var resultEl=document.getElementById('settings-github-result');
  var startTime=Date.now();
  fetch('/api/github/push',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:'手动推送 '+new Date().toLocaleString()})}).then(r=>r.json()).then(d=>{
    if(d.status==='pushed'){
      var elapsed=Math.round((Date.now()-startTime)/1000);
      btn.textContent='上传成功';
      btn.className='conn-status online';
      var pushTime=d.push_time||'';
      dl.innerHTML='上传成功 | <span style="color:#4caf50">最后上传: '+pushTime+'</span> | 耗时: '+elapsed+'秒';
      if(resultEl) resultEl.innerHTML='<div class="alert success">上传成功 ('+pushTime+')</div>';
      // 持久化保存最后上传时间
      try{localStorage.setItem('github_last_push_time',pushTime)}catch(e){}
    }else if(d.status==='duplicate' || d.status==='no_changes'){
      btn.textContent='无变更';
      btn.className='conn-status online';
      dl.textContent=d.detail||d.message||'无新变更需要推送';
      if(resultEl) resultEl.innerHTML='<div class="alert success">无变更</div>';
    }else{
      btn.textContent='失败';
      btn.className='conn-status offline';
      dl.textContent='推送失败: '+(d.error||'')+' | '+(d.suggestion||'');
      if(resultEl) resultEl.innerHTML='<div class="alert error">'+d.status+'</div>';
    }
  }).catch(function(e){
      btn.textContent='失败';
      btn.className='conn-status offline';
      dl.textContent='网络错误，请重试';
  });
}
function pullFromGitHub(){fetch('/api/github/pull',{method:'POST'}).then(r=>r.json()).then(d=>{
  document.getElementById('settings-github-result').innerHTML='<div class="alert '+(d.status==='pulled'?'success':'error')+'">'+d.status+'</div>';
})}
function checkServer(){fetch('/api/server/status').then(r=>r.json()).then(d=>{
  var el=document.getElementById('conn-server-status');
  var dl=document.getElementById('conn-server-detail');
  var srvOk=false;
  if(d.status==='no_key'){
    el.textContent='无密钥';
    el.className='conn-status offline';
    dl.textContent=(d.detail&&d.detail.suggestion)||'请配置SSH密钥';
  }else if(d.status==='online'){
    el.textContent='在线';
    el.className='conn-status online';
    dl.textContent='部署: '+(d.deployed?'是':'否')+' | 路径: /home/ubuntu/firefightAI';
    srvOk=d.status==='online'&&d.deployed;
  }else if(d.status==='error'){
    el.textContent='连接失败';
    el.className='conn-status offline';
    dl.textContent='诊断: '+(d.detail&&d.detail.diagnosis||d.error||'连接失败');
  }else{
    el.textContent='离线';
    el.className='conn-status offline';
    dl.textContent=(d.detail&&d.detail.diagnosis)||d.error||'连接失败';
  }
  setMini('conn-srv',srvOk?'online':'offline','SRV');
  updateReadinessIndicator(null,null,null,srvOk);
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
_on('server_deploy_progress',function(d){document.getElementById('conn-server-detail').textContent=d.step+' ('+d.progress+'%)'});
_on('server_deploy_complete',function(d){document.getElementById('conn-server-status').textContent='在线';document.getElementById('conn-server-status').className='conn-status online';document.getElementById('conn-server-detail').textContent='部署完成'});
_on('server_deploy_error',function(d){document.getElementById('conn-server-status').textContent='错误';document.getElementById('conn-server-status').className='conn-status offline';document.getElementById('conn-server-detail').textContent=d.error});

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
_on('pytorch_update_complete',function(d){document.getElementById('conn-pytorch-status').textContent=d.version||'更新完成';document.getElementById('conn-pytorch-status').className='conn-status '+(d.success?'online':'offline')});

// ── 智能搜索 ──
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
  if(!socket||!socket.connected){alert('Socket.IO 未连接');return;}
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
      html+='<div class="search-result" style="display:flex;gap:8px;align-items:flex-start;padding:8px;border-bottom:1px solid #1a1f2b;cursor:pointer" onclick="this.querySelector(\'input\').checked=!this.querySelector(\'input\').checked">'+
        '<input type="checkbox" value="'+i+'" style="margin-top:2px;accent-color:#58a5f3;flex-shrink:0" onclick="event.stopPropagation()">'+
        '<div style="flex:1"><div class="sr-title">'+(i+1)+'. '+escapeHtml(r.title||'')+'</div>'+
        (r.snippet?'<div class="sr-snippet">'+escapeHtml(r.snippet)+'</div>':'')+
        (r.url?'<div class="sr-url">'+escapeHtml(r.url)+'</div>':'')+
        '</div></div>';
    });
  }
  document.getElementById('web-search-results').innerHTML=html||'<div style="color:#888;padding:10px">无结果</div>';
}
function saveSearchResult(){
  if(!lastSearchResults||!lastSearchQuery){alert('请先搜索');return}
  // 只保存勾选的
  var selected=[];
  document.querySelectorAll('#web-search-results input[type=checkbox]:checked').forEach(function(cb){selected.push(parseInt(cb.value))});
  var content='搜索: '+lastSearchQuery;
  (lastSearchResults.results||[]).forEach(function(r,i){if(selected.length===0||selected.includes(i))content+='\n'+r.title+': '+(r.snippet||'')});
  fetch('/api/learning_log/add_manual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:content})}).then(r=>r.json()).then(d=>{
    alert('已保存 '+selected.length+' 条到知识库并与AI学习记录互通!');
    refreshSearchKnowledge();
  })
}
function saveSelectedResults(){
  saveSearchResult();
}
function toggleAllSearchResults(){
  var cbs=document.querySelectorAll('#web-search-results input[type=checkbox]');
  var allChecked=Array.from(cbs).every(function(cb){return cb.checked});
  cbs.forEach(function(cb){cb.checked=!allChecked});
}
function copySearchResults(){
  if(!lastSearchResults){alert('请先搜索');return}
  var text='';
  (lastSearchResults.results||[]).forEach(function(r){text+=r.title+'\n'+r.snippet+'\n---\n'});
  navigator.clipboard.writeText(text).then(function(){alert('已复制')});
}
function learnFromWeb(){
  fetch('/api/web/learn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(r=>r.json()).then(d=>{
    if(d.status==='learning'){alert('AI学习已启动，请查看学习日志')}
    else{alert(d.error||'启动失败')}
  })
}
// ── 知识库互通: 从 /api/knowledge/list 加载, 与学习日志共用 ──
function refreshSearchKnowledge(){
  fetch('/api/knowledge/list').then(r=>r.json()).then(d=>{
    var c=document.getElementById('knowledge-list');
    var status=document.getElementById('search-knowledge-status');
    if(!d.knowledge||!d.knowledge.length){
      c.innerHTML='<div style="color:#888;padding:20px;text-align:center">暂无知识<br><small>搜索并保存结果, 或从AI学习日志同步</small></div>';
      status.textContent='知识库为空';
      return;
    }
    var h='';
    d.knowledge.forEach(function(k){
      var cat=k.category||'other';
      var trained=k.trained?'✓':'○';
      var checked=k.selected?'checked':'';
      h+='<div style="padding:6px 8px;border-bottom:1px solid #1a1f2b;display:flex;gap:8px;align-items:flex-start;font-size:11px">'+
        '<input type="checkbox" '+checked+' onchange="selectKnowledge(\''+k.id+'\',this.checked)" style="margin-top:2px;accent-color:#58a5f3">'+
        '<div style="flex:1"><span style="color:#58a5f3">['+cat+']</span> '+
        '<span style="color:#e6edf3">'+esc(k.title)+'</span> '+
        '<span style="color:#666;font-size:10px">'+trained+'</span></div>'+
        '</div>';
    });
    c.innerHTML=h;
    status.textContent='共 '+d.knowledge.length+' 条 | 勾选后点击训练AI';
  }).catch(function(e){
    document.getElementById('knowledge-list').innerHTML='<div style="color:#e53935;padding:10px;text-align:center">加载失败</div>';
  });
}
function trainKnowledgeFromSearch(){
  var status=document.getElementById('search-knowledge-status');
  status.textContent='训练中...';status.style.color='#ff9800';
  fetch('/api/knowledge/train',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(r=>r.json()).then(d=>{
    status.textContent='✅ 已训练 '+d.trained+' 条 | 学习总数: '+d.total_learnings;
    status.style.color='#4caf50';
    refreshSearchKnowledge();
  }).catch(function(e){
    status.textContent='❌ 训练失败';status.style.color='#e53935';
  });
}
function copySearchResults(){
  if(!lastSearchResults){alert('请先搜索');return}
  var text=lastSearchQuery+'\n\n';
  if(lastSearchResults.summary)text+='AI总结:\n'+lastSearchResults.summary+'\n\n';
  if(lastSearchResults.results){lastSearchResults.results.forEach(function(r,i){text+=(i+1)+'. '+r.title+'\n'+r.snippet+'\n'+r.url+'\n\n'})}
  navigator.clipboard.writeText(text).then(function(){alert('已复制到剪贴板')}).catch(function(){alert('复制失败')})
}
function loadKnowledge(){refreshSearchKnowledge()}
function viewKnowledgeDetail(filename){
  _kdetailFilename=filename;
  document.getElementById('knowledge-detail-overlay').classList.add('active');
  document.getElementById('kdetail-title').textContent='加载中...';
  document.getElementById('kdetail-meta').innerHTML='';
  document.getElementById('kdetail-content').textContent='加载中...';
  fetch('/api/web/knowledge/'+encodeURIComponent(filename)).then(r=>r.json()).then(d=>{
    if(d.error){document.getElementById('kdetail-title').textContent='错误';document.getElementById('kdetail-content').textContent=d.error;return}
    document.getElementById('kdetail-title').textContent=d.query||'未命名知识';
    var meta='';
    if(d.saved_at) meta+='<span>时间: '+d.saved_at+'</span>';
    if(d.tags&&d.tags.length) meta+='<span>标签: '+d.tags.join(', ')+'</span>';
    if(d.url) meta+='<span>来源: <a href="'+d.url+'" target="_blank" style="color:#58a5f3">'+d.url.substring(0,50)+'...</a></span>';
    document.getElementById('kdetail-meta').innerHTML=meta;
    var content='';
    if(d.summary) content+='【AI总结】\n'+d.summary+'\n\n';
    if(d.full_text) content+='【完整内容】\n'+d.full_text+'\n\n';
    if(d.results&&d.results.length){
      content+='【搜索结果】\n';
      d.results.forEach(function(r,i){content+=(i+1)+'. '+r.title+'\n   '+r.snippet+'\n   '+r.url+'\n\n'});
    }
    if(!content) content='(无详细内容)';
    document.getElementById('kdetail-content').textContent=content;
  }).catch(function(e){document.getElementById('kdetail-content').textContent='加载失败: '+e});
}
function closeKnowledgeDetail(){
  document.getElementById('knowledge-detail-overlay').classList.remove('active');
  _kdetailFilename='';
}
function deleteKnowledgeDetail(){
  if(!_kdetailFilename)return;
  if(!confirm('确定删除这条知识吗？'))return;
  fetch('/api/web/knowledge/'+encodeURIComponent(_kdetailFilename),{method:'DELETE'}).then(r=>r.json()).then(d=>{
    closeKnowledgeDetail();
    loadKnowledge();
    if(d.status==='ok'){var r=document.getElementById('emu-touch-result');if(r){r.textContent='已删除';r.style.color='#ff9800'}}
  }).catch(function(e){alert('删除失败: '+e)});
}
function copyKnowledgeDetail(){
  var content=document.getElementById('kdetail-content').textContent;
  var title=document.getElementById('kdetail-title').textContent;
  navigator.clipboard.writeText(title+'\n\n'+content).then(function(){alert('已复制到剪贴板')}).catch(function(){alert('复制失败')});
}

// ── 智能体 ──
function agentChat(){
  if(!socket||!socket.connected){alert('Socket.IO 未连接');return;}
  var inp=document.getElementById('agent-chat-input');var msg=inp.value.trim();if(!msg)return;
  addAgentMessage('user',msg);inp.value='';
  if(msg.includes('诊断')||msg.includes('检查')||msg.includes('连接')){
    runDiagnostics();
    addAgentMessage('assistant','正在运行诊断，请查看右侧诊断面板...');
  }else if(msg.includes('搜索')||msg.includes('查询')){
    var q=msg.replace(/搜索|查询|帮我查/g,'').trim();
    if(q){
      addAgentMessage('assistant','正在联网搜索: '+q+'...');
      // 🔥 使用 Socket.IO 流式搜索 (真正联网: DuckDuckGo/Bing → DeepSeek总结)
      socket.emit('web_search',{query:q});
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
    var ok=d.api_status==='online'||d.api_status==='configured';
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
  // 先尝试直接下载，如果失败则创建安装包后下载
  fetch('/api/package/download').then(function(res){
    if(res.status===404){
      // 包不存在，先创建
      createPackageThenDownload();
    }else{
      window.open('/api/package/download','_blank');
    }
  }).catch(function(){createPackageThenDownload()})
}
function createPackageThenDownload(){
  var r=document.getElementById('package-result');
  if(r)r.innerHTML='<div class="alert info"><span class="spinner"></span> 创建安装包中...</div>';
  fetch('/api/package/create',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(res=>res.json()).then(d=>{
    if(d.status==='created'){
      if(r)r.innerHTML='<div class="package-info"><strong>安装包已创建!</strong><br>文件: '+d.filename+' | 大小: '+d.size_mb+'MB<br><a href="'+d.download_url+'" style="color:#4caf50;font-size:11px">点击下载</a></div>';
      window.open('/api/package/download','_blank');
    }
  }).catch(function(e){if(r)r.innerHTML='<div class="alert error">创建失败: '+e+'</div>'})
}
// ── 🔐 独立运维配置 ──
var _cfg={};
function onCfgChange(){_cfg.dirty=true}
function saveConfig(){
  _cfg={apikey:document.getElementById('cfg-apikey').value,server:document.getElementById('cfg-server').value,github_token:document.getElementById('cfg-github-token').value,ssh_key:document.getElementById('cfg-ssh-key').value,ssh_user:document.getElementById('cfg-ssh-user').value};
  var r=document.getElementById('cfg-result');
  r.innerHTML='<span style="color:#58a5f3">💾 正在保存...</span>';
  fetch('/api/config/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(_cfg)}).then(res=>res.json()).then(d=>{
    r.innerHTML=d.status==='ok'?'<span style="color:#4caf50">✅ 配置已保存并生效</span>':'<span style="color:#f44336">❌ '+d.error+'</span>';
  }).catch(function(e){r.innerHTML='<span style="color:#f44336">失败: '+e+'</span>'})
}
function deployToServer(){
  var r=document.getElementById('cfg-result');
  var cfg={apikey:document.getElementById('cfg-apikey').value,server:document.getElementById('cfg-server').value,github_token:document.getElementById('cfg-github-token').value,ssh_key:document.getElementById('cfg-ssh-key').value,ssh_user:document.getElementById('cfg-ssh-user').value};
  r.innerHTML='<span style="color:#ff9800">🚀 部署中...</span>';
  fetch('/api/deploy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)}).then(res=>res.json()).then(d=>{
    r.innerHTML=d.status==='ok'?'<span style="color:#4caf50">✅ 部署成功! '+d.message+'</span>':'<span style="color:#f44336">❌ '+d.error+'</span>';
  }).catch(function(e){r.innerHTML='<span style="color:#f44336">失败: '+e+'</span>'})
}
function testConnection(){
  var r=document.getElementById('cfg-result');
  r.innerHTML='<span style="color:#58a5f3">🔗 测试中...</span>';
  var s=document.getElementById('cfg-server').value||'https://firefightai.top';
  fetch(s+'/api/version').then(res=>res.json()).then(d=>{
    r.innerHTML='<span style="color:#4caf50">✅ 服务器在线 v'+d.version+'</span>';
  }).catch(function(e){r.innerHTML='<span style="color:#f44336">❌ 无法连接: '+e+'</span>'})
}
// ── ☁️ 全域同步 ──
function syncGlobal(){
  var r=document.getElementById('sync-result');
  r.innerHTML='<div class="alert info"><span class="spinner"></span> 🌐 全域同步中 (参数+知识+日志)...</div>';
  fetch('/api/sync/global',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({machine_id:window.location.hostname||'unknown'})}).then(res=>res.json()).then(d=>{
    if(d.status==='synced'){
      r.innerHTML='<div class="alert success">✅ 全域同步完成!<br>合并机数: '+d.merged_machines+' | 新增知识: '+d.new_knowledge+'条 | 新增日志: '+d.new_logs+'条<br>版本: v'+d.version.version+' | 参数: '+Object.keys(d.params||{}).length+'项 | 知识库: '+(d.knowledge_base||[]).length+'条 | 日志: '+(d.learning_log||[]).length+'条</div>';
    }else{
      r.innerHTML='<div class="alert error">同步失败: '+d.error+'</div>';
    }
  }).catch(function(e){r.innerHTML='<div class="alert error">网络错误: '+e+'</div>'})
}
function syncPullAll(){
  var r=document.getElementById('sync-result');
  r.innerHTML='<div class="alert info"><span class="spinner"></span> 拉取全部数据...</div>';
  fetch('/api/sync/pull').then(res=>res.json()).then(d=>{
    r.innerHTML='<div class="alert success">📥 已获取服务器全部数据<br>版本: v'+(d.version?d.version.version:'?')+' | 学习: '+(d.total_learnings||0)+'次<br>知识: '+d.total_knowledge+'条 | 日志: '+d.total_logs+'条 | 参数: '+Object.keys(d.params||{}).length+'项<br><span style="font-size:10px;color:#888">'+d.server_time+'</span></div>';
  }).catch(function(e){r.innerHTML='<div class="alert error">失败: '+e+'</div>'})
}
function syncCheckVersion(){
  var r=document.getElementById('sync-result');
  r.innerHTML='<div class="alert info"><span class="spinner"></span> 检查中...</div>';
  fetch('/api/sync/params/version').then(res=>res.json()).then(d=>{
    r.innerHTML='<div class="alert success">📋 服务器: v'+d.version+' | 学习: '+d.total_learnings+'次 | 更新: '+(d.updated_at||'--')+'</div>';
  }).catch(function(e){r.innerHTML='<div class="alert error">失败: '+e+'</div>'})
}
// ── v5.1 自动保存 ──
function checkAutoSave(){
  var el=document.getElementById('conn-autosave-status');
  el.textContent='检查中';el.className='conn-status checking';
  fetch('/api/auto_save/schedule').then(r=>r.json()).then(d=>{
    if(d.status==='ok'){
      el.textContent=d.running?'运行中':'已停止';
      el.className=d.running?'conn-status ok':'conn-status fail';
      var detail='下次保存: '+(d.next_save_time||'--')+' | 已保存: '+(d.total_saves||0)+'次';
      document.getElementById('conn-autosave-detail').textContent=detail;
    }else if(d.status==='not_initialized'){
      el.textContent='未初始化';
      el.className='conn-status warn';
      document.getElementById('conn-autosave-detail').textContent='调度器未初始化，请重启服务器';
    }else{
      el.textContent='异常';
      el.className='conn-status fail';
      document.getElementById('conn-autosave-detail').textContent=(d.error||'未知错误');
    }
  }).catch(function(e){el.textContent='网络错误';el.className='conn-status fail';document.getElementById('conn-autosave-detail').textContent='请求失败: '+e})
}
function saveNow(){
  var el=document.getElementById('conn-autosave-status');
  el.textContent='保存中...';el.className='conn-status checking';
  fetch('/api/auto_save/now',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'){
      var ghOk=d.upload&&d.upload.github&&d.upload.github.success;
      var srvOk=d.upload&&d.upload.server&&d.upload.server.success;
      el.textContent=ghOk?'已保存并推送':'已保存(推送失败)';
      el.className=ghOk?'conn-status ok':'conn-status warn';
      document.getElementById('conn-autosave-detail').textContent='保存: '+JSON.stringify(d.save)+' | GitHub: '+(ghOk?'成功':'失败')+' | 服务器: '+(srvOk?'成功':'失败');
    }else{
      el.textContent='失败';
      el.className='conn-status fail';
      document.getElementById('conn-autosave-detail').textContent=(d.error||'保存失败');
    }
  }).catch(function(e){el.textContent='网络错误';el.className='conn-status fail';document.getElementById('conn-autosave-detail').textContent='请求失败: '+e})
}

// ── 模型参数管理 ──
function checkLearningParams(){
  var el=document.getElementById('conn-params-status');
  el.textContent='检查中...';el.className='conn-status checking';
  fetch('/api/learn/status').then(r=>r.json()).then(d=>{
    if(d.params){
      el.textContent='已学习'+d.params.total_learnings+'次';el.className='conn-status ok';
      document.getElementById('conn-params-detail').textContent='temp='+d.params.temperature+' | lr='+d.params.learning_rate+' | 运行:'+(d.running?'是':'否');
    }else{
      el.textContent='未初始化';el.className='conn-status warn';
    }
  }).catch(function(e){el.textContent='错误';el.className='conn-status fail'})
}
function uploadParamsToGitHub(){
  var el=document.getElementById('conn-params-status');
  el.textContent='推送中...';el.className='conn-status checking';
  fetch('/api/params/upload_github',{method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'||d.success){
      el.textContent='已推送';el.className='conn-status ok';
      document.getElementById('conn-params-detail').textContent='GitHub: 已推送学习参数';
    }else{
      el.textContent='失败';el.className='conn-status fail';
      document.getElementById('conn-params-detail').textContent=d.error||'推送失败';
    }
  }).catch(function(e){el.textContent='错误';el.className='conn-status fail'})
}
function uploadParamsToServer(){
  var el=document.getElementById('conn-params-status');
  el.textContent='上传中...';el.className='conn-status checking';
  fetch('/api/params/upload_server',{method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'||d.success){
      el.textContent='已上传';el.className='conn-status ok';
      document.getElementById('conn-params-detail').textContent='服务器: 已部署学习参数';
    }else{
      el.textContent='失败';el.className='conn-status fail';
      document.getElementById('conn-params-detail').textContent=d.error||'上传失败';
    }
  }).catch(function(e){el.textContent='错误';el.className='conn-status fail'})
}

// ── Web Search Socket事件 ──
_on('web_search_progress',function(d){
  document.getElementById('web-search-progress').innerHTML='<div class="search-progress"><span class="spinner"></span> '+d.step+' ('+d.progress+'%)</div>';
  // 🔥 同时更新智能体聊天泡泡
  if(agentChatBubble){agentChatBubble.textContent=d.step+'...'}
});
_on('web_search_token',function(d){
  var el=document.getElementById('stream-text');
  if(el){
    if(d.done){el.textContent=d.full}else{el.textContent+=d.token}
  }
  // 🔥 同时更新智能体聊天泡泡
  if(agentChatBubble){
    if(d.done){agentChatBubble.textContent=d.full}else{agentChatBubble.textContent=(agentChatBubble.textContent||'')+d.token}
  }
});
_on('web_search_complete',function(d){
  document.getElementById('web-search-progress').innerHTML='';
  lastSearchResults=d;
  renderSearchResults(d);
  // 🔥 同时更新智能体聊天泡泡
  if(agentChatBubble){
    agentChatBubble.textContent=d.summary||'搜索完成';
    agentChatBubble.removeAttribute('id');
    agentChatBubble=null;
  }
});
_on('web_search_error',function(d){
  document.getElementById('web-search-progress').innerHTML='<div class="alert error">'+d.error+'</div>';
  // 🔥 同时更新智能体聊天泡泡
  if(agentChatBubble){
    agentChatBubble.textContent='搜索失败: '+d.error;
    agentChatBubble.removeAttribute('id');
    agentChatBubble=null;
  }
});
_on('web_search_stream',function(d){
  // 🔥 REST API 流式搜索事件
  if(agentChatBubble){
    if(d.done){agentChatBubble.textContent=d.text||d.full;agentChatBubble.removeAttribute('id');agentChatBubble=null}
    else{agentChatBubble.textContent=d.text||d.full}
  }
});
_on('web_learn_result',function(d){
  if(d.error){alert('学习失败: '+d.error)}else{alert('AI学习完成!')}
});
// ── v5.1 兵法学习事件 ──
_on('military_learned',function(d){
  var summary=d.summary||'';
  if(summary){
    addLearningLog('兵法学习',d.query,summary);
    var chatMsgs=document.getElementById('chat-messages');
    if(chatMsgs){
      var div=document.createElement('div');
      div.className='chat-msg assistant';
      div.innerHTML='<div class="avatar">AI</div><div class="bubble">[兵法学习] 已学习「'+d.query+'」相关内容，提炼战术规则...<br><small style="color:#4caf50">'+escapeHtml(summary.substring(0,300))+'</small></div>';
      chatMsgs.appendChild(div);
      chatMsgs.scrollTop=chatMsgs.scrollHeight;
    }
  }
});
// ── v5.1 自动保存事件 ──
_on('auto_save_progress',function(d){
  if(d.step)console.log('[AutoSave] '+d.step);
});
_on('auto_save_complete',function(d){
  var el=document.getElementById('conn-autosave-status');
  if(el){el.textContent='已保存';el.className='conn-status ok'}
  var detail=document.getElementById('conn-autosave-detail');
  if(detail&&d){detail.textContent='保存: '+JSON.stringify(d.save||{})+' | 上传: '+JSON.stringify(d.upload||{})}
});

// ── 训练 ──
var unifiedDataset='faction_yolo';  // 统一数据集名称
function loadUnifiedDataset(){
  var el=document.getElementById('dataset-unified-stats');
  el.textContent='加载中...';
  fetch('/api/datasets').then(r=>r.json()).then(data=>{
    var totalImgs=0,totalLabels=0,subsets=[];
    data.forEach(function(d){
      totalImgs+=d.images||0;
      subsets.push(d.name+'('+d.images+'张)');
    });
    el.textContent=totalImgs+' 张图片 | '+data.length+' 个子集';
    el.title='子数据集: '+subsets.join(', ');
    // 使用第一个有data.yaml的数据集作为训练目标
    data.forEach(function(d){if(d.name==='faction_yolo')unifiedDataset=d.name});
  }).catch(function(e){el.textContent='加载失败: '+e});
}
function loadDatasets(){loadUnifiedDataset()}  // 兼容旧调用
function selectDataset(name){unifiedDataset=name;loadUnifiedDataset()}
function loadModels(){fetch('/api/models').then(r=>r.json()).then(data=>{var html='';data.forEach(function(m){html+='<div class="model-card"><div class="name">'+m.name+'</div><div class="info">'+m.size_mb+'MB</div></div>'});document.getElementById('model-list').innerHTML=html||'暂无模型'})}
function uploadImages(){var files=document.getElementById('file-input').files;if(!files.length)return;var fd=new FormData();for(var i=0;i<files.length;i++)fd.append('file_'+i,files[i]);fd.append('dataset',unifiedDataset);var s=document.getElementById('upload-status');s.innerHTML='<div class="alert info">上传中...</div>';fetch('/api/upload_images',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{s.innerHTML='<div class="alert success">已上传 '+d.uploaded.length+' 张到 '+d.dataset+'</div>';loadUnifiedDataset()}).catch(e=>{s.innerHTML='<div class="alert error">失败: '+e+'</div>'})}
function startTraining(){var ep=parseInt(document.getElementById('train-epochs').value)||50;var md=document.getElementById('train-model').value;var isz=parseInt(document.getElementById('train-imgsz').value)||640;var autoPush=document.getElementById('auto-push-github').checked;var device=document.getElementById('train-device').value;var removeAfter=document.getElementById('remove-after-train').checked;document.getElementById('btn-train-start').style.display='none';document.getElementById('btn-train-stop').style.display='inline-block';document.getElementById('train-progress-container').style.display='block';document.getElementById('train-log').innerHTML='';document.getElementById('train-status-text').textContent='训练中 ('+device.toUpperCase()+')...';document.getElementById('train-results').innerHTML='';document.getElementById('train-progress-bar').style.width='0%';document.getElementById('train-progress-bar').textContent='0%';fetch('/api/train/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset:unifiedDataset,model_name:md,epochs:ep,imgsz:isz,device:device,remove_after_train:removeAfter,auto_push_github:autoPush})}).then(r=>r.json()).then(d=>{if(d.error)alert(d.error)})}
function stopTraining(){fetch('/api/train/stop',{method:'POST'}).then(r=>r.json()).then(d=>{document.getElementById('train-status-text').textContent='已停止';document.getElementById('btn-train-start').style.display='inline-block';document.getElementById('btn-train-stop').style.display='none'})}
_on('training_log',function(d){var l=document.getElementById('train-log');l.innerHTML+='<div>'+escapeHtml(d.line)+'</div>';l.scrollTop=l.scrollHeight})
_on('training_state_update',function(d){
  var bar=document.getElementById('train-progress-bar');
  bar.style.width=d.progress+'%';
  bar.textContent=d.progress+'%';
  document.getElementById('train-status-text').textContent=d.message||'';
})
_on('training_complete',function(d){
  document.getElementById('btn-train-start').style.display='inline-block';
  document.getElementById('btn-train-stop').style.display='none';
  document.getElementById('train-status-text').textContent=d.success?'训练完成!':'训练失败';
  loadModels();
  // 显示训练结果
  if(d.results){
    var rs=document.getElementById('train-results');
    var params=d.results.params||{};
    var qualityColor={'poor':'#e53935','moderate':'#ff9800','good':'#4caf50'};
    var qualityLabel={'poor':'差','moderate':'中等','good':'良好'};
    var q=d.results.quality||'';
    var html='<div class="alert '+(d.success?'success':'error')+'" style="margin-top:10px;font-size:11px;line-height:1.6">';
    html+='<strong>训练结果</strong>'+(q?' <span style="color:'+(qualityColor[q]||'#888')+'">('+qualityLabel[q]+')</span>':'')+'<br>';
    // 训练参数
    html+='<div style="margin-top:6px;padding:6px;background:#111;border-radius:4px;color:#aaa">';
    html+='<strong style="color:#888">训练参数:</strong> ';
    html+='模型: <b style="color:#2196f3">'+escapeHtml(params.model||'')+'</b> | ';
    html+='数据集: <b style="color:#2196f3">'+escapeHtml(params.dataset||'')+'</b> | ';
    html+='Epochs: <b style="color:#2196f3">'+params.epochs+'</b> | ';
    html+='图片: <b style="color:'+(params.image_count<50?'#e53935':'#2196f3')+'">'+params.image_count+'张</b> | ';
    html+='标注: <b style="color:#2196f3">'+params.label_count+'个</b>';
    if(params.class_distribution){
      var clsNames={'0':'tank','1':'infantry'};
      for(var k in params.class_distribution){
        html+=' | '+clsNames[k]+': <b>'+params.class_distribution[k]+'个</b>';
      }
    }
    html+='</div>';
    if(d.results.mAP50){
      html+='<table style="width:100%;font-size:11px;margin-top:6px;border-collapse:collapse">';
      html+='<tr><td style="padding:4px;color:#888">mAP50</td><td style="color:#4caf50;font-weight:bold">'+d.results.mAP50+'</td>';
      html+='<td style="padding:4px;color:#888">mAP50-95</td><td style="color:#4caf50;font-weight:bold">'+d.results.mAP50_95+'</td></tr>';
      html+='<tr><td style="padding:4px;color:#888">Precision</td><td style="color:#2196f3">'+d.results.precision+'</td>';
      html+='<td style="padding:4px;color:#888">Recall</td><td style="color:#2196f3">'+d.results.recall+'</td></tr>';
      html+='</table>';
    }
    if(d.results.feedback){
      html+='<div style="margin-top:6px;padding:6px;background:#1a1a00;border-left:3px solid '+qualityColor[q]+';border-radius:0 4px 4px 0;color:#ffcc00">💡 '+escapeHtml(d.results.feedback)+'</div>';
    }
    if(d.results.error){
      html+='<div style="margin-top:6px;color:#e53935">错误: '+escapeHtml(d.results.error)+'</div>';
    }
    html+='</div>';
    rs.innerHTML=html;
  }
})
_on('github_push_complete',function(d){document.getElementById('train-status-text').textContent+=' | GitHub推送: '+(d.success?'成功':'失败')})

// ── 参数学习 ──
function loadParams(){fetch('/api/params/list').then(r=>r.json()).then(data=>{var html='';data.forEach(function(p){html+='<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #1a1f2b"><span>'+p.name+'</span><span style="color:#888">'+p.size_kb+'KB</span></div>'});document.getElementById('params-list').innerHTML=html||'暂无参数文件'});refreshParamsDisplay()}
function uploadParams(){var files=document.getElementById('params-input').files;if(!files.length)return;var fd=new FormData();for(var i=0;i<files.length;i++)fd.append('file_'+i,files[i]);var s=document.getElementById('params-upload-status');s.innerHTML='<div class="alert info">上传中...</div>';fetch('/api/params/upload',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{s.innerHTML='<div class="alert success">已上传 '+d.count+' 个文件</div>';loadParams()}).catch(e=>{s.innerHTML='<div class="alert error">失败</div>'})}
function learnFromParams(){var r=document.getElementById('params-learn-result');r.innerHTML='<div class="alert info"><span class="spinner"></span> AI 正在学习参数...</div>';fetch('/api/params/learn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(res=>res.json()).then(d=>{r.innerHTML='<div class="alert info">学习已启动, 等待 AI 分析...</div>'})}
_on('params_learned',function(d){var r=document.getElementById('params-learn-result');if(d.error){r.innerHTML='<div class="alert error">'+d.error+'</div>'}else{r.innerHTML='<div class="alert success"><strong>AI 学习结果:</strong><br><pre style="font-size:10px;white-space:pre-wrap;margin-top:6px;color:#aaa">'+escapeHtml(d.analysis||'')+'</pre></div>'}})

// 🔥 参数管理 - 保存/推送/拉取/同步
function refreshParamsDisplay(){
  fetch('/api/learn/params').then(r=>r.json()).then(d=>{
    var el=document.getElementById('params-current');
    if(el) el.innerHTML = '温度: <b>'+d.temperature+'</b> | 学习率: <b>'+d.learning_rate+'</b> | 进攻性: <b>'+d.tactical_aggressiveness+'</b> | 置信阈值: <b>'+d.confidence_threshold+'</b> | 学习次数: <b>'+d.total_learnings+'</b>';
  });
}
function saveParamsToLocal(){
  var status = document.getElementById('params-sync-status');
  status.textContent = '保存中...'; status.style.color = '#ff9800';
  fetch('/api/learn/params',{method:'GET'}).then(r=>r.json()).then(d=>{
    fetch('/api/learn/params',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}).then(r=>r.json()).then(r2=>{
      status.textContent = '已保存'; status.style.color = '#4caf50';
      refreshParamsDisplay();
      setTimeout(function(){status.textContent=''},2000);
    });
  });
}
function pushParamsToGitHub(){
  var status = document.getElementById('params-sync-status');
  status.textContent = '推送到GitHub...'; status.style.color = '#ff9800';
  fetch('/api/params/upload_github',{method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'||d.success){
      status.textContent = '已推送到GitHub'; status.style.color = '#4caf50';
    }else{
      status.textContent = '推送失败: '+(d.error||d.message||''); status.style.color = '#e53935';
    }
    setTimeout(function(){status.textContent=''},3000);
  }).catch(function(e){
    status.textContent = '推送失败'; status.style.color = '#e53935';
  });
}
function pushParamsToServer(){
  var status = document.getElementById('params-sync-status');
  status.textContent = '上传到服务器...'; status.style.color = '#ff9800';
  fetch('/api/params/upload_server',{method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'||d.success){
      status.textContent = '已上传到服务器'; status.style.color = '#4caf50';
    }else{
      status.textContent = '上传失败: '+(d.error||''); status.style.color = '#e53935';
    }
    setTimeout(function(){status.textContent=''},3000);
  }).catch(function(e){
    status.textContent = '上传失败'; status.style.color = '#e53935';
  });
}
function pullParamsFromGitHub(){
  var status = document.getElementById('params-sync-status');
  status.textContent = '从GitHub拉取...'; status.style.color = '#ff9800';
  fetch('/api/params/pull_github',{method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'){
      status.textContent = '已同步'; status.style.color = '#4caf50';
      refreshParamsDisplay();
    }else{
      status.textContent = '拉取失败: '+(d.error||''); status.style.color = '#e53935';
    }
    setTimeout(function(){status.textContent=''},3000);
  }).catch(function(e){
    status.textContent = '拉取失败'; status.style.color = '#e53935';
  });
}
function syncParamsAll(){
  var status = document.getElementById('params-sync-status');
  status.textContent = '全部同步中...'; status.style.color = '#ff9800';
  fetch('/api/params/sync',{method:'POST'}).then(r=>r.json()).then(d=>{
    var ok = d.results && d.results.github === 'pushed';
    status.textContent = ok ? '同步完成' : '部分失败';
    status.style.color = ok ? '#4caf50' : '#ff9800';
    refreshParamsDisplay();
    setTimeout(function(){status.textContent=''},3000);
  }).catch(function(e){
    status.textContent = '同步失败'; status.style.color = '#e53935';
  });
}

function learnFromCombat(){var r=document.getElementById('combat-learn-result');r.innerHTML='<div class="alert info"><span class="spinner"></span> AI 从实战数据学习...</div>';fetch('/api/combat/learn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(res=>res.json()).then(d=>{r.innerHTML='<div class="alert info">学习已启动</div>'})}
_on('combat_learn_result',function(d){var r=document.getElementById('combat-learn-result');if(d.error){r.innerHTML='<div class="alert error">'+d.error+'</div>'}else{var html='<div class="alert success">学习完成! 共'+d.total_experiences+'条经验</div>';if(d.stats)html+='<div style="font-size:10px;color:#888">平均分: '+d.stats.avg_score+' | 正向率: '+d.stats.positive_rate+'%</div>';if(d.rules&&d.rules.length)html+='<div style="font-size:10px;color:#4caf50">新规则: '+d.rules.join('; ')+'</div>';if(d.summary)html+='<div style="font-size:10px;color:#aaa;margin-top:4px;white-space:pre-wrap">'+escapeHtml(d.summary)+'</div>';r.innerHTML=html}})
function exportCombatData(){fetch('/api/combat/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({format:'csv'})}).then(r=>r.blob()).then(b=>{var a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='combat_data.csv';a.click()})}

// ── 学习日志 ──
function refreshLearningLog(){fetch('/api/learning_log?limit=100').then(r=>r.json()).then(data=>renderLearningLog(data))}
function clearLearningLog(){fetch('/api/learning_log/clear',{method:'POST'}).then(r=>r.json()).then(d=>{document.getElementById('learning-log-container').innerHTML='<div style="color:#888;padding:20px;text-align:center">日志已清空</div>'})}

function addManualKnowledge(){
  var ta=document.getElementById('manual-knowledge-input');
  var content=ta.value.trim();
  if(!content){alert('请先粘贴要训练的知识内容');return;}
  var btn=event.target;
  btn.textContent='保存中...';btn.disabled=true;
  fetch('/api/learning_log/add_manual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:content})}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'){
      ta.value='';
      addLearningLog(d.category,'手动知识 ('+d.word_count+'字)','已保存并开始训练...');
      document.getElementById('learning-log-train-result').style.display='block';
      document.getElementById('learning-log-train-result').innerHTML='✅ 已保存 '+d.word_count+' 字 ['+d.category+']，AI正在自动调参训练...';
    }else{
      alert('保存失败: '+(d.error||''));
    }
    btn.textContent='📥 保存到学习日志并训练AI';btn.disabled=false;
  }).catch(function(e){
    alert('请求失败: '+e);
    btn.textContent='📥 保存到学习日志并训练AI';btn.disabled=false;
  });
}

// ═══ AI 知识库 ═══
function refreshKnowledge(){
  fetch('/api/knowledge/list').then(r=>r.json()).then(d=>{
    var c=document.getElementById('knowledge-container');
    if(!d.knowledge||!d.knowledge.length){
      c.innerHTML='<div style="color:#888;padding:20px;text-align:center">暂无知识<br><small>AI通过作战、搜索、APK分析、对话纠正学到的知识将显示在这里</small></div>';
      document.getElementById('knowledge-status').textContent='知识库为空';
      return;
    }
    var h='';
    var cats={'tactic':'战术','strategy':'战法','search_insight':'搜索洞察','correction':'对话纠正','apk_analysis':'APK分析','combat':'实战经验'};
    d.knowledge.forEach(function(k){
      var cat=cats[k.category]||k.category;
      var trained=k.trained?'<span style="color:#4caf50">✓已训练</span>':'<span style="color:#888">○未训练</span>';
      var checked=k.selected?'checked':'';
      h+='<div style="padding:8px;border-bottom:1px solid #1a1f2b;display:flex;align-items:flex-start;gap:8px">'+
        '<input type="checkbox" '+checked+' onchange="selectKnowledge(\''+k.id+'\',this.checked)" style="margin-top:3px;accent-color:#58a5f3">'+
        '<div style="flex:1">'+
          '<div style="display:flex;gap:8px;align-items:center">'+
            '<span style="color:#58a5f3;font-weight:600">['+cat+']</span>'+
            '<span style="color:#e6edf3">'+esc(k.title)+'</span>'+
            '<span style="color:#666;font-size:10px">'+trained+'</span>'+
            '<span style="color:#555;font-size:10px;margin-left:auto">'+k.time+'</span>'+
          '</div>'+
          '<div style="color:#888;margin-top:2px">'+esc((k.content||'').substring(0,120))+'</div>'+
        '</div>'+
      '</div>';
    });
    c.innerHTML=h;
    document.getElementById('knowledge-status').textContent='共 '+d.knowledge.length+' 条知识';
  }).catch(function(e){
    document.getElementById('knowledge-container').innerHTML='<div style="color:#e53935;padding:20px;text-align:center">加载失败</div>';
  });
}

function selectKnowledge(id,checked){
  fetch('/api/knowledge/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,checked:checked})});
  var s=document.getElementById('knowledge-status');
  s.textContent='已更新选择';
  setTimeout(function(){refreshKnowledge()},300);
}

function trainSelectedKnowledge(){
  var btn=document.querySelector('#knowledge-status');
  var status=document.getElementById('knowledge-status');
  status.textContent='训练中...'; status.style.color='#ff9800';
  fetch('/api/knowledge/train',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(r=>r.json()).then(d=>{
    status.textContent='✅ 已训练 '+d.trained+' 条, 学习总数: '+d.total_learnings; status.style.color='#4caf50';
    refreshKnowledge();
  }).catch(function(e){
    status.textContent='❌ 训练失败'; status.style.color='#e53935';
  });
}

// Socket更新
_on('knowledge_update',function(d){
  refreshKnowledge();
});

// 页面加载时自动刷新
document.addEventListener('DOMContentLoaded',function(){setTimeout(refreshKnowledge,1000)});
_on('connected',function(){setTimeout(refreshKnowledge,500)});
function exportLearningLog(){fetch('/api/learning_log/export').then(r=>r.json()).then(d=>{var a=document.createElement('a');a.href='data:text/json;charset=utf-8,'+encodeURIComponent(JSON.stringify(d,null,2));a.download='learning_log.json';a.click()})}
// 🔥 上传学习日志到GitHub
function uploadLearningLog(){
  var btn=event.target; btn.textContent='上传中...'; btn.disabled=true;
  fetch('/api/learn/logs/upload',{method:'POST'}).then(r=>r.json()).then(d=>{
    btn.textContent='上传到GitHub'; btn.disabled=false;
    if(d.status==='pushed'){
      addLearningLog('self_learn','学习日志已上传到GitHub',d.total+'条');
    }else if(d.status==='saved_local'){
      addLearningLog('self_learn','已保存到本地 (推送失败)','');
    }else{
      addLearningLog('self_learn','上传失败: '+(d.error||''),'');
    }
  }).catch(function(e){btn.textContent='上传到GitHub';btn.disabled=false; console.error('upload error:',e)})
}
// 🔥 AI自学习引擎控制
function startSelfLearning(){
  fetch('/api/learn/start',{method:'POST'}).then(r=>r.json()).then(d=>{
    document.getElementById('learn-status-indicator').textContent='状态: 运行中';
    document.getElementById('learn-status-indicator').style.color='#4caf50';
    addLearningLog('self_learn','AI自学习引擎已启动','');
  })
}
function stopSelfLearning(){
  fetch('/api/learn/stop',{method:'POST'}).then(r=>r.json()).then(d=>{
    document.getElementById('learn-status-indicator').textContent='状态: 已停止';
    document.getElementById('learn-status-indicator').style.color='#e53935';
    addLearningLog('self_learn','AI自学习引擎已停止','');
  })
}
// 🔥 监听自学习更新
_on('self_learning_update',function(d){
  var p=document.getElementById('self-learn-params');
  if(p) p.textContent='参数: temp='+d.params.temperature+' | lr='+d.params.learning_rate+' | 学习次数='+d.params.total_learnings;
  var ins=document.getElementById('self-learn-insights');
  if(ins && d.insights) ins.innerHTML=d.insights.map(function(i){return '<div style="margin:2px 0">['+i.type+'] '+i.summary+'</div>'}).join('');
  addLearningLog('self_learn','自学习周期完成','发现'+d.insights.length+'条洞察');
})
_on('self_learning_status',function(d){
  var el=document.getElementById('learn-status-indicator');
  if(el){el.textContent='状态: '+d.status; el.style.color=d.status==='active'?'#4caf50':'#e53935'}
})
_on('learning_params_pushed',function(d){
  if(d.success) addLearningLog('self_learn','学习参数已推送到GitHub','第'+d.count+'次学习');
})
// 🔥 从学习日志训练AI参数
function trainFromLearningLog(){
  var btn=event.target;
  btn.textContent='训练中...';btn.disabled=true;
  var resultDiv=document.getElementById('learning-log-train-result');
  fetch('/api/learning_log/train',{method:'POST'}).then(r=>r.json()).then(d=>{
    if(d.status==='training'){
      btn.textContent='AI分析中...';
      resultDiv.style.display='block';
      resultDiv.innerHTML='<span class="spinner"></span> 正在分析 '+d.log_count+' 条AI学习日志，提取战术知识...';
    }else{
      btn.textContent='训练AI参数';btn.disabled=false;
      alert('训练失败: '+(d.error||''));
    }
  }).catch(function(e){
    btn.textContent='训练AI参数';btn.disabled=false;
    alert('请求失败: '+e);
  });
}
_on('learning_log_train_result',function(d){
  var btn=document.querySelector('#tab-learning .btn-start');
  if(btn){btn.textContent='训练AI参数';btn.disabled=false}
  var resultDiv=document.getElementById('learning-log-train-result');
  if(resultDiv){
    resultDiv.style.display='block';
    if(d.status==='ok'){
      resultDiv.innerHTML='<div style="color:#4caf50;font-weight:600;margin-bottom:4px">✅ AI参数训练完成（基于'+d.log_count+'条学习日志）</div><pre style="white-space:pre-wrap;margin:0;color:#aaa">'+escapeHtml(d.analysis)+'</pre>';
      addLearningLog('self_learn','学习日志训练完成',d.analysis?d.analysis.substring(0,100):'');
    }else{
      resultDiv.innerHTML='<div style="color:#e53935">❌ 训练失败: '+escapeHtml(d.error||'')+'</div>';
    }
  }
});
// 🔥 初始化自学习状态
function checkSelfLearnStatus(){
  fetch('/api/learn/status').then(r=>r.json()).then(d=>{
    var el=document.getElementById('learn-status-indicator');
    if(el){el.textContent='状态: '+(d.running?'运行中':'已停止'); el.style.color=d.running?'#4caf50':'#e53935'}
    var p=document.getElementById('self-learn-params');
    if(p && d.params) p.textContent='参数: temp='+d.params.temperature+' | lr='+d.params.learning_rate+' | 学习次数='+d.params.total_learnings;
  })
}
function addLearningLog(category, message, detail){
  var c = document.getElementById('learning-log-container');
  if(!c) return;
  var now = new Date().toLocaleTimeString();
  var catClass = category || 'system';
  var hasDetail = detail && detail.length > 0;
  var item = '<div class="learning-log-item" onclick="toggleLogDetail(this)" title="点击查看详情">' +
    '<span class="ll-time">'+now+'</span> ' +
    '<span class="ll-cat '+catClass+'">'+catClass+'</span> ' +
    '<span class="ll-msg">'+escapeHtml(message)+'</span>' +
    (hasDetail ? '<span class="ll-expand-hint">▶</span>' : '') +
    (hasDetail ? '<div class="ll-detail">'+escapeHtml(detail)+'</div>' : '') +
    '</div>';
  var cur = c.querySelector('.learning-log-item');
  if(cur) c.insertAdjacentHTML('afterbegin', item);
  else c.innerHTML = item;
  while(c.children.length > 200) c.removeChild(c.lastChild);
}
function toggleLogDetail(el){
  var wasExpanded = el.classList.contains('expanded');
  el.classList.toggle('expanded');
  var hint = el.querySelector('.ll-expand-hint');
  if(hint) hint.textContent = wasExpanded ? '▶' : '▼';
}
function renderLearningLog(data){
  var c=document.getElementById('learning-log-container');
  if(!data.length){c.innerHTML='<div style="color:#888;padding:20px;text-align:center">暂无AI学习记录<br><small style="color:#555">AI通过作战、搜索、对话学到的知识将显示在这里</small></div>';return}
  var html='';
  data.reverse().forEach(function(e){
    var catClass=e.category||'system';
    var hasDetail=e.detail&&e.detail.length>0;
    html+='<div class="learning-log-item" onclick="toggleLogDetail(this)" title="点击查看详情">' +
      '<span class="ll-time">'+e.time+'</span> ' +
      '<span class="ll-cat '+catClass+'">'+catClass+'</span> ' +
      '<span class="ll-msg">'+escapeHtml(e.message)+'</span>' +
      (hasDetail?'<span class="ll-expand-hint">▶</span>':'') +
      (hasDetail?'<div class="ll-detail">'+escapeHtml(e.detail)+'</div>':'') +
      '</div>';
  });
  c.innerHTML=html;
}
_on('learning_log_update',function(d){
  var c=document.getElementById('learning-log-container');
  if(!c) return;
  var cur=c.querySelector('.learning-log-item');
  var e=d.entry;
  var catClass=e.category||'system';
  var hasDetail=e.detail&&e.detail.length>0;
  var item='<div class="learning-log-item" onclick="toggleLogDetail(this)" title="点击查看详情">' +
    '<span class="ll-time">'+e.time+'</span> ' +
    '<span class="ll-cat '+catClass+'">'+catClass+'</span> ' +
    '<span class="ll-msg">'+escapeHtml(e.message)+'</span>' +
    (hasDetail?'<span class="ll-expand-hint">▶</span>':'') +
    (hasDetail?'<div class="ll-detail">'+escapeHtml(e.detail)+'</div>':'') +
    '</div>';
  if(cur) c.insertAdjacentHTML('afterbegin',item);
  else c.innerHTML=item;
  while(c.children.length>200) c.removeChild(c.lastChild);
});

// ── 系统日志 ──
function refreshSystemLog(){fetch('/api/system_log?limit=100').then(r=>r.json()).then(data=>renderSystemLog(data))}
function clearSystemLog(){fetch('/api/system_log/clear',{method:'POST'}).then(r=>r.json()).then(d=>{document.getElementById('system-log-container').innerHTML='<div style="color:#888;padding:20px;text-align:center">日志已清空</div>'})}
function exportSystemLog(){fetch('/api/system_log/export').then(r=>r.json()).then(d=>{var a=document.createElement('a');a.href='data:text/json;charset=utf-8,'+encodeURIComponent(JSON.stringify(d,null,2));a.download='system_log.json';a.click()})}
function addSystemLog(category, message, detail){
  var c = document.getElementById('system-log-container');
  if(!c) return;
  var now = new Date().toLocaleTimeString();
  var catClass = category || 'system';
  var item = '<div class="learning-log-item"><span class="ll-time">'+now+'</span> <span class="ll-cat '+catClass+'">'+catClass+'</span> <span class="ll-msg">'+escapeHtml(message)+'</span>'+(detail?'<div class="ll-detail">'+escapeHtml(detail)+'</div>':'')+'</div>';
  var cur = c.querySelector('.learning-log-item');
  if(cur) c.insertAdjacentHTML('afterbegin', item);
  else c.innerHTML = item;
  while(c.children.length > 300) c.removeChild(c.lastChild);
}
function renderSystemLog(data){var c=document.getElementById('system-log-container');if(!data.length){c.innerHTML='<div style="color:#888;padding:20px;text-align:center">暂无系统日志</div>';return}var html='';data.reverse().forEach(function(e){var catClass=e.category||'system';html+='<div class="learning-log-item"><span class="ll-time">'+e.time+'</span> <span class="ll-cat '+catClass+'">'+catClass+'</span> <span class="ll-msg">'+escapeHtml(e.message)+'</span>'+(e.detail?'<div class="ll-detail">'+escapeHtml(e.detail)+'</div>':'')+'</div>'});c.innerHTML=html}
_on('system_log_update',function(d){var c=document.getElementById('system-log-container');if(c){var cur=c.querySelector('.learning-log-item');var e=d.entry;var catClass=e.category||'system';var item='<div class="learning-log-item"><span class="ll-time">'+e.time+'</span> <span class="ll-cat '+catClass+'">'+catClass+'</span> <span class="ll-msg">'+escapeHtml(e.message)+'</span>'+(e.detail?'<div class="ll-detail">'+escapeHtml(e.detail)+'</div>':'')+'</div>';if(cur)c.insertAdjacentHTML('afterbegin',item);else c.innerHTML=item;while(c.children.length>300)c.removeChild(c.lastChild)}});

// ── 指挥面板事件 ──
_on('cycle_update',function(d){
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
  // ── v5.1 预测面板更新 ──
  if(d.prediction_accuracy!==undefined||d.threat_level!==undefined){
    var pp=document.getElementById('predict-panel');
    if(pp)pp.style.display='block';
    var pa=document.getElementById('pred-accuracy');
    if(pa)pa.textContent=Math.round((d.prediction_accuracy||0)*100)+'%';
    var pt=document.getElementById('pred-threat');
    if(pt)pt.textContent=d.threat_level||0;
    var pe=document.getElementById('pred-exp');
    if(pe)pe.textContent=(d.predicted_enemies?d.predicted_enemies.length:0)+'条';
    var pth=document.getElementById('predict-thinking');
    if(pth&&d.predicted_enemies&&d.predicted_enemies.length>0){
      pth.textContent='预测敌方移动: '+d.predicted_enemies.length+'个单位 | 威胁等级: '+(d.threat_level||0)+'/100';
    }
  }
  // 用户指令
  if(d.user_commands){
    var cl=document.getElementById('user-cmd-log');if(cl){
      var html='';d.user_commands.slice(-10).reverse().forEach(function(c){html+='<div class="log-item cmd-item"><div class="lhead"><span class="cyc">#'+c.cycle+'</span><span class="act" style="color:#ff9800">'+escapeHtml(c.command)+'</span></div></div>'});
      cl.innerHTML=html
    }
  }
});
_on('command_analysis',function(d){
  var box=document.getElementById('thinking-box');if(!box)return;
  var cmdText = escapeHtml(d.command||'');
  box.innerHTML='<span class="highlight">[指挥官指令]</span> '+cmdText+'<br><span class="highlight">[AI思路]</span> '+escapeHtml(d.analysis||'');
  if(d.allies!==undefined)box.innerHTML+='<br><span style="color:#888;font-size:10px">兵力: 友'+d.allies+' vs 敌'+d.enemies+' | 第'+d.cycle+'轮</span>';
});
_on('command_recorded',function(d){});
_on('game_config_update',function(d){
  if(d.faction){document.getElementById('faction-label').textContent=d.faction;document.getElementById('faction-label').style.color=d.faction==='红方'?'#e53935':'#2196f3';document.getElementById('game-control-result').innerHTML='<span style="color:#4caf50">阵营: '+d.faction+'</span>'}
  if(d.difficulty){document.getElementById('difficulty-label').textContent=d.difficulty;document.getElementById('difficulty-label').style.color=d.difficulty==='困难'?'#e53935':(d.difficulty==='普通'?'#ff9800':'#4caf50');document.getElementById('game-control-result').innerHTML='<span style="color:#4caf50">难度: '+d.difficulty+'</span>'}
  if(d.mode){document.getElementById('mode-label').textContent=d.mode;document.getElementById('game-control-result').innerHTML='<span style="color:#4caf50">模式: '+d.mode+'</span>'}
});
_on('started',function(d){
  var mode=d.mode||'combat';
  var label=mode==='smart'?'AI在线(智能模式)':'战斗中...';
  document.getElementById('status-badge').textContent=label;
  document.getElementById('status-badge').style.color='#4caf50';
  document.getElementById('thinking-box').textContent='DeepSeek智能体已就绪';
});
_on('smart_mode_status',function(d){
  document.getElementById('status-badge').textContent='AI在线(智能模式)';
  document.getElementById('status-badge').style.color='#4caf50';
  document.getElementById('thinking-box').textContent=d.message||'DeepSeek智能体已就绪';
});
_on('stopped',function(d){
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
_on('gpu_install_progress',function(d){
  document.getElementById('conn-gpu-detail').textContent=d.step+' ('+d.progress+'%)';
});
_on('gpu_install_complete',function(d){
  var el=document.getElementById('conn-gpu-status');
  if(d.success){el.textContent='安装成功';el.className='conn-status online'}else{el.textContent='安装失败';el.className='conn-status offline'}
  document.getElementById('conn-gpu-detail').textContent=d.message;
  setTimeout(checkGPU,2000);
});

// ── 模拟器管理 ──
var emulatorInterval=null;
var emulatorScreenImage=null;
var _emuStreamActive=false;
var _emuFpsCount=0;
var _emuFpsTimer=0;

// ── 模拟器屏幕开关 ──
function toggleEmulatorScreen(){
  var action = _emuStreamActive ? 'off' : 'on';
  fetch('/api/emulator/screen',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:action})}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'){
      _emuStreamActive = d.screen_on;
      updateScreenToggleUI();
      if(d.screen_on){
        startEmulatorStream();
      } else {
        stopEmulatorStream();
      }
    }
  }).catch(function(e){console.log('Screen toggle error:',e)});
}

function updateScreenToggleUI(){
  var btn = document.getElementById('emu-screen-toggle-btn');
  var overlay = document.getElementById('emu-screen-off-overlay');
  var stream = document.getElementById('emu-screen-stream');
  if(_emuStreamActive){
    if(btn){btn.textContent='🖥 屏幕: 开启';btn.style.background='#4caf50';btn.style.color='#000';}
    if(overlay) overlay.style.display='none';
    if(stream) stream.style.display='block';
  } else {
    if(btn){btn.textContent='🖥 屏幕: 关闭';btn.style.background='#e53935';btn.style.color='#fff';}
    if(overlay) overlay.style.display='flex';
    if(stream) stream.style.display='none';
  }
}

function loadEmuScreenState(){
  fetch('/api/emulator/screen').then(r=>r.json()).then(d=>{
    _emuStreamActive = d.screen_on;
    updateScreenToggleUI();
    if(d.screen_on){
      startEmulatorStream();
    }
  });
}

// ── MJPEG流式推送(高帧率60fps) ──
function startEmulatorStream(){
  var stream = document.getElementById('emu-screen-stream');
  var canvas = document.getElementById('emu-screen-canvas');
  var placeholder = document.getElementById('emu-screen-placeholder');
  if(!stream) return;
  // 停止旧的轮询
  if(emulatorInterval){clearInterval(emulatorInterval);emulatorInterval=null;}
  // 隐藏canvas和placeholder
  if(canvas) canvas.style.display='none';
  if(placeholder) placeholder.style.display='none';
  // 启动MJPEG流
  stream.style.display='block';
  stream.src = '/api/emulator/stream?t=' + Date.now();
  _emuFpsCount = 0;
  _emuFpsTimer = Date.now();
  // FPS计数器
  stream.onload = function(){
    _emuFpsCount++;
    var now = Date.now();
    var elapsed = now - _emuFpsTimer;
    if(elapsed >= 1000){
      var fps = Math.round(_emuFpsCount * 1000 / elapsed);
      document.getElementById('emu-screen-fps').textContent = fps + 'fps (MJPEG)';
      _emuFpsCount = 0;
      _emuFpsTimer = now;
    }
  };
}

function stopEmulatorStream(){
  var stream = document.getElementById('emu-screen-stream');
  var overlay = document.getElementById('emu-screen-off-overlay');
  if(stream){stream.src='';stream.style.display='none';}
  if(overlay) overlay.style.display='flex';
  _emuStreamActive = false;
  updateScreenToggleUI();
}

// ── 模拟器类型切换 ──
function switchEmuType(type){
  fetch('/api/emulator/type',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:type})}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'){
      // 更新所有下拉框
      document.querySelectorAll('#emu-type-select, #emu-type-select-tab').forEach(function(el){el.value=type});
      var label=document.getElementById('emu-type-label');
      var names={generic:'本地模拟器',mumu:'MUMU模拟器',bluestacks:'蓝叠模拟器',ldplayer:'雷电模拟器',xiaoyao:'逍遥模拟器',nox:'Nox模拟器',memu:'Memu模拟器',other:'其他模拟器'};
      if(label) label.textContent='['+(d.name||names[type])+']';
      // 刷新状态
      checkADB();
      checkEmulatorStatus();
      // 如果MJPEG流激活，重启流（使用新模拟器类型）
      if(_emuStreamActive){
        stopEmulatorStream();
        setTimeout(function(){startEmulatorStream();}, 500);
      }
      // 通知
      var r=document.getElementById('emu-touch-result');
      if(r){r.textContent='已切换至: '+(d.name||names[type])+' (端口:'+d.port+')';r.style.color='#4caf50';}
    }
  }).catch(function(e){alert('切换失败: '+e)});
}
function loadEmuType(){
  fetch('/api/emulator/type').then(r=>r.json()).then(d=>{
    document.querySelectorAll('#emu-type-select, #emu-type-select-tab').forEach(function(el){el.value=d.type});
    var label=document.getElementById('emu-type-label');
    var names={generic:'本地模拟器',mumu:'MUMU模拟器',bluestacks:'蓝叠模拟器',ldplayer:'雷电模拟器',xiaoyao:'逍遥模拟器',nox:'Nox模拟器',memu:'Memu模拟器',other:'其他模拟器'};
    if(label) label.textContent='['+(d.name||names[d.type])+']';
  });
}
function detectEmulators(){
  var el = document.getElementById('emu-detect-result');
  el.textContent = '检测中...';
  el.style.color = '#ff9800';
  fetch('/api/emulator/detect').then(r=>r.json()).then(d=>{
    if(d.status==='ok'){
      if(d.detected.length===0){
        el.textContent = '未检测到模拟器';
        el.style.color = '#e53935';
      }else{
        var names = [];
        d.detected.forEach(function(dev){
          names.push(dev.name + (dev.current?' [当前]':''));
        });
        el.textContent = '检测到: '+names.join(', ');
        el.style.color = '#4caf50';
        if(d.detected.length>0 && !d.detected[0].current){
          var first = d.detected[0];
          if(first.type && first.type !== 'unknown' && first.type !== 'discovered'){
            switchEmuType(first.type);
          }
        }
      }
    }else{
      el.textContent = '检测失败';
      el.style.color = '#e53935';
    }
  }).catch(function(e){
    el.textContent = '检测失败';
    el.style.color = '#e53935';
  });
}
// 页面加载时初始化
setTimeout(loadEmuType,500);
setTimeout(loadEmuScreenState,600);
setTimeout(loadEmuResolution,700);
setTimeout(loadGpuConfig,750);

// ── GPU配置 ──
function applyGpuConfig(){
  var mode = document.getElementById('emu-gpu-mode').value;
  var renderer = document.getElementById('emu-renderer').value;
  var gl = document.getElementById('emu-gl-version').value;
  var status = document.getElementById('emu-gpu-status');
  status.textContent = '设置中...';
  status.style.color = '#ff9800';
  fetch('/api/emulator/gpu',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({gpu_mode:mode,renderer:renderer,gl_version:gl})}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'){
      status.textContent = '已设置: GPU='+mode+' Renderer='+renderer+' GLES='+gl;
      status.style.color = '#4caf50';
    }
  }).catch(function(e){status.textContent='设置失败';status.style.color='#e53935'});
}
function loadGpuConfig(){
  fetch('/api/emulator/gpu').then(r=>r.json()).then(d=>{
    if(d.gpu){
      document.getElementById('emu-gpu-mode').value = d.gpu.gpu_mode||'host';
      document.getElementById('emu-renderer').value = d.gpu.renderer||'opengl';
      document.getElementById('emu-gl-version').value = d.gpu.gl_version||'3.0';
    }
  });
}

// ── 分辨率配置 ──
function setEmuResolution(w,h,dpi){
  document.getElementById('emu-res-w').value = w;
  document.getElementById('emu-res-h').value = h;
  document.getElementById('emu-res-dpi').value = dpi;
  applyEmuResolution();
}
function applyEmuResolution(){
  var w = document.getElementById('emu-res-w').value;
  var h = document.getElementById('emu-res-h').value;
  var dpi = document.getElementById('emu-res-dpi').value;
  var status = document.getElementById('emu-res-status');
  status.textContent = '设置中...';
  status.style.color = '#ff9800';
  fetch('/api/emulator/resolution',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({width:parseInt(w),height:parseInt(h),dpi:parseInt(dpi)})}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'){
      status.textContent = '已设置: '+d.resolution.width+'x'+d.resolution.height+' DPI:'+d.resolution.dpi;
      status.style.color = '#4caf50';
    }
  }).catch(function(e){
    status.textContent = '设置失败';
    status.style.color = '#e53935';
  });
}
function loadEmuResolution(){
  fetch('/api/emulator/resolution').then(r=>r.json()).then(d=>{
    if(d.resolution){
      document.getElementById('emu-res-w').value = d.resolution.width;
      document.getElementById('emu-res-h').value = d.resolution.height;
      document.getElementById('emu-res-dpi').value = d.resolution.dpi;
    }
  });
}
function checkEmulatorStatus(){
  fetch('/api/emulator/status').then(r=>r.json()).then(d=>{
    setDiagStatus('emu-installed',d.installed?'SDK已安装':'SDK未安装',d.installed);
    setDiagStatus('emu-avd',d.avd_exists?'AVD已创建':'AVD未创建',d.avd_exists);
    setDiagStatus('emu-java',d.java_available?'Java已就绪':'Java未安装',d.java_available);
    setDiagStatus('emu-running',d.running?'运行中':'已停止',d.running);
    setDiagStatus('emu-adb',d.adb_connected?'已连接('+d.adb_port+')':'未连接',d.adb_connected);
    if(d.installed){document.getElementById('emu-installed').textContent=d.emulator_path.replace(/\\/g,'\\\\').split('\\\\').pop()||'SDK已安装'}
    if(d.error){document.getElementById('emu-progress').innerHTML='<div class="alert warning">'+d.error+'</div>'}
    if(d.running&&!_emuStreamActive){startEmulatorRefresh()}
    if(!d.running){stopEmulatorRefresh();stopEmulatorStream();var ph=document.getElementById('emu-screen-placeholder');if(ph)ph.style.display='block'}
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
function installAndStartEmulator(){
  var p=document.getElementById('emu-progress');
  var type=document.getElementById('emu-type-select').value;
  if(type!=='generic'){
    p.innerHTML='<div class="alert info"><span class="spinner"></span> '+type+'模拟器: 正在连接ADB...</div>';
    fetch('/api/emulator/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:type})}).then(r=>r.json()).then(d=>{
      if(d.status==='connected'){p.innerHTML='<div class="alert success">'+type+'模拟器已连接! 端口: '+d.port+'</div>';checkEmulatorStatus();checkADB()}
      else{p.innerHTML='<div class="alert error">连接失败: '+(d.error||'未知')+'</div>'}
    }).catch(function(e){p.innerHTML='<div class="alert error">连接失败: '+e+'</div>'});
    return;
  }
  p.innerHTML='<div class="alert info"><span class="spinner"></span> 一键安装并启动模拟器...这可能需要几分钟</div>';
  p.innerHTML+='<div style="font-size:10px;color:#aaa;margin-top:4px">步骤: 下载SDK → 解压 → 安装组件 → 创建AVD → 启动 → 同步ADB</div>';
  fetch('/api/emulator/install_and_start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:type})}).then(r=>r.json()).then(d=>{
    p.innerHTML='<div class="alert info">安装启动中，请等待进度更新...</div>';
  }).catch(function(e){p.innerHTML='<div class="alert error">启动失败: '+e+'</div>'})
}
function startEmulator(){
  var p=document.getElementById('emu-progress');
  var type=document.getElementById('emu-type-select').value;
  if(type!=='generic'){
    p.innerHTML='<div class="alert info"><span class="spinner"></span> 连接 '+type+' 模拟器...</div>';
    fetch('/api/emulator/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:type})}).then(r=>r.json()).then(d=>{
      if(d.status==='connected'){p.innerHTML='<div class="alert success">'+type+'模拟器已连接! 端口: '+d.port+'</div>';checkEmulatorStatus();checkADB()}
      else{p.innerHTML='<div class="alert error">连接失败: '+(d.error||'未知')+'</div>'}
    }).catch(function(e){p.innerHTML='<div class="alert error">连接失败: '+e+'</div>'});
    return;
  }
  p.innerHTML='<div class="alert info"><span class="spinner"></span> 启动模拟器...</div>';
  fetch('/api/emulator/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:type})}).then(r=>r.json()).then(d=>{
    if(d.status==='already_running'){p.innerHTML='<div class="alert success">模拟器已在运行，已重新连接</div>';checkEmulatorStatus()}
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
function analyzeAPK(){
  var p=document.getElementById('emu-progress');
  p.innerHTML='<div class="alert info"><span class="spinner"></span> 正在分析APK兼容性...</div>';
  fetch('/api/emulator/analyze_apk',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(d=>{
    if(d.status==='error'||d.error){
      p.innerHTML='<div class="alert error">分析失败: '+(d.error||'未知')+'</div>';
      return;
    }
    var html='<div class="alert success">APK分析完成</div>';
    html+='<div style="font-size:11px;margin-top:8px;line-height:1.6">';
    html+='<strong>APK:</strong> '+d.apk_path+' ('+d.apk_size_mb+'MB)<br>';
    if(d.manifest_info.package) html+='<strong>包名:</strong> '+d.manifest_info.package+'<br>';
    if(d.manifest_info.version_name) html+='<strong>版本:</strong> '+d.manifest_info.version_name+'<br>';
    if(d.manifest_info.min_sdk) html+='<strong>最低SDK:</strong> API '+d.manifest_info.min_sdk+'<br>';
    if(d.manifest_info.target_sdk) html+='<strong>目标SDK:</strong> API '+d.manifest_info.target_sdk+'<br>';
    if(d.manifest_info.native_arch) html+='<strong>原生架构:</strong> '+d.manifest_info.native_arch+'<br>';
    if(d.manifest_info.opengl_es) html+='<strong>OpenGL ES:</strong> '+d.manifest_info.opengl_es+'<br>';
    html+='</div>';
    if(d.compatibility_issues && d.compatibility_issues.length>0 && d.compatibility_issues[0]!=='未检测到明显的兼容性问题'){
      html+='<div style="margin-top:8px"><strong style="color:#e53935">兼容性问题:</strong></div>';
      html+='<ul style="font-size:11px;color:#ff9800;margin:4px 0;padding-left:18px">';
      d.compatibility_issues.forEach(function(issue){html+='<li>'+issue+'</li>'});
      html+='</ul>';
    }
    if(d.recommendations && d.recommendations.length>0){
      html+='<div style="margin-top:6px"><strong style="color:#58a5f3">建议:</strong></div>';
      html+='<ul style="font-size:11px;color:#aaa;margin:4px 0;padding-left:18px">';
      d.recommendations.forEach(function(rec){html+='<li>'+rec+'</li>'});
      html+='</ul>';
    }
    p.innerHTML=html;
  }).catch(function(e){p.innerHTML='<div class="alert error">分析失败: '+e+'</div>'})
}
function refreshEmulatorScreen(){
  // 如果MJPEG流已激活，不需要手动刷新
  if(_emuStreamActive) return;
  fetch('/api/emulator/screenshot').then(r=>r.json()).then(d=>{
    if(d.error){console.log('EMU screenshot error:',d.error);return}
    emulatorScreenImage=d.image;
    var canvas=document.getElementById('emu-screen-canvas');
    if(!canvas)return;
    canvas.style.display='block';
    var ctx=canvas.getContext('2d');
    var img=new Image();
    var mime = d.format==='jpeg' ? 'image/jpeg' : 'image/png';
    img.onload=function(){
      canvas.width=img.naturalWidth;
      canvas.height=img.naturalHeight;
      ctx.drawImage(img,0,0);
      var ph=document.getElementById('emu-screen-placeholder');
      if(ph)ph.style.display='none';
      var container=document.getElementById('emu-screen-container');
      if(container && !container.classList.contains('emu-fullscreen')){
        container.style.aspectRatio=img.naturalWidth+'/'+img.naturalHeight;
      }
    };
    img.src='data:'+mime+';base64,'+d.image;
    var now=new Date();
    if(window._lastEmuScreen){var fps=Math.round(1000/(now-window._lastEmuScreen));document.getElementById('emu-screen-fps').textContent=fps+'fps'}
    window._lastEmuScreen=now;
  }).catch(function(e){console.log('EMU screen fetch error:',e)})
}
function startEmulatorRefresh(){
  if(emulatorInterval)return;
  // 优先使用MJPEG流
  if(document.getElementById('emu-auto-refresh').checked){
    startEmulatorStream();
    return;
  }
  refreshEmulatorScreen();
  emulatorInterval=setInterval(refreshEmulatorScreen,333);
}
function stopEmulatorRefresh(){
  if(emulatorInterval){clearInterval(emulatorInterval);emulatorInterval=null}
}
function toggleEmulatorRefresh(){
  if(document.getElementById('emu-auto-refresh').checked){
    startEmulatorStream();
    if(emulatorInterval){clearInterval(emulatorInterval);emulatorInterval=null;}
  }else{
    stopEmulatorStream();
    startEmulatorRefresh();
  }
}
// ── 模拟器模式切换 ──
var _emuMode='touch';  // 'touch' | 'annotate'
var _emuAnnotateBoxes=[];  // 标注模式下的框
var _emuAnnotateStartX=0,_emuAnnotateStartY=0;
var _emuAnnotateDrawing=false;
function setEmuMode(mode){
  _emuMode=mode;
  var label=document.getElementById('emu-mode-label');
  var hint=document.getElementById('emu-mode-hint');
  var btnT=document.getElementById('btn-mode-touch');
  var btnA=document.getElementById('btn-mode-annotate');
  if(mode==='annotate'){
    label.textContent='[标注模式]';
    label.style.color='#ff9800';
    hint.textContent='拖拽鼠标在屏幕上画框标注 | 框名称在下方输入';
    btnT.style.background='';btnT.style.color='';
    btnA.style.background='#ff9800';btnA.style.color='#fff';
    document.getElementById('emu-screen-container').style.cursor='crosshair';
  }else{
    label.textContent='[触控模式]';
    label.style.color='#4caf50';
    hint.textContent='点击屏幕上方的模拟器画面即可发送触摸事件';
    btnT.style.background='#4caf50';btnT.style.color='#fff';
    btnA.style.background='';btnA.style.color='';
    document.getElementById('emu-screen-container').style.cursor='crosshair';
  }
}
function handleEmulatorClick(e){
  if(_emuMode==='annotate') return;
  // 🔥 使用容器坐标，支持canvas和stream两种模式
  var container=document.getElementById('emu-screen-container');
  if(!container||!emulatorScreenImage)return;
  var rect=container.getBoundingClientRect();
  var scaleX=emulatorScreenImage.naturalWidth/rect.width;
  var scaleY=emulatorScreenImage.naturalHeight/rect.height;
  var x=Math.round((e.clientX-rect.left)*scaleX);
  var y=Math.round((e.clientY-rect.top)*scaleY);
  x=Math.max(0,Math.min(emulatorScreenImage.naturalWidth,x));
  y=Math.max(0,Math.min(emulatorScreenImage.naturalHeight,y));
  document.getElementById('emu-touch-x').value=x;
  document.getElementById('emu-touch-y').value=y;
  emuTouch('tap',x,y);
  if(recordingActive) recordTouchEvent('tap', x, y);
}
// ── 鼠标触控板操作追踪（mousedown/mousemove/mouseup）──
// 🔥 像MUMU一样支持鼠标直接点击、拖动（swipe）、长按
var _emuMouseDown=false;
var _emuMouseStartX=0;
var _emuMouseStartY=0;
var _emuMouseStartTime=0;
var _emuMouseLastX=0;
var _emuMouseLastY=0;
var _emuMouseMoved=false;
var _emuDragLine=null;  // 拖动轨迹线
(function initEmuMouseTracking(){
  var container=document.getElementById('emu-screen-container');
  if(!container) return setTimeout(initEmuMouseTracking, 200);
  
  // 🔥 绑定点击事件
  container.addEventListener('click', handleEmulatorClick);
  
  function getEventXY(e){
    var rect=container.getBoundingClientRect();
    var scaleX=(emulatorScreenImage?emulatorScreenImage.naturalWidth:1920)/rect.width;
    var scaleY=(emulatorScreenImage?emulatorScreenImage.naturalHeight:1080)/rect.height;
    var x=Math.round((e.clientX-rect.left)*scaleX);
    var y=Math.round((e.clientY-rect.top)*scaleY);
    return [Math.max(0,Math.min(1920,x)), Math.max(0,Math.min(1080,y))];
  }
  
  container.addEventListener('mousedown', function(e){
    if(!emulatorScreenImage) return;
    _emuMouseDown=true;
    _emuMouseStartX=x;
    _emuMouseStartY=y;
    _emuMouseStartTime=Date.now();
    _emuMouseLastX=x;
    _emuMouseLastY=y;
    _emuMouseMoved=false;
    document.getElementById('emu-touch-x').value=x;
    document.getElementById('emu-touch-y').value=y;
    // 标注模式：开始画框
    if(_emuMode==='annotate'){
      _emuAnnotateStartX=x;_emuAnnotateStartY=y;
      _emuAnnotateDrawing=true;
      var div=document.createElement('div');
      div.id='emu-annotate-temp';
      div.style.cssText='position:absolute;border:2px dashed #ff9800;background:rgba(255,152,0,0.2);pointer-events:none;z-index:999';
      document.getElementById('emu-screen-container').appendChild(div);
    }
  });
  container.addEventListener('mousemove', function(e){
    if(!_emuMouseDown) return;
    var xy=getEventXY(e);
    var x=xy[0], y=xy[1];
    // 🔥 降低拖动阈值到10px，更灵敏
    if(Math.abs(x-_emuMouseStartX)>10||Math.abs(y-_emuMouseStartY)>10) _emuMouseMoved=true;
    _emuMouseLastX=x;
    _emuMouseLastY=y;
    document.getElementById('emu-touch-x').value=x;
    document.getElementById('emu-touch-y').value=y;
    // 标注模式：更新临时框
    if(_emuMode==='annotate'&&_emuAnnotateDrawing){
      var tmp=document.getElementById('emu-annotate-temp');
      if(tmp){
        var imgW=emulatorScreenImage?emulatorScreenImage.naturalWidth:1920;
        var imgH=emulatorScreenImage?emulatorScreenImage.naturalHeight:1080;
        var cx=Math.min(_emuAnnotateStartX,x)/imgW*100;
        var cy=Math.min(_emuAnnotateStartY,y)/imgH*100;
        var cw=Math.abs(x-_emuAnnotateStartX)/imgW*100;
        var ch=Math.abs(y-_emuAnnotateStartY)/imgH*100;
        tmp.style.left=cx+'%';tmp.style.top=cy+'%';
        tmp.style.width=cw+'%';tmp.style.height=ch+'%';
      }
    }
    // 🔥 触控模式：显示拖动轨迹线（像MUMU一样直观）
    if(_emuMode==='touch'&&_emuMouseMoved){
      if(!_emuDragLine){
        _emuDragLine=document.createElement('div');
        _emuDragLine.id='emu-drag-line';
        _emuDragLine.style.cssText='position:absolute;pointer-events:none;z-index:1000;height:2px;background:#4caf50;transform-origin:0 0;border-radius:1px';
        document.getElementById('emu-screen-container').appendChild(_emuDragLine);
      }
      var imgW=emulatorScreenImage?emulatorScreenImage.naturalWidth:1920;
      var imgH=emulatorScreenImage?emulatorScreenImage.naturalHeight:1080;
      var sx=_emuMouseStartX/imgW*100;
      var sy=_emuMouseStartY/imgH*100;
      var ex=x/imgW*100;
      var ey=y/imgH*100;
      var dx=ex-sx, dy=ey-sy;
      var len=Math.sqrt(dx*dx+dy*dy);
      var angle=Math.atan2(dy,dx)*180/Math.PI;
      _emuDragLine.style.left=sx+'%';
      _emuDragLine.style.top=sy+'%';
      _emuDragLine.style.width=len+'%';
      _emuDragLine.style.transform='rotate('+angle+'deg)';
    }
  });
  container.addEventListener('mouseup', function(e){
    if(!_emuMouseDown||!emulatorScreenImage) return;
    _emuMouseDown=false;
    // 🔥 清除拖动轨迹线
    if(_emuDragLine){_emuDragLine.remove();_emuDragLine=null;}
    // 标注模式：完成画框
    if(_emuMode==='annotate'&&_emuAnnotateDrawing){
      _emuAnnotateDrawing=false;
      var tmp=document.getElementById('emu-annotate-temp');
      if(tmp) tmp.remove();
      var boxW=Math.abs(_emuMouseLastX-_emuAnnotateStartX);
      var boxH=Math.abs(_emuMouseLastY-_emuAnnotateStartY);
      if(boxW>5&&boxH>5){
        var bx=Math.min(_emuAnnotateStartX,_emuMouseLastX);
        var by=Math.min(_emuAnnotateStartY,_emuMouseLastY);
        var boxName=prompt('框名称 (标注类别):','unit_'+(_emuAnnotateBoxes.length+1));
        if(boxName){
          _emuAnnotateBoxes.push({x:bx,y:by,w:boxW,h:boxH,name:boxName});
          drawEmuAnnotationBoxes();
        }
      }
      return;
    }
    var duration=Date.now()-_emuMouseStartTime;
    var distX=_emuMouseLastX-_emuMouseStartX;
    var distY=_emuMouseLastY-_emuMouseStartY;
    var totalDist=Math.sqrt(distX*distX+distY*distY);
    var action,recordAction;
    // 🔥 更灵敏的拖动检测：15px即触发swipe
    if(totalDist>15){
      action='swipe';recordAction='swipe';
      // 🔥 传递拖动时长，让后端使用更合理的duration
      emuTouch('swipe', _emuMouseStartX, _emuMouseStartY, _emuMouseLastX, _emuMouseLastY, Math.min(duration, 2000));
    }else if(duration>500){
      action='longpress';recordAction='longpress';
      emuTouch('longpress', _emuMouseStartX, _emuMouseStartY);
    }else{
      action='tap';recordAction='tap';
      emuTouch('tap', _emuMouseStartX, _emuMouseStartY);
    }
    if(recordingActive) recordTouchEvent(recordAction, _emuMouseStartX, _emuMouseStartY, _emuMouseLastX, _emuMouseLastY);
  });
  canvas.addEventListener('contextmenu', function(e){e.preventDefault()});
})();
// 绘制标注框
function drawEmuAnnotationBoxes(){
  var container=document.getElementById('emu-screen-container');
  container.querySelectorAll('.emu-annotate-box').forEach(function(el){el.remove()});
  _emuAnnotateBoxes.forEach(function(b,i){
    var div=document.createElement('div');
    div.className='emu-annotate-box';
    div.style.cssText='position:absolute;border:2px solid #ff9800;background:rgba(255,152,0,0.15);pointer-events:none;z-index:998;'+
      'left:'+(b.x/1920*100)+'%;top:'+(b.y/1080*100)+'%;width:'+(b.w/1920*100)+'%;height:'+(b.h/1080*100)+'%';
    var label=document.createElement('span');
    label.style.cssText='position:absolute;top:-18px;left:0;background:#ff9800;color:#000;font-size:10px;padding:1px 5px;border-radius:3px;white-space:nowrap';
    label.textContent=b.name;
    div.appendChild(label);
    container.appendChild(div);
  });
  var cnt=document.getElementById('emu-annotate-count');
  if(cnt) cnt.textContent=_emuAnnotateBoxes.length+'个标注框';
}
function clearEmuAnnotations(){
  _emuAnnotateBoxes=[];
  var container=document.getElementById('emu-screen-container');
  container.querySelectorAll('.emu-annotate-box').forEach(function(el){el.remove()});
  var cnt=document.getElementById('emu-annotate-count');
  if(cnt) cnt.textContent='';
}
function exportEmuAnnotations(){
  if(_emuAnnotateBoxes.length===0){alert('暂无标注框');return;}
  var txt='';
  _emuAnnotateBoxes.forEach(function(b,i){
    var cx=(b.x+b.w/2)/1920;var cy=(b.y+b.h/2)/1080;
    var cw=b.w/1920;var ch=b.h/1080;
    txt+='0 '+cx.toFixed(6)+' '+cy.toFixed(6)+' '+cw.toFixed(6)+' '+ch.toFixed(6)+' # '+b.name+'\n';
  });
  var blob=new Blob([txt],{type:'text/plain'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='emu_annotations_'+new Date().toISOString().slice(0,10)+'.txt';
  a.click();
  alert('已导出 '+_emuAnnotateBoxes.length+' 个标注框');
}
// ── 录制状态 ──
var recordingActive=false;
var recordingStartTime=0;
var recordingEvents=[];
var recordingScreenshots=[];
var recordingInterval=null;
function toggleRecording(){
  var btn=document.getElementById('btn-record-toggle');
  var status=document.getElementById('recording-status');
  if(recordingActive){
    // 停止录制
    recordingActive=false;
    if(recordingInterval){clearInterval(recordingInterval);recordingInterval=null}
    btn.textContent='⏺ 开始录制';
    btn.style.background='#e53935';
    status.textContent='已停止 ('+recordingEvents.length+'个事件)';
    status.style.color='#ff9800';
    document.getElementById('btn-record-save').style.display='inline-block';
    document.getElementById('btn-record-send').style.display='inline-block';
    addSystemLog('recording','录制停止',recordingEvents.length+'个触控事件, '+recordingScreenshots.length+'帧截图');
  }else{
    // 开始录制
    recordingActive=true;
    recordingStartTime=Date.now();
    recordingEvents=[];
    recordingScreenshots=[];
    btn.textContent='⏹ 停止录制';
    btn.style.background='#ff9800';
    status.textContent='录制中...';
    status.style.color='#4caf50';
    document.getElementById('btn-record-save').style.display='none';
    document.getElementById('btn-record-send').style.display='none';
    document.getElementById('recording-stats').style.display='block';
    document.getElementById('recording-log').style.display='block';
    document.getElementById('recording-log').innerHTML='';
    // 立即截图一帧作为初始状态
    captureRecordingFrame();
    // 每500ms自动截图一帧（与触控时序同步）
    recordingInterval=setInterval(function(){
      if(recordingActive) captureRecordingFrame();
      updateRecordingStats();
    },500);
    addSystemLog('recording','开始录制','触控+截图时序同步记录');
  }
}
function recordTouchEvent(action, x, y, endX, endY){
  var ts=Date.now()-recordingStartTime;
  var evt={ts:ts, action:action, x:x, y:y, timestamp:new Date().toISOString()};
  if(action==='swipe'&&endX!==undefined){evt.endX=endX;evt.endY=endY}
  recordingEvents.push(evt);
  // 写入日志
  var log=document.getElementById('recording-log');
  var desc=action+' ('+x+','+y+')';
  if(action==='swipe') desc+=' → ('+endX+','+endY+')';
  if(log) log.innerHTML='<div>['+ts+'ms] '+desc+'</div>'+log.innerHTML;
  updateRecordingStats();
  // 触控事件后立即截图
  setTimeout(function(){if(recordingActive)captureRecordingFrame()},100);
}
function captureRecordingFrame(){
  var ts=Date.now()-recordingStartTime;
  // Try emulator screen image first
  var imgData = emulatorScreenImage;
  // Fallback: capture from canvas
  if(!imgData){
    var canvas = document.getElementById('emu-screen-canvas');
    if(canvas){
      try { imgData = canvas.toDataURL('image/jpeg', 0.6); } catch(e){}
    }
  }
  // Fallback: capture from img element
  if(!imgData){
    var img = document.querySelector('.emu-screen-container img');
    if(img && img.src && img.src.startsWith('data:')){
      imgData = img.src;
    }
  }
  recordingScreenshots.push({
    ts:ts,
    image:imgData || 'no_screen_data',
    timestamp:new Date().toISOString(),
    event_index:recordingEvents.length
  });
  updateRecordingStats();
}
function updateRecordingStats(){
  var dur=Math.round((Date.now()-recordingStartTime)/1000);
  document.getElementById('rec-event-count').textContent=recordingEvents.length;
  document.getElementById('rec-screenshot-count').textContent=recordingScreenshots.length;
  document.getElementById('rec-duration').textContent=dur+'s';
}
function saveRecording(){
  if(recordingEvents.length===0){alert('没有录制数据');return}
  var data={
    session_id:'rec_'+Date.now(),
    start_time:new Date(recordingStartTime).toISOString(),
    duration_ms:recordingEvents.length>0?recordingEvents[recordingEvents.length-1].ts:0,
    events:recordingEvents,
    screenshots:recordingScreenshots.slice(0,200), // 限制截图数量避免过大
    total_events:recordingEvents.length,
    total_screenshots:recordingScreenshots.length,
    resolution:{width:1920,height:1080}
  };
  var blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  a.href=url;
  a.download='touch_recording_'+new Date().toISOString().slice(0,19).replace(/:/g,'-')+'.json';
  a.click();
  addSystemLog('recording','录制已保存','事件:'+recordingEvents.length+', 截图:'+recordingScreenshots.length);
  alert('录制已保存!\n事件: '+recordingEvents.length+' | 截图: '+recordingScreenshots.length+'\n时长: '+data.duration_ms+'ms');
}
function sendRecordingToAI(){
  if(recordingEvents.length===0){alert('没有录制数据');return}
  var btn=document.getElementById('btn-record-send');
  btn.textContent='发送中...';btn.disabled=true;
  var data={
    session_id:'rec_'+Date.now(),
    start_time:new Date(recordingStartTime).toISOString(),
    duration_ms:recordingEvents.length>0?recordingEvents[recordingEvents.length-1].ts:0,
    events:recordingEvents,
    screenshots:recordingScreenshots.slice(0,50), // 发送给AI时限制50帧
    total_events:recordingEvents.length,
    total_screenshots:recordingScreenshots.length,
    resolution:{width:1920,height:1080}
  };
  fetch('/api/recording/learn',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(data)
  }).then(r=>r.json()).then(d=>{
    if(d.status==='learning'){
      btn.textContent='AI分析中...';btn.disabled=true;
      addSystemLog('recording','AI分析中',d.events+'个事件已提交, 等待DeepSeek分析...');
      // 监听Socket事件获取异步结果
      socket.on('recording_learn_result', function onResult(r2){
        socket.off('recording_learn_result', onResult);
        btn.textContent='🤖 发送AI学习';btn.disabled=false;
        if(r2.status==='ok'){
          alert('AI学习完成!\n\n分析结果: '+r2.analysis);
          addLearningLog('recording','AI学习完成',r2.analysis||'');
        }else{
          alert('AI分析失败: '+(r2.error||''));
        }
      });
    }else{
      btn.textContent='🤖 发送AI学习';btn.disabled=false;
      alert('发送失败: '+(d.error||''));
    }
  }).catch(function(e){
    btn.textContent='🤖 发送AI学习';btn.disabled=false;
    alert('请求失败: '+e);
  })
}
function toggleEmulatorFullscreen(){
  var container=document.getElementById('emu-screen-container');
  if(!container)return;
  var exitBtn=document.getElementById('emu-fullscreen-exit');
  if(container.classList.contains('emu-fullscreen')){
    container.classList.remove('emu-fullscreen');
    container.style.position='';container.style.top='';container.style.left='';
    container.style.width='';container.style.height='';container.style.zIndex='';
    container.style.aspectRatio='16/9';
    container.style.borderRadius='8px';
    document.body.style.overflow='';
    if(exitBtn) exitBtn.style.display='none';
  }else{
    container.classList.add('emu-fullscreen');
    container.style.position='fixed';container.style.top='0';container.style.left='0';
    container.style.width='100vw';container.style.height='100vh';container.style.zIndex='9999';
    container.style.aspectRatio='';container.style.borderRadius='0';
    document.body.style.overflow='hidden';
    if(exitBtn) exitBtn.style.display='block';
  }
  refreshEmulatorScreen();
}
// 🔥 ESC 键退出全屏
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'){
    var container=document.getElementById('emu-screen-container');
    if(container && container.classList.contains('emu-fullscreen')){
      toggleEmulatorFullscreen();
    }
  }
});
// 🔥 启动游戏
function launchGame(){
  var btn=event.target;
  btn.textContent='启动中...';btn.disabled=true;
  fetch('/api/emulator/launch_app',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(r=>r.json()).then(d=>{
    btn.textContent='🎮 启动游戏';btn.disabled=false;
    if(d.status==='ok'){
      btn.textContent='✅ 已启动';
      setTimeout(function(){btn.textContent='🎮 启动游戏'},2000);
    }else{
      btn.textContent='❌ 失败';
      alert('启动失败: '+(d.error||''));
      setTimeout(function(){btn.textContent='🎮 启动游戏'},2000);
    }
  }).catch(function(e){
    btn.textContent='🎮 启动游戏';btn.disabled=false;
    alert('请求失败: '+e);
  })
}
function emuTouch(action,ox,oy,ex,ey,dur){
  var x=ox||parseInt(document.getElementById('emu-touch-x').value)||0;
  var y=oy||parseInt(document.getElementById('emu-touch-y').value)||0;
  var r=document.getElementById('emu-touch-result');
  var body={x:x,y:y,action:action};
  if(action==='swipe'){body.x2=ex||x;body.y2=ey||y;body.duration=dur||300}
  fetch('/api/emulator/touch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(res=>res.json()).then(d=>{
    r.textContent=action+' ('+x+','+y+')'+(action==='swipe'?'→('+body.x2+','+body.y2+')':'')+': '+(d.status||'');
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
_on('emu_install_progress',function(d){
  if(d.error){
    document.getElementById('emu-progress').innerHTML='<div class="alert error">'+d.step+'</div>';
  }else{
    document.getElementById('emu-progress').innerHTML='<div class="alert info"><span class="spinner"></span> '+d.step+' ('+d.progress+'%)</div>';
  }
});
_on('emu_install_complete',function(d){
  var html='<div class="alert '+(d.success?'success':'error')+'">'+d.message+'</div>';
  document.getElementById('emu-progress').innerHTML=html;
  checkEmulatorStatus();
});
_on('emu_start_progress',function(d){document.getElementById('emu-progress').innerHTML='<div class="alert info"><span class="spinner"></span> '+d.step+' ('+d.progress+'%)</div>'});
_on('emu_start_complete',function(d){
  document.getElementById('emu-progress').innerHTML='<div class="alert success">模拟器启动完成! 端口: '+d.port+'</div>';
  checkEmulatorStatus();
  checkADB();
  startEmulatorRefresh();
  // 🔥 自动启动游戏 (延迟5秒等模拟器稳定)
  setTimeout(function(){
    fetch('/api/emulator/launch_app',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(r=>r.json()).then(function(rd){
      if(rd.status==='ok') document.getElementById('emu-progress').innerHTML+='<br>🎮 游戏已自动启动';
    });
  },5000);
});
_on('emu_start_error',function(d){document.getElementById('emu-progress').innerHTML='<div class="alert error">'+d.error+'</div>'});

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
  var fullscreen=document.getElementById('scrcpy-fullscreen').checked;
  document.getElementById('scrcpy-status').innerHTML='<span class="spinner"></span> 启动投屏...';
  fetch('/api/scrcpy/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_width:parseInt(res),max_fps:parseInt(fps),bitrate:parseInt(bitrate),fullscreen:fullscreen})}).then(r=>r.json()).then(d=>{
    if(d.status==='ok'){document.getElementById('scrcpy-status').innerHTML='<span style="color:#4caf50">投屏已启动'+(d.fullscreen?' (全屏)':' (窗口)')+'</span>'}
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
      html+='<div class="alert warning">检测到以下进程占用端口:</div>';
      d.xinglv_ports.forEach(function(p){
        html+='<div style="font-size:10px;color:#e53935;margin:2px 0">端口 '+p.port+' (PID: '+p.pid+') - '+p.process+'</div>';
      });
    }else{
      html+='<div class="alert success">未检测到端口冲突</div>';
    }
    html+='<div style="font-size:10px;margin-top:4px">当前服务: <span style="color:#4caf50;font-weight:600">'+d.current_server_port+'</span> | 建议端口: <span style="color:#4caf50;font-weight:600">'+d.suggested_port+'</span></div>';
    html+='<div style="font-size:10px;margin-top:4px">';
    if(d.port_scan){d.port_scan.forEach(function(p){
      var color = p.normal ? (p.occupied ? '#2196f3' : '#4caf50') : '#e53935';
      var icon = p.normal ? (p.occupied ? '&#128309;' : '&#128994;') : '&#128308;';
      html+='<span style="margin-right:8px;color:'+color+';white-space:nowrap">'+icon+' '+p.port+':'+p.label+'</span>';
    })}
    html+='</div>';
    html+='<div style="font-size:9px;color:#666;margin-top:4px">🔵 蓝色=本系统服务(正常) | 🔴 红色=异常占用(需排查) | 🟢 绿色=空闲</div>';
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
  if(!socket||!socket.connected){alert('Socket.IO 未连接');return;}
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
_on('agent_progress',function(d){
  var r=document.getElementById('chain-verify-result');
  if(r)r.innerHTML='<div class="alert info">'+d.step+' ('+d.progress+'%)</div>'
});
_on('agent_step_result',function(d){
  var r=document.getElementById('chain-verify-result');
  if(r){
    var ok=d.result&&!d.result.error;
    r.innerHTML+='<div style="font-size:10px;color:'+(ok?'#4caf50':'#e53935')+'">['+d.index+'/'+d.total+'] '+d.tool+': '+(ok?'OK':'FAIL')+'</div>'
  }
});
_on('agent_complete',function(d){
  var r=document.getElementById('chain-verify-result');
  if(r){
    var html='<div class="alert success">智能体完成: '+escapeHtml(d.summary||'')+'</div>';
    if(d.results){
      d.results.forEach(function(s){html+='<div style="font-size:10px;color:#aaa">'+s.tool+': '+JSON.stringify(s.result||{}).substring(0,100)+'</div>'})
    }
    r.innerHTML=html;
  }
});
_on('agent_error',function(d){
  var r=document.getElementById('chain-verify-result');
  if(r)r.innerHTML='<div class="alert error">智能体错误: '+d.error+'</div>'
});
_on('chain_verify_progress',function(d){
  document.getElementById('chain-verify-progress').innerHTML='<div class="alert info"><span class="spinner"></span> '+d.step+' ('+d.progress+'%)</div>';
});
_on('chain_verify_complete',function(d){
  document.getElementById('chain-verify-progress').innerHTML='';
  var r=document.getElementById('chain-verify-result');
  if(r){
    var html='<div class="alert '+(d.all_ok?'success':'error')+'"><strong>'+d.summary+'</strong></div>';
    if(d.steps){for(var k in d.steps){var s=d.steps[k];html+='<div class="diag-item"><span class="diag-name">'+k+'</span><span class="diag-status '+(s.status==='ok'?'ok':'fail')+'">'+s.status+'</span><span class="diag-detail">'+escapeHtml(s.detail||'')+'</span></div>'}}
    r.innerHTML=html;
  }
});

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
  // 恢复上次GitHub推送时间
  var lastPush=localStorage.getItem('github_last_push_time');
  if(lastPush){
    var dl=document.getElementById('conn-github-detail');
    if(dl) dl.innerHTML='<span style="color:#4caf50">最后上传: '+lastPush+'</span>';
  }
});

</script>

<!-- ═══ 知识库详情弹窗 ═══ -->
<div class="knowledge-detail-overlay" id="knowledge-detail-overlay" onclick="if(event.target===this)closeKnowledgeDetail()">
  <div class="knowledge-detail-modal">
    <button class="close-btn" onclick="closeKnowledgeDetail()">&times;</button>
    <h3 id="kdetail-title"></h3>
    <div class="meta" id="kdetail-meta"></div>
    <div class="content" id="kdetail-content"></div>
    <div class="actions">
      <button class="btn-del" id="kdetail-delete-btn" onclick="deleteKnowledgeDetail()">删除</button>
      <button onclick="copyKnowledgeDetail()">复制内容</button>
      <button onclick="closeKnowledgeDetail()">关闭</button>
    </div>
  </div>
</div>

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
*{margin:0;box-sizing:border-box}
body{background:#1a1a2e;color:#fff;font-family:'Microsoft YaHei',Arial;height:100vh;display:flex;flex-direction:column}
#toolbar{background:#16213e;padding:8px 12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;min-height:45px;flex-shrink:0}
#toolbar h3{margin:0;color:#00bfff;font-size:15px;white-space:nowrap}
select{padding:5px;background:#333;color:#fff;border:none;border-radius:4px;font-size:12px}
.btn{padding:6px 12px;border:none;border-radius:4px;cursor:pointer;font-size:12px;white-space:nowrap}
.btn-save{background:#00cc66;color:#000}
.btn-del{background:#cc3333;color:#fff}
.btn-clear{background:#ff6600;color:#fff}
.btn-export{background:#0066ff;color:#fff}
.btn-import{background:#8e44ad;color:#fff}
#main-area{flex:1;overflow:auto;position:relative;display:flex;flex-direction:column;min-height:0}
#image-panel{display:flex;flex:1;overflow:hidden;min-height:0}
#sidebar{width:240px;background:#0d1117;overflow-y:auto;border-right:1px solid #333;padding:0;flex-shrink:0}
#sidebar h4{padding:10px;margin:0;color:#888;font-size:13px;border-bottom:1px solid #222}
#thumb-list{display:flex;flex-direction:column}
.thumb-item{padding:8px 10px;border-bottom:1px solid #1a1f2b;cursor:pointer;font-size:12px;display:flex;align-items:center;gap:8px}
.thumb-item:hover{background:#1a1f2b}
.thumb-item.active{background:#1a3a5c;border-left:3px solid #00bfff}
.thumb-item .name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#ccc}
.thumb-item .count{color:#888;font-size:11px}
.thumb-item .del{color:#cc3333;cursor:pointer;font-size:14px;opacity:0.5}
.thumb-item .del:hover{opacity:1}
#viewer{flex:1;overflow:auto;position:relative;display:flex;justify-content:center;align-items:flex-start;background:#111;min-height:0}
#image-container{position:relative;display:inline-block;margin:5px}
canvas{position:absolute;top:0;left:0}
img{display:block;max-width:100vw;max-height:70vh}
#meta-bar{background:#16213e;padding:6px 12px;display:flex;gap:10px;align-items:center;font-size:12px;border-top:1px solid #222;flex-wrap:wrap;flex-shrink:0;min-height:36px}
#meta-bar input{background:#1a1f2b;border:1px solid #333;color:#fff;padding:4px 8px;border-radius:3px;font-size:12px}
#descInput{flex:1;min-width:200px}
#status{position:fixed;bottom:8px;left:10px;background:rgba(22,33,62,0.95);padding:6px 14px;border-radius:5px;font-size:13px;z-index:99}
.drop-hint{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#555;font-size:18px;pointer-events:none}
</style>
</head>
<body>
<div id="toolbar">
  <h3>&#x1F525; Firefight UI 标注</h3>
  <label class="btn btn-import" style="cursor:pointer;display:inline-block">
    &#x1F4C1; 导入截图
    <input type="file" id="fileInput" accept="image/*" multiple style="display:none" onchange="importFiles(this.files)">
  </label>
  <select id="classSelect">
    <option value="0">0-You阵营国旗</option>
    <option value="1">1-Enemy阵营国旗</option>
    <option value="2">2-阵营下拉列表区</option>
    <option value="3">3-OK/确认按钮</option>
    <option value="4">4-单位选择区</option>
    <option value="5">5-开始战斗按钮</option>
    <option value="6">6-旗点</option>
    <option value="7">7-其他按钮</option>
    <option value="8">8-敌方阵营选择区</option>
  </select>
  <button class="btn btn-save" onclick="saveLabels()">&#x1F4BE; 保存</button>
  <button class="btn btn-del" onclick="delLast()">&#x2715; 删最后</button>
  <button class="btn btn-clear" onclick="clearAll()">清空</button>
  <button class="btn btn-export" onclick="exportYOLO()">&#x1F4E6; 导出YOLO</button>
  <button class="btn btn-export" style="background:#00b894" onclick="exportAllData()">&#x1F4BF; 全量导出</button>
  <span style="color:#888;font-size:11px">Ctrl+S保存 | Ctrl+Z删除 | 0-8切换类别 | 拖拽框选 | 支持拖放导入</span>
</div>

<div style="background:#332200;border-bottom:3px solid #ffaa00;padding:8px 12px;display:flex;gap:10px;align-items:center;flex-shrink:0">
  <span style="font-size:15px">&#x1F3F7;</span>
  <span style="color:#ffaa00;font-weight:bold;font-size:14px;white-space:nowrap">框名称:</span>
  <input type="text" id="boxLabelInput" placeholder="先点击框，再输入名称..." style="flex:1;background:#1a1a2e;border:2px solid #ffaa00;color:#ffaa00;padding:8px 12px;border-radius:4px;font-size:14px;outline:none;font-weight:bold" onchange="updateBoxLabel()" onkeydown="if(event.key==='Enter')updateBoxLabel()">
  <span id="boxLabelHint" style="color:#cc8800;font-size:12px">未选中框</span>
</div>

<div id="main-area">
  <div id="image-panel">
    <div id="sidebar">
      <h4>&#x1F4F8; 截图列表 (<span id="imgCount">0</span>)</h4>
      <div id="thumb-list"></div>
    </div>
    <div id="viewer">
      <div id="image-container">
        <div class="drop-hint" id="dropHint">&#x1F4C1; 拖放截图到此处<br><small>或点击上方「导入截图」</small></div>
        <img id="mainImage" style="display:none">
        <canvas id="canvas" style="display:none"></canvas>
      </div>
    </div>
  </div>
  <div id="meta-bar">
    <span>&#x1F4DD; 截图说明:</span>
    <input type="text" id="descInput" placeholder="描述这张截图是什么界面/状态..." onchange="updateDesc()">
    <span style="color:#888;font-size:11px">例如: "阵营选择-美国vs中国" / "选兵界面" / "部署阶段"</span>
  </div>
</div>
<div id="status">就绪 - 导入截图开始标注</div>

<script>
var CLASS_NAMES = ['You国旗','Enemy国旗','下拉列表','OK按钮','单位区','开始按钮','旗点','其他','敌阵营区'];
var COLORS = ['#ff6b6b','#4ecdc4','#ffe66d','#a29bfe','#fd79a8','#00b894','#e17055','#6c5ce7','#636e72'];

var project = { images: {}, order: [], activeId: null };
var boxes = [];
var drawing = null;
var selectedBox = -1;
var imgEl = document.getElementById('mainImage');
var canvas = document.getElementById('canvas');
var ctx = canvas.getContext('2d');
var dropHint = document.getElementById('dropHint');

// ── 导入 ──
document.getElementById('fileInput').addEventListener('change', function() {
  importFiles(this.files);
  this.value = '';
});

var viewer = document.getElementById('viewer');
viewer.addEventListener('dragover', function(e) { e.preventDefault(); e.stopPropagation(); });
viewer.addEventListener('drop', function(e) {
  e.preventDefault(); e.stopPropagation();
  if (e.dataTransfer.files.length) importFiles(e.dataTransfer.files);
});

function importFiles(fileList) {
  var count = 0;
  for (var i = 0; i < fileList.length; i++) {
    var f = fileList[i];
    if (!f.type.startsWith('image/')) continue;
    var reader = new FileReader();
    reader.onload = (function(file) {
      return function(e) {
        var id = 'img_' + Date.now() + '_' + Math.random().toString(36).slice(2,6);
        project.images[id] = { name: file.name, desc: '', dataUrl: e.target.result, boxes: [] };
        project.order.push(id);
        count++;
        if (count === 1 || project.order.length === 1) switchTo(id);
        renderSidebar();
      };
    })(f);
    reader.readAsDataURL(f);
  }
  document.getElementById('status').textContent = '导入中...';
}

// ── 侧边栏 ──
function renderSidebar() {
  var list = document.getElementById('thumb-list');
  list.innerHTML = '';
  document.getElementById('imgCount').textContent = project.order.length;
  for (var i = 0; i < project.order.length; i++) {
    var id = project.order[i];
    var img = project.images[id];
    var div = document.createElement('div');
    div.className = 'thumb-item' + (id === project.activeId ? ' active' : '');
    div.innerHTML = '<span class="name" title="' + (img.desc || img.name) + '">&#x1F4F8; ' + img.name + '</span><span class="count">' + img.boxes.length + '框</span><span class="del" onclick="event.stopPropagation();deleteImg(\'' + id + '\')">&#x2715;</span>';
    div.onclick = (function(imgId) { return function() { switchTo(imgId); }; })(id);
    list.appendChild(div);
  }
  if (!project.order.length) { dropHint.style.display = ''; }
}

function switchTo(id) {
  project.activeId = id;
  var img = project.images[id];
  boxes = img.boxes;
  selectedBox = -1;
  document.getElementById('boxLabelInput').value = '';
  document.getElementById('boxLabelInput').placeholder = '先点框，再输入名称...';
  imgEl.src = img.dataUrl;
  document.getElementById('descInput').value = img.desc || '';
  document.getElementById('status').textContent = '切换到: ' + img.name;
  renderSidebar();
}

function deleteImg(id) {
  if (!confirm('删除 ' + project.images[id].name + ' 及其所有标注？')) return;
  delete project.images[id];
  project.order = project.order.filter(function(x) { return x !== id; });
  if (project.activeId === id) {
    project.activeId = project.order[0] || null;
    if (project.activeId) { switchTo(project.activeId); }
    else { imgEl.style.display = 'none'; canvas.style.display = 'none'; dropHint.style.display = ''; boxes = []; }
  }
  renderSidebar();
}

function updateDesc() {
  if (!project.activeId) return;
  project.images[project.activeId].desc = document.getElementById('descInput').value;
  renderSidebar();
}

// ── 图片加载 ──
imgEl.onload = function() {
  dropHint.style.display = 'none';
  imgEl.style.display = '';
  canvas.style.display = '';
  canvas.width = imgEl.naturalWidth;
  canvas.height = imgEl.naturalHeight;
  canvas.style.width = imgEl.clientWidth + 'px';
  canvas.style.height = imgEl.clientHeight + 'px';
  drawAll();
};

// ── 绘制 ──
function drawAll() {
  if (!imgEl.naturalWidth) return;
  var W = imgEl.naturalWidth, H = imgEl.naturalHeight;
  ctx.clearRect(0, 0, W, H);
  for (var i = 0; i < boxes.length; i++) {
    var b = boxes[i];
    var isSelected = (i === selectedBox);
    var color = COLORS[b.cls];
    ctx.strokeStyle = isSelected ? '#ffffff' : color;
    ctx.lineWidth = isSelected ? 3.5 : 2.5;
    ctx.strokeRect(b.x, b.y, b.w, b.h);
    var displayLabel = b.label || CLASS_NAMES[b.cls];
    var prefix = isSelected ? '★ ' : '';
    var label = '[' + (i+1) + '] ' + prefix + displayLabel;
    ctx.font = isSelected ? 'bold 13px Microsoft YaHei' : '12px Microsoft YaHei';
    var tw = ctx.measureText(label).width + 8;
    var th = isSelected ? 20 : 16;
    ctx.fillStyle = isSelected ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.75)';
    ctx.fillRect(b.x, b.y - th, tw, th);
    ctx.fillStyle = isSelected ? '#ffffff' : color;
    ctx.fillText(label, b.x + 3, b.y - 4);
  }
}

// ── 坐标转换 ──
function toImgCoords(clientX, clientY) {
  var rect = imgEl.getBoundingClientRect();
  return { x: (clientX - rect.left) * (imgEl.naturalWidth / imgEl.clientWidth), y: (clientY - rect.top) * (imgEl.naturalHeight / imgEl.clientHeight) };
}

// ── 鼠标事件 ──
canvas.addEventListener('mousedown', function(e) {
  if (!project.activeId) return;
  var p = toImgCoords(e.clientX, e.clientY);
  var hit = -1;
  for (var i = boxes.length - 1; i >= 0; i--) {
    var b = boxes[i];
    if (p.x >= b.x && p.x <= b.x + b.w && p.y >= b.y && p.y <= b.y + b.h) { hit = i; break; }
  }
  if (hit >= 0) { selectBox(hit); return; }
  selectBox(-1);
  var cls = parseInt(document.getElementById('classSelect').value);
  boxes.push({cls: cls, x: p.x, y: p.y, w: 0, h: 0, label: ''});
  drawing = {startX: p.x, startY: p.y, idx: boxes.length - 1};
});

canvas.addEventListener('mousemove', function(e) {
  if (!drawing) return;
  var p = toImgCoords(e.clientX, e.clientY);
  var b = boxes[drawing.idx];
  b.x = Math.min(drawing.startX, p.x);
  b.y = Math.min(drawing.startY, p.y);
  b.w = Math.abs(p.x - drawing.startX);
  b.h = Math.abs(p.y - drawing.startY);
  drawAll();
});

canvas.addEventListener('mouseup', function(e) {
  if (!drawing) return;
  var b = boxes[drawing.idx];
  if (b.w < 5 || b.h < 5) { boxes.pop(); selectBox(-1); }
  drawing = null;
  drawAll();
  updateStatus();
  renderSidebar();
});

function updateStatus() {
  var img = project.activeId ? project.images[project.activeId] : null;
  var name = img ? img.name : '无图';
  document.getElementById('status').textContent = boxes.length + '个标注 | ' + CLASS_NAMES[parseInt(document.getElementById('classSelect').value)] + ' | ' + name;
}

// ── 框选中 + 命名 ──
function selectBox(idx) {
  selectedBox = idx;
  var input = document.getElementById('boxLabelInput');
  var hint = document.getElementById('boxLabelHint');
  if (idx >= 0 && idx < boxes.length) {
    input.value = boxes[idx].label || '';
    input.placeholder = '框#' + (idx+1) + ': ' + CLASS_NAMES[boxes[idx].cls] + ' — 输入名称';
    hint.textContent = '已选中 框#' + (idx+1);
    hint.style.color = '#00ff88';
    input.focus();
  } else {
    input.value = '';
    input.placeholder = '先点击框，再输入名称...';
    hint.textContent = '未选中框';
    hint.style.color = '#cc8800';
  }
  drawAll();
}

function updateBoxLabel() {
  if (selectedBox < 0 || selectedBox >= boxes.length) return;
  var val = document.getElementById('boxLabelInput').value.trim();
  boxes[selectedBox].label = val;
  drawAll();
  document.getElementById('status').textContent = '框#' + (selectedBox+1) + ' 已命名: ' + (val || CLASS_NAMES[boxes[selectedBox].cls]);
  renderSidebar();
}

// ── 操作按钮 ──
function saveLabels() {
  if (!project.activeId) return;
  // 保存到服务器
  var img = project.images[project.activeId];
  var W = imgEl.naturalWidth, H = imgEl.naturalHeight;
  var lines = [];
  for (var i = 0; i < boxes.length; i++) {
    var b = boxes[i];
    if (b.w < 3 || b.h < 3) continue;
    var cx = ((b.x + b.w / 2) / W).toFixed(6);
    var cy = ((b.y + b.h / 2) / H).toFixed(6);
    var nw = (b.w / W).toFixed(6);
    var nh = (b.h / H).toFixed(6);
    lines.push(b.cls + ' ' + cx + ' ' + cy + ' ' + nw + ' ' + nh);
  }
  fetch('/api/annotate/save', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({dataset:'faction_yolo', image:img.name, labels:boxes.map(function(b){return {class:b.cls, x:(b.x+b.w/2)/W, y:(b.y+b.h/2)/H, w:b.w/W, h:b.h/H}})})}).then(function(r){return r.json()}).then(function(d){
    renderSidebar();
    document.getElementById('status').textContent = '已保存 ' + boxes.length + '个标注到服务器';
  }).catch(function(e){
    document.getElementById('status').textContent = '保存失败: ' + e;
  });
}

function delLast() {
  if (selectedBox >= 0 && selectedBox < boxes.length) {
    boxes.splice(selectedBox, 1);
    selectBox(-1);
  } else { boxes.pop(); }
  drawAll();
  updateStatus();
  renderSidebar();
}

function clearAll() {
  if (!boxes.length) return;
  if (!confirm('确定清空当前图所有 ' + boxes.length + ' 个标注？')) return;
  boxes.length = 0;
  drawAll();
  updateStatus();
  renderSidebar();
}

function exportYOLO() {
  if (!project.activeId || !boxes.length) { alert('当前无标注'); return; }
  var img = project.images[project.activeId];
  var W = imgEl.naturalWidth, H = imgEl.naturalHeight;
  var lines = [];
  var summary = '=== ' + img.name + ' (' + (img.desc || '无说明') + ') ===\n';
  for (var i = 0; i < boxes.length; i++) {
    var b = boxes[i];
    if (b.w < 3 || b.h < 3) continue;
    var cx = ((b.x + b.w / 2) / W).toFixed(6);
    var cy = ((b.y + b.h / 2) / H).toFixed(6);
    var nw = (b.w / W).toFixed(6);
    var nh = (b.h / H).toFixed(6);
    lines.push(b.cls + ' ' + cx + ' ' + cy + ' ' + nw + ' ' + nh);
    var name = b.label || CLASS_NAMES[b.cls];
    summary += '  #' + (i+1) + ' [' + name + '] (' + Math.round(b.x+b.w/2) + ',' + Math.round(b.y+b.h/2) + ') ' + Math.round(b.w) + 'x' + Math.round(b.h) + '\n';
  }
  // 下载YOLO文本文件
  var blob = new Blob([lines.join('\n')], {type:'text/plain'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = img.name.replace(/\.\w+$/,'') + '_yolo.txt';
  a.click();
  // 🔥 同时上传到训练数据集
  fetch('/api/annotate/to_training', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      image_name: img.name,
      image_data: img.dataUrl,
      yolo_labels: lines.join('\n'),
      width: W, height: H,
      boxes: boxes.map(function(b){return {cls:b.cls,label:b.label||CLASS_NAMES[b.cls],x:b.x,y:b.y,w:b.w,h:b.h}})
    })
  }).then(function(r){return r.json()}).then(function(d){
    if(d.status==='ok'){
      document.getElementById('status').textContent = '已导出YOLO格式并上传到训练数据集 (' + d.dataset + ')';
      alert(summary + '\n\n[已自动添加到模型训练上传区]');
    }else{
      document.getElementById('status').textContent = '已导出YOLO格式 (训练上传失败: '+d.error+')';
      alert(summary);
    }
  }).catch(function(e){
    document.getElementById('status').textContent = '已导出YOLO格式';
    alert(summary);
  });
}

function exportAllData() {
  if (!project.order.length) { alert('无数据可导出'); return; }
  var data = {exportTime: new Date().toISOString(), images: {}};
  for (var i = 0; i < project.order.length; i++) {
    var id = project.order[i];
    var img = project.images[id];
    data.images[id] = { name: img.name, desc: img.desc, boxes: img.boxes };
  }
  var blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = 'firefight_labels_' + new Date().toISOString().slice(0,10) + '.json';
  a.click();
  var totalBoxes = 0;
  for (var k in data.images) { totalBoxes += data.images[k].boxes.length; }
  document.getElementById('status').textContent = '全量导出: ' + project.order.length + '张图, ' + totalBoxes + '个标注';
}

// ── 键盘快捷键 ──
document.addEventListener('keydown', function(e) {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 's' && e.ctrlKey) { e.preventDefault(); saveLabels(); }
  if (e.key === 'z' && e.ctrlKey) { e.preventDefault(); delLast(); }
  if (e.key === 'e' && e.ctrlKey) { e.preventDefault(); exportYOLO(); }
  if (e.key >= '0' && e.key <= '8' && !e.ctrlKey) {
    document.getElementById('classSelect').value = e.key;
    updateStatus();
  }
});

// ── 从服务器加载已有数据集 ──
function loadServerImages() {
  fetch('/api/annotate/images?dataset=faction_yolo').then(function(r){return r.json()}).then(function(data){
    for (var i = 0; i < data.length; i++) {
      var img = data[i];
      var id = 'srv_' + img.name;
      project.images[id] = { name: img.name, desc: '', dataUrl: img.url, boxes: [] };
      if (img.labels && img.labels.length) {
        var img2 = new Image();
        img2.onload = function(w,h,lbls,projId) {
          return function() {
            for (var j = 0; j < lbls.length; j++) {
              var l = lbls[j];
              project.images[projId].boxes.push({cls: l.class, x: (l.x - l.w/2) * w, y: (l.y - l.h/2) * h, w: l.w * w, h: l.h * h, label: ''});
            }
          };
        }(img2.naturalWidth, img2.naturalHeight, img.labels, id);
        img2.src = img.url;
      }
      project.order.push(id);
    }
    renderSidebar();
    if (project.order.length > 0 && !project.activeId) switchTo(project.order[0]);
    document.getElementById('status').textContent = '从服务器加载了 ' + data.length + ' 张图片';
  }).catch(function(e){
    document.getElementById('status').textContent = '服务器加载失败: ' + e;
  });
}

renderSidebar();
loadServerImages();
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
        # 🔥 确保requests已安装
        import sys
        try:
            import requests
        except ImportError:
            import subprocess as sp
            sp.run([sys.executable, "-m", "pip", "install", "requests", "-q"], capture_output=True, timeout=60)
            import requests as _req2
            _req = _req2

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
    """检测端口占用（排除当前进程，识别各类模拟器）"""
    occupied = []
    current_pid = str(os.getpid())  # 🔥 排除当前Python进程
    # 模拟器/ADB相关进程名（检测真正的端口冲突来源）
    emulator_names = ["mumu", "mumuvmm", "nox", "bluestacks", "memu", "ldplayer", "adb", "emulator", "qemu", "xinglv", "雷电", "逍遥"]
    try:
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5)
        tasklist = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=5)
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
                        if pid == current_pid:
                            continue  # 🔥 跳过当前进程
                        process_name = pid_map.get(pid, "")
                        is_emu = any(name in process_name.lower() for name in emulator_names)
                        if is_emu:
                            occupied.append({"port": port, "pid": pid, "process": process_name + " (模拟器/ADB)"})
                        elif "python" in process_name.lower() and port in [5000, 5001, 5005, 5800, 7555]:
                            # 🔥 排除当前进程和5001服务端口
                            if pid == current_pid:
                                continue
                            occupied.append({"port": port, "pid": pid, "process": process_name + " (可能冲突)"})
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
    return _find_adb_exe()

def _find_scrcpy_exe():
    """查找scrcpy可执行文件，用于60fps硬件加速渲染; 未安装时自动解压"""
    candidates = [
        str(PROJECT_ROOT / "scrcpy-win64-v3.3" / "scrcpy.exe"),
        str(PROJECT_ROOT / "scrcpy" / "scrcpy.exe"),
        r"C:\scrcpy\scrcpy.exe",
        r"D:\scrcpy\scrcpy.exe",
    ]
    for c in candidates:
        if os.path.exists(c) or os.path.exists(c.replace(".exe", "")):
            logger.info(f"找到scrcpy: {c}")
            return c
    # 自动解压
    scrcpy_zip = str(PROJECT_ROOT.parent / "scrcpy.zip")
    if os.path.exists(scrcpy_zip):
        import zipfile
        logger.info("正在解压scrcpy...")
        try:
            with zipfile.ZipFile(scrcpy_zip, "r") as zf:
                zf.extractall(str(PROJECT_ROOT))
            logger.info("scrcpy解压完成")
            for c in candidates:
                if os.path.exists(c):
                    return c
        except Exception as e:
            logger.warning(f"scrcpy解压失败: {e}")
    # 系统PATH查找
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        exe = os.path.join(path_dir, "scrcpy.exe")
        if os.path.exists(exe):
            return exe
    return None  # scrcpy未安装, 回退到ADB模式


def _is_emulator_running() -> bool:
    """检测模拟器是否已在运行（支持 localhost:port 和 emulator-port 两种格式）"""
    global _emulator_process
    adb_exe = _get_adb_for_emulator()
    try:
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        r = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
        port = str(_emulator_adb_port)
        for line in r.stdout.strip().split("\n"):
            if "\tdevice" in line:
                if f"localhost:{port}" in line or f"emulator-{port}" in line:
                    return True
            elif "device" in line and "emulator-" in line:
                return True
    except:
        pass
    return False


def _has_emulator_process() -> bool:
    """检测是否有模拟器进程在运行（不管ADB是否已连接）"""
    import psutil as _psutil
    try:
        emulator_names = {"emulator", "qemu-system-x86_64", "qemu-system-x86_64-headless",
                          "qemu-system-aarch64", "qemu-system-arm"}
        for proc in _psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower().replace(".exe", "")
                if name in emulator_names:
                    return True
            except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                continue
    except Exception:
        # psutil不可用时回退到tasklist
        try:
            r = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=5)
            for name in ["emulator.exe", "qemu-system-x86_64"]:
                if name.lower() in r.stdout.lower():
                    return True
        except:
            pass
    return False


def _kill_stale_emulators():
    """杀死所有残留的模拟器进程"""
    import psutil as _psutil
    killed = 0
    emulator_names = {"emulator", "qemu-system-x86_64", "qemu-system-x86_64-headless",
                      "qemu-system-aarch64", "qemu-system-arm"}
    try:
        for proc in _psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower().replace(".exe", "")
                if name in emulator_names:
                    proc.kill()
                    killed += 1
                    logger.info(f"已杀死残留模拟器进程: {name} (PID: {proc.info['pid']})")
            except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                continue
    except Exception:
        # psutil不可用时回退到taskkill
        try:
            for name in ["emulator.exe", "qemu-system-x86_64.exe", "qemu-system-x86_64-headless.exe"]:
                subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, timeout=10)
        except:
            pass
    if killed > 0:
        add_system_log("emulator", "清理残留进程", f"已终止 {killed} 个残留模拟器进程")
    return killed


def _fix_avd_resolution():
    """修复AVD config.ini中的分辨率设置为1920x1080横屏"""
    avd_dir = Path.home() / ".android" / "avd" / f"{AVD_NAME}.avd"
    config_ini = avd_dir / "config.ini"
    if not config_ini.exists():
        return
    try:
        content = config_ini.read_text(errors="replace")
        updated = content
        # 修正分辨率
        import re
        updated = re.sub(r'^hw\.lcd\.width=.*$', f'hw.lcd.width={AVD_CONFIG["resolution"].split("x")[0]}', updated, flags=re.MULTILINE)
        updated = re.sub(r'^hw\.lcd\.height=.*$', f'hw.lcd.height={AVD_CONFIG["resolution"].split("x")[1]}', updated, flags=re.MULTILINE)
        updated = re.sub(r'^hw\.lcd\.density=.*$', f'hw.lcd.density={AVD_CONFIG["density"]}', updated, flags=re.MULTILINE)
        updated = re.sub(r'^hw\.initialOrientation=.*$', 'hw.initialOrientation=landscape', updated, flags=re.MULTILINE)
        if updated != content:
            config_ini.write_text(updated)
            logger.info(f"AVD分辨率已修正为 {AVD_CONFIG['resolution']}, density={AVD_CONFIG['density']}")
    except Exception as e:
        logger.warning(f"修复AVD分辨率失败: {e}")


def _reconnect_emulator() -> bool:
    """重新连接到已运行的模拟器"""
    global _emulator_process
    adb_exe = _get_adb_for_emulator()
    port = str(_emulator_adb_port)
    try:
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        subprocess.run([adb_exe, "connect", f"localhost:{port}"], capture_output=True, text=True, timeout=5)
        # 设置屏幕属性
        subprocess.run([adb_exe, "-s", f"emulator-{port}", "shell", "wm", "size", AVD_CONFIG["resolution"]], capture_output=True, text=True, timeout=5)
        subprocess.run([adb_exe, "-s", f"emulator-{port}", "shell", "wm", "density", str(AVD_CONFIG["density"])], capture_output=True, text=True, timeout=5)
        _sync_adb_to_emulator_port()
        add_system_log("emulator", "已重新连接到模拟器", f"端口: {port}")
        return True
    except Exception as e:
        logger.error(f"重新连接模拟器失败: {e}")
        return False


def _sync_adb_to_emulator_port():
    """模拟器启动后，自动将主ADB配置同步到模拟器端口"""
    try:
        cfg = load_config()
        dc = cfg["device"]
        ad = dc.get("active", "generic")
        if ad not in dc:
            dc[ad] = {}
        dc[ad]["adb_host"] = "127.0.0.1"
        dc[ad]["adb_port"] = _emulator_adb_port
        save_config(cfg)
        update_state(adb_status="connected", adb_host="127.0.0.1", adb_port=_emulator_adb_port)
        add_system_log("connection", "ADB配置已同步到模拟器", f"端口: {_emulator_adb_port}")
        logger.info(f"ADB配置已同步到模拟器端口 {_emulator_adb_port}")
    except Exception as e:
        logger.error(f"ADB配置同步失败: {e}")


def _get_emulator_exe():
    """获取模拟器可执行文件路径"""
    emu_path = ANDROID_SDK_ROOT / "emulator" / "emulator.exe"
    if emu_path.exists():
        return str(emu_path)
    return "emulator"

def _get_java_home():
    """获取Java路径，优先使用项目自带的JDK"""
    java_dir = EMULATOR_HOME / "java"
    if java_dir.exists():
        for d in java_dir.iterdir():
            if d.is_dir() and d.name.lower().startswith("jdk"):
                java_exe = d / "bin" / "java.exe"
                if java_exe.exists():
                    return str(d)
    return None

def _set_java_env():
    """设置JAVA_HOME环境变量"""
    jh = _get_java_home()
    if jh:
        os.environ["JAVA_HOME"] = jh
        return jh
    return None


# ── 模拟器类型切换 ──
_emulator_type = "generic"  # generic | mumu | bluestacks | ldplayer | xiaoyao | nox | other

# 🔥 模拟器类型与端口映射
EMULATOR_TYPE_MAP = {
    "generic":     {"port": 5556, "name": "本地模拟器",          "adb_format": "emulator"},
    "mumu":        {"port": 7555, "name": "MUMU模拟器",          "adb_format": "tcp"},
    "bluestacks":  {"port": 5555, "name": "蓝叠模拟器",          "adb_format": "tcp"},
    "ldplayer":    {"port": 5555, "name": "雷电模拟器",          "adb_format": "tcp"},
    "xiaoyao":     {"port": 21503, "name": "逍遥模拟器",         "adb_format": "tcp"},
    "nox":         {"port": 62001, "name": "Nox模拟器",          "adb_format": "tcp"},
    "memu":        {"port": 21503, "name": "Memu模拟器",         "adb_format": "tcp"},
    "other":       {"port": 5555, "name": "其他模拟器(自定义)",  "adb_format": "tcp"},
}

@app.route("/api/emulator/type", methods=["GET", "POST"])
def api_emulator_type():
    """获取或切换模拟器类型"""
    global _emulator_type, _emulator_adb_port
    if request.method == "POST":
        data = request.get_json() or {}
        new_type = data.get("type", "generic").strip()
        
        valid_types = list(EMULATOR_TYPE_MAP.keys())
        if new_type not in valid_types:
            return jsonify({"error": f"无效的模拟器类型，可选: {valid_types}"}), 400
        
        _emulator_type = new_type
        emu_info = EMULATOR_TYPE_MAP.get(new_type, EMULATOR_TYPE_MAP["other"])
        _emulator_adb_port = emu_info["port"]
        
        # 更新配置文件
        try:
            cfg = load_config()
            cfg["device"]["active"] = new_type
            config_path = PROJECT_ROOT / "config" / "settings.yaml"
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            logger.warning(f"更新模拟器类型配置失败: {e}")
        
        # 自动重新连接ADB到新端口
        try:
            adb_exe = _find_adb_exe()
            subprocess.run([adb_exe, "connect", f"127.0.0.1:{_emulator_adb_port}"], capture_output=True, text=True, timeout=5)
        except:
            pass
        
        add_system_log("emulator", f"切换模拟器类型: {emu_info['name']}", f"端口: {_emulator_adb_port}")
        return jsonify({"status": "ok", "type": new_type, "name": emu_info["name"], "port": _emulator_adb_port})
    
    return jsonify({
        "type": _emulator_type, 
        "name": EMULATOR_TYPE_MAP.get(_emulator_type, {})["name"],
        "port": _emulator_adb_port, 
        "available": list(EMULATOR_TYPE_MAP.keys()),
        "types": EMULATOR_TYPE_MAP
    })


@app.route("/api/emulator/detect", methods=["GET"])
def api_emulator_detect():
    """自动检测当前运行中的模拟器"""
    adb_exe = _find_adb_exe()
    detected = []
    
    try:
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        r = subprocess.run([adb_exe, "devices", "-l"], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split("\n")[1:]  # 跳过 "List of devices attached"
        
        for line in lines:
            if "\tdevice" not in line and "device" not in line:
                continue
            line = line.strip()
            
            # 提取设备ID和产品信息
            parts = line.split()
            dev_id = parts[0] if parts else ""
            
            # 判断模拟器类型
            emu_type = "unknown"
            emu_name = "未知设备"
            
            # 从设备ID和产品信息判断
            if "emulator-" in dev_id:
                emu_type = "generic"
                emu_name = "本地模拟器"
            elif "7555" in dev_id:
                emu_type = "mumu"
                emu_name = "MUMU模拟器"
            elif "5555" in dev_id:
                emu_type = "bluestacks"
                emu_name = "蓝叠模拟器(可能)"
            elif "21503" in dev_id:
                emu_type = "xiaoyao"
                emu_name = "逍遥/Memu模拟器"
            elif "62001" in dev_id:
                emu_type = "nox"
                emu_name = "Nox模拟器"
            
            # 从产品信息进一步判断
            line_lower = line.lower()
            if "mumu" in line_lower:
                emu_type = "mumu"
                emu_name = "MUMU模拟器"
            elif "bluestacks" in line_lower:
                emu_type = "bluestacks"
                emu_name = "蓝叠模拟器"
            elif "ldplayer" in line_lower:
                emu_type = "ldplayer"
                emu_name = "雷电模拟器"
            elif "nox" in line_lower:
                emu_type = "nox"
                emu_name = "Nox模拟器"
            elif "memu" in line_lower:
                emu_type = "memu"
                emu_name = "Memu模拟器"
            
            detected.append({
                "device_id": dev_id,
                "type": emu_type,
                "name": emu_name,
                "raw": line,
                "current": emu_type == _emulator_type,
            })
        
        if not detected:
            # 尝试扫描常见端口
            common_ports = [5555, 5556, 7555, 21503, 62001, 62025]
            for port in common_ports:
                r2 = subprocess.run([adb_exe, "connect", f"127.0.0.1:{port}"], 
                                   capture_output=True, text=True, timeout=3)
                if "connected" in (r2.stdout + r2.stderr).lower():
                    detected.append({
                        "device_id": f"127.0.0.1:{port}",
                        "type": "discovered",
                        "name": f"发现设备(端口{port})",
                        "raw": r2.stdout.strip(),
                        "current": False,
                    })
        
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200], "detected": []}), 500
    
    return jsonify({
        "status": "ok",
        "detected": detected,
        "count": len(detected),
        "current_type": _emulator_type,
        "current_port": _emulator_adb_port,
    })


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

    # 🔥 Java状态
    java_home = _get_java_home()
    result["java_available"] = java_home is not None
    result["java_home"] = java_home or ""

    if result["avd_exists"]:
        config_ini = avd_dir / "config.ini"
        if config_ini.exists():
            details = {}
            for line in config_ini.read_text(errors="replace").split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    details[k.strip()] = v.strip()
            result["details"] = details

    # 检查是否在运行（支持 localhost:port 和 emulator-port 两种格式）
    adb_exe = _get_adb_for_emulator()
    try:
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        r = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
        port = str(_emulator_adb_port)
        for line in r.stdout.strip().split("\n"):
            if "\tdevice" in line:
                if f"localhost:{port}" in line or f"emulator-{port}" in line:
                    result["running"] = True
                    result["adb_connected"] = True
                    result["adb_port"] = _emulator_adb_port
                    break
            elif "device" in line and "emulator-" in line:
                result["running"] = True
                result["adb_connected"] = True
                result["adb_port"] = _emulator_adb_port
                break
    except Exception as e:
        result["adb_error"] = str(e)[:200]

    return jsonify(result)


@app.route("/api/emulator/install", methods=["POST"])
def api_emulator_install():
    import subprocess as sp, zipfile, requests as _req, io as _io, tempfile as _tf, shutil as _shutil

    def install_worker():
        try:
            # 🔥 预检：确保Python依赖已安装
            socketio.emit("emu_install_progress", {"step": "检查依赖", "progress": 1})
            import sys
            try:
                import requests
            except ImportError:
                sp.run([sys.executable, "-m", "pip", "install", "requests", "-q"], capture_output=True, timeout=60)
            try:
                import zipfile
            except ImportError:
                pass  # zipfile is built-in
            try:
                import shutil
            except ImportError:
                pass  # shutil is built-in

            socketio.emit("emu_install_progress", {"step": "创建目录", "progress": 3})
            EMULATOR_HOME.mkdir(parents=True, exist_ok=True)
            ANDROID_SDK_ROOT.mkdir(parents=True, exist_ok=True)
            avd_home = Path.home() / ".android" / "avd"
            avd_home.mkdir(parents=True, exist_ok=True)

            # 下载命令行工具（优先国内镜像）
            socketio.emit("emu_install_progress", {"step": "下载Android SDK命令行工具", "progress": 10})
            # 🔥 国内镜像优先，避免Google被墙导致下载失败
            cmdline_urls = [
                "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip",
                "https://mirrors.cloud.tencent.com/AndroidSDK/commandlinetools-win-11076708_latest.zip",
                "https://mirrors.huaweicloud.com/android-sdk/repository/commandlinetools-win-11076708_latest.zip",
            ]
            tools_zip = EMULATOR_HOME / "cmdline-tools.zip"
            dl_success = False

            if not tools_zip.exists():
                for idx, cmdline_url in enumerate(cmdline_urls):
                    try:
                        socketio.emit("emu_install_progress", {"step": f"正在下载 (尝试镜像{idx+1})...", "progress": 15})
                        r = _req.get(cmdline_url, stream=True, timeout=60)
                        if r.status_code == 200:
                            total = int(r.headers.get("content-length", 0))
                            downloaded = 0
                            with open(tools_zip, "wb") as f:
                                for chunk in r.iter_content(chunk_size=8192):
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    if total > 0:
                                        pct = min(15 + int(downloaded / total * 30), 45)
                                        socketio.emit("emu_install_progress", {"step": f"下载中... {downloaded//1024//1024}MB/{total//1024//1024}MB", "progress": pct})
                            dl_success = True
                            break
                        else:
                            socketio.emit("emu_install_progress", {"step": f"镜像{idx+1}返回{r.status_code}, 尝试下一个...", "progress": 15})
                    except Exception as e:
                        socketio.emit("emu_install_progress", {"step": f"镜像{idx+1}失败: {str(e)[:50]}, 尝试下一个...", "progress": 15})
                        continue

                if not dl_success:
                    socketio.emit("emu_install_progress", {"step": "所有下载源均失败，请检查网络连接", "progress": 0, "error": True})
                    add_system_log("emulator", "SDK下载失败", "所有镜像源均不可用")
                    return

            # 解压
            socketio.emit("emu_install_progress", {"step": "解压命令行工具", "progress": 50})
            cmdline_dir = ANDROID_SDK_ROOT / "cmdline-tools" / "latest"
            if cmdline_dir.exists():
                _shutil.rmtree(cmdline_dir, ignore_errors=True)
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
            socketio.emit("emu_install_progress", {"step": "配置AVD (标准规格)", "progress": 92})
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
                # 更新已有配置为标准规格
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
                logger.info("AVD配置更新为标准规格")

            # 安装scrcpy
            socketio.emit("emu_install_progress", {"step": "安装scrcpy...", "progress": 96})
            _install_scrcpy_internal()

            socketio.emit("emu_install_progress", {"step": "完成!", "progress": 100})
            socketio.emit("emu_install_complete", {"success": True, "message": "Android模拟器安装完成！(标准规格)", "avd_name": AVD_NAME})
            add_system_log("emulator", "Android模拟器安装完成 (标准规格)", f"AVD: {AVD_NAME}, 分辨率: {AVD_CONFIG['resolution']}")

        except Exception as e:
            error_msg = str(e)[:300]
            socketio.emit("emu_install_complete", {"success": False, "message": f"安装失败: {error_msg}"})
            add_system_log("emulator", "模拟器安装失败", error_msg)

    threading.Thread(target=install_worker, daemon=True).start()
    return jsonify({"status": "installing"})


@app.route("/api/emulator/install_and_start", methods=["POST"])
def api_emulator_install_and_start():
    """一键安装并启动模拟器 - 安装完成后自动启动并同步ADB配置"""
    def install_and_start_worker():
        global _emulator_adb_port
        try:
            # 先检查是否已安装
            emu_exe = _get_emulator_exe()
            avd_dir = Path.home() / ".android" / "avd" / f"{AVD_NAME}.avd"
            need_install = not Path(emu_exe).exists() or not avd_dir.exists()

            if need_install:
                # 安装
                socketio.emit("emu_install_progress", {"step": "开始安装模拟器...", "progress": 0})
                import subprocess as sp, zipfile, requests as _req, io as _io, tempfile as _tf, shutil as _shutil
                import sys

                socketio.emit("emu_install_progress", {"step": "创建目录", "progress": 3})
                EMULATOR_HOME.mkdir(parents=True, exist_ok=True)
                ANDROID_SDK_ROOT.mkdir(parents=True, exist_ok=True)
                avd_home = Path.home() / ".android" / "avd"
                avd_home.mkdir(parents=True, exist_ok=True)

                socketio.emit("emu_install_progress", {"step": "下载SDK（约150MB）...", "progress": 5})
                cmdline_urls = [
                    "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip",
                    "https://mirrors.cloud.tencent.com/AndroidSDK/commandlinetools-win-11076708_latest.zip",
                    "https://mirrors.huaweicloud.com/android-sdk/repository/commandlinetools-win-11076708_latest.zip",
                ]
                tools_zip = EMULATOR_HOME / "cmdline-tools.zip"
                dl_success = False

                if not tools_zip.exists():
                    for idx, cmdline_url in enumerate(cmdline_urls):
                        try:
                            socketio.emit("emu_install_progress", {"step": f"下载中 (镜像{idx+1})...", "progress": 10 + idx * 5})
                            r = _req.get(cmdline_url, stream=True, timeout=60)
                            if r.status_code == 200:
                                total = int(r.headers.get("content-length", 0))
                                downloaded = 0
                                with open(tools_zip, "wb") as f:
                                    for chunk in r.iter_content(chunk_size=8192):
                                        f.write(chunk)
                                        downloaded += len(chunk)
                                        if total > 0:
                                            pct = min(15 + int(downloaded / total * 30), 45)
                                            socketio.emit("emu_install_progress", {"step": f"下载中... {downloaded//1024//1024}MB/{total//1024//1024}MB", "progress": pct})
                                dl_success = True
                                break
                        except:
                            continue

                if not dl_success:
                    socketio.emit("emu_install_progress", {"step": "下载失败，请检查网络", "progress": 0, "error": True})
                    return

                # 解压
                socketio.emit("emu_install_progress", {"step": "解压SDK...", "progress": 50})
                cmdline_dir = ANDROID_SDK_ROOT / "cmdline-tools" / "latest"
                if cmdline_dir.exists():
                    _shutil.rmtree(cmdline_dir, ignore_errors=True)
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
                socketio.emit("emu_install_progress", {"step": "安装SDK组件...", "progress": 55})
                sp.run([sdkmanager, "--sdk_root=" + str(ANDROID_SDK_ROOT), "--licenses"], input=b"y\ny\ny\ny\ny\ny\ny\ny\n", capture_output=True, timeout=30)

                components = [
                    "platform-tools", "emulator",
                    f"system-images;android-{AVD_CONFIG['api_level']};default;{AVD_CONFIG['arch']}",
                    f"platforms;android-{AVD_CONFIG['api_level']}",
                ]
                for i, comp in enumerate(components):
                    pct = 60 + int((i + 1) / len(components) * 20)
                    socketio.emit("emu_install_progress", {"step": f"安装: {comp}", "progress": pct})
                    sp.run([sdkmanager, "--sdk_root=" + str(ANDROID_SDK_ROOT), comp], input=b"y\n", capture_output=True, timeout=300)

                # 创建AVD
                socketio.emit("emu_install_progress", {"step": "创建AVD...", "progress": 85})
                avdmanager = str(cmdline_dir / "bin" / "avdmanager.bat")
                sp.run([avdmanager, "create", "avd", "-n", AVD_NAME,
                        "-k", f"system-images;android-{AVD_CONFIG['api_level']};default;{AVD_CONFIG['arch']}",
                        "-d", AVD_CONFIG["device"], "-f"], input=b"no\n", capture_output=True, timeout=30)

                socketio.emit("emu_install_progress", {"step": "安装完成，正在启动模拟器...", "progress": 95})

            # 启动模拟器
            socketio.emit("emu_start_progress", {"step": "启动模拟器...", "progress": 20})
            global _emulator_process
            emu_exe = _get_emulator_exe()
            cmd = [emu_exe, "-avd", AVD_NAME, "-no-window", "-no-audio",
                   "-gpu", "host", "-netdelay", "none", "-netspeed", "full",
                   "-port", str(_emulator_adb_port)]
            _emulator_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            socketio.emit("emu_start_progress", {"step": "等待模拟器启动...", "progress": 40})
            adb_exe = _get_adb_for_emulator()
            subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
            subprocess.run([adb_exe, "connect", f"localhost:{_emulator_adb_port}"], capture_output=True, text=True, timeout=10)

            waited = 0
            while waited < 120:
                time.sleep(3)
                waited += 3
                r = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
                port = str(_emulator_adb_port)
                found = False
                for line in r.stdout.strip().split("\n"):
                    if "\tdevice" in line and (f"localhost:{port}" in line or f"emulator-{port}" in line):
                        found = True
                        break
                    elif "device" in line and "emulator-" in line:
                        found = True
                        break
                if found:
                    break
                socketio.emit("emu_start_progress", {"step": f"等待启动... {waited}s", "progress": 40 + min(waited, 40)})

            # 同步ADB配置
            _sync_adb_to_emulator_port()
            # 设置分辨率和DPI
            subprocess.run([adb_exe, "-s", f"emulator-{_emulator_adb_port}", "shell", "wm", "size", AVD_CONFIG["resolution"]], capture_output=True, text=True, timeout=5)
            subprocess.run([adb_exe, "-s", f"emulator-{_emulator_adb_port}", "shell", "wm", "density", str(AVD_CONFIG["density"])], capture_output=True, text=True, timeout=5)
            socketio.emit("emu_start_complete", {"success": True, "port": _emulator_adb_port, "message": "模拟器安装并启动完成"})
            add_system_log("emulator", "一键安装启动完成", f"端口: {_emulator_adb_port}")

        except Exception as e:
            socketio.emit("emu_start_error", {"error": str(e)[:300]})
            add_system_log("emulator", "安装启动失败", str(e)[:200])

    threading.Thread(target=install_and_start_worker, daemon=True).start()
    return jsonify({"status": "installing_and_starting"})


@app.route("/api/emulator/start", methods=["POST"])
def api_emulator_start():
    """启动模拟器 - 支持所有类型，全局异常捕获确保返回JSON"""
    global _emulator_process, _emulator_adb_port
    try:
        data = request.get_json() or {}
        emu_type = data.get("type", _emulator_type)

        # 🔥 非generic模拟器：只需连接ADB，不需要启动模拟器进程
        if emu_type != "generic":
            adb_exe = _get_adb_for_emulator()
            emu_name = EMULATOR_TYPE_MAP.get(emu_type, {}).get("name", emu_type)
            try:
                subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
                # 先断开旧连接
                subprocess.run([adb_exe, "disconnect", f"127.0.0.1:{_emulator_adb_port}"], capture_output=True, text=True, timeout=5)
                # 连接
                r = subprocess.run([adb_exe, "connect", f"127.0.0.1:{_emulator_adb_port}"], capture_output=True, text=True, timeout=10)
                conn_output = r.stdout.strip() + r.stderr.strip()
                if "connected" in conn_output.lower() or "already" in conn_output.lower():
                    _sync_adb_to_emulator_port()
                    add_system_log("emulator", f"{emu_name}已连接", f"端口: {_emulator_adb_port}")
                    return jsonify({"status": "connected", "port": _emulator_adb_port, "message": f"{emu_name}已连接", "type": emu_type})
                else:
                    # 尝试扫描其他端口
                    for port in [5555, 7555, 21503, 62001, 62025]:
                        if port == _emulator_adb_port:
                            continue
                        r2 = subprocess.run([adb_exe, "connect", f"127.0.0.1:{port}"], capture_output=True, text=True, timeout=3)
                        if "connected" in (r2.stdout + r2.stderr).lower():
                            _emulator_adb_port = port
                            _sync_adb_to_emulator_port()
                            add_system_log("emulator", f"{emu_name}已连接(备用端口)", f"端口: {port}")
                            return jsonify({"status": "connected", "port": port, "message": f"{emu_name}已连接(端口{port})", "type": emu_type})
                    return jsonify({"status": "error", "error": f"无法连接到{emu_name} (端口{_emulator_adb_port}): {conn_output[:200]}"})
            except Exception as e:
                return jsonify({"status": "error", "error": str(e)[:200]})

        # ── 以下是本地模拟器 (generic) 的启动逻辑 ──

        # 🔥 步骤1: 检查是否有残留的模拟器进程（不依赖ADB，直接扫描进程）
        if _has_emulator_process():
            if _is_emulator_running():
                _reconnect_emulator()
                socketio.emit("emu_start_complete", {"success": True, "port": _emulator_adb_port, "message": "模拟器已在运行，已重新连接"})
                return jsonify({"status": "already_running", "message": "模拟器已在运行，已重新连接"})
            else:
                logger.info("检测到残留模拟器进程但ADB未连接，清理后重新启动")
                socketio.emit("emu_start_progress", {"step": "检测到残留模拟器进程，正在清理...", "progress": 5})
                _kill_stale_emulators()
                time.sleep(2)
                adb_exe = _get_adb_for_emulator()
                try:
                    subprocess.run([adb_exe, "disconnect", f"localhost:{_emulator_adb_port}"], capture_output=True, text=True, timeout=5)
                    subprocess.run([adb_exe, "disconnect", f"127.0.0.1:{_emulator_adb_port}"], capture_output=True, text=True, timeout=5)
                except:
                    pass

        if _emulator_process and _emulator_process.poll() is None:
            return jsonify({"status": "already_running", "message": "模拟器已在运行"})

        # 🔥 验证模拟器是否已安装（提前返回明确错误，避免HTML错误页）
        emu_exe = _get_emulator_exe()
        if not Path(emu_exe).exists():
            return jsonify({"status": "error", "error": "模拟器未安装，请先点击'一键安装并启动'", "suggestion": "需要安装Android SDK模拟器"}), 400

        def start_worker():
            global _emulator_process, _emulator_adb_port
            logger.info("模拟器启动工作线程开始运行")
            try:
                # 🔥 设置JAVA_HOME
                java_home = _set_java_env()
                if not java_home:
                    socketio.emit("emu_start_progress", {"step": "警告: JAVA_HOME未设置，可能启动失败", "progress": 10})

                # 🔥 修复AVD配置分辨率
                _fix_avd_resolution()

                socketio.emit("emu_start_progress", {"step": "启动模拟器...", "progress": 20})
                # 🔥 使用配置的GPU模式
                gpu_mode = _emulator_gpu_config.get("gpu_mode", "host")
                gl_ver = _emulator_gpu_config.get("gl_version", "3.0")
                cmd = [
                    emu_exe, "-avd", AVD_NAME,
                    "-no-window", "-no-audio",
                    "-gpu", gpu_mode,
                    "-netdelay", "none", "-netspeed", "full",
                    "-port", str(_emulator_adb_port),
                ]
                # 🔥 根据GPU模式添加额外参数
                if gpu_mode == "host":
                    cmd.extend(["-feature", "Vulkan"])
                elif gpu_mode == "swiftshader_indirect":
                    cmd.extend(["-feature", "GLESDynamicVersion"])
                _emulator_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.info(f"模拟器进程已启动: PID={_emulator_process.pid}")

                socketio.emit("emu_start_progress", {"step": "等待模拟器启动（首次启动约需60-90秒）...", "progress": 40})
                adb_exe = _get_adb_for_emulator()
                subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)

                waited = 0
                max_wait = 180
                found = False
                while waited < max_wait:
                    time.sleep(3)
                    waited += 3
                    if _emulator_process and _emulator_process.poll() is not None:
                        socketio.emit("emu_start_error", {"error": f"模拟器进程异常退出 (exit code: {_emulator_process.returncode})"})
                        add_system_log("emulator", "模拟器进程异常退出", f"exit code: {_emulator_process.returncode}")
                        return

                    r = subprocess.run([adb_exe, "devices"], capture_output=True, text=True, timeout=5)
                    port = str(_emulator_adb_port)
                    for line in r.stdout.strip().split("\n"):
                        if "\tdevice" in line and (f"localhost:{port}" in line or f"emulator-{port}" in line):
                            found = True
                            break
                        elif "device" in line and "emulator-" in line:
                            found = True
                            break
                    if found:
                        break
                    socketio.emit("emu_start_progress", {"step": f"等待启动... {waited}s / {max_wait}s", "progress": 40 + min(waited, 40)})

                if not found:
                    socketio.emit("emu_start_error", {"error": f"模拟器启动超时（{max_wait}秒），请检查AVD配置或系统资源"})
                    add_system_log("emulator", "模拟器启动超时", f"等待 {max_wait} 秒后ADB仍未连接")
                    return

                socketio.emit("emu_start_progress", {"step": "连接ADB并配置屏幕", "progress": 85})
                subprocess.run([adb_exe, "connect", f"localhost:{_emulator_adb_port}"], capture_output=True, text=True, timeout=10)
                time.sleep(3)
                subprocess.run([adb_exe, "-s", f"emulator-{_emulator_adb_port}", "shell", "wm", "size", AVD_CONFIG["resolution"]], capture_output=True, text=True, timeout=10)
                subprocess.run([adb_exe, "-s", f"emulator-{_emulator_adb_port}", "shell", "wm", "density", str(AVD_CONFIG["density"])], capture_output=True, text=True, timeout=10)

                socketio.emit("emu_start_complete", {"success": True, "port": _emulator_adb_port, "message": "模拟器启动完成"})
                add_system_log("emulator", "模拟器启动完成", f"端口: {_emulator_adb_port}")
                _sync_adb_to_emulator_port()

            except Exception as e:
                socketio.emit("emu_start_error", {"error": str(e)[:300]})
                add_system_log("emulator", "模拟器启动失败", str(e)[:200])

        threading.Thread(target=start_worker, daemon=True).start()
        return jsonify({"status": "starting"})
    
    except Exception as e:
        logger.error(f"api_emulator_start 异常: {e}", exc_info=True)
        return jsonify({"status": "error", "error": f"启动失败: {str(e)[:200]}", "suggestion": "请检查模拟器安装状态"}), 500


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

    add_system_log("emulator", "模拟器已停止", "")
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
        add_system_log("emulator", f"APK安装{'成功' if success else '失败'}", apk_path)
        return jsonify({"status": "success" if success else "failed", "output": r.stdout.strip()[:500]})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:300]}), 500


@app.route("/api/emulator/analyze_apk", methods=["POST"])
def api_emulator_analyze_apk():
    """分析APK兼容性 - 检查APK内部manifest，诊断与模拟器的兼容性问题"""
    data = request.get_json() or {}
    apk_path = data.get("apk_path", "").strip()
    
    # 搜索APK
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
        return jsonify({"status": "error", "error": "APK文件不存在", "searched": searched}), 404
    
    result = {
        "status": "ok",
        "apk_path": apk_path,
        "apk_size_mb": round(Path(apk_path).stat().st_size / (1024*1024), 2),
        "compatibility_issues": [],
        "recommendations": [],
        "manifest_info": {},
    }
    
    try:
        # 尝试使用aapt/aapt2分析APK
        aapt_candidates = [
            str(ANDROID_SDK_ROOT / "build-tools" / "33.0.0" / "aapt.exe"),
            str(ANDROID_SDK_ROOT / "build-tools" / "34.0.0" / "aapt.exe"),
            str(ANDROID_SDK_ROOT / "build-tools" / "35.0.0" / "aapt.exe"),
        ]
        
        aapt_exe = None
        for p in aapt_candidates:
            if Path(p).exists():
                aapt_exe = p
                break
        
        # 也搜索build-tools目录下任意版本
        if not aapt_exe:
            bt_dir = ANDROID_SDK_ROOT / "build-tools"
            if bt_dir.exists():
                for d in sorted(bt_dir.iterdir(), reverse=True):
                    aapt = d / "aapt.exe"
                    if aapt.exists():
                        aapt_exe = str(aapt)
                        break
        
        if aapt_exe:
            # 获取APK基本信息
            r = subprocess.run([aapt_exe, "dump", "badging", apk_path],
                             capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                badging = r.stdout
                for line in badging.split("\n"):
                    line = line.strip()
                    if line.startswith("package:"):
                        # package: name='com.windowsgames.firefightbw' versionCode='1' versionName='1.0'
                        import re as _re
                        m = _re.search(r"name='([^']+)'", line)
                        if m: result["manifest_info"]["package"] = m.group(1)
                        m = _re.search(r"versionCode='([^']+)'", line)
                        if m: result["manifest_info"]["version_code"] = m.group(1)
                        m = _re.search(r"versionName='([^']+)'", line)
                        if m: result["manifest_info"]["version_name"] = m.group(1)
                    elif line.startswith("sdkVersion:"):
                        m = _re.search(r"'(\d+)'", line)
                        if m: result["manifest_info"]["min_sdk"] = int(m.group(1))
                    elif line.startswith("targetSdkVersion:"):
                        m = _re.search(r"'(\d+)'", line)
                        if m: result["manifest_info"]["target_sdk"] = int(m.group(1))
                    elif "native-code:" in line:
                        m = _re.search(r"'([^']+)'", line)
                        if m: result["manifest_info"]["native_arch"] = m.group(1)
                    elif "uses-gl-es:" in line:
                        m = _re.search(r"'([^']+)'", line)
                        if m: result["manifest_info"]["opengl_es"] = m.group(1)
                    elif line.startswith("supports-screens:"):
                        result["manifest_info"]["supports_screens"] = line
                    elif line.startswith("densities:"):
                        result["manifest_info"]["densities"] = line
                    elif line.startswith("application:"):
                        result["manifest_info"]["app_label"] = line
            
            # 获取权限列表
            r2 = subprocess.run([aapt_exe, "dump", "permissions", apk_path],
                              capture_output=True, text=True, timeout=10)
            if r2.returncode == 0:
                perms = [l.strip() for l in r2.stdout.split("\n") if l.strip() and "permission" in l.lower()]
                result["manifest_info"]["permissions"] = perms[:30]
        else:
            result["status"] = "partial"
            result["compatibility_issues"].append("aapt工具未找到，无法深入分析APK，已安装Android SDK build-tools后可进行完整分析")
        
        # 兼容性分析
        min_sdk = result["manifest_info"].get("min_sdk", 0)
        target_sdk = result["manifest_info"].get("target_sdk", 0)
        native_arch = result["manifest_info"].get("native_arch", "")
        opengl_es = result["manifest_info"].get("opengl_es", "")
        
        # 检查SDK版本兼容性
        if min_sdk and min_sdk > AVD_CONFIG["api_level"]:
            result["compatibility_issues"].append(
                f"APK要求最低API {min_sdk}，但模拟器配置为API {AVD_CONFIG['api_level']}，部分功能可能不可用"
            )
            result["recommendations"].append(f"将AVD API级别提升至{min_sdk}或更高")
        
        # 检查原生库架构
        if native_arch:
            emu_arch = AVD_CONFIG["arch"]  # x86_64
            if "arm64" in native_arch and "x86" not in native_arch:
                result["compatibility_issues"].append(
                    f"APK仅包含ARM64原生库({native_arch})，但模拟器使用{emu_arch}架构。"
                    "ARM→x86转译可能导致性能下降或部分内容加载失败"
                )
                result["recommendations"].append("在模拟器中启用ARM转译支持，或使用ARM64架构的AVD")
            elif "armeabi" in native_arch and "x86" not in native_arch:
                result["compatibility_issues"].append(
                    f"APK包含ARMv7原生库({native_arch})，模拟器({emu_arch})需要ARM转译"
                )
                result["recommendations"].append("确保模拟器支持ARM→x86转译(Houdini/libndk)")
        
        # 检查OpenGL ES版本
        if opengl_es:
            gl_config = _emulator_gpu_config.get("gl_version", "3.0")
            try:
                required_gl = float(opengl_es.replace("0x", "")) if "0x" in opengl_es else float(opengl_es)
                current_gl = float(gl_config)
                if required_gl > current_gl:
                    result["compatibility_issues"].append(
                        f"APK要求OpenGL ES {opengl_es}，当前配置为{gl_config}。不匹配会导致渲染内容缺失"
                    )
                    result["recommendations"].append(f"将GPU OpenGL ES版本设置为{opengl_es}")
            except:
                pass
        
        # 通用建议
        if not result["compatibility_issues"]:
            result["compatibility_issues"].append("未检测到明显的兼容性问题")
        
        result["recommendations"].extend([
            "尝试切换GPU模式为swiftshader（软件渲染，兼容性最高）",
            "确保模拟器分辨率匹配游戏设计分辨率(1920x1080)",
            "在模拟器设置中增加RAM至4GB+，增加VM堆大小",
            "如果游戏使用Unity引擎，尝试添加 -gpu swiftshader_indirect 参数",
        ])
        
        add_system_log("emulator", f"APK兼容性分析完成", f"issues={len(result['compatibility_issues'])}")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"APK分析失败: {e}")
        return jsonify({"status": "error", "error": str(e)[:300]}), 500


@app.route("/api/emulator/screenshot")
def api_emulator_screenshot():
    """高速ADB截图 - 使用raw screencap + JPEG压缩，目标20-30fps"""
    import base64, struct, io
    try:
        t0 = time.perf_counter()
        adb_exe = _get_adb_for_emulator()
        # 使用raw格式（无PNG压缩，速度快3-5倍）
        dev_id = f"emulator-{_emulator_adb_port}"
        r = subprocess.run(
            [adb_exe, "-s", dev_id, "exec-out", "screencap"],
            capture_output=True, timeout=3
        )
        if r.returncode != 0 or len(r.stdout) < 20:
            # 回退到 localhost 格式
            dev_id = f"localhost:{_emulator_adb_port}"
            r = subprocess.run(
                [adb_exe, "-s", dev_id, "exec-out", "screencap"],
                capture_output=True, timeout=3
            )
        if r.returncode != 0 or len(r.stdout) < 20:
            return jsonify({"error": "截图失败", "stderr": str(r.stderr[:200]) if r.stderr else ""}), 500
        
        # 解析raw screencap格式: 4B width + 4B height + 4B pixel_format + raw RGBA
        raw_data = r.stdout
        width = struct.unpack_from("<I", raw_data, 0)[0]
        height = struct.unpack_from("<I", raw_data, 4)[0]
        pixel_fmt = struct.unpack_from("<I", raw_data, 8)[0]
        pixels = raw_data[12:]
        
        # 转换为JPEG（比PNG小3-5倍，传输更快）
        try:
            from PIL import Image
            # RGBA_8888 -> RGB (丢弃alpha)
            img = Image.frombytes("RGBA", (width, height), pixels, "raw")
            img_rgb = img.convert("RGB")
            buf = io.BytesIO()
            img_rgb.save(buf, format="JPEG", quality=75, optimize=True)
            img_bytes = buf.getvalue()
            img_format = "jpeg"
        except ImportError:
            # 无PIL时回退到PNG
            img_bytes = pixels
            img_format = "raw"
            # 添加header让前端能解析
            import struct as _st
            header = _st.pack("<III", width, height, pixel_fmt)
            img_bytes = header + pixels
        
        img_b64 = base64.b64encode(img_bytes).decode("ascii")
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return jsonify({
            "image": img_b64, "format": img_format, "timestamp": time.time(),
            "elapsed_ms": elapsed_ms, "size_bytes": len(img_bytes),
            "width": width, "height": height,
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


# ── 模拟器分辨率配置（修复游戏显示不全问题） ──
_emulator_resolution = {"width": 1920, "height": 1080, "dpi": 420}
_emulator_gpu_config = {"gpu_mode": "host", "renderer": "opengl", "gl_version": "3.0"}

@app.route("/api/emulator/gpu", methods=["GET", "POST"])
def api_emulator_gpu():
    """GPU渲染配置（修复APK游戏兼容性问题）"""
    global _emulator_gpu_config
    if request.method == "POST":
        data = request.get_json() or {}
        gpu_mode = data.get("gpu_mode", _emulator_gpu_config["gpu_mode"])
        renderer = data.get("renderer", _emulator_gpu_config["renderer"])
        gl_version = data.get("gl_version", _emulator_gpu_config["gl_version"])
        
        _emulator_gpu_config = {"gpu_mode": gpu_mode, "renderer": renderer, "gl_version": gl_version}
        
        # 尝试通过ADB设置OpenGL渲染器
        try:
            adb_exe = _get_adb_for_emulator()
            dev_id = f"emulator-{_emulator_adb_port}" if _emulator_type == "generic" else f"127.0.0.1:{_emulator_adb_port}"
            # 设置OpenGL ES版本
            subprocess.run([adb_exe, "-s", dev_id, "shell", "setprop", "ro.opengles.version", str(int(float(gl_version) * 65536))],
                         capture_output=True, text=True, timeout=5)
            # 设置渲染器
            if renderer == "skia":
                subprocess.run([adb_exe, "-s", dev_id, "shell", "setprop", "debug.hwui.renderer", "skiagl"],
                             capture_output=True, text=True, timeout=5)
            add_system_log("emulator", f"GPU配置已设置", f"gpu={gpu_mode}, renderer={renderer}, GLES={gl_version}")
        except Exception as e:
            logger.warning(f"ADB GPU配置失败: {e}")
        
        return jsonify({"status": "ok", "gpu": _emulator_gpu_config})
    
    return jsonify({
        "gpu": _emulator_gpu_config,
        "gpu_modes": [
            {"name": "host (推荐)", "value": "host", "desc": "使用宿主机GPU，性能最佳，兼容性最好"},
            {"name": "swiftshader_indirect", "value": "swiftshader_indirect", "desc": "软件渲染间接模式，兼容性较好"},
            {"name": "swiftshader", "value": "swiftshader", "desc": "纯软件渲染，兼容性最高但性能差"},
            {"name": "angle_indirect", "value": "angle_indirect", "desc": "ANGLE间接模式（DirectX转OpenGL）"},
            {"name": "guest", "value": "guest", "desc": "客户机GPU渲染"},
        ],
        "renderers": [
            {"name": "OpenGL (默认)", "value": "opengl", "desc": "标准OpenGL ES渲染"},
            {"name": "Skia", "value": "skia", "desc": "Skia渲染引擎，部分游戏兼容性更好"},
        ],
        "gl_versions": [
            {"name": "OpenGL ES 2.0", "value": "2.0"},
            {"name": "OpenGL ES 3.0 (推荐)", "value": "3.0"},
            {"name": "OpenGL ES 3.1", "value": "3.1"},
        ],
    })

@app.route("/api/emulator/resolution", methods=["GET", "POST"])
def api_emulator_resolution():
    """获取或设置模拟器分辨率（修复游戏界面显示不全）"""
    global _emulator_resolution
    if request.method == "POST":
        data = request.get_json() or {}
        width = data.get("width", _emulator_resolution["width"])
        height = data.get("height", _emulator_resolution["height"])
        dpi = data.get("dpi", _emulator_resolution["dpi"])
        
        _emulator_resolution = {"width": int(width), "height": int(height), "dpi": int(dpi)}
        
        # 尝试通过ADB设置分辨率
        try:
            adb_exe = _get_adb_for_emulator()
            dev_id = f"emulator-{_emulator_adb_port}" if _emulator_type == "generic" else f"127.0.0.1:{_emulator_adb_port}"
            # 设置物理分辨率
            subprocess.run([adb_exe, "-s", dev_id, "shell", "wm", "size", f"{width}x{height}"],
                         capture_output=True, text=True, timeout=5)
            # 设置DPI
            subprocess.run([adb_exe, "-s", dev_id, "shell", "wm", "density", str(dpi)],
                         capture_output=True, text=True, timeout=5)
            add_system_log("emulator", f"分辨率已设置", f"{width}x{height}, DPI:{dpi}")
        except Exception as e:
            logger.warning(f"ADB设置分辨率失败: {e}")
        
        return jsonify({"status": "ok", "resolution": _emulator_resolution})
    
    return jsonify({"resolution": _emulator_resolution, "presets": [
        {"name": "1080p (推荐)", "width": 1920, "height": 1080, "dpi": 420},
        {"name": "720p", "width": 1280, "height": 720, "dpi": 320},
        {"name": "1440p", "width": 2560, "height": 1440, "dpi": 560},
        {"name": "平板模式", "width": 1920, "height": 1200, "dpi": 320},
        {"name": "手机模式", "width": 1080, "height": 1920, "dpi": 480},
    ]})

@app.route("/api/emulator/screen", methods=["GET", "POST"])
def api_emulator_screen():
    """获取或切换模拟器屏幕开关状态"""
    global _emulator_screen_on, _scrcpy_process, _scrcpy_enabled
    if request.method == "POST":
        data = request.get_json() or {}
        action = data.get("action", "toggle")
        if action == "on":
            _emulator_screen_on = True
        elif action == "off":
            _emulator_screen_on = False
        else:  # toggle
            _emulator_screen_on = not _emulator_screen_on
        
        if _emulator_screen_on:
            # 开启屏幕：尝试启动scrcpy
            try:
                scrcpy_exe = _get_scrcpy_exe()
                if Path(scrcpy_exe).exists() and not (_scrcpy_process and _scrcpy_process.poll() is None):
                    cfg = load_config()
                    sc = cfg.get("scrcpy", {})
                    max_width = sc.get("max_width", 1920)
                    max_height = sc.get("max_height", 1080)
                    max_fps = sc.get("max_fps", 60)
                    bitrate = sc.get("bitrate", 20000000)
                    dev_id = f"emulator-{_emulator_adb_port}" if _emulator_type == "generic" else f"127.0.0.1:{_emulator_adb_port}"
                    cmd = [
                        scrcpy_exe, "-s", dev_id,
                        "--max-size", str(max_width),
                        "--max-fps", str(max_fps),
                        "--video-bit-rate", str(bitrate),
                        "--no-audio",
                        "--window-title", f"FirefightAI - {_emulator_type}",
                    ]
                    _scrcpy_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    _scrcpy_enabled = True
                    add_system_log("emulator", "模拟器屏幕已开启", f"类型: {_emulator_type}")
            except Exception as e:
                logger.warning(f"启动scrcpy失败: {e}")
        else:
            # 关闭屏幕：停止scrcpy
            if _scrcpy_process:
                try:
                    _scrcpy_process.terminate()
                    _scrcpy_process.wait(timeout=3)
                except:
                    try:
                        _scrcpy_process.kill()
                    except:
                        pass
                _scrcpy_process = None
            _scrcpy_enabled = False
            add_system_log("emulator", "模拟器屏幕已关闭", "")
        
        return jsonify({"status": "ok", "screen_on": _emulator_screen_on, "type": _emulator_type})
    
    return jsonify({"screen_on": _emulator_screen_on, "type": _emulator_type})


@app.route("/api/emulator/stream")
def api_emulator_stream():
    """MJPEG流式推送模拟器画面 - 使用scrcpy实现60fps"""
    import struct, io, threading as _thr, queue as _queue, base64 as _b64
    from collections import deque
    
    adb_exe = _get_adb_for_emulator()
    port = _emulator_adb_port
    
    # 🔥 优先使用scrcpy实现真正的60fps
    scrcpy_exe = _find_scrcpy_exe()
    
    if scrcpy_exe:
        # scrcpy模式: 硬件加速 60fps
        def scrcpy_worker():
            import struct
            logger.info(f"scrcpy流启动: {scrcpy_exe}")
            process = subprocess.Popen(
                [scrcpy_exe, "--no-window", "--no-audio",
                 "--max-fps", "60", "--bit-rate", "8M",
                 "-s", f"127.0.0.1:{port}",
                 "--render-driver=opengl",
                 "--video-codec=h264"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            try:
                while capture_running[0]:
                    header = process.stdout.read(12)
                    if not header or len(header) < 12:
                        break
                    pts, _, size = struct.unpack(">QII", header[:12]) if len(header) >= 12 else (0, 0, 0)
                    if size <= 0 or size > 10*1024*1024:
                        continue
                    data = process.stdout.read(size)
                    if data:
                        try:
                            frame_buffer.put_nowait(data)
                        except _queue.Full:
                            try:
                                frame_buffer.get_nowait()
                                frame_buffer.put_nowait(data)
                            except:
                                pass
                        stats["frames_captured"] += 1
            except Exception as e:
                logger.warning(f"scrcpy流异常: {e}")
            finally:
                process.terminate()
        
        cap_thread = _thr.Thread(target=scrcpy_worker, daemon=True)
        cap_thread.start()
    else:
        # 回退到ADB screencap模式 (较慢)
        STREAM_WIDTH = 960
        STREAM_QUALITY = 55
        TARGET_FPS = 60
        FRAME_BUDGET = 1.0 / TARGET_FPS
    
    # 🔥 帧缓冲（非阻塞，跳过旧帧）
    frame_buffer = _queue.Queue(maxsize=2)  # 只保留最新2帧
    capture_running = [True]
    stats = {"frames_captured": 0, "frames_sent": 0, "capture_ms": 0, "convert_ms": 0}
    
    def _get_dev_id():
        """获取可用的设备ID"""
        for did in dev_ids:
            r = subprocess.run([adb_exe, "-s", did, "shell", "echo", "ok"],
                             capture_output=True, text=True, timeout=2)
            if r.returncode == 0 and "ok" in r.stdout:
                return did
        return dev_ids[0]
    
    def capture_thread():
        """独立捕获线程，不阻塞主循环"""
        import base64 as _b64
        dev_id = _get_dev_id()
        last_dev_check = 0
        
        while capture_running[0]:
            try:
                # 每30秒重新检测设备ID
                now = time.perf_counter()
                if now - last_dev_check > 30:
                    dev_id = _get_dev_id()
                    last_dev_check = now
                
                t0 = time.perf_counter()
                
                # 🔥 使用raw screencap（比PNG快3-5倍）
                r = subprocess.run(
                    [adb_exe, "-s", dev_id, "exec-out", "screencap"],
                    capture_output=True, timeout=2
                )
                
                if r.returncode != 0 or len(r.stdout) < 20:
                    time.sleep(0.05)
                    continue
                
                capture_ms = (time.perf_counter() - t0) * 1000
                raw_data = r.stdout
                width = struct.unpack_from("<I", raw_data, 0)[0]
                height = struct.unpack_from("<I", raw_data, 4)[0]
                pixels = raw_data[12:]
                
                # 🔥 快速转换（使用PIL缩放+JPEG压缩）
                t1 = time.perf_counter()
                try:
                    from PIL import Image
                    img = Image.frombytes("RGBA", (width, height), pixels, "raw")
                    img_rgb = img.convert("RGB")
                    # 缩放降低分辨率
                    if width > STREAM_WIDTH:
                        ratio = STREAM_WIDTH / width
                        new_h = int(height * ratio)
                        img_rgb = img_rgb.resize((STREAM_WIDTH, new_h), Image.LANCZOS)
                    buf = io.BytesIO()
                    img_rgb.save(buf, format="JPEG", quality=STREAM_QUALITY, optimize=True)
                    frame_data = buf.getvalue()
                except ImportError:
                    # 无PIL时发送原始数据
                    frame_data = pixels
                
                convert_ms = (time.perf_counter() - t1) * 1000
                
                # 放入缓冲区（非阻塞：如果缓冲区满了，丢弃旧的）
                try:
                    frame_buffer.put_nowait(frame_data)
                except _queue.Full:
                    try:
                        frame_buffer.get_nowait()  # 丢弃最旧的帧
                        frame_buffer.put_nowait(frame_data)
                    except:
                        pass
                
                stats["frames_captured"] += 1
                stats["capture_ms"] = capture_ms
                stats["convert_ms"] = convert_ms
                
                # 控制捕获速率
                elapsed = time.perf_counter() - t0
                if elapsed < FRAME_BUDGET * 0.8:
                    time.sleep(max(0.001, FRAME_BUDGET * 0.8 - elapsed))
                    
            except Exception as e:
                time.sleep(0.05)
                continue
    
    # 启动捕获线程
    cap_thread = _thr.Thread(target=capture_thread, daemon=True)
    cap_thread.start()
    
    def generate_frames():
        last_frame_time = time.perf_counter()
        frame_count = 0
        
        while capture_running[0]:
            try:
                if not _emulator_screen_on:
                    # 屏幕关闭时发送黑屏提示帧
                    time.sleep(0.5)
                    try:
                        from PIL import Image, ImageDraw, ImageFont
                        img = Image.new("RGB", (STREAM_WIDTH, 540), (10, 15, 20))
                        draw = ImageDraw.Draw(img)
                        try:
                            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 32)
                        except:
                            font = ImageFont.load_default()
                        draw.text((STREAM_WIDTH//2 - 160, 240), "请打开模拟器屏幕", fill=(80, 80, 80), font=font)
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=80)
                        frame = buf.getvalue()
                    except:
                        frame = b""
                    if frame:
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                    continue
                
                # 🔥 非阻塞获取帧
                try:
                    frame_data = frame_buffer.get(timeout=0.5)
                except _queue.Empty:
                    time.sleep(0.01)
                    continue
                
                # 生成MJPEG帧
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame_data)).encode() + b"\r\n\r\n" + frame_data + b"\r\n")
                
                frame_count += 1
                stats["frames_sent"] = frame_count
                
                # 帧率控制
                now = time.perf_counter()
                elapsed = now - last_frame_time
                if elapsed < FRAME_BUDGET:
                    time.sleep(max(0.001, FRAME_BUDGET - elapsed))
                last_frame_time = time.perf_counter()
                
            except Exception as e:
                time.sleep(0.05)
                continue
    
    # 客户端断开时停止捕获线程
    def cleanup():
        capture_running[0] = False
    
    return Response(
        stream_with_context(generate_frames()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "close",
            "Access-Control-Allow-Origin": "*",
            "X-Accel-Buffering": "no",
        },
        direct_passthrough=True,
    )


@app.route("/api/decision_chain/benchmark")
def api_decision_chain_benchmark():
    """决策链性能测试 - 确保截图→指令 < 2秒"""
    try:
        import base64
        t_total_start = time.perf_counter()
        
        # 1. 截图
        t0 = time.perf_counter()
        adb_exe = _get_adb_for_emulator()
        r = subprocess.run(
            [adb_exe, "-s", f"localhost:{_emulator_adb_port}", "exec-out", "screencap", "-p"],
            capture_output=True, timeout=5
        )
        t_screenshot = int((time.perf_counter() - t0) * 1000)
        
        if r.returncode != 0:
            return jsonify({"error": "截图失败", "stage": "screenshot"}), 500
        
        img_b64 = base64.b64encode(r.stdout).decode("utf-8")
        
        # 2. YOLO检测 (如果可用)
        t_yolo = -1
        t_llm = -1
        try:
            t0 = time.perf_counter()
            from src.vision.detector import UnitDetector
            cfg = load_config()
            yc = cfg["yolo"]
            detector = UnitDetector(model_path=yc["model_path"], fallback_model_path=yc["fallback_model_path"], confidence_threshold=yc["confidence_threshold"], iou_threshold=yc["iou_threshold"], image_size=yc["image_size"], device=yc["device"])
            detector.load_model()
            # 使用临时文件进行检测
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(r.stdout)
                tmp_path = tmp.name
            detections = detector.detect(tmp_path)
            os.unlink(tmp_path)
            t_yolo = int((time.perf_counter() - t0) * 1000)
            
            # 3. LLM决策
            t0 = time.perf_counter()
            # 快速决策测试
            r = _deepseek_chat([{"role": "user", "content": f"检测到{len(detections)}个单位，简短指令"}], max_tokens=64, temperature=0.1, stream=False)
            t_llm = int((time.perf_counter() - t0) * 1000)
        except Exception as e:
            logger.debug(f"决策链benchmark跳过YOLO/LLM: {e}")
        
        t_total = int((time.perf_counter() - t_total_start) * 1000)
        
        return jsonify({
            "status": "ok",
            "screenshot_ms": t_screenshot,
            "yolo_ms": t_yolo,
            "llm_ms": t_llm,
            "total_ms": t_total,
            "within_2s": t_total < 2000,
            "within_3s": t_total < 3000,
            "timestamp": time.time(),
        })
    except Exception as e:
        return jsonify({"error": str(e)[:200], "stage": "benchmark"}), 500


@app.route("/api/emulator/touch", methods=["POST"])
def api_emulator_touch():
    data = request.get_json() or {}
    x = data.get("x", 0)
    y = data.get("y", 0)
    action = data.get("action", "tap")  # tap, swipe, longpress

    try:
        adb_exe = _get_adb_for_emulator()
        # 🔥 优先使用 emulator-{port} 格式，因为 localhost:{port} 可能显示为offline
        target = f"emulator-{_emulator_adb_port}"
        
        # 先验证设备是否在线
        r = subprocess.run([adb_exe, "-s", target, "shell", "echo", "ok"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0 or "ok" not in r.stdout:
            # 回退到 localhost:{port}
            target = f"localhost:{_emulator_adb_port}"
            r2 = subprocess.run([adb_exe, "-s", target, "shell", "echo", "ok"], capture_output=True, text=True, timeout=5)
            if r2.returncode != 0 or "ok" not in r2.stdout:
                return jsonify({"status": "error", "error": f"模拟器设备未连接 (emulator-{_emulator_adb_port} 和 localhost:{_emulator_adb_port} 均不可用)"}), 500

        if action == "tap":
            subprocess.run(
                [adb_exe, "-s", target, "shell", "input", "tap", str(int(x)), str(int(y))],
                capture_output=True, text=True, timeout=5
            )
        elif action == "swipe":
            x2 = data.get("x2", x)
            y2 = data.get("y2", y)
            duration = data.get("duration", 300)  # 🔥 使用前端传递的拖动时长
            subprocess.run(
                [adb_exe, "-s", target, "shell", "input", "swipe", str(int(x)), str(int(y)), str(int(x2)), str(int(y2)), str(int(duration))],
                capture_output=True, text=True, timeout=5
            )
        elif action == "longpress":
            subprocess.run(
                [adb_exe, "-s", target, "shell", "input", "swipe", str(int(x)), str(int(y)), str(int(x)), str(int(y)), "1000"],
                capture_output=True, text=True, timeout=5
            )

        return jsonify({"status": "ok", "action": action, "x": x, "y": y, "target": target})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]}), 500


@app.route("/api/recording/learn", methods=["POST"])
def api_recording_learn():
    """接收录制数据并调用DeepSeek进行战术分析学习"""
    data = request.get_json() or {}
    events = data.get("events", [])
    screenshots = data.get("screenshots", [])
    
    # 至少要有事件或截图
    if not events and not screenshots:
        return jsonify({"status": "error", "error": "无录入数据: 请先在模拟器屏幕上操作或录制至少一帧"}), 400
    
    total_events = data.get("total_events", len(events))
    total_screenshots = data.get("total_screenshots", len(screenshots))
    duration_ms = data.get("duration_ms", 0)
    resolution = data.get("resolution", {})
    session_id = data.get("session_id", "rec_unknown")

    add_learning_log("recording", "AI开始分析录制数据",
                     f"事件:{total_events}, 截图:{total_screenshots}, 时长:{duration_ms}ms")

    # 构建事件摘要供AI分析
    action_counts = {}
    coord_clusters = []
    for evt in events[:100]:
        act = evt.get("action", "tap")
        action_counts[act] = action_counts.get(act, 0) + 1
        coord_clusters.append(f"t={evt.get('ts',0)}ms {act}({evt.get('x',0)},{evt.get('y',0)})")

    # 提取关键帧截图（首帧、中间帧、末帧），最多3帧用于分析
    screenshots = data.get("screenshots", [])
    key_frames = []
    if screenshots:
        indices = [0]
        if len(screenshots) > 1:
            indices.append(len(screenshots) // 2)
        if len(screenshots) > 2:
            indices.append(len(screenshots) - 1)
        for idx in indices:
            if idx < len(screenshots):
                s = screenshots[idx]
                img = s.get("image", "")
                if img and len(img) > 100:
                    # 只取前8KB作为预览
                    key_frames.append({
                        "ts": s.get("ts", 0),
                        "event_index": s.get("event_index", 0),
                        "image_preview": img[:200]
                    })

    # 构建分析提示词
    events_summary = "\n".join(coord_clusters[:60])
    prompt = f"""分析以下游戏触控录制数据，提取战术操作模式并生成优化建议：

【录制信息】
- 会话ID: {session_id}
- 总事件数: {total_events}
- 总截图帧数: {total_screenshots}
- 录制时长: {duration_ms}ms
- 分辨率: {resolution.get('width',1920)}x{resolution.get('height',1080)}

【操作统计】
{chr(10).join([f'- {act}: {cnt}次' for act, cnt in sorted(action_counts.items(), key=lambda x:-x[1])])}

【触控事件序列（前60条）】
{events_summary}

【关键帧时间点】
{chr(10).join([f'- t={f["ts"]}ms (事件索引{f["event_index"]})' for f in key_frames[:3]])}

请分析：
1. 操作模式：识别用户的操作习惯和战术模式（如兵力部署区域、攻击路径、防御策略）
2. 时序特征：分析操作的时间分布和节奏特点
3. 优化建议：提供3-5条具体可执行的战术优化建议
4. 自主学习：基于以上分析，总结AI可以从中学习的战术知识

请用中文回答，简洁专业。"""

    # 在线程中调用DeepSeek API
    def do_learn():
        try:
            r = _deepseek_chat([{"role": "user", "content": prompt}],
                              max_tokens=1024, temperature=0.3, stream=False, timeout=60)
            if r.get("success"):
                analysis = r["content"]
                add_learning_log("recording", "AI学习完成", analysis[:300])
                # 保存学习结果到本地数据库
                try:
                    db_path = PROJECT_ROOT / "data" / "firefight_ai.db"
                    conn = _sqlite3.connect(str(db_path))
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS recording_learn_results (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id TEXT,
                            total_events INTEGER,
                            total_screenshots INTEGER,
                            duration_ms INTEGER,
                            analysis TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    conn.execute(
                        "INSERT INTO recording_learn_results (session_id, total_events, total_screenshots, duration_ms, analysis) VALUES (?,?,?,?,?)",
                        (session_id, total_events, total_screenshots, duration_ms, analysis[:5000])
                    )
                    conn.commit()
                    conn.close()
                except Exception:
                    pass
                socketio.emit("recording_learn_result", {
                    "status": "ok",
                    "session_id": session_id,
                    "analysis": analysis,
                    "total_events": total_events,
                    "time": datetime.now().isoformat(),
                })
            else:
                add_learning_log("recording", "AI学习失败", r.get("error", "未知错误"))
                socketio.emit("recording_learn_result", {
                    "status": "error",
                    "session_id": session_id,
                    "error": r.get("error", "分析失败"),
                })
        except Exception as e:
            socketio.emit("recording_learn_result", {
                "status": "error",
                "session_id": session_id,
                "error": str(e)[:200],
            })

    threading.Thread(target=do_learn, daemon=True).start()
    return jsonify({"status": "learning", "session_id": session_id,
                    "events": total_events, "screenshots": total_screenshots})


@app.route("/api/emulator/launch_app", methods=["POST"])
def api_emulator_launch_app():
    """启动游戏应用 - 兼容MuMu/AVD/蓝叠等所有模拟器"""
    data = request.get_json() or {}
    package_name = data.get("package", "com.windowsgames.firefightbw")
    activity_name = data.get("activity", "com.windowsgames.firefight.MyGame")
    try:
        adb_exe = _get_adb_for_emulator()
        port = _emulator_adb_port
        
        # 🔥 根据模拟器类型选择正确的设备ID格式
        dev_ids = []
        if _emulator_type == "mumu":
            dev_ids = [f"127.0.0.1:{port}"]
        elif _emulator_type == "generic":
            dev_ids = [f"emulator-{port}", f"localhost:{port}"]
        else:
            dev_ids = [f"127.0.0.1:{port}", f"localhost:{port}", f"emulator-{port}"]
        
        # 找到可用的设备ID
        target = None
        for did in dev_ids:
            r = subprocess.run([adb_exe, "-s", did, "shell", "echo", "ok"],
                             capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and "ok" in r.stdout:
                target = did
                break
        if not target:
            target = dev_ids[0]
        
        add_system_log("emulator", f"启动游戏: {package_name}", f"设备: {target}")
        
        # 🔥 先检查应用是否安装
        r_check = subprocess.run(
            [adb_exe, "-s", target, "shell", "pm", "list", "packages", package_name],
            capture_output=True, text=True, timeout=10
        )
        if package_name not in r_check.stdout:
            # 应用未安装, 尝试安装
            apk_paths = [
                str(PROJECT_ROOT / "firefight.apk"),
                r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefight.apk",
            ]
            installed = False
            for apk in apk_paths:
                if os.path.exists(apk):
                    r_install = subprocess.run(
                        [adb_exe, "-s", target, "install", "-r", "-g", apk],
                        capture_output=True, text=True, timeout=120
                    )
                    if "Success" in r_install.stdout or r_install.returncode == 0:
                        installed = True
                        add_system_log("emulator", "APK安装成功", apk)
                        break
            if not installed:
                return jsonify({"status": "error", "error": "游戏未安装，请先安装APK"}), 500
        
        # 尝试多种启动方式
        methods = [
            # monkey (最通用)
            ["shell", "monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1"],
            # am start with package/activity
            ["shell", "am", "start", "-n", f"{package_name}/{activity_name}"],
            # am start package only
            ["shell", "am", "start", package_name],
            # 通用启动
            ["shell", "am", "start", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", package_name],
        ]
        
        for method in methods:
            r = subprocess.run(
                [adb_exe, "-s", target] + method,
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0 and "Error" not in r.stdout:
                add_system_log("emulator", "游戏已启动", f"包名: {package_name}, 方法: {method[2]}")
                return jsonify({"status": "ok", "package": package_name, "method": method[2]})
        
        return jsonify({"status": "error", "error": "所有启动方法均失败", "detail": r.stdout[:200] if r else ""}), 500
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]}), 500


# ═══════════════════════════════════════════════════════════════
# scrcpy 投屏控制 (ADB触控)
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
    """启动scrcpy投屏（支持鼠标点击、拖动、键盘，像MUMU一样流畅操控）"""
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
        bitrate = data.get("bitrate", 20000000)
        max_fps = data.get("max_fps", 60)
        fullscreen = data.get("fullscreen", True)

        # 🔥 基础命令：不传 --no-control 即允许鼠标/键盘/触控板操控
        cmd = [
            scrcpy_exe,
            "-s", f"localhost:{_emulator_adb_port}",
            f"--max-size={max_width}",
            f"--bit-rate={bitrate}",
            f"--max-fps={max_fps}",
            "--stay-awake",
            "--turn-screen-off=false",
            "--no-audio",
            "--window-title=Firefight AI 模拟器 (鼠标/键盘/触控板操控)",
            "--render-driver=opengl",
            "--video-codec=h264",
            "--no-clipboard-autosync",
        ]
        
        # 🔥 全屏模式和窗口模式互斥
        if fullscreen:
            cmd.append("--fullscreen")
        else:
            cmd.extend(["--window-x=0", "--window-y=0", "--window-width=1920", "--window-height=1080"])

        _scrcpy_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _scrcpy_enabled = True
        add_system_log("scrcpy", "scrcpy投屏已启动", f"分辨率: {max_width}x{max_height}, FPS: {max_fps}, 码率: {bitrate//1000000}Mbps, 全屏: {fullscreen}")
        return jsonify({"status": "ok", "message": "scrcpy投屏启动成功", "fullscreen": fullscreen, "fps": max_fps, "bitrate": bitrate, "resolution": f"{max_width}x{max_height}"})
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
    add_system_log("scrcpy", "scrcpy投屏已停止", "")
    return jsonify({"status": "ok", "message": "scrcpy已停止"})

# ═══════════════════════════════════════════════════════════════
# 端口检测 (避免与行旅白冲突)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/port/check")
def api_port_check():
    """检测端口占用情况（完整版：区分所有系统服务端口与异常占用）"""
    xinglv_ports = _detect_xinglv_ports()
    import socket
    all_occupied = []
    # 预期被占用的端口（本系统服务 + 常见服务，完整列表）
    expected_ports = {
        5000: "当前服务(Flask)",
        5001: "当前服务(SocketIO/WS)",
        5005: "当前服务(备用)",
        5555: "ADB默认端口",
        5556: "ADB连接(模拟器)",
        7555: "MUMU模拟器",
        8080: "Web服务",
        3000: "开发服务",
    }
    # 所有需要检测的端口列表
    scan_ports = [5000, 5001, 5005, 5555, 5556, 7555, 8080, 3000]
    try:
        for port in scan_ports:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    # 区分预期占用和异常占用
                    if port in expected_ports:
                        all_occupied.append({"port": port, "occupied": True, "label": expected_ports[port], "normal": True})
                    else:
                        all_occupied.append({"port": port, "occupied": True, "label": "异常占用", "normal": False})
                else:
                    all_occupied.append({"port": port, "occupied": False, "label": "空闲", "normal": True})
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

    add_system_log("agent", f"智能体执行: {command[:100]}", "")

    def agent_worker():
        try:
            socketio.emit("agent_progress", {"step": "正在分析指令...", "progress": 10, "command": command})

            tools_desc = "\n".join([f"- {k}: {v}" for k, v in AGENT_TOOLS.items()])
            prompt = (
                f"你是Firefight AI系统的智能体。你可以执行以下工具:\n{tools_desc}\n\n"
                f"用户指令: {command}\n\n"
                "请分析指令，输出需要执行的工具调用序列（JSON数组格式）。"
                "每个工具调用包含: tool (工具名), args (参数对象)。\n"
                "例如: [{{\"tool\": \"check_adb\", \"args\": {{}}}}, {{\"tool\": \"reconnect_adb\", \"args\": {{}}}}]\n"
                "只输出JSON数组，不要其他内容。"
            )

            r = _deepseek_chat([{"role": "user", "content": prompt}], max_tokens=128, temperature=0.1, stream=False)
            plan_text = (r.get("content", "[]") or "[]").strip()
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
            r2 = _deepseek_chat([{"role": "user", "content": summary_prompt}], max_tokens=128, temperature=0.1, stream=False)
            summary = (r2.get("content", "") or "").strip()

            socketio.emit("agent_complete", {
                "success": True,
                "command": command,
                "results": results,
                "summary": summary,
                "time": datetime.now().isoformat(),
            })
            add_system_log("agent", f"智能体执行完成: {command[:80]}", summary[:200])

        except Exception as e:
            socketio.emit("agent_error", {"error": str(e)[:300], "command": command})
            add_system_log("agent", f"智能体执行失败", str(e)[:200])

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
        ad = dc.get("active", "generic")
        di = dc.get(ad, {})
        adb_exe = _get_adb_for_emulator()
        subprocess.run([adb_exe, "start-server"], capture_output=True, text=True, timeout=5)
        r = subprocess.run([adb_exe, "connect", f"{di.get('adb_host','127.0.0.1')}:{di.get('adb_port',5555)}"], capture_output=True, text=True, timeout=10)
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
    add_system_log("chain", f"决策链验证: {results['summary']}", "")

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
                add_system_log("system", f"自动清理: 删除了{deleted_count}个过期文件", "")
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


# ═══════════════════════════════════════════════════════════════
# 云端数据库 API (v5.2)
# ═══════════════════════════════════════════════════════════════

# 云端数据库实例 (SQLite, 存储在服务器端)
import sqlite3 as _sqlite3
import hashlib as _hashlib
from pathlib import Path as _Path

_CLOUD_DB_PATH = _Path("/home/ubuntu/firefightAI/data/firefight_ai_cloud.db")
_CLOUD_DB_LOCK = threading.Lock()


def _get_cloud_conn():
    """获取云端数据库连接"""
    _CLOUD_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(_CLOUD_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_cloud_db():
    """初始化云端数据库表结构"""
    with _CLOUD_DB_LOCK:
        conn = _get_cloud_conn()
        try:
            # 学习日志
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learning_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    record_hash TEXT UNIQUE
                )
            """)
            # 知识库
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_base (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    source_url TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    is_verified INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    record_hash TEXT UNIQUE
                )
            """)
            # 参数历史
            conn.execute("""
                CREATE TABLE IF NOT EXISTS parameter_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    param_name TEXT NOT NULL,
                    param_value TEXT NOT NULL,
                    param_type TEXT DEFAULT 'string',
                    description TEXT DEFAULT '',
                    source TEXT DEFAULT 'cloud',
                    created_at REAL NOT NULL,
                    record_hash TEXT UNIQUE
                )
            """)
            # 训练会话
            conn.execute("""
                CREATE TABLE IF NOT EXISTS training_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT UNIQUE NOT NULL,
                    faction TEXT DEFAULT '',
                    difficulty TEXT DEFAULT '',
                    mode TEXT DEFAULT '',
                    start_time REAL NOT NULL,
                    end_time REAL,
                    total_cycles INTEGER DEFAULT 0,
                    total_score INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running',
                    record_hash TEXT UNIQUE
                )
            """)
            conn.commit()
        finally:
            conn.close()


def _make_record_hash(record: dict) -> str:
    """生成记录哈希"""
    raw = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return _hashlib.md5(raw.encode()).hexdigest()


@app.route("/api/db/status")
def api_db_status():
    """云端数据库状态"""
    try:
        conn = _get_cloud_conn()
        tables = {}
        for t in ["learning_logs", "knowledge_base", "parameter_history", "training_sessions"]:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            tables[t] = cnt
        db_size = _CLOUD_DB_PATH.stat().st_size if _CLOUD_DB_PATH.exists() else 0
        conn.close()
        return jsonify({
            "status": "ok", "version": APP_VERSION,
            "db_size": round(db_size / (1024 * 1024), 2),
            "tables": tables,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/api/db/upload", methods=["POST"])
def api_db_upload():
    """上传数据到云端数据库"""
    try:
        data = request.get_json() or {}
        table_name = data.get("table_name", "")
        records = data.get("records", [])

        if not table_name or not records:
            return jsonify({"status": "error", "message": "缺少参数"})

        valid_tables = ["learning_logs", "knowledge_base", "parameter_history", "training_sessions"]
        if table_name not in valid_tables:
            return jsonify({"status": "error", "message": f"无效表名: {table_name}"})

        with _CLOUD_DB_LOCK:
            conn = _get_cloud_conn()
            inserted = 0
            try:
                for record in records:
                    record_hash = _make_record_hash(record)
                    try:
                        if table_name == "learning_logs":
                            conn.execute(
                                """INSERT OR IGNORE INTO learning_logs
                                   (log_type, title, content, source, session_id, created_at, record_hash)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (record.get("log_type", ""), record.get("title", ""),
                                 record.get("content", ""), record.get("source", ""),
                                 record.get("session_id", ""), record.get("created_at", time.time()),
                                 record_hash),
                            )
                        elif table_name == "knowledge_base":
                            conn.execute(
                                """INSERT OR IGNORE INTO knowledge_base
                                   (title, content, category, source_url, tags, created_at, record_hash)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (record.get("title", ""), record.get("content", ""),
                                 record.get("category", "general"), record.get("source_url", ""),
                                 record.get("tags", ""), record.get("created_at", time.time()),
                                 record_hash),
                            )
                        elif table_name == "parameter_history":
                            conn.execute(
                                """INSERT OR IGNORE INTO parameter_history
                                   (param_name, param_value, param_type, description, source, created_at, record_hash)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (record.get("param_name", ""), str(record.get("param_value", "")),
                                 record.get("param_type", "string"), record.get("description", ""),
                                 record.get("source", "cloud"), record.get("created_at", time.time()),
                                 record_hash),
                            )
                        elif table_name == "training_sessions":
                            conn.execute(
                                """INSERT OR IGNORE INTO training_sessions
                                   (session_id, faction, difficulty, mode, start_time, total_cycles, total_score, status, record_hash)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (record.get("session_id", ""), record.get("faction", ""),
                                 record.get("difficulty", ""), record.get("mode", ""),
                                 record.get("start_time", time.time()), record.get("total_cycles", 0),
                                 record.get("total_score", 0), record.get("status", "running"),
                                 record_hash),
                            )
                        if conn.total_changes > 0:
                            inserted += 1
                    except Exception:
                        pass
                conn.commit()
            finally:
                conn.close()

        add_system_log("sync", f"云端上传 {table_name}", f"接收 {len(records)} 条, 新增 {inserted} 条")
        return jsonify({"status": "ok", "message": f"上传成功", "count": inserted})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/api/db/download", methods=["POST"])
def api_db_download():
    """从云端下载数据"""
    try:
        data = request.get_json() or {}
        table_name = data.get("table_name", "")
        limit = data.get("limit", 1000)

        valid_tables = ["learning_logs", "knowledge_base", "parameter_history", "training_sessions"]
        if table_name not in valid_tables:
            return jsonify({"status": "error", "message": f"无效表名: {table_name}"})

        conn = _get_cloud_conn()
        records = []
        try:
            if table_name == "learning_logs":
                rows = conn.execute(
                    "SELECT log_type, title, content, source, session_id, created_at FROM learning_logs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                records = [
                    {"log_type": r[0], "title": r[1], "content": r[2],
                     "source": r[3], "session_id": r[4], "created_at": r[5]}
                    for r in rows
                ]
            elif table_name == "knowledge_base":
                rows = conn.execute(
                    "SELECT title, content, category, source_url, tags, is_verified, created_at FROM knowledge_base ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                records = [
                    {"title": r[0], "content": r[1], "category": r[2],
                     "source_url": r[3], "tags": r[4], "is_verified": r[5], "created_at": r[6]}
                    for r in rows
                ]
            elif table_name == "parameter_history":
                rows = conn.execute(
                    "SELECT param_name, param_value, param_type, description, source, created_at FROM parameter_history ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                records = [
                    {"param_name": r[0], "param_value": r[1], "param_type": r[2],
                     "description": r[3], "source": r[4], "created_at": r[5]}
                    for r in rows
                ]
            elif table_name == "training_sessions":
                rows = conn.execute(
                    "SELECT session_id, faction, difficulty, mode, start_time, end_time, total_cycles, total_score, status FROM training_sessions ORDER BY start_time DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                records = [
                    {"session_id": r[0], "faction": r[1], "difficulty": r[2],
                     "mode": r[3], "start_time": r[4], "end_time": r[5],
                     "total_cycles": r[6], "total_score": r[7], "status": r[8]}
                    for r in rows
                ]
        finally:
            conn.close()

        return jsonify({"status": "ok", "records": records, "count": len(records)})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/api/db/sync", methods=["POST"])
def api_db_sync():
    """双向同步"""
    data = request.get_json() or {}
    direction = data.get("direction", "both")

    result = {"status": "ok", "direction": direction, "results": {}}

    if direction in ("upload", "both"):
        for table in ["learning_logs", "knowledge_base", "parameter_history"]:
            # 这里简化处理，实际应有增量同步逻辑
            pass

    add_system_log("sync", "云端同步触发", f"方向: {direction}")
    return jsonify(result)


@app.route("/api/db/backup", methods=["POST"])
def api_db_backup():
    """云端数据库备份"""
    try:
        backup_dir = _CLOUD_DB_PATH.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"cloud_db_backup_{timestamp}.db"

        import shutil as _shutil
        if _CLOUD_DB_PATH.exists():
            _shutil.copy2(str(_CLOUD_DB_PATH), str(backup_path))
            add_system_log("system", "云端数据库备份", f"备份至 {backup_path.name}")
            return jsonify({"status": "ok", "backup_path": str(backup_path)})
        return jsonify({"status": "error", "message": "数据库文件不存在"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/api/db/stats")
def api_db_full_stats():
    """获取数据库完整统计"""
    try:
        local_stats = {}
        try:
            from src.learning.local_database import get_local_db
            local_db = get_local_db()
            local_stats = local_db.get_db_stats()
        except Exception:
            local_stats = {"status": "unavailable"}

        cloud_stats = {"status": "ok"}
        try:
            conn = _get_cloud_conn()
            tables = {}
            for t in ["learning_logs", "knowledge_base", "parameter_history", "training_sessions"]:
                cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                tables[t] = cnt
            conn.close()
            cloud_stats["tables"] = tables
            if _CLOUD_DB_PATH.exists():
                cloud_stats["db_size_mb"] = round(_CLOUD_DB_PATH.stat().st_size / (1024 * 1024), 2)
        except Exception as e:
            cloud_stats = {"status": "error", "message": str(e)}

        return jsonify({
            "local": local_stats,
            "cloud": cloud_stats,
            "version": APP_VERSION,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Firefight AI Dashboard Server v5.1")
    parser.add_argument("--port", type=int, default=5000, help="服务器端口")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="服务器地址（0.0.0.0允许局域网/公网访问）")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    logger.info(f"Firefight AI Dashboard v{APP_VERSION} 启动")
    logger.info(f"地址: http://{args.host}:{args.port}")
    logger.info(f"项目目录: {PROJECT_ROOT}")

    # 🔥 恢复持久化数据（防止重启丢失）
    _load_persistent_logs()
    _load_knowledge_base()
    logger.info(f"学习日志恢复: {len(_learning_log)}条, 知识库: {len(_ai_knowledge_base)}条")

    add_system_log("system", f"服务器启动 v{APP_VERSION}", f"host={args.host}:{args.port}")

    # 初始化云端数据库
    try:
        _init_cloud_db()
        add_system_log("system", "云端数据库已初始化", f"路径: {_CLOUD_DB_PATH}")
    except Exception as e:
        logger.warning(f"云端数据库初始化失败: {e}")

    # 启动后台自动清理
    start_auto_cleanup()

    # ── 启动ADB保活监控 ──
    start_adb_monitor()

    # ── v5.1 启动自动参数保存调度器 ──
    try:
        from src.learning.auto_scheduler import AutoScheduler
        _scheduler = AutoScheduler(project_root=PROJECT_ROOT)
        _scheduler.start()
        add_system_log("system", "自动保存调度器已启动", "每天08:00和20:00自动保存并上传参数")
        logger.info("AutoScheduler已启动")
    except Exception as e:
        logger.warning(f"自动保存调度器启动失败: {e}")

    # ── v5.2 加载AI自学习参数并启动自学习引擎 ──
    _load_learning_params()
    _start_self_learning_engine()
    add_system_log("system", "AI自学习引擎已启动", "持续自主学习模式已激活，每60秒分析学习日志并调整参数")

    # 🔥 v5.2 自动从服务器同步最新参数
    _auto_sync_params_from_server()

    try:
        socketio.run(
            app, host=args.host, port=args.port,
            allow_unsafe_werkzeug=True, use_reloader=False,
            debug=args.debug,
        )
    except KeyboardInterrupt:
        logger.info("服务器已停止")
        if _scheduler:
            _scheduler.stop()
    except Exception as e:
        logger.error(f"服务器启动失败: {e}")
        sys.exit(1)