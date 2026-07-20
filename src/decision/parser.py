"""指令解析器 - 将LLM输出的归一化坐标转换为像素坐标,校验指令合法性"""
from __future__ import annotations

from typing import Optional

from loguru import logger

from src.state.models import Command, LLMResponse, GameState, ActionType


class CommandParser:
    """LLM指令解析和校验"""

    def __init__(self, screen_size: tuple[int, int] = (1280, 720)):
        self.screen_size = screen_size

    def parse(self, llm_response: LLMResponse, game_state: GameState) -> list[ParsedCommand]:
        """解析LLM响应,转换为可执行的像素级指令

        Args:
            llm_response: LLM返回的响应
            game_state: 当前游戏状态

        Returns:
            可执行的指令列表
        """
        parsed_commands: list[ParsedCommand] = []

        for cmd in llm_response.commands:
            parsed = self._parse_single(cmd, game_state)
            if parsed:
                parsed_commands.append(parsed)

        logger.info(f"指令解析完成: {len(llm_response.commands)}条 -> {len(parsed_commands)}条有效")
        return parsed_commands

    def _parse_single(self, cmd: Command, game_state: GameState) -> Optional["ParsedCommand"]:
        """解析单条指令"""
        sw, sh = self.screen_size

        # zoom 指令不需要 unit_ids
        if cmd.action in (ActionType.ZOOM_IN, ActionType.ZOOM_OUT):
            return ParsedCommand(
                action=cmd.action,
                unit_ids=[],
                target_pixel=None,
                target_enemy_pixel=None,
                reason=cmd.reason,
            )

        # 校验unit_ids是否存在
        valid_units = game_state.get_units_by_ids(cmd.unit_ids)
        if not valid_units:
            logger.warning(f"指令中的unit_ids无效: {cmd.unit_ids}")
            return None

        valid_ids = [u.track_id for u in valid_units]

        # 转换目标坐标
        target_pixel: Optional[tuple[int, int]] = None
        if cmd.target is not None:
            tx = int(self._clamp(cmd.target[0], 0, 1) * sw)
            ty = int(self._clamp(cmd.target[1], 0, 1) * sh)
            target_pixel = (tx, ty)

        # 校验攻击目标
        target_enemy_pixel: Optional[tuple[int, int]] = None
        if cmd.target_enemy_id is not None:
            enemy = game_state.get_unit_by_id(cmd.target_enemy_id)
            if enemy is None:
                logger.warning(f"攻击目标不存在: {cmd.target_enemy_id}, 降级为移动到敌方位置")
                # 降级:尝试从历史中查找
                if target_pixel is None and cmd.target is not None:
                    target_pixel = (
                        int(self._clamp(cmd.target[0], 0, 1) * sw),
                        int(self._clamp(cmd.target[1], 0, 1) * sh),
                    )
            else:
                target_enemy_pixel = (enemy.x, enemy.y)

        return ParsedCommand(
            action=cmd.action,
            unit_ids=valid_ids,
            target_pixel=target_pixel,
            target_enemy_pixel=target_enemy_pixel,
            reason=cmd.reason,
        )

    def generate_fallback_commands(self, game_state: GameState) -> list["ParsedCommand"]:
        """LLM失败时的高级战术策略 - 不再单纯防御，而是主动接敌"""
        sw, sh = self.screen_size
        ally_ids = [u.track_id for u in game_state.allies]
        enemy_list = game_state.enemies

        if not ally_ids:
            return []

        # 🔥 如果有敌人，分散友军进攻最近敌人
        if enemy_list:
            cmds = []
            # 分批：一半进攻，一半掩护
            mid = len(ally_ids) // 2 or 1
            attack_ids = ally_ids[:mid]
            cover_ids = ally_ids[mid:]
            
            # 找最近敌人
            center_x = int(sum(e.x for e in enemy_list) / len(enemy_list))
            center_y = int(sum(e.y for e in enemy_list) / len(enemy_list))
            
            if attack_ids:
                cmds.append(ParsedCommand(
                    action=ActionType.SELECT, unit_ids=attack_ids,
                    target_pixel=None, reason="[智能] 选中进攻编队"
                ))
                cmds.append(ParsedCommand(
                    action=ActionType.MOVE, unit_ids=attack_ids,
                    target_pixel=(min(sw-50, max(50, center_x+20)), min(sh-50, max(50, center_y+10))),
                    reason="[智能] 主动接敌"
                ))
            if cover_ids:
                cmds.append(ParsedCommand(
                    action=ActionType.MOVE, unit_ids=cover_ids,
                    target_pixel=(int(sw*0.5), int(sh*0.7)),
                    reason="[智能] 掩护编队"
                ))
            return cmds
        
        # 无敌人：扇形散开搜索
        spread = len(ally_ids)
        cmds = []
        for i, uid in enumerate(ally_ids):
            angle = (i / spread) * 3.14 - 0.8
            sx = int(sw * 0.5 + sw * 0.35 * (i / max(spread-1, 1) - 0.5))
            sy = int(sh * 0.3 + abs(i - spread/2) * 8)
            sx = max(50, min(sw-50, sx))
            sy = max(50, min(sh-150, sy))
            cmds.append(ParsedCommand(
                action=ActionType.MOVE, unit_ids=[uid],
                target_pixel=(sx, sy),
                reason="[智能] 扇形搜索"
            ))
        return cmds

    @staticmethod
    def _clamp(value: float, min_val: float, max_val: float) -> float:
        """限制值在范围内"""
        return max(min_val, min(max_val, value))


class ParsedCommand:
    """解析后的可执行指令(像素坐标)"""

    def __init__(
        self,
        action: ActionType,
        unit_ids: list[int],
        target_pixel: Optional[tuple[int, int]] = None,
        target_enemy_pixel: Optional[tuple[int, int]] = None,
        reason: str = "",
    ):
        self.action = action
        self.unit_ids = unit_ids
        self.target_pixel = target_pixel
        self.target_enemy_pixel = target_enemy_pixel
        self.reason = reason

    def __repr__(self) -> str:
        return (
            f"ParsedCommand(action={self.action.value}, "
            f"units={self.unit_ids}, "
            f"target={self.target_pixel}, "
            f"enemy_target={self.target_enemy_pixel})"
        )