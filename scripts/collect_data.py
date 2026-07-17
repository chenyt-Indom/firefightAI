"""数据采集脚本 - 从游戏画面中采集标注数据

使用方法:
  python scripts/collect_data.py --adb_host 192.168.1.100 --output datasets/firefight_mod/
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import yaml

# 添加src到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.execution.adb_utils import ADBUtils
from src.screen.capture import ScreenCapture
from loguru import logger


def collect_frames(
    adb_host: str = "192.168.1.100",
    adb_port: int = 5555,
    output_dir: str = "datasets/firefight_mod",
    num_frames: int = 500,
    interval: float = 2.0,
    prefix: str = "frame",
) -> None:
    """采集游戏画面帧

    Args:
        adb_host: 设备IP
        adb_port: ADB端口
        output_dir: 输出目录
        num_frames: 采集帧数
        interval: 采集间隔秒数
        prefix: 文件名前缀
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 初始化ADB和屏幕捕获
    adb = ADBUtils(host=adb_host, port=adb_port)
    if not adb.ensure_connected():
        logger.error("设备连接失败")
        return

    capture = ScreenCapture(adb=adb)
    capture.start()
    time.sleep(1)

    logger.info(f"开始采集, 目标: {num_frames}帧, 间隔: {interval}s")
    collected = 0

    try:
        while collected < num_frames:
            frame = capture.grab_latest_frame()
            if frame is None:
                logger.warning("帧获取失败,重试...")
                time.sleep(0.5)
                continue

            # 保存帧
            filename = output_path / f"{prefix}_{collected:04d}.jpg"
            cv2.imwrite(str(filename), frame)
            collected += 1

            if collected % 10 == 0:
                logger.info(f"已采集: {collected}/{num_frames}")

            if collected < num_frames:
                time.sleep(interval)

    except KeyboardInterrupt:
        logger.info(f"用户中断,已采集{collected}帧")
    finally:
        capture.stop()

    logger.info(f"采集完成! 共{collected}帧, 保存到: {output_path.absolute()}")

    # 生成数据集配置文件
    dataset_config = {
        "path": str(output_path.absolute()),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "nc": 6,
        "names": [
            "tank",        # 0: 主战坦克
            "ifv",         # 1: 步兵战车
            "infantry",    # 2: 步兵
            "sniper",      # 3: 狙击手
            "helicopter",  # 4: 武装直升机
            "building",    # 5: 建筑/据点
        ],
    }

    config_path = output_path / "dataset.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(dataset_config, f, allow_unicode=True)
    logger.info(f"数据集配置已保存: {config_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="采集游戏画面数据")
    parser.add_argument("--adb_host", default="192.168.1.100", help="设备IP")
    parser.add_argument("--adb_port", type=int, default=5555, help="ADB端口")
    parser.add_argument("--output", "-o", default="datasets/firefight_mod", help="输出目录")
    parser.add_argument("--num_frames", "-n", type=int, default=500, help="采集帧数")
    parser.add_argument("--interval", "-i", type=float, default=2.0, help="采集间隔(秒)")
    args = parser.parse_args()

    collect_frames(
        adb_host=args.adb_host,
        adb_port=args.adb_port,
        output_dir=args.output,
        num_frames=args.num_frames,
        interval=args.interval,
    )