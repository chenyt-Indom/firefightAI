"""
离线分析模块 - 分析录制的游戏会话, 提取状态-动作对

输入: sessions/<session_name>/ (frames/ + touch_events.txt + metadata.json)
输出: sessions/<session_name>/analysis.json (状态-动作对)

使用方法:
  python scripts/analyze_session.py --session my_game_1
  python scripts/analyze_session.py --session my_game_1 --skip_frames 5
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.state.models import UnitType, Team
from loguru import logger


class TouchEvent:
    """单个触控事件"""
    def __init__(self):
        self.timestamp: float = 0.0
        self.action: str = ""  # DOWN, UP, MOVE
        self.x: int = 0
        self.y: int = 0
        self.pressure: int = 0


class TouchGesture:
    """完整的触控手势 (一次DOWN→MOVE*→UP)"""
    def __init__(self):
        self.start_time: float = 0.0
        self.end_time: float = 0.0
        self.start_x: int = 0
        self.start_y: int = 0
        self.end_x: int = 0
        self.end_y: int = 0
        self.duration_ms: float = 0.0
        self.distance: float = 0.0  # 欧氏距离
        self.gesture_type: str = ""  # tap, short_press, drag, long_press


class TouchParser:
    """解析ADB getevent触控事件流"""

    # getevent -lt 输出格式:
    # [  123456.789] /dev/input/event1: EV_ABS  ABS_MT_POSITION_X    00000345
    _LINE_RE = re.compile(
        r'\[\s*([\d.]+)\]\s+(\S+)\s+(\S+)\s+(\S+)'
    )

    def __init__(self, touch_file: Path):
        self.touch_file = touch_file
        self.events: list[TouchEvent] = []
        self.gestures: list[TouchGesture] = []

    def parse(self) -> int:
        """解析触控事件文件, 返回手势数量"""
        if not self.touch_file.exists():
            logger.warning(f"触控文件不存在: {self.touch_file}")
            return 0

        raw = self.touch_file.read_text(encoding="utf-8", errors="replace")

        current_event = TouchEvent()
        collecting = False

        for line in raw.split("\n"):
            if line.startswith("#"):
                continue

            m = self._LINE_RE.match(line.strip())
            if not m:
                continue

            timestamp = float(m.group(1))
            ev_type = m.group(2)
            ev_code = m.group(3)
            ev_value_str = m.group(4)
            try:
                ev_value = int(ev_value_str, 16)
            except ValueError:
                ev_value = ev_value_str  # 文本标签 DOWN/UP

            # 只关注触控相关事件
            if ev_type == "EV_ABS":
                if ev_code == "ABS_MT_POSITION_X" or ev_code == "ABS_MT_POSITION_X":
                    current_event.x = ev_value
                elif ev_code == "ABS_MT_POSITION_Y" or ev_code == "ABS_MT_POSITION_Y":
                    current_event.y = ev_value
                elif ev_code == "ABS_MT_PRESSURE":
                    current_event.pressure = ev_value

            elif ev_type == "EV_KEY":
                if ev_code in ("BTN_TOUCH", "BTN_TOCH"):
                    if ev_value == "DOWN" or ev_value == 1:  # DOWN
                        # 保存之前累积的坐标
                        saved_x, saved_y, saved_p = current_event.x, current_event.y, current_event.pressure
                        current_event = TouchEvent()
                        current_event.timestamp = timestamp
                        current_event.action = "DOWN"
                        current_event.x = saved_x
                        current_event.y = saved_y
                        current_event.pressure = saved_p
                        collecting = True
                        self.events.append(current_event)
                    elif ev_value == "UP" or ev_value == 0:  # UP
                        if collecting:
                            up_event = TouchEvent()
                            up_event.timestamp = timestamp
                            up_event.action = "UP"
                            up_event.x = current_event.x
                            up_event.y = current_event.y
                            up_event.pressure = current_event.pressure
                            self.events.append(up_event)
                            collecting = False
                            current_event = TouchEvent()

            elif ev_type == "EV_SYN" and ev_code == "SYN_REPORT":
                if collecting:
                    # 保存move事件
                    move_event = TouchEvent()
                    move_event.timestamp = timestamp
                    move_event.action = "MOVE"
                    move_event.x = current_event.x
                    move_event.y = current_event.y
                    move_event.pressure = current_event.pressure
                    self.events.append(move_event)

        # 将事件分组为手势
        self._group_gestures()

        logger.info(f"触控解析: {len(self.events)} 事件 -> {len(self.gestures)} 手势")
        return len(self.gestures)

    def _group_gestures(self) -> None:
        """将零散事件分组为完整手势"""
        gesture = None
        for ev in self.events:
            if ev.action == "DOWN":
                gesture = TouchGesture()
                gesture.start_time = ev.timestamp
                gesture.start_x = ev.x
                gesture.start_y = ev.y
                gesture.end_x = ev.x
                gesture.end_y = ev.y
            elif ev.action == "MOVE" and gesture:
                gesture.end_x = ev.x
                gesture.end_y = ev.y
            elif ev.action == "UP" and gesture:
                gesture.end_time = ev.timestamp
                gesture.end_x = ev.x
                gesture.end_y = ev.y
                gesture.duration_ms = (gesture.end_time - gesture.start_time) * 1000
                gesture.distance = (
                    (gesture.end_x - gesture.start_x) ** 2 +
                    (gesture.end_y - gesture.start_y) ** 2
                ) ** 0.5
                gesture.gesture_type = self._classify_gesture(gesture)
                self.gestures.append(gesture)
                gesture = None

    def _classify_gesture(self, g: TouchGesture) -> str:
        """分类手势类型"""
        if g.duration_ms < 200:
            if g.distance < 30:
                return "tap"
            else:
                return "swipe"
        elif g.duration_ms < 500:
            if g.distance < 30:
                return "short_press"
            else:
                return "drag"
        else:
            if g.distance < 30:
                return "long_press"
            else:
                return "long_drag"


class GameAction:
    """从触控手势推断的游戏动作"""
    def __init__(self):
        self.timestamp: float = 0.0
        self.frame_idx: int = -1
        self.action_type: str = ""  # select, deselect, move, attack, swipe_map
        self.selected_unit: Optional[str] = None  # 选中的单位track_id
        self.target_x: float = 0.0  # 归一化坐标
        self.target_y: float = 0.0
        self.target_enemy: Optional[str] = None  # 攻击目标ID
        self.raw_gesture: Optional[TouchGesture] = None


class SessionAnalyzer:
    """会话分析器 - 将录屏+触控数据转化为状态-动作对"""

    def __init__(
        self,
        session_dir: str | Path,
        screen_width: int = 1280,
        screen_height: int = 720,
    ):
        self.session_dir = Path(session_dir)
        self.screen_width = screen_width
        self.screen_height = screen_height

        self.frames_dir = self.session_dir / "frames"
        self.touch_file = self.session_dir / "touch_events.txt"
        self.metadata_file = self.session_dir / "metadata.json"
        self.frame_index_file = self.session_dir / "frame_index.jsonl"

        self.metadata: dict = {}
        self.frame_index: list[dict] = []
        self.gestures: list[TouchGesture] = []
        self.actions: list[GameAction] = []
        self.state_action_pairs: list[dict] = []

    def load(self) -> bool:
        """加载会话数据"""
        if not self.session_dir.exists():
            logger.error(f"会话目录不存在: {self.session_dir}")
            return False

        # 加载元数据
        if self.metadata_file.exists():
            self.metadata = json.loads(self.metadata_file.read_text(encoding="utf-8"))

        # 加载帧索引
        if self.frame_index_file.exists():
            for line in self.frame_index_file.read_text(encoding="utf-8").strip().split("\n"):
                if line.strip():
                    self.frame_index.append(json.loads(line))

        # 解析触控事件
        parser = TouchParser(self.touch_file)
        parser.parse()
        self.gestures = parser.gestures

        logger.info(f"会话加载: {len(self.frame_index)} 帧, {len(self.gestures)} 手势")
        return True

    def analyze(self, skip_frames: int = 5) -> list[dict]:
        """
        分析会话, 提取状态-动作对

        skip_frames: 每隔多少帧分析一次 (减少计算量)
        """
        logger.info(f"开始分析会话 (skip_frames={skip_frames})...")

        if not self.gestures:
            logger.warning("没有触控手势数据, 无法进行动作分析")
            return []

        # 将手势匹配到最近的帧
        self._match_gestures_to_frames()

        # 提取状态-动作对
        self._extract_state_action_pairs(skip_frames)

        # 保存分析结果
        self._save_results()

        return self.state_action_pairs

    def _match_gestures_to_frames(self) -> None:
        """将触控手势时间戳匹配到帧"""
        if not self.frame_index or not self.gestures:
            return

        # 获取帧时间基准
        base_time = self.frame_index[0]["timestamp"] if self.frame_index else 0

        # 将手势时间戳转换为相对于帧的偏移
        gesture_start = self.gestures[0].start_time if self.gestures else 0

        for g in self.gestures:
            # 手势时间相对于录屏开始时间
            relative_time = g.start_time - gesture_start

            # 找到最近的帧
            best_frame = 0
            best_diff = float("inf")
            for frame in self.frame_index:
                diff = abs(frame["elapsed"] - relative_time)
                if diff < best_diff:
                    best_diff = diff
                    best_frame = frame["frame"]

            action = GameAction()
            action.timestamp = relative_time
            action.frame_idx = best_frame
            action.raw_gesture = g
            action.action_type = self._infer_action_type(g)
            action.target_x = g.end_x / self.screen_width
            action.target_y = g.end_y / self.screen_height

            self.actions.append(action)

        logger.info(f"手势-帧匹配: {len(self.actions)} 个动作")

    def _infer_action_type(self, g: TouchGesture) -> str:
        """从手势推断游戏动作类型"""
        if g.gesture_type == "tap":
            # 单击 = 选择单位 / 取消选择
            return "select"
        elif g.gesture_type == "swipe":
            # 快速滑动 = 移动画面 / 小地图
            return "swipe_map"
        elif g.gesture_type == "drag":
            # 拖拽 = 移动单位 / 攻击移动
            return "move"
        elif g.gesture_type == "long_press":
            # 长按 = 可能有特殊操作
            return "long_press"
        elif g.gesture_type == "short_press":
            return "select"
        elif g.gesture_type == "long_drag":
            return "move"
        return "unknown"

    def _extract_state_action_pairs(self, skip_frames: int = 5) -> None:
        """提取状态-动作对"""
        # 对每个动作, 找到对应的帧作为"状态"
        pairs = []

        for action in self.actions:
            frame_idx = action.frame_idx

            # 找到最近的帧文件
            frame_file = self.frames_dir / f"frame_{frame_idx:06d}.png"
            if not frame_file.exists():
                continue

            state_action = {
                "frame_idx": frame_idx,
                "frame_file": str(frame_file.relative_to(self.session_dir)),
                "action_type": action.action_type,
                "target": [round(action.target_x, 4), round(action.target_y, 4)],
                "gesture_type": action.raw_gesture.gesture_type if action.raw_gesture else "",
                "duration_ms": round(action.raw_gesture.duration_ms, 1) if action.raw_gesture else 0,
                "start_pos": [action.raw_gesture.start_x, action.raw_gesture.start_y] if action.raw_gesture else [],
                "end_pos": [action.raw_gesture.end_x, action.raw_gesture.end_y] if action.raw_gesture else [],
            }

            pairs.append(state_action)

        self.state_action_pairs = pairs
        logger.info(f"状态-动作对: {len(pairs)} 个")

    def _save_results(self) -> None:
        """保存分析结果"""
        result = {
            "session_name": self.metadata.get("session_name", self.session_dir.name),
            "analyzed_at": datetime.now().isoformat(),
            "total_frames": len(self.frame_index),
            "total_gestures": len(self.gestures),
            "total_actions": len(self.actions),
            "state_action_pairs": len(self.state_action_pairs),
            "screen_size": [self.screen_width, self.screen_height],
            "metadata": self.metadata,
            "gesture_stats": self._gesture_stats(),
            "actions": [
                {
                    "frame_idx": a.frame_idx,
                    "action_type": a.action_type,
                    "target_x": round(a.target_x, 4),
                    "target_y": round(a.target_y, 4),
                    "gesture": a.raw_gesture.gesture_type if a.raw_gesture else "",
                }
                for a in self.actions[:500]  # 最多保存500个
            ],
            "state_action_pairs": self.state_action_pairs[:500],
        }

        out_path = self.session_dir / "analysis.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"分析结果已保存: {out_path}")

    def _gesture_stats(self) -> dict:
        """手势统计"""
        stats = defaultdict(int)
        for a in self.actions:
            stats[a.action_type] += 1
        return dict(stats)

    def print_summary(self) -> None:
        """打印分析摘要"""
        print(f"\n{'='*50}")
        print(f"  会话分析: {self.session_dir.name}")
        print(f"  总帧数: {len(self.frame_index)}")
        print(f"  触控手势: {len(self.gestures)}")
        print(f"  推断动作: {len(self.actions)}")
        print(f"  状态-动作对: {len(self.state_action_pairs)}")
        print(f"\n  动作类型分布:")
        stats = self._gesture_stats()
        for action_type, count in sorted(stats.items(), key=lambda x: -x[1]):
            print(f"    {action_type}: {count}")
        print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(
        description="Firefight AI - 会话分析器 (触控→游戏动作)",
        epilog="""
示例:
  python scripts/analyze_session.py --session my_game_1
  python scripts/analyze_session.py --session my_game_1 --skip_frames 10
        """,
    )
    parser.add_argument("--session", "-s", required=True, help="会话名称")
    parser.add_argument("--sessions_dir", default="sessions", help="会话根目录")
    parser.add_argument("--skip_frames", type=int, default=5,
                        help="每隔N帧分析一次, 默认5")
    parser.add_argument("--width", type=int, default=1280, help="屏幕宽度")
    parser.add_argument("--height", type=int, default=720, help="屏幕高度")

    args = parser.parse_args()

    session_dir = Path(args.sessions_dir) / args.session

    analyzer = SessionAnalyzer(
        session_dir=session_dir,
        screen_width=args.width,
        screen_height=args.height,
    )

    if not analyzer.load():
        logger.error("会话加载失败!")
        sys.exit(1)

    analyzer.analyze(skip_frames=args.skip_frames)
    analyzer.print_summary()


if __name__ == "__main__":
    main()