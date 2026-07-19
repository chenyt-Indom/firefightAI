"""YOLO模型训练脚本 - 训练现代MOD单位检测模型

使用方法:
  python scripts/train_model.py --data datasets/firefight_mod/dataset.yaml --epochs 100
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ultralytics import YOLO
from loguru import logger


def train_model(
    data_yaml: str = "datasets/firefight_mod/dataset.yaml",
    model_name: str = "yolov8n.pt",
    epochs: int = 100,
    imgsz: int = 640,
    batch: int = 16,
    device: str = "cuda:0",
    project: str = "models",
    name: str = "firefight_mod",
    **kwargs,
) -> str:
    """训练YOLO模型

    Args:
        data_yaml: 数据集配置文件
        model_name: 预训练模型(yolov8n/yolov8s/yolov8m)
        epochs: 训练轮数
        imgsz: 输入图像尺寸
        batch: 批次大小
        device: 训练设备
        project: 项目目录
        name: 实验名称

    Returns:
        最佳模型路径
    """
    logger.info(f"开始训练: model={model_name}, epochs={epochs}, data={data_yaml}")

    # 加载模型
    model = YOLO(model_name)

    # 训练
    results = model.train(
        data=data_yaml,
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        device=device,
        project=project,
        name=name,
        # 数据增强
        hsv_h=0.015,   # HSV-Hue 增强
        hsv_s=0.7,     # HSV-Saturation 增强
        hsv_v=0.4,     # HSV-Value 增强
        degrees=5.0,   # 旋转角度
        translate=0.1, # 平移
        scale=0.5,     # 缩放
        shear=0.0,     # 剪切
        perspective=0.0,
        flipud=0.0,    # 上下翻转
        fliplr=0.5,    # 左右翻转
        mosaic=1.0,    # Mosaic增强
        mixup=0.1,     # MixUp增强
        copy_paste=0.1,
        # 验证
        val=True,
        # 早停
        patience=20,
        # 保存
        save=True,
        save_period=10,
        **kwargs,
    )

    # 获取最佳模型路径
    best_path = Path(project) / name / "weights" / "best.pt"
    logger.info(f"训练完成! 最佳模型: {best_path.absolute()}")

    # 打印指标
    if results and hasattr(results, 'results_dict'):
        metrics = results.results_dict
        logger.info(f"训练指标: {metrics}")

    return str(best_path.absolute())


def resume_training(
    checkpoint_path: str,
    data_yaml: str = "datasets/firefight_mod/dataset.yaml",
    epochs: int = 50,
    **kwargs,
) -> str:
    """从检查点恢复训练"""
    logger.info(f"从检查点恢复训练: {checkpoint_path}")
    model = YOLO(checkpoint_path)
    results = model.train(
        data=data_yaml,
        epochs=epochs,
        resume=True,
        **kwargs,
    )
    return str(Path(checkpoint_path).parent / "best.pt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="训练Firefight MOD单位检测模型")
    parser.add_argument("--data", default="datasets/firefight_mod/dataset.yaml", help="数据集配置")
    parser.add_argument("--model", default="yolov8n.pt", help="预训练模型(yolov8n/s/m)")
    parser.add_argument("--epochs", type=int, default=100, help="训练轮数")
    parser.add_argument("--imgsz", type=int, default=640, help="图像尺寸")
    parser.add_argument("--batch", type=int, default=16, help="批次大小")
    parser.add_argument("--device", default="cuda:0", help="训练设备")
    parser.add_argument("--resume", help="从检查点恢复训练")
    parser.add_argument("--name", default="firefight_mod", help="实验名称")
    args = parser.parse_args()

    if args.resume:
        resume_training(
            checkpoint_path=args.resume,
            data_yaml=args.data,
            epochs=args.epochs,
        )
    else:
        train_model(
            data_yaml=args.data,
            model_name=args.model,
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            device=args.device,
            name=args.name,
        )