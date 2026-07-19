"""ADB底层封装 - 通过ADB命令控制Android设备"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

from loguru import logger


class ADBUtils:
    """ADB命令封装,支持无线连接"""

    # 自动检测ADB路径
    _ADB_CANDIDATES = [
        r"d:\firefight\adb\adb.exe",
        r"C:\adb\platform-tools\platform-tools\adb.exe",
        r"d:\MuMuPlayer\nx_device\12.0\shell\adb.exe",
        "adb",  # PATH fallback
    ]

    @staticmethod
    def _find_adb() -> str:
        """自动查找ADB可执行文件"""
        for candidate in ADBUtils._ADB_CANDIDATES:
            if candidate == "adb":
                return "adb"
            if Path(candidate).exists():
                logger.info(f"找到ADB: {candidate}")
                return candidate
        return "adb"  # 最后回退

    def __init__(
        self,
        host: str = "192.168.1.100",
        port: int = 5555,
        connect_timeout: int = 10,
        command_timeout: int = 5,
        retry_count: int = 3,
        adb_path: str | None = None,
    ):
        self.host = host
        self.port = port
        self.device_addr = f"{host}:{port}"
        self.adb_path = adb_path or self._find_adb()
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.retry_count = retry_count
        self._connected = False

    # ---- 连接管理 ----

    def connect(self) -> bool:
        """通过WiFi连接设备"""
        for attempt in range(1, self.retry_count + 1):
            logger.info(f"尝试连接设备 {self.device_addr} (第{attempt}次)")
            try:
                result = self._run_adb(
                    ["connect", self.device_addr],
                    timeout=self.connect_timeout,
                )
                if "connected" in result.lower() or "already connected" in result.lower():
                    self._connected = True
                    logger.info(f"设备连接成功: {self.device_addr}")
                    return True
                logger.warning(f"连接返回: {result.strip()}")
            except Exception as e:
                logger.error(f"连接失败: {e}")
            time.sleep(2)
        logger.error(f"设备连接失败,已重试{self.retry_count}次")
        return False

    def disconnect(self) -> None:
        """断开设备连接"""
        try:
            self._run_adb(["disconnect", self.device_addr], timeout=5)
            self._connected = False
            logger.info("设备已断开")
        except Exception as e:
            logger.warning(f"断开连接时出错: {e}")

    def is_connected(self) -> bool:
        """检查设备是否已连接"""
        try:
            result = self._run_adb(["devices"], timeout=3)
            return self.device_addr in result
        except Exception:
            return False

    def ensure_connected(self) -> bool:
        """确保设备已连接,未连接则重新连接"""
        if self.is_connected():
            return True
        return self.connect()

    # ---- 基础操作 ----

    def tap(self, x: int, y: int) -> bool:
        """点击屏幕坐标(像素)"""
        logger.debug(f"ADB tap: ({x}, {y})")
        return self._run_with_retry(["shell", "input", "tap", str(x), str(y)])

    def swipe(
        self,
        x1: int, y1: int,
        x2: int, y2: int,
        duration_ms: int = 300,
    ) -> bool:
        """滑动屏幕"""
        logger.debug(f"ADB swipe: ({x1},{y1}) -> ({x2},{y2}), duration={duration_ms}ms")
        return self._run_with_retry([
            "shell", "input", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(duration_ms),
        ])

    def long_press(self, x: int, y: int, duration_ms: int = 500) -> bool:
        """长按"""
        return self.swipe(x, y, x, y, duration_ms)

    def keyevent(self, keycode: int) -> bool:
        """发送事件"""
        logger.debug(f"ADB keyevent: {keycode}")
        return self._run_with_retry(["shell", "input", "keyevent", str(keycode)])

    def back(self) -> bool:
        """按返回键"""
        return self.keyevent(4)

    def home(self) -> bool:
        """按Home键"""
        return self.keyevent(3)

    # ---- 应用管理 ----

    def launch_app(self, package_name: str, activity_name: str) -> bool:
        """启动应用"""
        cmd = f"am start -n {package_name}/{activity_name}"
        logger.info(f"启动应用: {package_name}")
        return self._run_s_with_retry(cmd)

    def force_stop_app(self, package_name: str) -> bool:
        """强制停止应用"""
        cmd = f"am force-stop {package_name}"
        logger.info(f"强制停止应用: {package_name}")
        return self._run_s_with_retry(cmd)

    def get_current_activity(self) -> Optional[str]:
        """获取当前前台Activity"""
        try:
            result = self._run_adb([
                "shell", "dumpsys", "activity", "activities"
            ], timeout=5)
            for line in result.split("\n"):
                if "mResumedActivity" in line or "topResumedActivity" in line:
                    return line.strip()
        except Exception as e:
            logger.warning(f"获取当前Activity失败: {e}")
        return None

    # ---- scrcpy管理 ----

    def push_scrcpy_server(self, server_path: str = "scrcpy") -> bool:
        """推送scrcpy server到设备"""
        if not Path(server_path).exists():
            logger.warning(f"scrcpy server文件不存在: {server_path}")
            return False
        try:
            self._run_adb(["push", server_path, "/data/local/tmp/scrcpy"])
            self._run_s("chmod 755 /data/local/tmp/scrcpy")
            logger.info("scrcpy server推送成功")
            return True
        except Exception as e:
            logger.error(f"推送scrcpy server失败: {e}")
            return False

    # ---- 截图(备选方案) ----

    def screenshot(self, save_path: str) -> bool:
        """ADB截图(备选方案,较慢)"""
        try:
            self._run_s("screencap -p /sdcard/screenshot.png")
            self._run_adb(["pull", "/sdcard/screenshot.png", save_path])
            self._run_s("rm /sdcard/screenshot.png")
            return True
        except Exception as e:
            logger.error(f"ADB截图失败: {e}")
            return False

    # ---- 内部方法 ----

    def _run_adb(self, args: list[str], timeout: int | None = None) -> str:
        """执行adb命令"""
        if timeout is None:
            timeout = self.command_timeout
        cmd = [self.adb_path, "-s", self.device_addr] + args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 and result.stderr:
            logger.debug(f"ADB stderr: {result.stderr.strip()}")
        return result.stdout

    def _run_s(self, cmd: str, timeout: int | None = None) -> str:
        """执行adb shell命令"""
        return self._run_adb(["shell", cmd], timeout=timeout)

    def _run_with_retry(self, args: list[str]) -> bool:
        """带重试的ADB命令执行"""
        for attempt in range(1, self.retry_count + 1):
            try:
                self._run_adb(args)
                return True
            except subprocess.TimeoutExpired:
                logger.warning(f"ADB命令超时 (第{attempt}次): {' '.join(args)}")
            except Exception as e:
                logger.warning(f"ADB命令失败 (第{attempt}次): {e}")
            if attempt < self.retry_count:
                time.sleep(1)
        logger.error(f"ADB命令重试{self.retry_count}次后仍失败: {' '.join(args)}")
        return False

    def _run_s_with_retry(self, cmd: str) -> bool:
        """带重试的shell命令执行"""
        for attempt in range(1, self.retry_count + 1):
            try:
                self._run_s(cmd)
                return True
            except subprocess.TimeoutExpired:
                logger.warning(f"Shell命令超时 (第{attempt}次): {cmd}")
            except Exception as e:
                logger.warning(f"Shell命令失败 (第{attempt}次): {e}")
            if attempt < self.retry_count:
                time.sleep(1)
        logger.error(f"Shell命令重试{self.retry_count}次后仍失败: {cmd}")
        return False