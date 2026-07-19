"""MuMu 模拟器触控注入模块 (MuMuManager.exe 版本)

通过 MuMuManager.exe control tool cmd 子命令直接向模拟器注入
Android input 事件。与 ADB input 不同，这个通道走模拟器内部工具栏命令，
可以穿透 SDL2 游戏的反作弊层。

已验证: Firefight (SDL2 原生游戏) 对这种方法有响应。

用法:
    MuMuManager.exe control -v 0 tool cmd -c "input tap X Y"
    MuMuManager.exe control -v 0 tool cmd -c "input swipe X1 Y1 X2 Y2 DURATION"
"""

from __future__ import annotations

import subprocess
import time
import os
from typing import Optional
from loguru import logger


class MuMuManagerTouch:
    """通过 MuMuManager.exe 注入触控事件

    特点:
    - 不需要连接/断开，每次调用是独立的子进程
    - 性能略低于 IPC (每次 tap 约 100-200ms)
    - 稳定性高，不受 DLL 版本/日期限制
    """

    # MuMuManager.exe 默认路径
    EXE_PATH = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"

    def __init__(
        self,
        exe_path: Optional[str] = None,
        verbosity: int = 0,
        timeout: float = 5.0,
    ):
        self.exe_path = exe_path or self.EXE_PATH
        self.verbosity = verbosity
        self.timeout = timeout

        if not os.path.exists(self.exe_path):
            logger.warning(f"MuMuManager.exe 不存在: {self.exe_path}")

    # ---------------------------------------------------------------
    # 核心接口
    # ---------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """MuMuManager 无需持久连接，始终可用 (只要 exe 存在)"""
        return os.path.exists(self.exe_path)

    def tap(self, x: int, y: int, delay_ms: int = 0) -> bool:
        """点击屏幕

        Args:
            x, y: 游戏画面像素坐标 (基于 1920x1080)
            delay_ms: 保留参数，MuMuManager 自带延时

        Returns:
            是否成功
        """
        cmd = f"input tap {x} {y}"
        return self._run_cmd(cmd)

    def swipe(
        self,
        x1: int, y1: int,
        x2: int, y2: int,
        duration_ms: int = 300,
    ) -> bool:
        """滑动屏幕

        Args:
            x1, y1: 起点
            x2, y2: 终点
            duration_ms: 滑动时长(ms)

        Returns:
            是否成功
        """
        cmd = f"input swipe {x1} {y1} {x2} {y2} {duration_ms}"
        return self._run_cmd(cmd)

    def long_press(self, x: int, y: int, duration_ms: int = 500) -> bool:
        """长按 = swipe 起点=终点"""
        return self.swipe(x, y, x, y, duration_ms)

    def drag(
        self,
        x1: int, y1: int,
        x2: int, y2: int,
        duration_ms: int = 300,
    ) -> bool:
        """拖拽 - 同 swipe"""
        return self.swipe(x1, y1, x2, y2, duration_ms)

    # ---------------------------------------------------------------
    # 内部
    # ---------------------------------------------------------------

    def _run_cmd(self, cmd: str) -> bool:
        """执行单条 MuMuManager 命令"""
        args = [
            self.exe_path,
            "control",
            "-v", str(self.verbosity),
            "tool", "cmd",
            "-c", cmd,
        ]

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            if "errcode: 0" in stdout or result.returncode == 0:
                logger.debug(f"[MuMuManager] {cmd} -> OK")
                return True
            else:
                logger.warning(
                    f"[MuMuManager] {cmd} -> FAIL "
                    f"(rc={result.returncode}, stdout={stdout[:100]})"
                )
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"[MuMuManager] {cmd} -> TIMEOUT")
            return False
        except FileNotFoundError:
            logger.error(f"[MuMuManager] 可执行文件不存在: {self.exe_path}")
            return False
        except Exception as e:
            logger.error(f"[MuMuManager] {cmd} -> 异常: {e}")
            return False
