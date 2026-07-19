"""核心数据模型 - 使用dataclass定义游戏状态和单位"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ============================================================
# 单位相关
# ============================================================

class UnitType(str, Enum):
    """现代MOD单位类型 (基于APK逆向分析, 1554个现代单位, 17个国家阵营)"""
    # 本MOD为纯地面作战模组, 无直升机和建筑单位
    TANK = "tank"              # 主战坦克/突击炮 (409个: M1A2/T-90/Leopard2/ZTZ-99等)
    IFV = "ifv"                # 步兵战车/装甲车 (527个: BMP/BTR/Bradley/Stryker等)
    INFANTRY = "infantry"      # 步兵班组/重武器 (618个: 步兵班/HMG/迫击炮/ATGM/AT炮)
    SNIPER = "sniper"          # 狙击手 (13种狙击步枪, 作为步兵子类)
    HELICOPTER = "helicopter"  # 武装直升机 (本MOD不含, 预留扩展)
    BUILDING = "building"      # 建筑/据点 (本MOD不含, 预留扩展)


class Team(str, Enum):
    ALLY = "ally"
    ENEMY = "enemy"
    UNKNOWN = "unknown"  # 颜色检测无法判定时使用


# 单位类型中文名映射
UNIT_TYPE_CN: dict[UnitType, str] = {
    UnitType.TANK: "主战坦克",
    UnitType.IFV: "步兵战车",
    UnitType.INFANTRY: "步兵班组",
    UnitType.SNIPER: "狙击手",
    UnitType.HELICOPTER: "直升机(预留)",
    UnitType.BUILDING: "建筑(预留)",
}

# 单位类型威胁等级 (1-5, 5最高)
# 基于APK武器数据分析: 坦克炮(149种, 120/125mm) > ATGM(26种) > IFV机炮(30mm) > HMG > 步兵轻武器
UNIT_THREAT_LEVEL: dict[UnitType, int] = {
    UnitType.TANK: 5,        # 主战坦克威胁最高, 120/125mm主炮可一击摧毁IFV
    UnitType.IFV: 4,         # IFV搭载ATGM和机炮, 对步兵和坦克均有威胁
    UnitType.INFANTRY: 2,    # 步兵威胁较低, 但ATGM/RPG班组对装甲威胁大
    UnitType.SNIPER: 3,      # 狙击手对步兵威胁高, 对装甲无威胁
    UnitType.HELICOPTER: 4,  # 预留
    UnitType.BUILDING: 1,    # 预留
}


@dataclass
class Unit:
    """战场单位"""
    track_id: int              # 持久ID (ByteTrack分配, 敌方+100偏移)
    unit_type: UnitType        # 单位类型
    team: Team                 # 友方/敌方
    x: int                     # 屏幕像素坐标(中心点)
    y: int
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    confidence: float          # 检测置信度
    health: Optional[int] = None   # 血量百分比 (OCR读取)
    stale: bool = False        # 是否使用了上一帧的陈旧结果

    @property
    def center(self) -> tuple[int, int]:
        """中心点坐标"""
        return (self.x, self.y)

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    def to_normalized(self, screen_w: int, screen_h: int) -> tuple[float, float]:
        """转换为归一化坐标(0-1)"""
        return (self.x / screen_w, self.y / screen_h)

    def to_dict(self) -> dict:
        return {
            "track_id": self.track_id,
            "unit_type": self.unit_type.value,
            "team": self.team.value,
            "x": self.x,
            "y": self.y,
            "bbox": list(self.bbox),
            "confidence": round(self.confidence, 3),
            "health": self.health,
            "stale": self.stale,
        }

    def to_llm_text(self, screen_w: int, screen_h: int) -> str:
        """生成给LLM看的文本描述"""
        nx, ny = self.to_normalized(screen_w, screen_h)
        cn_name = UNIT_TYPE_CN.get(self.unit_type, self.unit_type.value)
        parts = [
            f"| {self.track_id} | {cn_name}({self.unit_type.value})",
            f"| ({nx:.2f},{ny:.2f})",
        ]
        if self.health is not None:
            parts.append(f"| {self.health}%")
        else:
            parts.append("| ?")
        if self.stale:
            parts.append("| [陈旧]")
        return " ".join(parts)


# ============================================================
# 游戏状态
# ============================================================

@dataclass
class GameState:
    """一帧游戏状态快照"""
    frame_id: int
    units: list[Unit] = field(default_factory=list)
    ui: dict = field(default_factory=dict)  # OCR读取的UI数据
    screen_size: tuple[int, int] = (1280, 720)
    timestamp: float = 0.0

    # UI数据字段
    credits: int = 0         # 资金
    population: int = 0      # 当前人口
    max_population: int = 0  # 最大人口
    is_paused: bool = False
    is_game_over: bool = False
    is_victory: bool = False

    @property
    def allies(self) -> list[Unit]:
        """筛选友方单位"""
        return [u for u in self.units if u.team == Team.ALLY]

    @property
    def enemies(self) -> list[Unit]:
        """筛选敌方单位"""
        return [u for u in self.units if u.team == Team.ENEMY]

    @property
    def ally_count(self) -> int:
        return len(self.allies)

    @property
    def enemy_count(self) -> int:
        return len(self.enemies)

    def get_unit_by_id(self, track_id: int) -> Optional[Unit]:
        """根据track_id获取单位"""
        for u in self.units:
            if u.track_id == track_id:
                return u
        return None

    def get_units_by_ids(self, track_ids: list[int]) -> list[Unit]:
        """根据track_id列表获取单位"""
        id_set = set(track_ids)
        return [u for u in self.units if u.track_id in id_set]

    def get_units_by_type(self, unit_type: UnitType, team: Optional[Team] = None) -> list[Unit]:
        """按类型筛选单位"""
        result = [u for u in self.units if u.unit_type == unit_type]
        if team:
            result = [u for u in result if u.team == team]
        return result

    def to_dict(self) -> dict:
        return {
            "frame_id": self.frame_id,
            "units": [u.to_dict() for u in self.units],
            "ui": self.ui,
            "screen_size": list(self.screen_size),
            "credits": self.credits,
            "population": self.population,
            "max_population": self.max_population,
            "is_paused": self.is_paused,
            "is_game_over": self.is_game_over,
            "is_victory": self.is_victory,
        }

    def to_llm_text(self, compact: bool = True) -> str:
        """序列化为LLM可读的文本格式

        compact=True: 精简格式 (少tokens, 快响应, 用于实时模式)
        compact=False: 完整表格格式 (用于调试和分析)
        """
        screen_w, screen_h = self.screen_size

        if compact:
            return self._to_compact_text(screen_w, screen_h)
        return self._to_full_text(screen_w, screen_h)

    def _to_compact_text(self, sw: int, sh: int) -> str:
        """精简文本格式 — 最小token开销"""
        lines = [f"友{self.ally_count}vs敌{self.enemy_count}"]

        if self.allies:
            # 只列前20个友军 (按x坐标分组, 取代表)
            sample = self.allies[:min(len(self.allies), 20)]
            ally_strs = []
            for u in sample:
                nx, ny = u.to_normalized(sw, sh)
                ally_strs.append(f"({nx:.2f},{ny:.2f})")
            lines.append(f"A:{','.join(ally_strs)}")

        if self.enemies:
            sample = self.enemies[:min(len(self.enemies), 20)]
            enemy_strs = []
            for u in sample:
                nx, ny = u.to_normalized(sw, sh)
                enemy_strs.append(f"({nx:.2f},{ny:.2f})")
            lines.append(f"E:{','.join(enemy_strs)}")

        return "\n".join(lines)

    def _to_full_text(self, sw: int, sh: int) -> str:
        """完整格式 (调试用)"""
        lines = [
            "## 战场状态",
            f"己方={self.ally_count} 敌方={self.enemy_count}",
            f"资金={self.credits} 人口={self.population}/{self.max_population}",
        ]

        if self.allies:
            lines.append("### 己方")
            for u in self.allies:
                nx, ny = u.to_normalized(sw, sh)
                cn = UNIT_TYPE_CN.get(u.unit_type, u.unit_type.value)
                lines.append(f"| {u.track_id} | {cn} | ({nx:.2f},{ny:.2f})")

        if self.enemies:
            lines.append("### 敌方")
            for u in self.enemies:
                nx, ny = u.to_normalized(sw, sh)
                cn = UNIT_TYPE_CN.get(u.unit_type, u.unit_type.value)
                threat = UNIT_THREAT_LEVEL.get(u.unit_type, 1)
                lines.append(f"| {u.track_id} | {cn} | ({nx:.2f},{ny:.2f}) {'★'*threat}")

        return "\n".join(lines)


# ============================================================
# LLM 指令模型
# ============================================================

class ActionType(str, Enum):
    SELECT = "select"      # tap选中=自动接敌
    MOVE = "move"          # 双击拖拽精确移动
    ATTACK = "attack"
    ATTACK_GROUND = "attack_ground"
    STOP = "stop"
    RETREAT = "retreat"
    ZOOM_IN = "zoom_in"    # 双指放大(看清局部)
    ZOOM_OUT = "zoom_out"  # 双指缩小(掌控全局)


class Command(BaseModel):
    """LLM输出的单条指令"""
    action: ActionType = Field(description="指令类型: move/attack/attack_ground/stop/retreat")
    unit_ids: list[int] = Field(default=[], description="操作哪些单位(zoom/select可为空)")
    target_enemy_id: Optional[int] = Field(default=None, description="攻击目标敌方单位ID(仅attack)")  # noqa: E501
    target: Optional[list[float]] = Field(default=None, description="[x,y] 归一化坐标0-1 (move/attack_ground)")  # noqa: E501
    reason: str = Field(default="", description="执行此指令的战术理由")

    @field_validator("target")
    @classmethod
    def validate_target(cls, v: Optional[list[float]]) -> Optional[list[float]]:
        if v is not None:
            if len(v) != 2:
                raise ValueError("target必须是[x, y]格式")
            if not all(0.0 <= c <= 1.0 for c in v):
                raise ValueError("target坐标必须在0-1范围内")
        return v

    @field_validator("unit_ids")
    @classmethod
    def validate_unit_ids(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("unit_ids不能为空")
        return v


class LLMResponse(BaseModel):
    """LLM完整响应"""
    analysis: str = Field(description="简要战场分析(1-2句)")
    next_prediction: str = Field(default="", description="预测敌军可能下一步行动")
    commands: list[Command] = Field(description="战术指令列表")

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, v: list[Command]) -> list[Command]:
        # 允许空指令(无单位时可返回空列表)
        return v