"""战场预测系统 — 基于DeepSeek AI预测敌方位置和移动趋势

工作流:
  1. scan_map: 开局扫描全图, 预测敌方可能位置和热区
  2. predict_enemy_movement: 交战中预测敌方下一步动向
  3. learn_from_outcome: 对比预测与实际结果, 积累经验
  4. get_accumulated_wisdom: 将学习成果注入后续prompt

性能要求:
  - API调用timeout=6s
  - 地图分析结果缓存复用
  - 单次预测控制在1.5s内
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from openai import OpenAI


DEFAULT_SAVE_PATH = Path(__file__).parent.parent.parent / "data" / "battle_predictions.json"
MAX_EXPERIENCE = 500
ANALYSIS_INTERVAL = 10  # 每10轮分析一次准确率


class BattlefieldPredictor:
    """AI战场预测引擎, 调用DeepSeek进行敌方位置和移动预测"""

    def __init__(
        self,
        screen_size: tuple[int, int],
        api_key: str = "",
        api_base: str = "https://api.deepseek.com/v1",
    ):
        self.screen_size = screen_size
        self.api_key = api_key
        self.api_base = api_base
        self.model = "deepseek-v4-flash"
        self.temperature = 0.1
        self.timeout = 6

        self._client: Optional[OpenAI] = None
        self._experience: list[dict] = []       # 预测→实际结果 经验积累
        self._map_cache: dict[str, dict] = {}   # 地图分析缓存: screenshot_hash → prediction
        self._total_predictions = 0
        self._correct_predictions = 0
        self.save_path = str(DEFAULT_SAVE_PATH)

    # ------------------------------------------------------------------
    # 初始化 & 客户端
    # ------------------------------------------------------------------

    def _get_client(self) -> Optional[OpenAI]:
        if self._client is None:
            if not self.api_key:
                logger.error("DeepSeek API Key未配置, 预测功能不可用")
                return None
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base,
                timeout=self.timeout,
            )
        return self._client

    # ------------------------------------------------------------------
    # 地图扫描: 开局预测敌方位置
    # ------------------------------------------------------------------

    def scan_map(self, screenshot_path: str, cycle: int = 0) -> dict:
        """开局扫描全图, 预测敌方可能位置和热区

        Args:
            screenshot_path: 战场截图路径
            cycle: 当前轮次 (0=开局)

        Returns:
            {
                "predicted_positions": [(x1,y1), (x2,y2), ...],  # 预测的敌方坐标
                "hot_zones": [{"center": (x,y), "radius": r, "danger": "high/medium/low"}, ...],
                "recommended_routes": [{"from": (x1,y1), "to": (x2,y2), "reason": "..."}, ...],
                "confidence": 0.0-1.0,
                "raw_response": "原始LLM响应文本"
            }
        """
        start_time = time.time()

        # 检查缓存
        cache_key = self._compute_file_hash(screenshot_path)
        if cache_key and cache_key in self._map_cache:
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"地图扫描命中缓存 ({elapsed:.0f}ms)")
            cached = self._map_cache[cache_key]
            cached["from_cache"] = True
            return cached

        client = self._get_client()
        if client is None:
            return self._empty_scan_result("API Key未配置")

        # 读取并编码截图
        try:
            image_b64 = self._encode_image(screenshot_path)
        except Exception as e:
            logger.error(f"截图编码失败: {e}")
            return self._empty_scan_result(f"截图编码失败: {e}")

        prompt = self._build_scan_prompt(cycle)

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._scan_system_prompt()},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                        ],
                    },
                ],
                temperature=self.temperature,
                max_tokens=1024,
                timeout=self.timeout,
            )

            raw = response.choices[0].message.content or ""
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"地图扫描完成 ({elapsed:.0f}ms): {raw[:100]}...")

            result = self._parse_scan_response(raw, screenshot_path)
            result["elapsed_ms"] = round(elapsed)

            # 缓存结果
            if cache_key:
                self._map_cache[cache_key] = result
                logger.debug(f"地图扫描结果已缓存 (key={cache_key[:8]}...)")

            self._total_predictions += 1
            return result

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"地图扫描API调用失败 ({elapsed:.0f}ms): {e}")
            return self._empty_scan_result(f"API调用失败: {e}")

    def _scan_system_prompt(self) -> str:
        return """你是一个专业的战场分析AI。你需要分析战场截图,预测敌方单位可能的位置。
战场坐标系统: 左上角为原点(0,0), 右下角为屏幕最大坐标。x轴向右, y轴向下。

请严格按照以下JSON格式输出(不要包含markdown代码块):
{
  "predicted_positions": [[x1,y1], [x2,y2], ...],
  "hot_zones": [
    {"center": [x,y], "radius": 50, "danger": "high", "reason": "..."},
    ...
  ],
  "recommended_routes": [
    {"from": [x1,y1], "to": [x2,y2], "reason": "..."},
    ...
  ],
  "confidence": 0.8,
  "analysis": "对战场形势的简要分析"
}"""

    def _build_scan_prompt(self, cycle: int) -> str:
        parts = [f"请分析这张战场截图(屏幕尺寸: {self.screen_size[0]}x{self.screen_size[1]})。"]
        if cycle == 0:
            parts.append("这是开局第0轮, 请预测敌方单位可能出现的初始位置。")
        else:
            parts.append(f"当前为第{cycle}轮, 请根据地图地形和当前态势预测敌方位置。")

        # 注入积累的智慧
        wisdom = self.get_accumulated_wisdom()
        if wisdom:
            parts.append(f"\n## 历史经验\n{wisdom}")

        parts.append("\n请输出JSON格式的预测结果。")
        return "\n".join(parts)

    def _parse_scan_response(self, raw: str, screenshot_path: str) -> dict:
        """解析地图扫描的LLM响应"""
        try:
            text = self._clean_json(raw)
            data = json.loads(text)

            return {
                "predicted_positions": data.get("predicted_positions", []),
                "hot_zones": data.get("hot_zones", []),
                "recommended_routes": data.get("recommended_routes", []),
                "confidence": float(data.get("confidence", 0.5)),
                "analysis": data.get("analysis", ""),
                "raw_response": raw,
                "screenshot_path": screenshot_path,
                "from_cache": False,
                "prediction_type": "scan",
            }
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"扫描响应解析失败: {e}, 原始响应: {raw[:200]}")
            return self._empty_scan_result(f"响应解析失败: {e}")

    def _empty_scan_result(self, reason: str = "") -> dict:
        return {
            "predicted_positions": [],
            "hot_zones": [],
            "recommended_routes": [],
            "confidence": 0.0,
            "analysis": reason,
            "raw_response": "",
            "screenshot_path": "",
            "from_cache": False,
            "prediction_type": "scan",
        }

    # ------------------------------------------------------------------
    # 敌方移动预测
    # ------------------------------------------------------------------

    def predict_enemy_movement(
        self,
        current_enemies: list[dict],
        prev_enemies: list[dict],
        terrain_info: dict,
        cycle: int,
    ) -> dict:
        """预测敌方单位的下一步移动

        Args:
            current_enemies: 当前敌方单位列表 [{"id": "E1", "x": 100, "y": 200, "type": "infantry"}, ...]
            prev_enemies: 上一轮敌方单位 [同上]
            terrain_info: 地形信息 {"obstacles": [...], "cover": [...], ...}
            cycle: 当前轮次

        Returns:
            {
                "predicted_moves": [
                    {"unit_id": "E1", "from": (x1,y1), "to": (x2,y2), "confidence": 0.8},
                    ...
                ],
                "threat_level": 0-100,
                "suggested_response": "战术建议文本",
                "confidence": 0.0-1.0,
                "raw_response": ""
            }
        """
        start_time = time.time()

        client = self._get_client()
        if client is None:
            return self._empty_movement_result("API Key未配置")

        prompt = self._build_movement_prompt(current_enemies, prev_enemies, terrain_info, cycle)

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._movement_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=1024,
                timeout=self.timeout,
            )

            raw = response.choices[0].message.content or ""
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"移动预测完成 ({elapsed:.0f}ms)")

            result = self._parse_movement_response(raw)
            result["elapsed_ms"] = round(elapsed)

            self._total_predictions += 1
            return result

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"移动预测API调用失败 ({elapsed:.0f}ms): {e}")
            return self._empty_movement_result(f"API调用失败: {e}")

    def _movement_system_prompt(self) -> str:
        return """你是一个战场战术AI,专门分析敌方单位移动模式。
你需要根据当前和上一轮的敌方位置, 预测他们下一步将移动到哪里。

请严格按照以下JSON格式输出:
{
  "predicted_moves": [
    {"unit_id": "E1", "from": [x1,y1], "to": [x2,y2], "confidence": 0.8},
    ...
  ],
  "threat_level": 65,
  "suggested_response": "建议我方采取的行动",
  "analysis": "移动模式分析"
}
threat_level范围0-100, 0=无威胁, 100=极度危险。"""

    def _build_movement_prompt(
        self,
        current_enemies: list[dict],
        prev_enemies: list[dict],
        terrain_info: dict,
        cycle: int,
    ) -> str:
        parts = [f"## 第{cycle}轮 - 敌方移动预测\n"]

        # 当前敌方位置
        parts.append("### 当前敌方单位")
        for e in current_enemies:
            parts.append(f"- {e.get('id', '?')}: 位置({e.get('x',0)},{e.get('y',0)}) 类型={e.get('type','?')}")

        # 上一轮位置
        if prev_enemies:
            parts.append("\n### 上一轮敌方位置")
            for e in prev_enemies:
                parts.append(f"- {e.get('id', '?')}: 位置({e.get('x',0)},{e.get('y',0)})")

            # 计算移动向量
            parts.append("\n### 移动向量分析")
            for cur in current_enemies:
                eid = cur.get("id", "")
                prev = next((p for p in prev_enemies if p.get("id") == eid), None)
                if prev:
                    dx = cur["x"] - prev["x"]
                    dy = cur["y"] - prev["y"]
                    parts.append(f"- {eid}: 从({prev['x']},{prev['y']})→({cur['x']},{cur['y']}) 位移=({dx:+d},{dy:+d})")

        # 地形信息
        if terrain_info:
            parts.append(f"\n### 地形信息\n{json.dumps(terrain_info, ensure_ascii=False)}")

        # 注入经验
        wisdom = self.get_accumulated_wisdom()
        if wisdom:
            parts.append(f"\n### 历史经验\n{wisdom}")

        parts.append(f"\n屏幕尺寸: {self.screen_size[0]}x{self.screen_size[1]}")
        parts.append("\n请预测每个敌方单位下一步可能移动到的位置。")
        return "\n".join(parts)

    def _parse_movement_response(self, raw: str) -> dict:
        try:
            text = self._clean_json(raw)
            data = json.loads(text)

            predicted_moves = []
            for m in data.get("predicted_moves", []):
                predicted_moves.append({
                    "unit_id": m.get("unit_id", "?"),
                    "from": tuple(m.get("from", [0, 0])),
                    "to": tuple(m.get("to", [0, 0])),
                    "confidence": float(m.get("confidence", 0.5)),
                })

            return {
                "predicted_moves": predicted_moves,
                "threat_level": min(100, max(0, int(data.get("threat_level", 50)))),
                "suggested_response": data.get("suggested_response", ""),
                "analysis": data.get("analysis", ""),
                "confidence": float(data.get("confidence", 0.5)),
                "raw_response": raw,
                "prediction_type": "movement",
            }
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"移动预测响应解析失败: {e}")
            return self._empty_movement_result(f"响应解析失败: {e}")

    def _empty_movement_result(self, reason: str = "") -> dict:
        return {
            "predicted_moves": [],
            "threat_level": 50,
            "suggested_response": reason,
            "analysis": reason,
            "confidence": 0.0,
            "raw_response": "",
            "prediction_type": "movement",
        }

    # ------------------------------------------------------------------
    # 学习: 对比预测与结果
    # ------------------------------------------------------------------

    def learn_from_outcome(self, prediction: dict, actual_outcome: dict, cycle: int) -> None:
        """对比预测与实际结果, 积累经验

        Args:
            prediction: 之前做出的预测 dict
            actual_outcome: 实际战场结果 {"enemy_positions": [...], "enemy_moves": [...], ...}
            cycle: 当前轮次
        """
        pred_type = prediction.get("prediction_type", "unknown")

        if pred_type == "scan":
            accuracy = self._evaluate_scan_accuracy(prediction, actual_outcome)
        elif pred_type == "movement":
            accuracy = self._evaluate_movement_accuracy(prediction, actual_outcome)
        else:
            accuracy = 0.0

        entry = {
            "cycle": cycle,
            "prediction_type": pred_type,
            "prediction": prediction,
            "actual": actual_outcome,
            "accuracy": accuracy,
            "timestamp": time.time(),
        }

        self._experience.append(entry)

        # 维护经验上限
        if len(self._experience) > MAX_EXPERIENCE:
            self._experience = self._experience[-MAX_EXPERIENCE:]

        # 更新统计
        if accuracy >= 0.5:
            self._correct_predictions += 1

        logger.info(
            f"经验积累: 第{cycle}轮 {pred_type} 准确率={accuracy:.1%} "
            f"(总预测={self._total_predictions}, 正确={self._correct_predictions})"
        )

        # 每10轮分析一次
        if cycle > 0 and cycle % ANALYSIS_INTERVAL == 0:
            self._analyze_recent_accuracy(cycle)

    def _evaluate_scan_accuracy(self, prediction: dict, actual: dict) -> float:
        """评估地图扫描预测的准确率"""
        predicted = prediction.get("predicted_positions", [])
        actual_positions = actual.get("enemy_positions", [])

        if not predicted or not actual_positions:
            return 0.0

        # 对每个实际位置, 找最近的预测位置
        matched = 0
        threshold = max(self.screen_size) * 0.15  # 15%屏幕尺寸为匹配阈值

        for ax, ay in actual_positions:
            min_dist = float("inf")
            for px, py in predicted:
                dist = ((ax - px) ** 2 + (ay - py) ** 2) ** 0.5
                if dist < min_dist:
                    min_dist = dist
            if min_dist < threshold:
                matched += 1

        return matched / max(len(actual_positions), 1)

    def _evaluate_movement_accuracy(self, prediction: dict, actual: dict) -> float:
        """评估移动预测的准确率"""
        predicted_moves = prediction.get("predicted_moves", [])
        actual_moves = actual.get("enemy_moves", [])

        if not predicted_moves or not actual_moves:
            return 0.0

        matched = 0
        threshold = max(self.screen_size) * 0.1

        for am in actual_moves:
            actual_to = am.get("to", (0, 0))
            min_dist = float("inf")
            for pm in predicted_moves:
                pred_to = pm.get("to", (0, 0))
                dist = ((actual_to[0] - pred_to[0]) ** 2 + (actual_to[1] - pred_to[1]) ** 2) ** 0.5
                if dist < min_dist:
                    min_dist = dist
            if min_dist < threshold:
                matched += 1

        return matched / max(len(actual_moves), 1)

    def _analyze_recent_accuracy(self, cycle: int) -> None:
        """分析最近10轮的准确率, 调整策略"""
        recent = [e for e in self._experience if e["cycle"] > cycle - ANALYSIS_INTERVAL]
        if not recent:
            return

        scan_exps = [e for e in recent if e["prediction_type"] == "scan"]
        move_exps = [e for e in recent if e["prediction_type"] == "movement"]

        scan_acc = sum(e["accuracy"] for e in scan_exps) / max(len(scan_exps), 1)
        move_acc = sum(e["accuracy"] for e in move_exps) / max(len(move_exps), 1)

        logger.info(
            f"📊 第{cycle}轮准确率分析: "
            f"扫描={scan_acc:.1%} ({len(scan_exps)}次), "
            f"移动={move_acc:.1%} ({len(move_exps)}次)"
        )

        # 准确率过低时调整temperature
        if scan_acc < 0.3 and len(scan_exps) >= 5:
            logger.warning("地图扫描准确率过低, 可能需要调整预测策略")

        if move_acc < 0.3 and len(move_exps) >= 5:
            logger.warning("移动预测准确率过低, 考虑增加地形信息权重")

    # ------------------------------------------------------------------
    # 积累的智慧
    # ------------------------------------------------------------------

    def get_accumulated_wisdom(self) -> str:
        """返回积累的学习成果, 用于注入后续prompt

        Returns:
            格式化的经验文本, 或空字符串
        """
        if not self._experience:
            return ""

        # 取最近的有效经验
        high_acc = [e for e in self._experience[-50:] if e["accuracy"] >= 0.5]
        if not high_acc:
            # 如果没有高准确率经验, 取最近10条
            high_acc = self._experience[-10:]

        overall_acc = self._correct_predictions / max(self._total_predictions, 1)

        lines = [
            f"总体预测准确率: {overall_acc:.1%} ({self._correct_predictions}/{self._total_predictions})",
            f"累计经验: {len(self._experience)}条",
        ]

        if high_acc:
            lines.append("\n最近成功预测:")
            for e in high_acc[-3:]:
                ptype = e["prediction_type"]
                acc = e["accuracy"]
                lines.append(f"- 第{e['cycle']}轮 [{ptype}] 准确率={acc:.1%}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 持久化: 保存/加载
    # ------------------------------------------------------------------

    def save(self, path: Optional[str] = None) -> None:
        """保存经验和模型状态到JSON文件

        Args:
            path: 保存路径, 默认使用 self.save_path
        """
        save_path = Path(path or self.save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        state = {
            "screen_size": list(self.screen_size),
            "total_predictions": self._total_predictions,
            "correct_predictions": self._correct_predictions,
            "experience": self._experience,
            "map_cache_keys": list(self._map_cache.keys()),
            "map_cache": self._map_cache,
            "saved_at": time.time(),
        }

        save_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            f"预测状态已保存: {save_path} "
            f"(经验={len(self._experience)}条, 缓存={len(self._map_cache)}个)"
        )

    def load(self, path: Optional[str] = None) -> bool:
        """从JSON文件加载经验和模型状态

        Args:
            path: 加载路径, 默认使用 self.save_path

        Returns:
            是否加载成功
        """
        load_path = Path(path or self.save_path)
        if not load_path.exists():
            logger.warning(f"预测状态文件不存在: {load_path}")
            return False

        try:
            raw = load_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            # 文件名不是dict就跳过
            if not isinstance(data, dict):
                logger.warning(f"预测文件格式异常(type={type(data).__name__}), 重新初始化")
                return False
            self._total_predictions = int(data.get("total_predictions", 0))
            self._correct_predictions = int(data.get("correct_predictions", 0))
            self._experience = data.get("experience", []) if isinstance(data.get("experience"), list) else []
            self._map_cache = data.get("map_cache", {}) if isinstance(data.get("map_cache"), dict) else {}

            # 裁剪经验到上限
            if len(self._experience) > MAX_EXPERIENCE:
                self._experience = self._experience[-MAX_EXPERIENCE:]

            logger.info(
                f"预测状态已加载: {load_path} "
                f"(经验={len(self._experience)}条, 缓存={len(self._map_cache)}个, "
                f"准确率={self._correct_predictions}/{self._total_predictions})"
            )
            return True

        except Exception as e:
            logger.error(f"预测状态加载失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _encode_image(self, path: str) -> str:
        """将图片编码为base64"""
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _compute_file_hash(self, path: str) -> str:
        """计算文件MD5哈希, 用于缓存key"""
        try:
            with open(path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""

    def _clean_json(self, raw: str) -> str:
        """清理LLM响应中的markdown代码块"""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return text

    @property
    def accuracy(self) -> float:
        """总体预测准确率"""
        if self._total_predictions == 0:
            return 0.0
        return self._correct_predictions / self._total_predictions

    @property
    def experience_count(self) -> int:
        return len(self._experience)

    def clear_cache(self) -> None:
        """清除地图分析缓存"""
        self._map_cache.clear()
        logger.info("地图分析缓存已清除")

    def reset(self) -> None:
        """重置预测器状态 (新游戏开始时调用)"""
        self._experience = []
        self._map_cache = {}
        self._total_predictions = 0
        self._correct_predictions = 0
        logger.info("预测器状态已重置")