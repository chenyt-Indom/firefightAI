"""MuMu 模拟器 IPC 触控注入模块

通过 external_renderer_ipc.dll 的 nemu_input_event_touch_down/up API
直接向 SDL2 游戏注入触控事件，绕过游戏的反作弊检测。

原理: Firefight 是 SDL2 原生 C++ 游戏，SDL2 在 Android 上直接读
/dev/input/event*，不走 Android InputDispatcher。ADB input/sendevent
都被忽略。MuMu 的 external_renderer_ipc.dll 是模拟器厂商提供的
内部通信接口，能够将事件直接注入到模拟器的渲染管线中。
"""

from __future__ import annotations

import ctypes
import time
import os
from typing import Optional
from loguru import logger


class MuMuTouchController:
    """MuMu 模拟器触控控制器 - 通过 IPC DLL 注入触控事件"""

    # DLL 路径和参数
    DLL_PATH = r"D:\MuMuPlayer\nx_device\12.0\shell\sdk\external_renderer_ipc.dll"
    INSTALL_PATH = r"D:\MuMuPlayer"
    INSTANCE_INDEX = 0  # 多开实例索引，默认第0个
    APP_INDEX = 0       # 应用实例索引

    def __init__(
        self,
        package_name: str = "com.windowsgames.firefightbw",
        instance_index: int = 0,
        app_index: int = 0,
        dll_path: Optional[str] = None,
    ):
        self.package_name = package_name.encode("ascii")
        self.instance_index = instance_index
        self.app_index = app_index
        self.dll_path = dll_path or self.DLL_PATH
        self._dll = None
        self._handle = 0
        self._display_id = 0
        self._connected = False

    # ---------------------------------------------------------------
    # 生命周期
    # ---------------------------------------------------------------

    def connect(self) -> bool:
        """连接到 MuMu 模拟器的外部渲染器 IPC

        返回: 是否连接成功
        """
        if self._connected:
            return True

        if not os.path.exists(self.dll_path):
            logger.error(f"MuMu IPC DLL 不存在: {self.dll_path}")
            return False

        try:
            self._dll = ctypes.windll.LoadLibrary(self.dll_path)

            # nemu_connect(install_path: wchar_p, instance_index: int) -> handle: int
            self._dll.nemu_connect.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
            self._dll.nemu_connect.restype = ctypes.c_int

            # nemu_disconnect(handle: int) -> void
            self._dll.nemu_disconnect.argtypes = [ctypes.c_int]
            self._dll.nemu_disconnect.restype = None

            # nemu_get_display_id(handle: int, package_name: bytes, app_index: int) -> int
            self._dll.nemu_get_display_id.argtypes = [
                ctypes.c_int, ctypes.c_char_p, ctypes.c_int
            ]
            self._dll.nemu_get_display_id.restype = ctypes.c_int

            # nemu_input_event_touch_down(handle: int, display_id: int, x: int, y: int) -> int
            self._dll.nemu_input_event_touch_down.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int
            ]
            self._dll.nemu_input_event_touch_down.restype = ctypes.c_int

            # nemu_input_event_touch_up(handle: int, display_id: int) -> int
            self._dll.nemu_input_event_touch_up.argtypes = [
                ctypes.c_int, ctypes.c_int
            ]
            self._dll.nemu_input_event_touch_up.restype = ctypes.c_int

            # 连接
            self._handle = self._dll.nemu_connect(
                ctypes.c_wchar_p(self.INSTALL_PATH),
                ctypes.c_int(self.instance_index),
            )
            if self._handle <= 0:
                logger.error(f"nemu_connect 失败, 返回: {self._handle}")
                return False

            logger.info(f"nemu_connect 成功, handle={self._handle}")

            # 获取 display_id
            self._display_id = self._dll.nemu_get_display_id(
                ctypes.c_int(self._handle),
                ctypes.c_char_p(self.package_name),
                ctypes.c_int(self.app_index),
            )
            if self._display_id <= 0:
                logger.error(f"nemu_get_display_id 失败, 返回: {self._display_id}")
                self.disconnect()
                return False

            logger.info(f"nemu_get_display_id 成功, display_id={self._display_id}")
            self._connected = True
            return True

        except OSError as e:
            logger.error(f"加载 MuMu IPC DLL 失败: {e}")
            return False
        except Exception as e:
            logger.error(f"MuMu IPC 连接异常: {e}")
            return False

    def disconnect(self) -> None:
        """断开与 MuMu IPC 的连接"""
        if self._dll and self._handle > 0:
            try:
                self._dll.nemu_disconnect(ctypes.c_int(self._handle))
                logger.info("nemu_disconnect 完成")
            except Exception as e:
                logger.warning(f"nemu_disconnect 异常: {e}")
        self._handle = 0
        self._display_id = 0
        self._connected = False
        self._dll = None

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected and self._handle > 0

    # ---------------------------------------------------------------
    # 触控操作
    # ---------------------------------------------------------------

    def touch_down(self, x: int, y: int) -> bool:
        """按下触控点

        Args:
            x, y: 屏幕像素坐标 (基于游戏画面的分辨率，1080x1920)

        Returns:
            是否成功
        """
        if not self._check_connected():
            return False

        try:
            ret = self._dll.nemu_input_event_touch_down(
                ctypes.c_int(self._handle),
                ctypes.c_int(self._display_id),
                ctypes.c_int(x),
                ctypes.c_int(y),
            )
            logger.debug(f"touch_down ({x}, {y}) -> {ret}")
            return ret >= 0
        except Exception as e:
            logger.error(f"touch_down 失败: {e}")
            return False

    def touch_up(self) -> bool:
        """释放触控点"""
        if not self._check_connected():
            return False

        try:
            ret = self._dll.nemu_input_event_touch_up(
                ctypes.c_int(self._handle),
                ctypes.c_int(self._display_id),
            )
            logger.debug(f"touch_up -> {ret}")
            return ret >= 0
        except Exception as e:
            logger.error(f"touch_up 失败: {e}")
            return False

    def tap(self, x: int, y: int, delay_ms: int = 50) -> bool:
        """点击屏幕

        Args:
            x, y: 屏幕像素坐标
            delay_ms: touch_down 和 touch_up 之间的延迟(ms)

        Returns:
            是否成功
        """
        if not self.touch_down(x, y):
            return False
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)
        return self.touch_up()

    def swipe(
        self,
        x1: int, y1: int,
        x2: int, y2: int,
        duration_ms: int = 300,
        steps: int = 20,
    ) -> bool:
        """滑动屏幕

        Args:
            x1, y1: 起点像素坐标
            x2, y2: 终点像素坐标
            duration_ms: 滑动总时长(ms)
            steps: 中间插值步数

        Returns:
            是否成功
        """
        if not self.touch_down(x1, y1):
            return False

        step_delay = duration_ms / 1000.0 / steps
        for i in range(1, steps + 1):
            t = i / steps
            cx = int(x1 + (x2 - x1) * t)
            cy = int(y1 + (y2 - y1) * t)
            # MuMu IPC 的 touch_down 同时支持移动触点(滑动中)
            # 通过连续的 touch_down 调用来模拟滑动
            try:
                self._dll.nemu_input_event_touch_down(
                    ctypes.c_int(self._handle),
                    ctypes.c_int(self._display_id),
                    ctypes.c_int(cx),
                    ctypes.c_int(cy),
                )
            except Exception:
                pass
            time.sleep(step_delay)

        return self.touch_up()

    def long_press(self, x: int, y: int, duration_ms: int = 500) -> bool:
        """长按

        Args:
            x, y: 屏幕像素坐标
            duration_ms: 按住时长(ms)

        Returns:
            是否成功
        """
        if not self.touch_down(x, y):
            return False
        time.sleep(duration_ms / 1000.0)
        return self.touch_up()

    def drag(
        self,
        x1: int, y1: int,
        x2: int, y2: int,
        duration_ms: int = 300,
    ) -> bool:
        """拖拽 - 同 swipe，语义别名"""
        return self.swipe(x1, y1, x2, y2, duration_ms)

    # ---------------------------------------------------------------
    # 内部方法
    # ---------------------------------------------------------------

    def _check_connected(self) -> bool:
        """检查连接状态，未连接时自动尝试连接"""
        if self._connected:
            return True
        logger.warning("MuMu IPC 未连接，尝试自动连接...")
        return self.connect()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def __del__(self):
        try:
            self.disconnect()
        except Exception:
            pass
