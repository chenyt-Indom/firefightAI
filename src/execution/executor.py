"""指令执行器 - 将解析后的指令通过 MuMuManager 触控执行到游戏

触控注入方式:
  优先: MuMuManager (MuMuManager.exe tool cmd) — 绕过 SDL2 反作弊, 已验证可用
  回退: ADB input — 兼容非 MuMu 设备
"""
from __future__ import annotations

import time
from typing import Optional

from loguru import logger

from src.execution.adb_utils import ADBUtils
from src.execution.mumu_manager import MuMuManagerTouch
from src.decision.parser import ParsedCommand
from src.state.models import ActionType
from src.utils.logger import log_execution


class CommandExecutor:
    """指令执行器,将逻辑指令转换为屏幕操作

    支持两种触控注入后端:
    - MuMuManager: 通过 MuMuManager.exe 直驱触控 (SDL2 游戏可响应)
    - ADB input: 兼容模式, 回退方案
    """

    def __init__(
        self,
        adb: ADBUtils,
        screen_size: tuple[int, int] = (1920, 1080),
        touch: Optional[MuMuManagerTouch] = None,
        # UI坐标(像素,需通过校准工具获取)
        # 横屏: 暂停按钮在右上角
        pause_button: tuple[int, int] = (1824, 54),  # 1920x1080 的 95%x5%
        command_buttons: Optional[dict[str, tuple[int, int]]] = None,
    ):
        self.adb = adb
        self.touch = touch  # MuMu IPC 触控(优先)
        self.screen_size = screen_size
        self.pause_button = pause_button

        # 命令按钮坐标 - 基于 1920x1080 横屏
        # 实际游戏中命令按钮在控制面板区域
        # 这里使用相对屏幕的比例位置
        sw, sh = screen_size
        self.command_buttons = command_buttons or {
            "move":          (int(sw * 0.05), int(sh * 0.89)),
            "attack":        (int(sw * 0.10), int(sh * 0.89)),
            "stop":          (int(sw * 0.15), int(sh * 0.89)),
            "retreat":       (int(sw * 0.20), int(sh * 0.89)),
            "attack_ground": (int(sw * 0.25), int(sh * 0.89)),
        }
        self._execution_count = 0

    # -----------------------------------------------------------
    # 触控底层
    # -----------------------------------------------------------

    def _tap(self, x: int, y: int, delay_ms: int = 50) -> bool:
        """点击屏幕 - MuMuManager 优先, ADB 回退"""
        if self.touch and self.touch.is_connected:
            return self.touch.tap(x, y, delay_ms=delay_ms)
        return self.adb.tap(x, y)

    def _swipe(
        self, x1: int, y1: int, x2: int, y2: int,
        duration_ms: int = 300,
    ) -> bool:
        """滑动屏幕"""
        if self.touch and self.touch.is_connected:
            return self.touch.swipe(x1, y1, x2, y2, duration_ms)
        return self.adb.swipe(x1, y1, x2, y2, duration_ms)

    def execute(self, commands: list[ParsedCommand]) -> bool:
        """执行指令列表

        Args:
            commands: 解析后的指令列表

        Returns:
            是否全部执行成功
        """
        if not commands:
            logger.warning("无指令可执行")
            return False

        all_success = True
        for i, cmd in enumerate(commands):
            log_execution(f"执行指令 [{i+1}/{len(commands)}]: {cmd}")
            success = self._execute_single(cmd)
            if not success:
                all_success = False
                logger.error(f"指令执行失败: {cmd}")
            self._execution_count += 1
            time.sleep(0.3)  # 指令间间隔

        return all_success

    def _execute_single(self, cmd: ParsedCommand) -> bool:
        """执行单条指令"""
        if not cmd.unit_ids:
            return False

        # 1. 选中单位
        if not self._select_units(cmd.unit_ids):
            return False

        time.sleep(0.15)

        # 2. 根据指令类型执行
        if cmd.action == ActionType.MOVE:
            if cmd.target_pixel is None:
                logger.error("MOVE指令缺少目标坐标")
                return False
            return self._execute_move(cmd.target_pixel)

        elif cmd.action == ActionType.ATTACK:
            if cmd.target_enemy_pixel is not None:
                return self._execute_attack(cmd.target_enemy_pixel)
            elif cmd.target_pixel is not None:
                # 降级为攻击地面
                return self._execute_attack_ground(cmd.target_pixel)
            else:
                logger.error("ATTACK指令缺少目标")
                return False

        elif cmd.action == ActionType.ATTACK_GROUND:
            if cmd.target_pixel is None:
                logger.error("ATTACK_GROUND指令缺少目标坐标")
                return False
            return self._execute_attack_ground(cmd.target_pixel)

        elif cmd.action == ActionType.STOP:
            return self._execute_stop()

        elif cmd.action == ActionType.RETREAT:
            if cmd.target_pixel is not None:
                return self._execute_move(cmd.target_pixel)
            else:
                # 默认撤退到屏幕底部中央
                default_retreat = (self.screen_size[0] // 2, int(self.screen_size[1] * 0.9))
                return self._execute_move(default_retreat)

        return False

    def _select_units(self, unit_ids: list[int]) -> bool:
        """选中单位

        策略:
        1. 少量单位: 逐个点击单位像素位置
        2. 大量单位: 框选(左下滑到右上)

        注意: 由于 executor 不直接持有单位坐标,
        这里使用框选作为默认策略。当传入 unit_pixels 时,
        可以逐个点击。
        """
        if len(unit_ids) <= 2:
            # 少量单位逐个点击 -- 需要外部传入像素坐标
            # TODO: 从 StateManager 获取单位坐标
            pass

        # 框选: 从战场左下滑到右上来选中前线单位
        sw, sh = self.screen_size
        x1 = int(sw * 0.1)
        y1 = int(sh * 0.90)
        x2 = int(sw * 0.7)
        y2 = int(sh * 0.50)
        return self._swipe(x1, y1, x2, y2, duration_ms=200)

    def _execute_move(self, target: tuple[int, int]) -> bool:
        """执行移动指令: 点击移动按钮,然后点击目标位置"""
        tx, ty = target
        # 点击移动按钮
        btn = self.command_buttons.get("move")
        if not self._tap(btn[0], btn[1]):
            return False
        time.sleep(0.1)
        # 点击目标位置
        return self._tap(tx, ty)

    def _execute_attack(self, target: tuple[int, int]) -> bool:
        """执行攻击指令: 点击攻击按钮,然后点击敌方单位"""
        tx, ty = target
        btn = self.command_buttons.get("attack")
        if not self._tap(btn[0], btn[1]):
            return False
        time.sleep(0.1)
        return self._tap(tx, ty)

    def _execute_attack_ground(self, target: tuple[int, int]) -> bool:
        """执行地面攻击指令"""
        tx, ty = target
        btn = self.command_buttons.get("attack_ground")
        if not self._tap(btn[0], btn[1]):
            return False
        time.sleep(0.1)
        return self._tap(tx, ty)

    def _execute_stop(self) -> bool:
        """执行停止指令"""
        btn = self.command_buttons.get("stop")
        return self._tap(btn[0], btn[1])

    def pause(self) -> bool:
        """点击暂停按钮"""
        return self._tap(self.pause_button[0], self.pause_button[1])

    def resume(self) -> bool:
        """点击暂停按钮(恢复)"""
        return self._tap(self.pause_button[0], self.pause_button[1])

    @property
    def execution_count(self) -> int:
        return self._execution_count