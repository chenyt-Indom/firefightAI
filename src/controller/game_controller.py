"""快速实时AI指挥循环 — 截图→检测→决策→执行→学习, 全程≤3秒

特性:
  - 实时模式 (不暂停游戏)
  - 纯颜色检测 (skip YOLO/OCR, ~20ms)
  - MuMuManager 命令批处理
  - LLM响应缓存 (状态不变时跳过)
  - piped screencap (无文件IO)
  - L1 经验回放: 每轮自动评估效果 + 记录经验 + 检索注入
  - L2 策略提炼: 每15轮/每局后自动总结战术规则
"""
from __future__ import annotations

import time
import math
import hashlib
from typing import Optional

import cv2
import numpy as np
from loguru import logger

from src.screen.capture import ScreenCapture
from src.vision.detector import UnitDetector
from src.state.manager import StateManager
from src.state.models import GameState, Unit, UnitType, Team
from src.decision.commander import TacticalCommander
from src.decision.parser import CommandParser, ParsedCommand
from src.execution.executor import CommandExecutor
from src.execution.adb_utils import ADBUtils
from src.learning.battle_memory import BattleMemory
from src.learning.outcome_eval import OutcomeEvaluator
from src.learning.memory_retriever import MemoryRetriever
from src.learning.strategy_compressor import StrategyCompressor
from src.utils.logger import log_decision, log_state


class GameController:
    """快速实时AI指挥循环 (target: ≤3s/cycle)"""

    # 颜色检测 HSV 范围 (与 detector.py 保持一致)
    ALLY_HSV_LOW  = np.array([95, 100, 100], dtype=np.uint8)
    ALLY_HSV_HIGH = np.array([115, 255, 255], dtype=np.uint8)
    ENEMY_HSV_LOW_1  = np.array([0, 100, 180], dtype=np.uint8)     # 提高V下限过滤深色等高线
    ENEMY_HSV_HIGH_1 = np.array([10, 255, 255], dtype=np.uint8)
    ENEMY_HSV_LOW_2  = np.array([170, 100, 180], dtype=np.uint8)
    ENEMY_HSV_HIGH_2 = np.array([180, 255, 255], dtype=np.uint8)

    # 触控参数 (双击中圈拖拽, 已验证有效 2026-07-19)
    CLUSTER_EPS = 50         # 聚类
    SELECT_WAIT = 0.5        # 选中等候
    DRAG_DURATION = 2000     # 拖拽时长ms
    MUMU_EXE = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"

    def __init__(
        self,
        adb: ADBUtils,
        capture: ScreenCapture,
        detector: UnitDetector,
        state_manager: StateManager,
        commander: TacticalCommander,
        parser: CommandParser,
        executor: CommandExecutor,
        max_cycles: int = 500,
        game_over_timeout: int = 600,
        save_screenshots: bool = True,
        save_replay: bool = True,
        # ── 学习系统 (可选, 不传则禁用) ──
        battle_memory: Optional[BattleMemory] = None,
        outcome_eval: Optional[OutcomeEvaluator] = None,
        memory_retriever: Optional[MemoryRetriever] = None,
        strategy_compressor: Optional[StrategyCompressor] = None,
        game_session: str = "",
    ):
        self.adb = adb
        self.capture = capture
        self.detector = detector
        self.state_manager = state_manager
        self.commander = commander
        self.parser = parser
        self.executor = executor

        self.max_cycles = max_cycles
        self.game_over_timeout = game_over_timeout
        self.save_screenshots = save_screenshots
        self.save_replay = save_replay

        # 学习系统
        self.battle_memory = battle_memory
        self.outcome_eval = outcome_eval
        self.memory_retriever = memory_retriever
        self.strategy_compressor = strategy_compressor
        self.game_session = game_session or str(int(time.time()))

        self._cycle_count = 0
        self._start_time = 0.0
        self._running = False
        self._game_over = False
        self._victory = False
        self._replay_data: list[dict] = []

        # LLM 缓存
        self._state_hash: str = ""
        self._cached_commands: list[ParsedCommand] = []
        self._cache_hit_count = 0
        self._cache_max_age = 2  # 最多缓存2轮

        # 学习: 缓存上一轮数据用于延迟评估
        self._prev_frame: Optional[np.ndarray] = None
        self._prev_ally_count: int = 0
        self._prev_enemy_count: int = 0
        self._prev_decision: dict = {}
        self._learning_enabled: bool = battle_memory is not None and outcome_eval is not None

    # ================================================================
    # 主循环
    # ================================================================

    def run(self) -> bool:
        """运行主循环 — 实时模式, 含学习反馈

        每轮流程:
          1. 截图 → 2. 评估上轮效果(L1) → 3. 颜色检测 → 4. 构建状态
          → 5. LLM决策(含few-shot注入) → 6. 批量执行 → 7. 记录经验
          → 8. (每15轮)策略提炼(L2)
        """
        logger.info("=" * 60)
        logger.info("Firefight AI 指挥系统启动 (快速实时 + 学习模式)")
        if self._learning_enabled:
            logger.info(f"学习系统: 启用 | 场次: {self.game_session}")
        else:
            logger.info("学习系统: 禁用 (未配置)")
        logger.info("=" * 60)

        self._running = True
        self._start_time = time.time()
        self._cycle_count = 0

        try:
            while self._running and self._cycle_count < self.max_cycles:
                self._cycle_count += 1
                t_start = time.time()

                # ── 1. 截图 (piped, 无文件IO) ──
                frame, t_capture = self._fast_capture()

                # ── 1.5. 评估上一轮决策效果 (L1: 延迟评估) ──
                outcome = None
                if self._learning_enabled and self._prev_frame is not None and frame is not None:
                    outcome = self._evaluate_previous_cycle(frame)

                # ── 2. 颜色检测 (纯HSV, ~20ms) ──
                allies, enemies, t_detect = self._fast_detect(frame)

                # 缓存当前帧 + 计数 (供下一轮评估使用)
                self._prev_frame = frame
                self._prev_ally_count = len(allies)
                self._prev_enemy_count = len(enemies)

                # ── 3. 构建精简状态 ──
                state = self._fast_build_state(allies, enemies, frame.shape)

                # 游戏结束检查
                if self._check_game_over(state):
                    self._game_over = True
                    break

                # ── 4. LLM决策 (带缓存 + few-shot注入) ──
                commands, t_llm = self._fast_decide(state)

                if commands is None:
                    commands = self.parser.generate_fallback_commands(state)

                # 缓存当前决策 (供下一轮评估使用)
                self._prev_decision = self._decision_to_dict(commands, state)

                # ── 5. 批量执行指令 ──
                t_exec = self._fast_execute(commands, state)

                # ── 6. 记录 (含学习) ──
                self._record_cycle(state, outcome, commands)

                # ── 7. 策略提炼 (L2: 每15轮) ──
                if self._learning_enabled and self.strategy_compressor:
                    self._maybe_compress()

                total = (time.time() - t_start) * 1000
                learn_tags = ""
                if outcome:
                    learn_tags = f"评分{outcome['score']:+.0f} "
                    if outcome['score'] > 10:
                        learn_tags += "✅"
                    elif outcome['score'] < -5:
                        learn_tags += "⚠️"

                logger.info(
                    f"#{self._cycle_count} | "
                    f"截图={t_capture:.0f}ms 检测={t_detect:.0f}ms "
                    f"LLM={t_llm:.0f}ms 执行={t_exec:.0f}ms | "
                    f"总计={total:.0f}ms {learn_tags}"
                    f"{'⚡缓存' if self._cache_hit_count > 0 and t_llm < 50 else ''}"
                )

                if total > 3000:
                    logger.warning(f"⚠️ 本轮超时 {total:.0f}ms > 3000ms!")

        except KeyboardInterrupt:
            logger.info("用户中断")
        except Exception as e:
            logger.exception(f"主循环异常: {e}")
        finally:
            # 游戏结束时执行最终策略提炼
            if self._learning_enabled and self.strategy_compressor:
                try:
                    self.strategy_compressor.compress_on_game_over(
                        self._cycle_count, self.game_session
                    )
                except Exception as e:
                    logger.error(f"最终策略提炼失败: {e}")
            self._cleanup()

        return self._victory

    # ================================================================
    # 快速截图 (piped screencap, 无文件IO)
    # ================================================================

    def _fast_capture(self):
        """直接管道截图, 避免文件IO"""
        import subprocess

        t0 = time.time()

        try:
            result = subprocess.run(
                [self.adb.adb_path, "-s", self.adb.device_addr,
                 "exec-out", "screencap", "-p"],
                capture_output=True,
                timeout=8,
            )
            if result.returncode != 0 or not result.stdout:
                logger.warning("ADB截图失败")
                return None, 0

            # 直接从内存解码
            img_array = np.frombuffer(result.stdout, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

            elapsed = (time.time() - t0) * 1000
            if frame is not None:
                logger.debug(f"截图 {frame.shape[1]}x{frame.shape[0]} {elapsed:.0f}ms")
            return frame, elapsed

        except Exception as e:
            logger.error(f"截图异常: {e}")
            return None, 0

    # ================================================================
    # 距离聚类 — 把同一班组的士兵合并为一个单位
    # ================================================================

    @staticmethod
    def _cluster_units(points: list[dict], eps: int = 50) -> list[dict]:
        """距离聚类: 将距离 < eps 的检测点合并为一个单位

        每个步兵班组有3-8个士兵, 颜色检测会把每个士兵当成独立点。
        聚类后用"离中心最近的蓝点"作为选中目标, 保证点击命中。
        """
        if len(points) <= 1:
            return points

        clusters: list[list[dict]] = []
        used: set[int] = set()

        for i, p1 in enumerate(points):
            if i in used:
                continue
            cluster = [p1]
            used.add(i)
            for j, p2 in enumerate(points):
                if j in used:
                    continue
                dx = p1["x"] - p2["x"]
                dy = p1["y"] - p2["y"]
                if dx * dx + dy * dy < eps * eps:
                    cluster.append(p2)
                    used.add(j)
            clusters.append(cluster)

        # 每个聚类取"离几何中心最近的蓝点"作为选中坐标
        result = []
        for cluster in clusters:
            cx = sum(p["x"] for p in cluster) / len(cluster)
            cy = sum(p["y"] for p in cluster) / len(cluster)
            best = min(cluster, key=lambda p: (p["x"] - cx) ** 2 + (p["y"] - cy) ** 2)
            result.append({
                "x": best["x"],
                "y": best["y"],
                "area": sum(p["area"] for p in cluster),
                "w": best.get("w", 30),
                "h": best.get("h", 30),
                "member_count": len(cluster),
            })
        return result

    # ================================================================
    # 快速颜色检测 (纯HSV + 聚类)
    # ================================================================

    def _fast_detect(self, frame: np.ndarray):
        """纯颜色检测: 从HSV蒙版中提取友军/敌军单位坐标

        Returns:
            (allies: list[dict], enemies: list[dict], elapsed_ms)
            每个单位: {x, y, area, w, h}
        """
        if frame is None:
            return [], [], 0

        t0 = time.time()
        h, w = frame.shape[:2]

        # 缩小到一半加速 (对标记检测不影响)
        small = cv2.resize(frame, (w // 2, h // 2))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

        # 颜色蒙版
        ally_mask = cv2.inRange(hsv, self.ALLY_HSV_LOW, self.ALLY_HSV_HIGH)
        enemy_1 = cv2.inRange(hsv, self.ENEMY_HSV_LOW_1, self.ENEMY_HSV_HIGH_1)
        enemy_2 = cv2.inRange(hsv, self.ENEMY_HSV_LOW_2, self.ENEMY_HSV_HIGH_2)
        enemy_mask = cv2.bitwise_or(enemy_1, enemy_2)

        def extract_units(mask, scale=2):
            n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
            units = []
            for i in range(1, n):
                area = stats[i, 4]
                if area < 10:  # 缩略图阈值
                    continue
                units.append({
                    "x": int(centroids[i, 0] * scale),
                    "y": int(centroids[i, 1] * scale),
                    "area": area,
                    "w": int(stats[i, 2] * scale),
                    "h": int(stats[i, 3] * scale),
                })
            return sorted(units, key=lambda u: u["area"], reverse=True)

        allies = extract_units(ally_mask)
        enemies = extract_units(enemy_mask)

        # 距离聚类: 把同一班组的士兵合并为一个可指挥单位
        raw_ally, raw_enemy = len(allies), len(enemies)
        allies = self._cluster_units(allies, eps=self.CLUSTER_EPS)
        enemies = self._cluster_units(enemies, eps=self.CLUSTER_EPS)

        elapsed = (time.time() - t0) * 1000
        logger.debug(
            f"检测: 友{raw_ally}→{len(allies)}聚类 | "
            f"敌{raw_enemy}→{len(enemies)}聚类 | {elapsed:.0f}ms"
        )
        return allies, enemies, elapsed

    # ================================================================
    # 构建精简状态
    # ================================================================

    def _fast_build_state(
        self, allies: list[dict], enemies: list[dict], frame_shape: tuple
    ) -> GameState:
        """构建精简状态 (不含YOLO类型, 不含OCR)"""
        h, w = frame_shape[:2]
        units: list[Unit] = []

        for i, a in enumerate(allies):
            units.append(Unit(
                track_id=i,
                unit_type=UnitType.INFANTRY,  # 颜色检测不知道类型
                team=Team.ALLY,
                x=a["x"], y=a["y"],
                bbox=(a["x"]-a["w"]//2, a["y"]-a["h"]//2,
                      a["x"]+a["w"]//2, a["y"]+a["h"]//2),
                confidence=0.9,
            ))

        for j, e in enumerate(enemies):
            units.append(Unit(
                track_id=j + 1000,  # 敌方ID偏移
                unit_type=UnitType.INFANTRY,
                team=Team.ENEMY,
                x=e["x"], y=e["y"],
                bbox=(e["x"]-e["w"]//2, e["y"]-e["h"]//2,
                      e["x"]+e["w"]//2, e["y"]+e["h"]//2),
                confidence=0.9,
            ))

        return GameState(
            frame_id=self._cycle_count,
            units=units,
            screen_size=(w, h),
            timestamp=time.time(),
        )

    # ================================================================
    # LLM决策 (带缓存)
    # ================================================================

    def _fast_decide(self, state: GameState) -> tuple[Optional[list[ParsedCommand]], float]:
        """LLM决策, 带状态哈希缓存 + few-shot经验注入

        Returns:
            (commands, elapsed_ms)
        """
        t0 = time.time()

        # 计算状态哈希
        hash_str = self._hash_state(state)

        # 缓存命中: 状态没变, 复用上次决策(最多2轮)
        if hash_str == self._state_hash and self._cached_commands:
            self._cache_hit_count += 1
            elapsed = (time.time() - t0) * 1000
            logger.debug(f"缓存命中 (#{self._cache_hit_count})")
            return self._cached_commands, elapsed

        # ── L1 经验注入: 检索相似成功案例 ──
        if self._learning_enabled and self.memory_retriever:
            try:
                self.commander.set_learned_examples(
                    self.memory_retriever, hash_str,
                    state.ally_count, state.enemy_count
                )
            except Exception as e:
                logger.warning(f"经验注入失败: {e}")

        # 调用LLM
        llm_response = self.commander.decide(state)

        if llm_response is None:
            self._state_hash = ""
            self._cached_commands = []
            return None, (time.time() - t0) * 1000

        # 解析指令
        commands = self.parser.parse(llm_response, state)

        # 更新缓存
        self._state_hash = hash_str
        self._cached_commands = commands

        elapsed = (time.time() - t0) * 1000
        return commands, elapsed

    # ================================================================
    # L1 学习: 评估 + 记录
    # ================================================================

    def _evaluate_previous_cycle(self, current_frame: np.ndarray) -> Optional[dict]:
        """评估上一轮决策效果 (延迟评估, ~3秒反应时间)

        对比当前帧与上一帧的友军/敌军数量:
          - 敌人减少 = 有效击杀 (+10分/个)
          - 友军减少 = 己方伤亡 (-10分/个)
          - 阵线进退 = 小幅加减分

        评估完成后自动记录到 BattleMemory。
        """
        if self.outcome_eval is None:
            return None

        try:
            # 使用当前帧与缓存的上轮帧对比
            # outcome_eval 内部已缓存了上轮的计数
            result = self.outcome_eval.evaluate(current_frame)

            # 记录到经验库
            if self.battle_memory and self._prev_decision:
                self.battle_memory.record(
                    state_hash=self._state_hash,
                    ally_count=self._prev_ally_count,
                    enemy_count=self._prev_enemy_count,
                    ally_positions=[],  # 精简模式不存详细坐标
                    decision=self._prev_decision,
                    outcome_score=result["score"],
                    cycle_num=self._cycle_count - 1,
                    game_session=self.game_session,
                )

            return result

        except Exception as e:
            logger.warning(f"效果评估失败: {e}")
            return None

    def _decision_to_dict(self, commands: list[ParsedCommand], state: GameState) -> dict:
        """将指令列表转为可存储的字典格式"""
        if not commands:
            return {"action": "idle", "reason": "无行动"}

        # 取第一条有效指令为代表
        for cmd in commands:
            if cmd.action is None:
                continue
            result = {
                "action": cmd.action.value if hasattr(cmd.action, 'value') else str(cmd.action),
                "reason": getattr(cmd, 'reason', ''),
            }
            if cmd.target_pixel:
                sw, sh = state.screen_size
                result["target"] = [
                    round(cmd.target_pixel[0] / sw, 3),
                    round(cmd.target_pixel[1] / sh, 3),
                ]
            if cmd.unit_ids:
                result["unit_ids"] = cmd.unit_ids[:5]  # 最多存5个
            if cmd.target_enemy_pixel:
                sw, sh = state.screen_size
                result["target_enemy"] = [
                    round(cmd.target_enemy_pixel[0] / sw, 3),
                    round(cmd.target_enemy_pixel[1] / sh, 3),
                ]
            return result

        return {"action": "idle", "reason": "无有效指令"}

    def _maybe_compress(self) -> None:
        """触发热提炼 (每15轮)"""
        if self.strategy_compressor and self.strategy_compressor.should_compress(self._cycle_count):
            try:
                new_rules = self.strategy_compressor.compress(
                    self._cycle_count, self.game_session
                )
                if new_rules:
                    # 重新加载规则到 commander 的系统 prompt
                    self.commander.reload_tactics_rules()
            except Exception as e:
                logger.error(f"策略提炼失败: {e}")

    def _hash_state(self, state: GameState) -> str:
        """快速状态哈希 (只关注关键变化)"""
        parts = [
            f"{state.ally_count}",
            f"{state.enemy_count}",
        ]
        # 取前10个友军坐标
        for u in state.allies[:10]:
            parts.append(f"{u.x//20},{u.y//20}")  # 20px粒度
        # 取前10个敌军坐标
        for u in state.enemies[:10]:
            parts.append(f"{u.x//20},{u.y//20}")
        return hashlib.md5("|".join(parts).encode()).hexdigest()[:8]

    # ================================================================
    # 批量执行指令 (semicolon批处理)
    # ================================================================

    def _fast_execute(self, commands: list[ParsedCommand], state: GameState) -> float:
        """执行指令: select=批量tap(快), move=双击中圈+swipe拖拽(已验证)

        select: 批量tap → 选中自动接敌 (<=150ms)
        move:   单单位快速版 = tap (~0.2s) + 双击 (~0.1s) + swipe (~1.5s)
                多单位协同: 验证中圈可批量预抓取 (2026-07-21)
        
        AI自适应: 根据历史成功率动态调节 tap_delay/swipe_duration
        """
        if not commands:
            return 0

        import subprocess

        t0 = time.time()
        select_cmds: list[str] = []
        move_cmds: list[tuple] = []  # (unit, target_pixel)

        # ── 先收集所有指令 ──
        for cmd in commands:
            if not cmd.unit_ids or cmd.action is None:
                continue

            if cmd.action.value in ("select",):
                for uid in cmd.unit_ids[:8]:
                    unit = state.get_unit_by_id(uid)
                    if unit:
                        select_cmds.append(f"input tap {unit.x} {unit.y}")

            elif cmd.action.value in ("move", "attack"):
                if cmd.target_pixel and cmd.unit_ids:
                    unit = state.get_unit_by_id(cmd.unit_ids[0])
                    if unit:
                        move_cmds.append((unit, cmd.target_pixel))

        # ── AI 自适应参数 ──
        tap_select_delay = 0.2    # tap 后等待注册 (原0.6s)
        double_tap_gap   = 0.03   # 双击间隔 (原0.05s)
        post_double_wait = 0.05   # 双击后等待 (原0.1s)
        swipe_duration   = 1000   # 拖拽时长ms (原2000)
        post_swipe_wait  = 1.2    # 拖拽后等待 (原2.3s)
        
        # 从学习参数动态调节
        if hasattr(self, '_ai_timing'):
            tap_select_delay = max(0.1, min(0.5, self._ai_timing.get('tap_delay', 0.2)))
            swipe_duration   = max(500, min(2000, self._ai_timing.get('swipe_ms', 1000)))

        # ① 批量 select (快)
        if select_cmds:
            batch = "; ".join(select_cmds)
            try:
                subprocess.run(
                    [self.MUMU_EXE, "control", "-v", "0", "tool", "cmd", "-c", batch],
                    capture_output=True, text=True, timeout=3,
                )
                logger.debug(f"select batch: {len(select_cmds)} taps")
            except Exception as e:
                logger.error(f"批量tap失败: {e}")

        # ② 双指缩放
        for cmd in commands:
            if cmd.action is None or cmd.action.value not in ("zoom_in", "zoom_out"):
                continue
            direction = "in" if cmd.action.value == "zoom_in" else "out"
            sw, sh = state.screen_size
            self._zoom(direction, sw // 2, sh // 2)

        # ③ 快速 move 序列 (多单位协同)
        if move_cmds:
            # 批量预执行: tap选中 (可重叠, ADB用semicolon)
            pre_cmds = []
            for unit, target in move_cmds:
                ux, uy = unit.x, unit.y
                pre_cmds.append(f"input tap {ux} {uy}")
            if pre_cmds:
                batch = "; ".join(pre_cmds)
                try:
                    subprocess.run(
                        [self.MUMU_EXE, "control", "-v", "0", "tool", "cmd", "-c", batch],
                        capture_output=True, text=True, timeout=5,
                    )
                except:
                    pass
                time.sleep(tap_select_delay)  # 等待选中注册

            # 逐条双击+拖拽 (核心操作)
            for unit, target in move_cmds[:6]:  # 单轮最多6个move
                ux, uy = unit.x, unit.y
                tx, ty = target
                cx, cy = ux, uy

                try:
                    # 双击中圈 (快速, semicolon合并)
                    subprocess.run(
                        [self.MUMU_EXE, "control", "-v", "0", "tool", "cmd", "-c",
                         f"input tap {cx} {cy}; input tap {cx} {cy}"],
                        capture_output=True, text=True, timeout=3,
                    )
                    time.sleep(post_double_wait)

                    # 快速 swipe 拖拽
                    subprocess.run(
                        [self.MUMU_EXE, "control", "-v", "0", "tool", "cmd", "-c",
                         f"input swipe {cx} {cy} {tx} {ty} {swipe_duration}"],
                        capture_output=True, text=True, timeout=5,
                    )
                    time.sleep(post_swipe_wait)

                    logger.debug(f"move unit({ux},{uy})→({tx},{ty})")
                except Exception as e:
                    logger.warning(f"move失败: {str(e)[:60]}")

        elapsed = (time.time() - t0) * 1000
        total_moves = len(move_cmds)
        if select_cmds or move_cmds:
            logger.info(
                f"执行: {len(select_cmds)}select + {total_moves}move ({elapsed:.0f}ms) "
                f"[delay={tap_select_delay}s, swipe={swipe_duration}ms]"
            )

        # ── AI 学习: 记录执行效率, 迭代调参 ──
        if total_moves > 0 and hasattr(self, 'commander'):
            move_time_per_unit = elapsed / total_moves if total_moves > 0 else 0
            if move_time_per_unit > 2000:  # 单单位超2s → 加速
                if not hasattr(self, '_ai_timing'):
                    self._ai_timing = {'tap_delay': 0.2, 'swipe_ms': 1000}
                self._ai_timing['tap_delay'] = max(0.1, self._ai_timing['tap_delay'] * 0.9)
                self._ai_timing['swipe_ms'] = max(500, self._ai_timing['swipe_ms'] - 50)
                logger.info(f"AI自调时序: tap={self._ai_timing['tap_delay']:.2f}s, swipe={self._ai_timing['swipe_ms']}ms")

        return elapsed

    def _mumu_cmd(self, cmd: str, timeout: int = 5) -> None:
        """发送单条 MuMuManager 命令 (内部)"""
        import subprocess
        try:
            subprocess.run(
                [self.MUMU_EXE, "control", "-v", "0", "tool", "cmd", "-c", cmd],
                capture_output=True, text=True, timeout=timeout,
            )
        except Exception:
            pass  # 静默失败, 拖拽操作有时不返回errcode

    def _detect_mid_circle(self, ux: int, uy: int) -> tuple[int, int] | None:
        """截图找单位选中后的中间圆圈位置"""
        import subprocess
        try:
            r = subprocess.run(
                [self.adb.adb_path, "-s", self.adb.device_addr,
                 "exec-out", "screencap", "-p"],
                capture_output=True, timeout=8,
            )
            if r.returncode != 0 or not r.stdout:
                return None
            img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            return None

        h, w = img.shape[:2]
        sr = 150
        x1, y1 = max(0, ux - sr), max(0, uy - 50)
        x2, y2 = min(w, ux + sr), min(h, uy + sr + 60)

        roi = img[y1:y2, x1:x2]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        bm = cv2.inRange(hsv, np.array([90, 40, 40]), np.array([140, 255, 255]))
        n, _, cst, cct = cv2.connectedComponentsWithStats(bm, 8)

        circles = []
        for i in range(1, n):
            if cst[i, 4] < 3:
                continue
            cx = int(cct[i][0]) + x1
            cy = int(cct[i][1]) + y1
            dist = math.hypot(cx - ux, cy - uy)
            if cst[i, 4] > 300 or dist < 10 or dist > 120:
                continue
            circles.append((cx, cy, cst[i, 4], dist))

        if len(circles) < 3:
            return None

        # 按Y分组(±10px), 取最多圆那一行
        circles.sort(key=lambda c: c[1])
        best_group, best_size = [], 0
        i = 0
        while i < len(circles):
            group = [circles[i]]
            j = i + 1
            while j < len(circles) and abs(circles[j][1] - circles[i][1]) <= 10:
                group.append(circles[j])
                j += 1
            if len(group) > best_size:
                best_group, best_size = group, len(group)
            i = j

        if len(best_group) < 2:
            return None

        # 取最佳组X最小/最大 → 中间=最接近中点的圆
        best_group.sort(key=lambda c: c[0])
        left, right = best_group[0], best_group[-1]
        mid_x = (left[0] + right[0]) / 2
        mid = min(best_group, key=lambda c: abs(c[0] - mid_x))
        return (mid[0], mid[1])

    def _zoom(self, direction: str = "in", cx: int = 540, cy: int = 960) -> None:
        """缩放: Win32 keybd_event(Ctrl) + mouse_event(Wheel) → MuMu窗口

        唯一有效方案: 直发 Win32 键盘+滚轮事件到 MuMu 窗口,
        标记变化验证: 蓝74→17(放大) →83(缩小), 确认真缩放!
        """
        import ctypes
        user32 = ctypes.windll.user32

        # 找MuMu窗口
        result = []
        def cb(h, _):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, buf, 256)
            if buf.value == "MuMu安卓设备":
                result.append(h)
            return True
        user32.EnumWindows(
            ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)(cb), 0
        )
        hwnd = result[0] if result else None

        if not hwnd:
            logger.warning("找不到MuMu窗口, 缩放失败")
            return

        user32.SetForegroundWindow(hwnd)
        sign = 120 if direction == "in" else -120
        n = 10  # 10格缩放

        for _ in range(n):
            user32.keybd_event(0x11, 0, 0, 0)  # Ctrl down
            user32.mouse_event(0x0800, 0, 0, sign, 0)  # wheel
            user32.keybd_event(0x11, 0, 2, 0)  # Ctrl up
            time.sleep(0.01)
        time.sleep(0.2)

    def _mumu(self, cmd: str, timeout: int = 10) -> None:
        """发送单条 MuMuManager 命令"""
        import subprocess
        try:
            r = subprocess.run(
                [self.MUMU_EXE, "control", "-v", "0", "tool", "cmd", "-c", cmd],
                capture_output=True, text=True, timeout=timeout,
            )
            if r.returncode != 0 and "errcode" not in r.stdout:
                logger.warning(f"MuMuManager rc={r.returncode}")
        except Exception as e:
            logger.error(f"MuMuManager 失败: {str(e)[:80]}")

    # ================================================================
    # 游戏结束检查
    # ================================================================

    def _check_game_over(self, state: GameState) -> bool:
        elapsed = time.time() - self._start_time
        if elapsed > self.game_over_timeout:
            logger.info(f"游戏超时({self.game_over_timeout}s)")
            return True
        if state.ally_count == 0 and self._cycle_count > 3:
            logger.info("己方全灭")
            self._victory = False
            return True
        if state.enemy_count == 0 and self._cycle_count > 5:
            logger.info("敌方全灭, 胜利!")
            self._victory = True
            return True
        return False

    # ================================================================
    # 记录 & 清理
    # ================================================================

    def _record_cycle(self, state, outcome, commands):
        record = {
            "cycle": self._cycle_count,
            "timestamp": time.time(),
            "ally_count": state.ally_count,
            "enemy_count": state.enemy_count,
            "commands": [{"action": c.action.value, "reason": c.reason} for c in (commands or [])],
        }
        if outcome:
            record["outcome"] = {
                "score": outcome["score"],
                "details": outcome["details"],
            }
        self._replay_data.append(record)

    def _cleanup(self):
        self._running = False
        elapsed = time.time() - self._start_time
        logger.info("=" * 60)
        logger.info(f"游戏结束 | 总轮次:{self._cycle_count} | 耗时:{elapsed:.0f}s | "
                    f"缓存命中:{self._cache_hit_count}")
        logger.info(f"LLM决策:{self.commander.decision_count}次 | "
                    f"平均:{self.commander.avg_decision_time:.0f}ms")

        # 学习统计
        if self._learning_enabled and self.battle_memory:
            try:
                stats = self.battle_memory.get_stats(self.game_session)
                logger.info(
                    f"学习记录:{stats['total']}条 | "
                    f"平均得分:{stats['avg_score']} | "
                    f"有效率:{stats['positive_rate']}%"
                )
            except Exception:
                pass

        logger.info("=" * 60)
        self.capture.stop()

        if self.save_replay and self._replay_data:
            self._save_replay()

    def _save_replay(self):
        import json
        from pathlib import Path
        replay_path = Path("sessions") / f"replay_{int(self._start_time)}.json"
        try:
            with open(replay_path, "w", encoding="utf-8") as f:
                json.dump(self._replay_data, f, ensure_ascii=False, indent=2)
            logger.info(f"回放已保存: {replay_path}")
        except Exception as e:
            logger.error(f"保存回放失败: {e}")

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def is_running(self) -> bool:
        return self._running

    def stop(self):
        self._running = False
