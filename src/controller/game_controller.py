"""主控状态机 - 核心游戏循环: PAUSE→CAPTURE→DETECT→OCR→DECIDE→EXECUTE→RESUME"""
from __future__ import annotations

import time
from typing import Optional

from loguru import logger

from src.screen.capture import ScreenCapture
from src.vision.detector import UnitDetector
from src.vision.ocr_reader import UIReader
from src.state.manager import StateManager
from src.state.models import GameState
from src.decision.commander import TacticalCommander
from src.decision.parser import CommandParser, ParsedCommand
from src.execution.executor import CommandExecutor
from src.execution.adb_utils import ADBUtils
from src.utils.logger import log_decision, log_execution, log_state


class GameController:
    """主控状态机,实现完整的AI指挥循环"""

    def __init__(
        self,
        adb: ADBUtils,
        capture: ScreenCapture,
        detector: UnitDetector,
        ocr: UIReader,
        state_manager: StateManager,
        commander: TacticalCommander,
        parser: CommandParser,
        executor: CommandExecutor,
        # 循环参数
        cycle_interval: float = 3.0,
        max_cycles: int = 200,
        game_over_timeout: int = 600,
        # 调试参数
        step_by_step: bool = False,
        show_detection_window: bool = False,
        save_screenshots: bool = True,
        save_replay: bool = True,
    ):
        self.adb = adb
        self.capture = capture
        self.detector = detector
        self.ocr = ocr
        self.state_manager = state_manager
        self.commander = commander
        self.parser = parser
        self.executor = executor

        self.cycle_interval = cycle_interval
        self.max_cycles = max_cycles
        self.game_over_timeout = game_over_timeout
        self.step_by_step = step_by_step
        self.show_detection_window = show_detection_window
        self.save_screenshots = save_screenshots
        self.save_replay = save_replay

        self._cycle_count = 0
        self._start_time = 0.0
        self._running = False
        self._game_over = False
        self._victory = False
        self._replay_data: list[dict] = []

    def run(self) -> bool:
        """运行主循环"""
        logger.info("=" * 60)
        logger.info("Firefight AI 指挥系统启动")
        logger.info("=" * 60)

        self._running = True
        self._start_time = time.time()
        self._cycle_count = 0

        try:
            while self._running and self._cycle_count < self.max_cycles:
                self._cycle_count += 1
                cycle_start = time.time()

                logger.info(f"\n{'='*40}\n  第 {self._cycle_count} 轮决策\n{'='*40}")

                # === 步骤1: 暂停游戏 ===
                if not self._step_pause():
                    break

                # === 步骤2: 捕获画面 ===
                frame = self._step_capture()
                if frame is None:
                    logger.warning("帧捕获失败,跳过本轮")
                    self._step_resume()
                    continue

                # === 步骤3: YOLO检测 ===
                units = self._step_detect(frame)

                # === 步骤4: OCR读取UI ===
                ui_data = self._step_ocr(frame)

                # === 步骤5: 构建状态 ===
                game_state = self._step_build_state(units, ui_data, frame)

                # 检查游戏是否结束
                if self._check_game_over(game_state):
                    self._game_over = True
                    break

                # === 步骤6: LLM决策 ===
                llm_response = self._step_decide(game_state)

                # === 步骤7: 解析指令 ===
                if llm_response is not None:
                    commands = self._step_parse(llm_response, game_state)
                else:
                    # LLM失败,使用降级策略
                    logger.warning("LLM决策失败,使用降级保守防御指令")
                    commands = self.parser.generate_fallback_commands(game_state)

                # === 步骤8: 执行指令 ===
                if not self._step_execute(commands):
                    logger.warning("部分指令执行失败")

                # === 步骤9: 恢复游戏 ===
                self._step_resume()

                # === 步骤10: 等待指令生效 ===
                self._step_wait()

                # 记录本轮数据
                self._record_cycle(game_state, llm_response, commands)

                cycle_elapsed = time.time() - cycle_start
                logger.info(
                    f"第{self._cycle_count}轮完成, "
                    f"耗时{cycle_elapsed:.1f}s, "
                    f"总计{time.time() - self._start_time:.0f}s"
                )

                # 逐步模式
                if self.step_by_step:
                    input("按Enter继续下一轮...")

        except KeyboardInterrupt:
            logger.info("用户中断")
        except Exception as e:
            logger.exception(f"主循环异常: {e}")
        finally:
            self._cleanup()

        return self._victory

    # ---- 各步骤实现 ----

    def _step_pause(self) -> bool:
        """暂停游戏"""
        logger.debug("→ 暂停游戏")
        self.executor.pause()
        time.sleep(0.15)  # 等待暂停生效
        return True

    def _step_capture(self):
        """捕获画面"""
        logger.debug("→ 捕获画面")
        from src.utils.logger import log_vision
        frame = self.capture.grab_latest_frame()
        if frame is not None:
            log_vision(f"帧捕获: {frame.shape}, FPS={self.capture.fps:.1f}")
        return frame

    def _step_detect(self, frame):
        """YOLO检测"""
        logger.debug("→ 单位检测")
        units = self.detector.predict(frame)
        return units

    def _step_ocr(self, frame):
        """OCR读取UI"""
        logger.debug("→ OCR读取UI")
        # 从配置读取UI区域,此处使用默认值
        ui_regions = {
            "resource_bar": (0.05, 0.02, 0.95, 0.08),
            "unit_info": (0.02, 0.08, 0.30, 0.25),
        }
        if self.ocr.is_loaded:
            return self.ocr.read_ui(frame, ui_regions)
        return {}

    def _step_build_state(self, units, ui_data, frame):
        """构建游戏状态"""
        logger.debug("→ 构建状态")
        return self.state_manager.build(units, ui_data, frame)

    def _step_decide(self, game_state: GameState):
        """LLM决策"""
        logger.debug("→ AI战术决策")
        return self.commander.decide(game_state)

    def _step_parse(self, llm_response, game_state: GameState) -> list[ParsedCommand]:
        """解析指令"""
        logger.debug("→ 解析指令")
        return self.parser.parse(llm_response, game_state)

    def _step_execute(self, commands: list[ParsedCommand]) -> bool:
        """执行指令"""
        logger.debug("→ 执行指令")
        return self.executor.execute(commands)

    def _step_resume(self) -> None:
        """恢复游戏"""
        logger.debug("→ 恢复游戏")
        self.executor.resume()
        time.sleep(0.1)

    def _step_wait(self) -> None:
        """等待指令生效"""
        wait_time = self.cycle_interval
        logger.debug(f"→ 等待{wait_time:.1f}s让指令生效...")
        time.sleep(wait_time)

    def _check_game_over(self, game_state: GameState) -> bool:
        """检查游戏是否结束"""
        # 检查是否超时
        elapsed = time.time() - self._start_time
        if elapsed > self.game_over_timeout:
            logger.info(f"游戏超时({self.game_over_timeout}s)")
            return True

        # 检查是否一方被全歼
        if game_state.ally_count == 0 and self._cycle_count > 3:
            logger.info("己方单位全灭,游戏结束")
            self._victory = False
            return True
        if game_state.enemy_count == 0 and self._cycle_count > 5:
            logger.info("敌方单位全灭,胜利!")
            self._victory = True
            return True

        return False

    def _record_cycle(
        self,
        game_state: GameState,
        llm_response,
        commands: list[ParsedCommand],
    ) -> None:
        """记录本轮数据用于回放"""
        self._replay_data.append({
            "cycle": self._cycle_count,
            "timestamp": time.time(),
            "state": game_state.to_dict(),
            "llm_analysis": llm_response.analysis if llm_response else "[降级]",
            "commands": [
                {
                    "action": c.action.value,
                    "unit_ids": c.unit_ids,
                    "target": c.target_pixel,
                    "enemy_target": c.target_enemy_pixel,
                    "reason": c.reason,
                }
                for c in commands
            ],
        })

    def _cleanup(self) -> None:
        """清理资源"""
        self._running = False
        elapsed = time.time() - self._start_time

        logger.info("=" * 60)
        logger.info("游戏结束统计")
        logger.info(f"  总轮次: {self._cycle_count}")
        logger.info(f"  总耗时: {elapsed:.0f}s")
        logger.info(f"  结果: {'胜利' if self._victory else '失败/中断'}")
        logger.info(f"  LLM决策次数: {self.commander.decision_count}")
        logger.info(f"  LLM平均耗时: {self.commander.avg_decision_time:.0f}ms")
        logger.info(f"  YOLO平均耗时: {self.detector.avg_inference_time:.0f}ms")
        logger.info(f"  ADB执行次数: {self.executor.execution_count}")
        logger.info("=" * 60)

        # 停止屏幕捕获
        self.capture.stop()

        # 保存回放数据
        if self.save_replay and self._replay_data:
            self._save_replay()

    def _save_replay(self) -> None:
        """保存回放数据"""
        import json
        from pathlib import Path

        replay_path = Path("sessions") / f"replay_{int(self._start_time)}.json"
        try:
            with open(replay_path, "w", encoding="utf-8") as f:
                json.dump(self._replay_data, f, ensure_ascii=False, indent=2)
            logger.info(f"回放数据已保存: {replay_path}")
        except Exception as e:
            logger.error(f"保存回放数据失败: {e}")

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def is_running(self) -> bool:
        return self._running

    def stop(self) -> None:
        """停止游戏循环"""
        self._running = False