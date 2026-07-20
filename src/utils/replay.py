"""录像回放系统 - 加载和回放已保存的session数据"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger


class ReplayLoader:
    """回放数据加载器"""

    def __init__(self, replay_path: str):
        self.replay_path = Path(replay_path)
        self._data: list[dict] = []
        self._current_index = 0
        self._loaded = False

    def load(self) -> bool:
        """加载回放文件"""
        if not self.replay_path.exists():
            logger.error(f"回放文件不存在: {self.replay_path}")
            return False
        try:
            with open(self.replay_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            self._loaded = True
            logger.info(f"回放加载成功: {len(self._data)}轮, 文件: {self.replay_path}")
            return True
        except Exception as e:
            logger.error(f"回放加载失败: {e}")
            return False

    def get_cycle(self, index: int) -> Optional[dict]:
        """获取指定轮次的数据"""
        if 0 <= index < len(self._data):
            self._current_index = index
            return self._data[index]
        return None

    def next_cycle(self) -> Optional[dict]:
        """获取下一轮数据"""
        if self._current_index < len(self._data):
            data = self._data[self._current_index]
            self._current_index += 1
            return data
        return None

    def prev_cycle(self) -> Optional[dict]:
        """获取上一轮数据"""
        if self._current_index > 0:
            self._current_index -= 1
            return self._data[self._current_index]
        return None

    def reset(self) -> None:
        """重置到第一轮"""
        self._current_index = 0

    @property
    def cycle_count(self) -> int:
        return len(self._data)

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def get_summary(self) -> dict:
        """获取回放摘要"""
        if not self._data:
            return {}

        first = self._data[0]
        last = self._data[-1]

        total_commands = sum(len(c["commands"]) for c in self._data)
        actions = {}
        for c in self._data:
            for cmd in c["commands"]:
                action = cmd["action"]
                actions[action] = actions.get(action, 0) + 1

        return {
            "total_cycles": len(self._data),
            "total_duration": last["timestamp"] - first["timestamp"],
            "total_commands": total_commands,
            "actions_summary": actions,
            "start_time": first["timestamp"],
            "end_time": last["timestamp"],
        }


def print_replay_summary(replay_path: str) -> None:
    """打印回放摘要"""
    loader = ReplayLoader(replay_path)
    if not loader.load():
        return

    summary = loader.get_summary()
    print(f"\n=== 回放摘要: {Path(replay_path).name} ===")
    print(f"总轮次: {summary['total_cycles']}")
    print(f"总耗时: {summary['total_duration']:.0f}s")
    print(f"总指令: {summary['total_commands']}")
    print(f"指令分布: {summary['actions_summary']}")
    print()

    for i, cycle in enumerate(loader._data):
        print(f"--- 第{cycle['cycle']}轮 ---")
        llm_analysis = cycle.get("llm_analysis", "N/A")
        print(f"  AI分析: {llm_analysis}")
        for cmd in cycle["commands"]:
            print(f"  [{cmd['action']}] units={cmd['unit_ids']} reason={cmd['reason']}")
        print()