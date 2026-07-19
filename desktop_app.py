"""Firefight AI 桌面应用 — 本地窗口启动器

启动方式:
    py -3 desktop_app.py
    或双击 launch.bat
    或双击桌面快捷方式

架构:
    1. 后台启动 Flask + SocketIO 服务器 (localhost:5000)
    2. 尝试用 PyWebView 嵌入本地窗口
    3. 如果 PyWebView 不可用，自动打开浏览器
"""

import sys
import os
import threading
import time
import signal
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
os.chdir(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <level>{message}</level>",
    level="INFO",
)
logger.add(
    PROJECT_ROOT / "logs" / "desktop_{time:YYYY-MM-DD}.log",
    rotation="10 MB",
    retention="7 days",
    level="DEBUG",
)


def start_flask_server():
    from dashboard_server import app, socketio
    logger.info("启动 Flask 服务器: http://127.0.0.1:5000")
    try:
        socketio.run(
            app, host="127.0.0.1", port=5000,
            allow_unsafe_werkzeug=True, use_reloader=False,
        )
    except Exception as e:
        logger.error(f"Flask 服务器启动失败: {e}")


def wait_for_server(timeout: int = 15) -> bool:
    import socket
    for i in range(timeout * 2):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(("127.0.0.1", 5000))
            s.close()
            logger.info("服务器就绪")
            return True
        except:
            time.sleep(0.5)
    return False


def try_pywebview():
    """尝试用 PyWebView 打开本地窗口，失败则返回 False"""
    try:
        import webview

        window = webview.create_window(
            title="Firefight AI 战术指挥系统 v3.0",
            url="http://127.0.0.1:5000",
            width=1400,
            height=900,
            min_size=(1024, 700),
            resizable=True,
            confirm_close=True,
            text_select=True,
        )
        webview.start(debug=False, http_server=False)
        return True
    except Exception as e:
        logger.warning(f"PyWebView 不可用 ({e})，将使用浏览器")
        return False


def main():
    print()
    print("=" * 60)
    print("  Firefight AI 战术指挥系统 v3.0")
    print("=" * 60)
    print()

    # 1. 检查依赖
    try:
        import flask
        import flask_socketio
        import yaml
    except ImportError as e:
        print(f"[错误] 缺少依赖: {e}")
        print("请运行: py -3 -m pip install flask flask-socketio pyyaml loguru openai pydantic requests pillow")
        input("按 Enter 退出...")
        sys.exit(1)

    # 2. 检查配置
    settings_path = PROJECT_ROOT / "config" / "settings.yaml"
    if not settings_path.exists():
        print("[错误] 配置文件不存在: config/settings.yaml")
        input("按 Enter 退出...")
        sys.exit(1)

    # 3. 启动 Flask 服务器
    flask_thread = threading.Thread(target=start_flask_server, daemon=True)
    flask_thread.start()

    if not wait_for_server():
        print("[错误] 无法启动服务器")
        input("按 Enter 退出...")
        sys.exit(1)

    print("[OK] 服务已启动")

    # 4. 尝试 PyWebView，失败则用浏览器
    url = "http://127.0.0.1:5000"

    webview_ok = False
    try:
        import webview
        webview_ok = True
    except ImportError:
        pass

    if webview_ok:
        print("[信息] 正在打开桌面窗口...")
        try:
            window = webview.create_window(
                title="Firefight AI 战术指挥系统 v3.0",
                url=url,
                width=1400,
                height=900,
                min_size=(1024, 700),
                resizable=True,
                confirm_close=True,
                text_select=True,
            )
            webview.start(debug=False, http_server=False)
        except Exception as e:
            logger.warning(f"PyWebView 窗口创建失败: {e}")
            print(f"[信息] 桌面窗口不可用，正在打开浏览器...")
            webbrowser.open(url)
    else:
        print("[信息] PyWebView 未安装，正在打开浏览器...")
        webbrowser.open(url)

    # 5. 保持服务器运行（浏览器模式）
    print()
    print("[运行中] 关闭此窗口停止服务")
    print("  按 Ctrl+C 退出")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        print("[信息] 正在关闭...")

    print("应用已关闭")


if __name__ == "__main__":
    main()