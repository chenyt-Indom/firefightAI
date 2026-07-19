"""结果评估器 — 对比前后截图, 计算决策效果评分

评估维度:
  - 敌军减少: +10分/个 (有效击杀了敌人)
  - 友军减少: -10分/个 (己方伤亡)
  - 阵线推进: +3分  (友军向敌方半场移动)
  - 阵线后退: -3分  (友军被逼退)

评分时机: 在下一轮截图时评估上一轮的决策效果
  (执行→等待~3秒→下轮截图→对比上轮截图)

使用颜色检测快速计数 (与 controller 相同的 HSV 方法)
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np


# 颜色检测 HSV 范围 (与 controller/detector 保持一致)
ALLY_HSV_LOW = np.array([95, 100, 100], dtype=np.uint8)
ALLY_HSV_HIGH = np.array([115, 255, 255], dtype=np.uint8)
ENEMY_HSV_LOW_1 = np.array([0, 100, 100], dtype=np.uint8)
ENEMY_HSV_HIGH_1 = np.array([10, 255, 255], dtype=np.uint8)
ENEMY_HSV_LOW_2 = np.array([170, 100, 100], dtype=np.uint8)
ENEMY_HSV_HIGH_2 = np.array([180, 255, 255], dtype=np.uint8)


class OutcomeEvaluator:
    """决策效果评估器

    在每轮开始时, 用当前截图与上一轮截图对比, 评估上一轮决策的效果。
    延迟评估的好处: 给游戏 2-3 秒反应时间, 更准确反映战术效果。
    """

    def __init__(self):
        self._prev_frame: Optional[np.ndarray] = None
        self._prev_ally_count: int = 0
        self._prev_enemy_count: int = 0
        self._prev_ally_y_sum: float = 0.0  # 友军y坐标总和 (判断推进/后退)

    def evaluate(
        self,
        current_frame: np.ndarray,
        prev_frame: Optional[np.ndarray] = None,
    ) -> dict:
        """评估上一轮决策效果

        Args:
            current_frame: 当前帧 (本轮截图)
            prev_frame: 上一轮截图 (可选, 默认用缓存的上次截图)

        Returns:
            {
                "score": 总评分,
                "enemy_delta": 敌军变化数,
                "ally_delta": 友军变化数,
                "enemy_score": 击杀得分,
                "ally_score": 友军伤亡扣分,
                "advance_score": 阵线进退分,
                "details": "人类可读的评估描述"
            }
        """
        # 获取当前计数
        cur_ally, cur_enemy, cur_ally_y = self._count_units(current_frame)

        # 使用上一帧
        prev_ally = self._prev_ally_count
        prev_enemy = self._prev_enemy_count
        prev_ally_y = self._prev_ally_y_sum

        # 计算变化
        enemy_delta = prev_enemy - cur_enemy  # 正=敌人少了 (击杀)
        ally_delta = prev_ally - cur_ally      # 正=友军少了 (伤亡)

        enemy_score = enemy_delta * 10
        ally_score = ally_delta * (-10)

        # 阵线进退: 友军平均y坐标变化
        advance_score = 0
        if prev_ally > 0 and cur_ally > 0:
            prev_avg_y = prev_ally_y / prev_ally
            cur_avg_y = cur_ally_y / cur_ally
            # y坐标越大越靠近底部 (敌方半场在下方)
            # y增加 = 向下推进 = +分
            delta_y = cur_avg_y - prev_avg_y
            advance_score = delta_y * 0.5  # 每像素0.5分

        total = enemy_score + ally_score + advance_score

        # 生成描述
        details = []
        if enemy_delta > 0:
            details.append(f"击杀{enemy_delta}个敌人(+{enemy_score})")
        elif enemy_delta < 0:
            details.append(f"敌人增援{abs(enemy_delta)}个")
        if ally_delta > 0:
            details.append(f"损失{ally_delta}个友军({ally_score})")
        if abs(advance_score) > 1:
            direction = "推进" if advance_score > 0 else "后退"
            details.append(f"阵线{direction}({advance_score:+.1f})")

        # 缓存当前帧数据供下一轮使用
        self._cache_current(cur_ally, cur_enemy, cur_ally_y, current_frame)

        return {
            "score": round(total, 1),
            "enemy_delta": enemy_delta,
            "ally_delta": ally_delta,
            "enemy_score": enemy_score,
            "ally_score": ally_score,
            "advance_score": round(advance_score, 1),
            "details": "; ".join(details) if details else "无明显变化",
        }

    def _count_units(self, frame: np.ndarray) -> tuple[int, int, float]:
        """颜色检测: 统计友军/敌军数量及友军y坐标总和"""
        import cv2

        h, w = frame.shape[:2]

        # 缩小到一半加速
        small = cv2.resize(frame, (w // 2, h // 2))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

        ally_mask = cv2.inRange(hsv, ALLY_HSV_LOW, ALLY_HSV_HIGH)
        enemy_1 = cv2.inRange(hsv, ENEMY_HSV_LOW_1, ENEMY_HSV_HIGH_1)
        enemy_2 = cv2.inRange(hsv, ENEMY_HSV_LOW_2, ENEMY_HSV_HIGH_2)
        enemy_mask = cv2.bitwise_or(enemy_1, enemy_2)

        def count(mask, scale=2):
            n, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
            valid = 0
            y_sum = 0.0
            for i in range(1, n):
                if stats[i, 4] >= 10:  # 面积阈值
                    valid += 1
                    y_sum += centroids[i, 1] * scale  # 还原到原始分辨率
            return valid, y_sum

        ally_n, ally_y = count(ally_mask)
        enemy_n, _ = count(enemy_mask)

        return ally_n, enemy_n, ally_y

    def _cache_current(self, ally: int, enemy: int, ally_y: float, frame: np.ndarray) -> None:
        """缓存当前帧数据"""
        self._prev_ally_count = ally
        self._prev_enemy_count = enemy
        self._prev_ally_y_sum = ally_y
        self._prev_frame = frame

    def reset(self) -> None:
        """重置评估器状态 (新游戏开始时调用)"""
        self._prev_frame = None
        self._prev_ally_count = 0
        self._prev_enemy_count = 0
        self._prev_ally_y_sum = 0.0
