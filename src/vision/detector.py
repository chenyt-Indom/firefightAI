"""单位检测器 - YOLOv8 + ByteTrack 实现现代MOD单位识别与跟踪"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

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

    def __init__(
        self,
        model_path: str = "models/best.engine",
        fallback_model_path: str = "models/best.pt",
        confidence_threshold: float = 0.5,
        iou_threshold: float = 0.45,
        image_size: int = 640,
        device: str = "cuda:0",
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

            # 敌我识别:使用位置启发式(下半屏=友方,上半屏=敌方)
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