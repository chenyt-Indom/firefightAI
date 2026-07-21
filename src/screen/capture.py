"""屏幕捕获 - 通过scrcpy视频流获取游戏画面,ADB截图作为备选"""
from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np
from loguru import logger

try:
    from scrcpy_client import ScrcpyClient
    HAS_SCRCPY = True
except ImportError:
    HAS_SCRCPY = False
    logger.debug("scrcpy_client未安装,使用ADB截图备选方案")

from src.execution.adb_utils import ADBUtils


class ScreenCapture:
    """屏幕捕获器: scrcpy优先, ADB截图备选"""

    def __init__(
        self,
        adb: ADBUtils,
        max_fps: int = 30,
        bitrate: int = 8_000_000,
        max_width: int = 1280,
        max_height: int = 720,
        crop: Optional[str] = None,
        timeout: int = 5,
    ):
        self.adb = adb
        self.max_fps = max_fps
        self.bitrate = bitrate
        self.max_width = max_width
        self.max_height = max_height
        self.crop = crop
        self.timeout = timeout

        self._scrcpy: Optional[ScrcpyClient] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._running = False
        self._use_scrcpy = HAS_SCRCPY
        self._frame_count = 0
        self._start_time = 0.0

    # ---- scrcpy模式 ----

    def start(self) -> bool:
        """启动屏幕捕获"""
        if self._use_scrcpy and HAS_SCRCPY:
            return self._start_scrcpy()
        else:
            logger.info("使用ADB截图备选方案(较慢)")
            self._running = True
            self._start_time = time.time()
            return True

    def _start_scrcpy(self) -> bool:
        """启动scrcpy视频流"""
        try:
            self._scrcpy = ScrcpyClient(
                max_fps=self.max_fps,
                bitrate=self.bitrate,
                max_width=self.max_width,
                max_height=self.max_height,
                crop=self.crop,
                no_control=True,  # 不接管触控,由ADB操控
            )
            # 启动视频流接收线程
            self._running = True
            self._start_time = time.time()
            self._thread = threading.Thread(target=self._scrcpy_loop, daemon=True)
            self._thread.start()
            logger.info(f"scrcpy屏幕捕获已启动, max_fps={self.max_fps}")
            return True
        except Exception as e:
            logger.error(f"scrcpy启动失败: {e}, 降级到ADB截图")
            self._use_scrcpy = False
            self._running = True
            self._start_time = time.time()
            return True  # 降级后仍返回True

    def _scrcpy_loop(self) -> None:
        """scrcpy帧接收循环"""
        if self._scrcpy is None:
            return
        try:
            for frame in self._scrcpy.stream():
                if not self._running:
                    break
                with self._frame_lock:
                    self._latest_frame = frame
                    self._frame_count += 1
        except Exception as e:
            logger.error(f"scrcpy视频流异常: {e}")
            self._use_scrcpy = False

    def stop(self) -> None:
        """停止屏幕捕获"""
        self._running = False
        elapsed = time.time() - self._start_time
        fps = self._frame_count / elapsed if elapsed > 0 else 0
        logger.info(f"屏幕捕获已停止, 共{self._frame_count}帧, 平均{elapsed:.1f}s, 约{fps:.1f}FPS")
        if self._scrcpy:
            try:
                self._scrcpy.stop()
            except Exception:
                pass

    # ---- 帧获取 ----

    def grab_latest_frame(self) -> Optional[np.ndarray]:
        """获取最新帧"""
        if self._use_scrcpy:
            return self._grab_scrcpy_frame()
        else:
            return self._grab_adb_frame()

    def _grab_scrcpy_frame(self) -> Optional[np.ndarray]:
        """获取scrcpy最新帧"""
        for _ in range(int(self.timeout * 10)):  # 100ms轮询
            with self._frame_lock:
                if self._latest_frame is not None:
                    return self._latest_frame.copy()
            time.sleep(0.1)
        logger.warning("scrcpy帧抓取超时")
        return None

    def _grab_adb_frame(self) -> Optional[np.ndarray]:
        """ADB截图获取帧(备选)"""
        import cv2

        temp_path = "temp_screenshot.png"
        try:
            if self.adb.screenshot(temp_path):
                frame = cv2.imread(temp_path)
                if frame is not None:
                    self._frame_count += 1
                    return frame
        except Exception as e:
            logger.error(f"ADB截图失败: {e}")
        return None

    @property
    def fps(self) -> float:
        """当前帧率"""
        elapsed = time.time() - self._start_time
        return self._frame_count / elapsed if elapsed > 0 else 0

    @property
    def is_running(self) -> bool:
        return self._running