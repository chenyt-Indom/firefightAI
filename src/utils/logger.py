"""日志系统 - 基于loguru的统一日志管理"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logger(
    session_dir: str = "sessions",
    level: str = "INFO",
    save_screenshots: bool = True,
    save_replay: bool = True,
) -> None:
    """初始化日志系统

    Args:
        session_dir: 日志保存目录
        level: 日志级别
        save_screenshots: 是否保存截图
        save_replay: 是否保存录像
    """
    # 移除默认handler
    logger.remove()

    # 控制台输出 - 彩色格式
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        level=level,
        colorize=True,
    )

    # 确保session目录存在
    session_path = Path(session_dir)
    session_path.mkdir(parents=True, exist_ok=True)

    # 全量日志文件
    logger.add(
        session_path / "full.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        level="DEBUG",
        rotation="50 MB",
        retention="7 days",
        encoding="utf-8",
    )

    # 决策日志 - 专门记录LLM决策
    logger.add(
        session_path / "decisions.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
        level="INFO",
        filter=lambda record: record["extra"].get("category") == "decision",
        rotation="10 MB",
        encoding="utf-8",
    )

    # 执行日志 - 记录ADB操作
    logger.add(
        session_path / "execution.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
        level="DEBUG",
        filter=lambda record: record["extra"].get("category") == "execution",
        rotation="10 MB",
        encoding="utf-8",
    )

    logger.info(f"日志系统初始化完成, session目录: {session_path.absolute()}")


def get_logger():
    """获取logger实例"""
    return logger


def log_decision(message: str, **kwargs) -> None:
    """记录战术决策日志"""
    logger.bind(category="decision").info(message, **kwargs)


def log_execution(message: str, **kwargs) -> None:
    """记录执行操作日志"""
    logger.bind(category="execution").debug(message, **kwargs)


def log_state(message: str, **kwargs) -> None:
    """记录状态变化日志"""
    logger.bind(category="state").info(message, **kwargs)


def log_vision(message: str, **kwargs) -> None:
    """记录视觉识别日志"""
    logger.bind(category="vision").debug(message, **kwargs)