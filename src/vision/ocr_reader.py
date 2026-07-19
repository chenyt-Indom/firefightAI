"""OCR读取器 - 使用PaddleOCR读取游戏UI中的数字和文字"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
from loguru import logger

from src.utils.logger import log_vision


class UIReader:
    """PaddleOCR UI读取器,用于读取游戏界面中的资源、血量等数字"""

    def __init__(
        self,
        use_angle_cls: bool = True,
        lang: str = "en",
        det_db_thresh: float = 0.3,
        rec_batch_num: int = 6,
    ):
        self.use_angle_cls = use_angle_cls
        self.lang = lang
        self.det_db_thresh = det_db_thresh
        self.rec_batch_num = rec_batch_num
        self._ocr = None
        self._inference_count = 0
        self._total_time = 0.0

    def load_model(self) -> bool:
        """加载PaddleOCR模型"""
        try:
            from paddleocr import PaddleOCR
            self._ocr = PaddleOCR(
                use_angle_cls=self.use_angle_cls,
                lang=self.lang,
                det_db_thresh=self.det_db_thresh,
                rec_batch_num=self.rec_batch_num,
                use_gpu=True,
                show_log=False,
            )
            logger.info("PaddleOCR模型加载成功")
            return True
        except ImportError:
            logger.warning("PaddleOCR未安装,UI读取功能不可用")
            return False
        except Exception as e:
            logger.error(f"PaddleOCR加载失败: {e}")
            return False

    def read_region(
        self,
        frame: np.ndarray,
        region: tuple[float, float, float, float],
    ) -> Optional[str]:
        """读取指定区域内的文字

        Args:
            frame: 完整帧
            region: 归一化区域 (x1, y1, x2, y2) 0-1

        Returns:
            识别到的文字,无结果返回None
        """
        if self._ocr is None:
            return None

        h, w = frame.shape[:2]
        x1 = int(region[0] * w)
        y1 = int(region[1] * h)
        x2 = int(region[2] * w)
        y2 = int(region[3] * h)

        # 裁剪区域
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return None

        try:
            start = time.time()
            results = self._ocr.ocr(roi, cls=True)
            elapsed = (time.time() - start) * 1000
            self._inference_count += 1
            self._total_time += elapsed

            if results and results[0]:
                # 拼接所有识别结果
                texts = []
                for line in results[0]:
                    text = line[1][0]
                    confidence = line[1][1]
                    if confidence > 0.5:  # 过滤低置信度
                        texts.append(text)
                result = " ".join(texts).strip()
                log_vision(f"OCR识别: '{result}' (区域: {region}, 耗时{elapsed:.1f}ms)")
                return result if result else None
            return None
        except Exception as e:
            logger.warning(f"OCR识别失败: {e}")
            return None

    def read_number(
        self,
        frame: np.ndarray,
        region: tuple[float, float, float, float],
    ) -> Optional[int]:
        """读取指定区域内的数字

        Args:
            frame: 完整帧
            region: 归一化区域

        Returns:
            识别到的数字,无结果返回None
        """
        text = self.read_region(frame, region)
        if text is None:
            return None

        # 提取数字
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            return int(numbers[0])
        return None

    def read_ui(self, frame: np.ndarray, ui_regions: dict) -> dict:
        """读取所有UI区域

        Args:
            frame: 完整帧
            ui_regions: UI区域配置字典 {name: (x1,y1,x2,y2)}

        Returns:
            UI数据字典
        """
        ui_data = {}
        for name, region in ui_regions.items():
            result = self.read_region(frame, region)
            if result:
                ui_data[name] = result
            else:
                ui_data[name] = ""
        return ui_data

    def read_unit_health(
        self,
        frame: np.ndarray,
        bbox: tuple[int, int, int, int],
    ) -> Optional[int]:
        """读取单位血量(从单位信息面板)

        Args:
            frame: 完整帧
            bbox: 单位边界框 (x1,y1,x2,y2)

        Returns:
            血量百分比
        """
        # 血量通常显示在单位上方或信息面板中
        # 这里在单位框上方添加一个小的OCR区域
        x1, y1, x2, _ = bbox
        h, w = frame.shape[:2]

        # 血量区域:单位上方10-30像素,宽40像素
        health_x1 = max(0, (x1 - 10) / w)
        health_y1 = max(0, (y1 - 35) / h)
        health_x2 = min(1.0, (x1 + 50) / w)
        health_y2 = max(0, (y1 - 5) / h)

        return self.read_number(frame, (health_x1, health_y1, health_x2, health_y2))

    @property
    def avg_time(self) -> float:
        if self._inference_count == 0:
            return 0.0
        return self._total_time / self._inference_count

    @property
    def is_loaded(self) -> bool:
        return self._ocr is not None