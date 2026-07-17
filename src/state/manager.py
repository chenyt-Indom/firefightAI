"""状态管理器 - 组装游戏状态,执行敌我识别,维护跟踪数据"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

import numpy as np
from loguru import logger

from src.state.models import GameState, Unit, UnitType, Team
from src.utils.logger import log_state


class StateManager:
    """游戏状态管理器"""

    def __init__(
        self,
        screen_size: tuple[int, int] = (1280, 720),
        ally_region: tuple[float, float, float, float] = (0.0, 0.55, 1.0, 1.0),
        enemy_region: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 0.45),
    ):
        self.screen_size = screen_size
        self.ally_region = ally_region
        self.enemy_region = enemy_region
        self.frame_id = 0
        self._unit_history: dict[int, list[Unit]] = defaultdict(list)
        self._max_history = 10  # 保留最近10帧历史
        self._last_state: Optional[GameState] = None

    def build(
        self,
        detected_units: list[Unit],
        ui_data: dict,
        frame: Optional[np.ndarray] = None,
    ) -> GameState:
        """构建当前帧的游戏状态

        Args:
            detected_units: YOLO检测到的单位列表
            ui_data: OCR读取的UI数据
            frame: 原始帧(可选,用于调试)

        Returns:
            GameState实例
        """
        self.frame_id += 1

        # 更新单位历史
        for unit in detected_units:
            history = self._unit_history[unit.track_id]
            history.append(unit)
            if len(history) > self._max_history:
                history.pop(0)

        # 敌我识别优化:结合位置和历史
        for unit in detected_units:
            if unit.team == Team.ALLY:
                # 检查是否穿越到了敌方区域
                if unit.y < self.screen_size[1] * 0.45:
                    # 可能是误判,检查历史
                    history = self._unit_history.get(unit.track_id, [])
                    if history:
                        ally_count = sum(1 for u in history if u.team == Team.ALLY)
                        if ally_count < len(history) * 0.5:
                            unit.team = Team.ENEMY
                            unit.track_id += 100

        # 解析UI数据
        credits = self._parse_int(ui_data.get("resource_bar", ""), 0)
        population = 0
        max_population = 30

        # 构建GameState
        state = GameState(
            frame_id=self.frame_id,
            units=detected_units,
            ui=ui_data,
            screen_size=self.screen_size,
            timestamp=time.time(),
            credits=credits,
            population=population,
            max_population=max_population,
        )

        self._last_state = state

        log_state(
            f"Frame {self.frame_id}: "
            f"友方={state.ally_count}, 敌方={state.enemy_count}, "
            f"资金={credits}"
        )

        return state

    def get_missing_allies(self) -> list[int]:
        """检测已消失的友方单位(被消灭)"""
        if self._last_state is None:
            return []
        current_ids = {u.track_id for u in self._last_state.allies}
        # 查找历史中存在但当前不存在的友方ID
        missing = []
        for tid, history in self._unit_history.items():
            if tid not in current_ids and history:
                last_unit = history[-1]
                if last_unit.team == Team.ALLY:
                    missing.append(tid)
        return missing

    def get_unit_history(self, track_id: int) -> list[Unit]:
        """获取单位历史轨迹"""
        return self._unit_history.get(track_id, [])

    @property
    def last_state(self) -> Optional[GameState]:
        return self._last_state

    @staticmethod
    def _parse_int(text: str, default: int = 0) -> int:
        """从文本中解析整数"""
        import re
        numbers = re.findall(r'\d+', text)
        return int(numbers[0]) if numbers else default