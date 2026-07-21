"""数据模型单元测试"""
import pytest

from src.state.models import (
    Unit, UnitType, Team, GameState,
    Command, LLMResponse, ActionType,
)


class TestUnit:
    def test_create_tank(self):
        unit = Unit(
            track_id=1,
            unit_type=UnitType.TANK,
            team=Team.ALLY,
            x=200, y=500,
            bbox=(180, 480, 220, 520),
            confidence=0.95,
        )
        assert unit.unit_type == UnitType.TANK
        assert unit.team == Team.ALLY
        assert unit.center == (200, 500)

    def test_to_normalized(self):
        unit = Unit(
            track_id=1,
            unit_type=UnitType.TANK,
            team=Team.ALLY,
            x=200, y=500,
            bbox=(180, 480, 220, 520),
            confidence=0.95,
        )
        nx, ny = unit.to_normalized(1280, 720)
        assert 0.15 < nx < 0.16
        assert 0.69 < ny < 0.70

    def test_stale_flag(self):
        unit = Unit(
            track_id=1,
            unit_type=UnitType.INFANTRY,
            team=Team.ALLY,
            x=100, y=100,
            bbox=(90, 90, 110, 110),
            confidence=0.5,
            stale=True,
        )
        assert unit.stale is True


class TestGameState:
    def test_allies_enemies(self):
        state = GameState(
            frame_id=1,
            units=[
                Unit(1, UnitType.TANK, Team.ALLY, 100, 500, (80, 480, 120, 520), 0.9),
                Unit(2, UnitType.IFV, Team.ALLY, 150, 520, (130, 500, 170, 540), 0.9),
                Unit(101, UnitType.TANK, Team.ENEMY, 900, 200, (880, 180, 920, 220), 0.9),
                Unit(102, UnitType.SNIPER, Team.ENEMY, 1000, 150, (990, 140, 1010, 160), 0.8),
            ],
            screen_size=(1280, 720),
        )
        assert state.ally_count == 2
        assert state.enemy_count == 2
        assert len(state.allies) == 2
        assert len(state.enemies) == 2

    def test_get_unit_by_id(self):
        state = GameState(
            frame_id=1,
            units=[Unit(5, UnitType.TANK, Team.ALLY, 100, 100, (90, 90, 110, 110), 0.9)],
            screen_size=(1280, 720),
        )
        unit = state.get_unit_by_id(5)
        assert unit is not None
        assert unit.track_id == 5
        assert state.get_unit_by_id(999) is None

    def test_to_llm_text(self):
        state = GameState(
            frame_id=1,
            units=[
                Unit(1, UnitType.TANK, Team.ALLY, 200, 500, (180, 480, 220, 520), 0.95),
                Unit(101, UnitType.HELICOPTER, Team.ENEMY, 900, 200, (880, 180, 920, 220), 0.9),
            ],
            screen_size=(1280, 720),
            credits=500,
            population=8,
            max_population=30,
        )
        text = state.to_llm_text()
        assert "己方总兵力: 1" in text
        assert "敌方总兵力: 1" in text
        assert "主战坦克" in text
        assert "直升机" in text


class TestCommand:
    def test_valid_command(self):
        cmd = Command(
            action=ActionType.MOVE,
            unit_ids=[1, 2],
            target=[0.5, 0.5],
            reason="test",
        )
        assert cmd.action == ActionType.MOVE
        assert cmd.target == [0.5, 0.5]

    def test_invalid_target(self):
        with pytest.raises(Exception):
            Command(
                action=ActionType.MOVE,
                unit_ids=[1],
                target=[1.5, 0.5],  # 超出范围
                reason="test",
            )

    def test_empty_unit_ids(self):
        with pytest.raises(Exception):
            Command(
                action=ActionType.MOVE,
                unit_ids=[],  # 空列表
                target=[0.5, 0.5],
                reason="test",
            )


class TestLLMResponse:
    def test_valid_response(self):
        resp = LLMResponse(
            analysis="测试分析",
            commands=[
                Command(
                    action=ActionType.MOVE,
                    unit_ids=[1],
                    target=[0.5, 0.5],
                    reason="test",
                )
            ],
        )
        assert resp.analysis == "测试分析"
        assert len(resp.commands) == 1

    def test_empty_commands(self):
        with pytest.raises(Exception):
            LLMResponse(analysis="测试", commands=[])