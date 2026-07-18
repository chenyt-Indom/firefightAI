"""单位检测器 - YOLOv8 + ByteTrack 实现现代MOD单位识别与跟踪

敌我识别: 基于精确颜色检测(友军 #58A5F3 蓝色标记 / 敌军 #FD8177 红色标记)
颜色值来源: 游戏 mod.txt 配置文件, 是游戏引擎绘制的单位标记色
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from loguru import logger

from src.state.models import Unit, UnitType, Team
from src.utils.logger import log_vision


class UnitDetector:
    """基于YOLOv8的单位检测器,集成ByteTrack跟踪"""

    # 现代MOD类别映射 (训练模型: 0=tank, 1=infantry)
    CLASS_MAP: dict[int, UnitType] = {
        0: UnitType.TANK,
        1: UnitType.INFANTRY,
    }

    # 友军蓝色标记 HSV 范围 -- 来自 mod.txt: <friend>#58A5F3</friend>
    # OpenCV H: 0-180 (标准 H 360° 的一半)
    # #58A5F3 (BGR=243,165,88) -> H≈105, S≈0.64, V≈0.95
    ALLY_HSV_LOW = np.array([95, 100, 100], dtype=np.uint8)
    ALLY_HSV_HIGH = np.array([115, 255, 255], dtype=np.uint8)

    # 敌军红色标记 HSV 范围(两个区间, 因为红色在 HSV 跨越 0°/180° 边界)
    # -- 来自 mod.txt: <enemy>#FD8177</enemy>
    # #FD8177 (BGR=119,129,253) -> H≈5, S≈0.53, V≈0.99
    ENEMY_HSV_LOW_1 = np.array([0, 100, 100], dtype=np.uint8)
    ENEMY_HSV_HIGH_1 = np.array([10, 255, 255], dtype=np.uint8)
    ENEMY_HSV_LOW_2 = np.array([170, 100, 100], dtype=np.uint8)
    ENEMY_HSV_HIGH_2 = np.array([180, 255, 255], dtype=np.uint8)

    # 颜色采样参数
    COLOR_ROI_RATIO = 0.3       # 检测框上部30%区域用于颜色采样
    COLOR_MATCH_THRESHOLD = 0.15  # 颜色匹配的最低像素比例

    def __init__(
        self,
        model_path: str = "models/best.engine",
        fallback_model_path: str = "models/best.pt",
        confidence_threshold: float = 0.5,
        iou_threshold: float = 0.45,
        image_size: int = 640,
        device: str = "cuda:0",
        # 颜色检测配置(可选覆盖默认值)
        ally_hsv_low: Optional[tuple] = None,
        ally_hsv_high: Optional[tuple] = None,
        enemy_hsv_low_1: Optional[tuple] = None,
        enemy_hsv_high_1: Optional[tuple] = None,
        enemy_hsv_low_2: Optional[tuple] = None,
        enemy_hsv_high_2: Optional[tuple] = None,
        color_roi_ratio: float = 0.3,
        color_match_threshold: float = 0.15,
    ):
        self.model_path = model_path
        self.fallback_model_path = fallback_model_path
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.image_size = image_size
        self.device = device
        self._model = None
        self._model_type = "unknown"  # "tensorrt" | "pytorch"
        self._inference_count = 0
        self._total_inference_time = 0.0
        self._last_results = None  # 上一帧结果(用于降级)
        self._last_frame = None    # 上一帧画面(颜色检测时使用)

        # 颜色检测配置
        self.ally_hsv_low = np.array(ally_hsv_low, dtype=np.uint8) if ally_hsv_low else self.ALLY_HSV_LOW
        self.ally_hsv_high = np.array(ally_hsv_high, dtype=np.uint8) if ally_hsv_high else self.ALLY_HSV_HIGH
        self.enemy_hsv_low_1 = np.array(enemy_hsv_low_1, dtype=np.uint8) if enemy_hsv_low_1 else self.ENEMY_HSV_LOW_1
        self.enemy_hsv_high_1 = np.array(enemy_hsv_high_1, dtype=np.uint8) if enemy_hsv_high_1 else self.ENEMY_HSV_HIGH_1
        self.enemy_hsv_low_2 = np.array(enemy_hsv_low_2, dtype=np.uint8) if enemy_hsv_low_2 else self.ENEMY_HSV_LOW_2
        self.enemy_hsv_high_2 = np.array(enemy_hsv_high_2, dtype=np.uint8) if enemy_hsv_high_2 else self.ENEMY_HSV_HIGH_2
        self.color_roi_ratio = color_roi_ratio
        self.color_match_threshold = color_match_threshold

    def load_model(self) -> bool:
        """加载YOLO模型,优先TensorRT,回退PyTorch"""
        from ultralytics import YOLO

        # 尝试加载TensorRT
        trt_path = Path(self.model_path)
        if trt_path.suffix == ".engine" and trt_path.exists():
            try:
                self._model = YOLO(str(trt_path), task="detect")
                self._model_type = "tensorrt"
                logger.info(f"YOLO TensorRT模型加载成功: {trt_path}")
                return True
            except Exception as e:
                logger.warning(f"TensorRT模型加载失败: {e}, 尝试PyTorch回退")

        # 回退到PyTorch
        pt_path = Path(self.fallback_model_path)
        if pt_path.exists():
            try:
                self._model = YOLO(str(pt_path), task="detect")
                self._model_type = "pytorch"
                logger.info(f"YOLO PyTorch模型加载成功: {pt_path}")
                return True
            except Exception as e:
                logger.error(f"PyTorch模型加载失败: {e}")
                return False

        # 使用预训练模型作为临时替代
        logger.warning("未找到训练好的模型,使用YOLOv8n预训练权重作为临时替代")
        try:
            self._model = YOLO("yolov8n.pt", task="detect")
            self._model_type = "pytorch_pretrained"
            return True
        except Exception as e:
            logger.error(f"预训练模型加载失败: {e}")
            return False

    def predict(self, frame: np.ndarray) -> list[Unit]:
        """对一帧画面进行检测和跟踪

        Args:
            frame: BGR格式的numpy数组 (H, W, 3)

        Returns:
            检测到的单位列表
        """
        if self._model is None:
            logger.error("模型未加载")
            return []

        try:
            start_time = time.time()

            # 保存帧用于颜色检测
            self._last_frame = frame

            # YOLO推理 + ByteTrack跟踪
            results = self._model.track(
                frame,
                persist=True,
                conf=self.confidence_threshold,
                iou=self.iou_threshold,
                imgsz=self.image_size,
                device=self.device,
                verbose=False,
            )

            elapsed = (time.time() - start_time) * 1000
            self._inference_count += 1
            self._total_inference_time += elapsed

            # 解析结果
            units = self._parse_results(results, frame.shape)
            self._last_results = results

            log_vision(
                f"检测完成: {len(units)}个单位, "
                f"耗时{elapsed:.1f}ms, "
                f"平均{self.avg_inference_time:.1f}ms"
            )
            return units

        except Exception as e:
            logger.error(f"YOLO推理失败: {e}")
            # 降级:返回上一帧结果(标注stale)
            if self._last_results is not None:
                logger.warning("使用上一帧检测结果(标注stale)")
                units = self._parse_results(self._last_results, frame.shape)
                for u in units:
                    u.stale = True
                return units
            return []

    def _parse_results(self, results, frame_shape: tuple) -> list[Unit]:
        """解析YOLO+ByteTrack结果"""
        units: list[Unit] = []
        h, w = frame_shape[:2]

        if results is None or len(results) == 0:
            return units

        result = results[0]

        if result.boxes is None or len(result.boxes) == 0:
            return units

        boxes = result.boxes
        for i in range(len(boxes)):
            # 获取检测框
            xyxy = boxes.xyxy[i].cpu().numpy()
            x1, y1, x2, y2 = map(int, xyxy)
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2

            # 获取类别
            cls_id = int(boxes.cls[i].cpu().numpy())
            unit_type = self.CLASS_MAP.get(cls_id)
            if unit_type is None:
                # 如果类别不在映射中,使用ID mod 类别数
                cls_id = cls_id % len(self.CLASS_MAP)
                unit_type = self.CLASS_MAP.get(cls_id, UnitType.INFANTRY)

            # 获取置信度
            confidence = float(boxes.conf[i].cpu().numpy())

            # 获取track_id (ByteTrack分配)
            track_id = i  # 默认使用索引
            if boxes.id is not None:
                track_id = int(boxes.id[i].cpu().numpy())

            # 敌我识别: 基于颜色检测(精确HSV匹配)
            team = self._detect_team_by_color(frame, x1, y1, x2, y2)

            # 颜色检测失败时回退到位置启发式
            if team == Team.UNKNOWN:
                team = Team.ALLY if cy > h * 0.55 else Team.ENEMY

            # 敌方ID加100偏移避免冲突
            if team == Team.ENEMY:
                track_id += 100

            unit = Unit(
                track_id=track_id,
                unit_type=unit_type,
                team=team,
                x=cx,
                y=cy,
                bbox=(x1, y1, x2, y2),
                confidence=confidence,
            )
            units.append(unit)

        return units

    def _detect_team_by_color(
        self, frame: np.ndarray,
        x1: int, y1: int, x2: int, y2: int,
    ) -> Team:
        """通过颜色检测判断单位所属阵营

        原理: 游戏在单位上方/身上绘制颜色标记
        - 友军: #58A5F3 蓝色 (H≈205°, S≈0.64, V≈0.95)
        - 敌军: #FD8177 红色 (H≈5°, S≈0.53, V≈0.99)

        采样策略: 检测框上部30%区域(颜色标记通常在单位图标上方)
        计算 ally_pixels / total_pixels 和 enemy_pixels / total_pixels,
        超过阈值则判定。

        Args:
            frame: BGR 格式的完整帧
            x1, y1, x2, y2: 检测框坐标

        Returns:
            Team.ALLY / Team.ENEMY / Team.UNKNOWN
        """
        try:
            h, w = frame.shape[:2]

            # 裁剪检测框上部区域(标记通常在单位上方)
            box_h = y2 - y1
            roi_y1 = max(0, y1 - int(box_h * 0.5))  # 在框上方扩展50%
            roi_y2 = min(h, y1 + int(box_h * self.color_roi_ratio))
            roi_x1 = max(0, x1)
            roi_x2 = min(w, x2)

            if roi_y2 <= roi_y1 or roi_x2 <= roi_x1:
                return Team.UNKNOWN

            roi = frame[roi_y1:roi_y2, roi_x1:roi_x2]
            if roi.size == 0:
                return Team.UNKNOWN

            # 转换到 HSV
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

            # 创建颜色掩码
            ally_mask = cv2.inRange(hsv, self.ally_hsv_low, self.ally_hsv_high)
            enemy_mask_1 = cv2.inRange(hsv, self.enemy_hsv_low_1, self.enemy_hsv_high_1)
            enemy_mask_2 = cv2.inRange(hsv, self.enemy_hsv_low_2, self.enemy_hsv_high_2)
            enemy_mask = cv2.bitwise_or(enemy_mask_1, enemy_mask_2)

            # 计算匹配像素比例
            total_pixels = roi.shape[0] * roi.shape[1]
            if total_pixels == 0:
                return Team.UNKNOWN

            ally_ratio = np.count_nonzero(ally_mask) / total_pixels
            enemy_ratio = np.count_nonzero(enemy_mask) / total_pixels

            # 判定: 超过阈值且明显占优的一方
            if ally_ratio > self.color_match_threshold and ally_ratio > enemy_ratio:
                return Team.ALLY
            elif enemy_ratio > self.color_match_threshold and enemy_ratio > ally_ratio:
                return Team.ENEMY

            return Team.UNKNOWN

        except Exception as e:
            logger.debug(f"颜色检测异常: {e}")
            return Team.UNKNOWN

    @property
    def avg_inference_time(self) -> float:
        """平均推理时间(ms)"""
        if self._inference_count == 0:
            return 0.0
        return self._total_inference_time / self._inference_count

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_type(self) -> str:
        return self._model_type