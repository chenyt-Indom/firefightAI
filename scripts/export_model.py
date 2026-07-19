"""模型导出脚本 - 将PyTorch模型导出为ONNX/TensorRT格式

使用方法:
  python scripts/export_model.py --model models/firefight_mod/weights/best.pt --format engine
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ultralytics import YOLO
from loguru import logger


def export_model(
    model_path: str,
    format: str = "engine",  # engine / onnx / openvino / tflite
    imgsz: int = 640,
    device: str = "cuda:0",
    half: bool = True,  # FP16
    dynamic: bool = False,
    workspace: int = 4,  # TensorRT workspace GB
) -> str:
    """导出模型

    Args:
        model_path: PyTorch模型路径
        format: 导出格式 (engine/onnx/openvino/tflite)
        imgsz: 输入尺寸
        device: 设备
        half: 是否使用FP16
        dynamic: 是否使用动态batch
        workspace: TensorRT工作空间大小

    Returns:
        导出后的模型路径
    """
    logger.info(f"导出模型: {model_path} -> {format}")

    model = YOLO(model_path)

    export_kwargs = {
        "format": format,
        "imgsz": imgsz,
        "device": device,
        "half": half,
        "dynamic": dynamic,
        "verbose": True,
    }

    if format == "engine":
        export_kwargs["workspace"] = workspace

    try:
        export_path = model.export(**export_kwargs)
        logger.info(f"导出成功: {export_path}")
        return str(export_path)
    except Exception as e:
        logger.error(f"导出失败: {e}")

        # 如果TensorRT导出失败,尝试ONNX
        if format == "engine":
            logger.info("TensorRT导出失败,尝试导出ONNX...")
            try:
                onnx_path = export_model(
                    model_path=model_path,
                    format="onnx",
                    imgsz=imgsz,
                    device=device,
                    half=half,
                )
                logger.info(f"ONNX导出成功: {onnx_path}")
                return onnx_path
            except Exception as e2:
                logger.error(f"ONNX导出也失败: {e2}")

        raise


def validate_model(model_path: str, data_yaml: str | None = None) -> None:
    """验证模型性能"""
    logger.info(f"验证模型: {model_path}")
    model = YOLO(model_path)

    if data_yaml and Path(data_yaml).exists():
        metrics = model.val(data=data_yaml)
        logger.info(f"验证指标: {metrics}")
    else:
        logger.info("未提供验证数据集,跳过验证")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导出Firefight MOD检测模型")
    parser.add_argument("--model", "-m", required=True, help="PyTorch模型路径(best.pt)")
    parser.add_argument("--format", "-f", default="engine",
                        choices=["engine", "onnx", "openvino", "tflite"],
                        help="导出格式")
    parser.add_argument("--imgsz", type=int, default=640, help="输入尺寸")
    parser.add_argument("--device", default="cuda:0", help="设备")
    parser.add_argument("--fp32", action="store_true", help="使用FP32(默认FP16)")
    parser.add_argument("--dynamic", action="store_true", help="动态batch")
    parser.add_argument("--workspace", type=int, default=4, help="TensorRT工作空间(GB)")
    parser.add_argument("--validate", action="store_true", help="导出后验证")
    parser.add_argument("--data", help="验证数据集(配合--validate使用)")
    args = parser.parse_args()

    export_model(
        model_path=args.model,
        format=args.format,
        imgsz=args.imgsz,
        device=args.device,
        half=not args.fp32,
        dynamic=args.dynamic,
        workspace=args.workspace,
    )

    if args.validate:
        validate_model(args.model, args.data)