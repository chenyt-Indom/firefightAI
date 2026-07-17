"""指令执行器 - 将解析后的指令通过ADB执行到游戏"""
from __future__ import annotations

import time
from typing import Optional

from loguru import logger

from src.execution.adb_utils import ADBUtils
from src.decision.parser import ParsedCommand
from src.state.models import ActionType
from src.utils.logger import log_execution


class CommandExecutor:
    """ADB指令执行器,将逻辑指令转换为屏幕操作"""

    def __init__(
        self,
        adb: ADBUtils,
        screen_size: tuple[int, int] = (1280, 720),
        # UI坐标(像素,需通过校准工具获取)
        pause_button: tuple[int, int] = (1216, 36),
        command_buttons: Optional[dict[str, tuple[int, int]]] = None,
    ):
        self.adb = adb
        self.screen_size = screen_size
        self.pause_button = pause_button
        self.command_buttons = command_buttons or {
            "move": (int(110 * screen_size[0] / 1280), int(680 * screen_size[1] / 720)),
            "attack": (int(240 * screen_size[0] / 1280), int(680 * screen_size[1] / 720)),
            "stop": (int(370 * screen_size[0] / 1280), int(680 * screen_size[1] / 720)),
            "retreat": (int(500 * screen_size[0] / 1280), int(680 * screen_size[1] / 720)),
            "attack_ground": (int(630 * screen_size[0] / 1280), int(680 * screen_size[1] / 720)),
        }
        self._execution_count = 0

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

        注意: 由于通过ADB无法直接按track_id选中单位,
        这里采用简化策略: 点击屏幕中央偏下进行框选,
        或通过多次点击逐个选中。实际使用中需要根据游戏
        具体UI调整选中策略。
        """
        # 策略1: 如果只有少量单位,逐个点击
        if len(unit_ids) <= 2:
            for uid in unit_ids:
                # 点击单位位置(需要从状态中获取,此处简化)
                # 实际应传入单位坐标,此处使用屏幕中央作为默认
                pass

        # 策略2: 框选 - 从屏幕左下滑到右上(选择前方区域)
        sw, sh = self.screen_size
        x1 = int(sw * 0.1)
        y1 = int(sh * 0.85)
        x2 = int(sw * 0.7)
        y2 = int(sh * 0.55)
        return self.adb.swipe(x1, y1, x2, y2, duration_ms=200)

    def _execute_move(self, target: tuple[int, int]) -> bool:
        """执行移动指令: 点击移动按钮,然后点击目标位置"""
        tx, ty = target
        # 点击移动按钮
        btn = self.command_buttons.get("move", (110, 680))
        if not self.adb.tap(btn[0], btn[1]):
            return False
        time.sleep(0.1)
        # 点击目标位置
        return self.adb.tap(tx, ty)

    def _execute_attack(self, target: tuple[int, int]) -> bool:
        """执行攻击指令: 点击攻击按钮,然后点击敌方单位"""
        tx, ty = target
        btn = self.command_buttons.get("attack", (240, 680))
        if not self.adb.tap(btn[0], btn[1]):
            return False
        time.sleep(0.1)
        return self.adb.tap(tx, ty)

    def _execute_attack_ground(self, target: tuple[int, int]) -> bool:
        """执行地面攻击指令"""
        tx, ty = target
        btn = self.command_buttons.get("attack_ground", (630, 680))
        if not self.adb.tap(btn[0], btn[1]):
            return False
        time.sleep(0.1)
        return self.adb.tap(tx, ty)

    def _execute_stop(self) -> bool:
        """执行停止指令"""
        btn = self.command_buttons.get("stop", (370, 680))
        return self.adb.tap(btn[0], btn[1])

    def pause(self) -> bool:
        """点击暂停按钮"""
        return self.adb.tap(self.pause_button[0], self.pause_button[1])

    def resume(self) -> bool:
        """点击暂停按钮(恢复)"""
        return self.adb.tap(self.pause_button[0], self.pause_button[1])

    @property
    def execution_count(self) -> int:
        return self._execution_count