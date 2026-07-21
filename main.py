# Firefight AI 指挥系统 - 主入口

import argparse
import sys
import time
from pathlib import Path

import yaml
from loguru import logger

# 添加src到路径
sys.path.insert(0, str(Path(__file__).parent))

from src.execution.adb_utils import ADBUtils
from src.screen.capture import ScreenCapture
from src.vision.detector import UnitDetector
from src.vision.ocr_reader import UIReader
from src.state.manager import StateManager
from src.decision.commander import TacticalCommander
from src.decision.parser import CommandParser
from src.execution.executor import CommandExecutor
from src.controller.game_controller import GameController
from src.learning.battle_memory import BattleMemory
from src.learning.outcome_eval import OutcomeEvaluator
from src.learning.memory_retriever import MemoryRetriever
from src.learning.strategy_compressor import StrategyCompressor
from src.utils.logger import setup_logger
from src.utils.replay import print_replay_summary


def load_config(config_path: str = "config/settings.yaml") -> dict:
    """加载配置文件"""
    path = Path(config_path)
    if not path.exists():
        logger.error(f"配置文件不存在: {path.absolute()}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_components(cfg: dict) -> dict:
    """根据配置构建所有组件"""
    game_cfg = cfg["game"]
    device_cfg = cfg["device"]
    scrcpy_cfg = cfg["scrcpy"]
    yolo_cfg = cfg["yolo"]
    ocr_cfg = cfg["ocr"]
    llm_cfg = cfg["llm"]
    loop_cfg = cfg["game_loop"]
    team_cfg = cfg["team_detection"]
    log_cfg = cfg["logging"]
    debug_cfg = cfg["debug"]

    # 屏幕尺寸
    screen_size = (game_cfg["screen_width"], game_cfg["screen_height"])

    # 1. ADB - 根据active字段选择设备
    active_device = device_cfg.get("active", "generic")
    device_info = device_cfg.get(active_device, {})
    adb = ADBUtils(
        host=device_info.get("adb_host", "127.0.0.1"),
        port=device_info.get("adb_port", 5555),
        connect_timeout=device_cfg["adb_connect_timeout"],
        command_timeout=device_cfg["adb_command_timeout"],
        retry_count=device_cfg["adb_retry_count"],
    )
    logger.info(f"使用设备: {active_device} ({device_info.get('adb_host')}:{device_info.get('adb_port')})")

    # 2. 屏幕捕获
    capture = ScreenCapture(
        adb=adb,
        max_fps=scrcpy_cfg["max_fps"],
        bitrate=scrcpy_cfg["bitrate"],
        max_width=scrcpy_cfg["max_width"],
        max_height=scrcpy_cfg["max_height"],
        crop=scrcpy_cfg.get("crop"),
        timeout=scrcpy_cfg["timeout"],
    )

    # 3. YOLO检测器
    detector = UnitDetector(
        model_path=yolo_cfg["model_path"],
        fallback_model_path=yolo_cfg["fallback_model_path"],
        confidence_threshold=yolo_cfg["confidence_threshold"],
        iou_threshold=yolo_cfg["iou_threshold"],
        image_size=yolo_cfg["image_size"],
        device=yolo_cfg["device"],
        # 颜色检测配置 (来自 team_detection)
        color_roi_ratio=team_cfg.get("color_roi_ratio", 0.3),
        color_match_threshold=team_cfg.get("color_match_threshold", 0.15),
    )

    # 4. OCR
    ocr = UIReader(
        use_angle_cls=ocr_cfg["use_angle_cls"],
        lang=ocr_cfg["lang"],
        det_db_thresh=ocr_cfg["det_db_thresh"],
        rec_batch_num=ocr_cfg["rec_batch_num"],
    )

    # 5. 状态管理器
    state_manager = StateManager(
        screen_size=screen_size,
        ally_region=tuple(team_cfg["ally_region"]),
        enemy_region=tuple(team_cfg["enemy_region"]),
    )

    # 6. LLM指挥官
    commander = TacticalCommander(
        provider=llm_cfg["provider"],
        model=llm_cfg["model"],
        api_key=llm_cfg["api_key"],
        api_base=llm_cfg["api_base"],
        temperature=llm_cfg["temperature"],
        max_tokens=llm_cfg["max_tokens"],
        timeout=llm_cfg["timeout"],
        retry_count=llm_cfg["retry_count"],
        fallback_provider=llm_cfg["fallback_provider"],
        fallback_model=llm_cfg["fallback_model"],
        fallback_api_key=llm_cfg["fallback_api_key"],
        fallback_api_base=llm_cfg["fallback_api_base"],
    )

    # 7. 指令解析器
    parser = CommandParser(screen_size=screen_size)

    # ── 学习系统 ──
    learn_cfg = cfg.get("learning", {})
    battle_memory = None
    outcome_eval = None
    memory_retriever = None
    strategy_compressor = None
    game_session = str(int(time.time()))

    if learn_cfg.get("enabled", True):
        logger.info("初始化学习系统...")

        # 经验库
        battle_memory = BattleMemory()
        logger.info(f"  经验库: {battle_memory.db_path} (已有{battle_memory.count()}条)")

        # 结果评估器
        outcome_eval = OutcomeEvaluator()

        # 记忆检索器
        memory_retriever = MemoryRetriever(battle_memory)

        # 策略提炼器
        strategy_compressor = StrategyCompressor(
            battle_memory=battle_memory,
            api_key=llm_cfg["api_key"],
            api_base=llm_cfg["api_base"],
            model=llm_cfg["model"],
        )
        strategy_compressor._compress_interval = learn_cfg.get("compress_interval", 15)
        logger.info("  学习系统就绪")
    else:
        logger.info("学习系统已禁用 (learning.enabled=false)")

    # 8. 触控：使用 ADB input（通用方案，不依赖任何模拟器）
    touch = None

    # 9. 指令执行器
    pause_x = int(loop_cfg["pause_button_x"] * screen_size[0])
    pause_y = int(loop_cfg["pause_button_y"] * screen_size[1])
    executor = CommandExecutor(
        adb=adb,
        screen_size=screen_size,
        touch=touch,
        pause_button=(pause_x, pause_y),
    )

    # 9. 主控制器 (快速实时 + 学习模式)
    controller = GameController(
        adb=adb,
        capture=capture,
        detector=detector,
        state_manager=state_manager,
        commander=commander,
        parser=parser,
        executor=executor,
        max_cycles=loop_cfg["max_cycles"],
        game_over_timeout=loop_cfg["game_over_timeout"],
        save_screenshots=log_cfg["save_screenshots"],
        save_replay=log_cfg["save_replay"],
        # 学习系统
        battle_memory=battle_memory,
        outcome_eval=outcome_eval,
        memory_retriever=memory_retriever,
        strategy_compressor=strategy_compressor,
        game_session=game_session,
    )

    return {
        "adb": adb,
        "capture": capture,
        "detector": detector,
        "ocr": ocr,
        "state_manager": state_manager,
        "commander": commander,
        "parser": parser,
        "executor": executor,
        "controller": controller,
    }


def cmd_run(args) -> None:
    """运行AI指挥"""
    cfg = load_config(args.config)
    log_cfg = cfg["logging"]
    debug_cfg = cfg["debug"]

    # 初始化日志
    setup_logger(
        session_dir=log_cfg["session_dir"],
        level=log_cfg["level"],
        save_screenshots=log_cfg["save_screenshots"],
        save_replay=log_cfg["save_replay"],
    )

    components = build_components(cfg)
    controller: GameController = components["controller"]

    # 1. 连接设备
    adb: ADBUtils = components["adb"]
    if not adb.ensure_connected():
        logger.error("设备连接失败,请检查ADB配置")
        return

    # 2. 启动游戏
    if args.launch:
        game_cfg = cfg["game"]
        adb.launch_app(game_cfg["package_name"], game_cfg["activity_name"])
        import time
        time.sleep(3)

    # 3. 加载模型
    detector: UnitDetector = components["detector"]
    if not detector.load_model():
        logger.error("YOLO模型加载失败")
        return

    ocr: UIReader = components["ocr"]
    ocr.load_model()  # OCR失败不影响运行

    # 4. 加载prompts
    commander: TacticalCommander = components["commander"]
    commander.load_prompts()

    # 5. 启动屏幕捕获
    capture: ScreenCapture = components["capture"]
    if not capture.start():
        logger.error("屏幕捕获启动失败")
        return

    # 6. 运行主循环
    import time
    time.sleep(1)
    victory = controller.run()

    if victory:
        logger.info("游戏胜利!")
    else:
        logger.info("游戏结束")


def cmd_calibrate(args) -> None:
    """运行UI校准工具"""
    from src.utils.calibration import run_calibration
    run_calibration(args.host, args.port)


def cmd_replay(args) -> None:
    """查看回放"""
    print_replay_summary(args.replay_file)


def cmd_test(args) -> None:
    """运行测试"""
    cfg = load_config(args.config)
    log_cfg = cfg["logging"]
    setup_logger(session_dir=log_cfg["session_dir"], level="DEBUG")

    if args.component == "adb":
        active_device = cfg["device"].get("active", "generic")
        device_info = cfg["device"].get(active_device, {})
        adb = ADBUtils(
            host=device_info.get("adb_host", "127.0.0.1"),
            port=device_info.get("adb_port", 5555),
        )
        if adb.connect():
            print("ADB连接成功!")
            print(f"设备: {adb.device_addr}")
            activity = adb.get_current_activity()
            print(f"当前Activity: {activity}")
        else:
            print("ADB连接失败!")

    elif args.component == "yolo":
        detector = UnitDetector(**cfg["yolo"])
        if detector.load_model():
            print(f"YOLO模型加载成功! 类型: {detector.model_type}")
        else:
            print("YOLO模型加载失败!")

    elif args.component == "ocr":
        ocr = UIReader(**cfg["ocr"])
        if ocr.load_model():
            print("PaddleOCR加载成功!")
        else:
            print("PaddleOCR加载失败!")


def main():
    parser = argparse.ArgumentParser(
        description="Firefight AI 指挥系统 - 现代MOD版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py run                    # 运行AI指挥
  python main.py run --launch           # 启动游戏并运行AI指挥
  python main.py calibrate              # UI校准
  python main.py replay sessions/replay_xxx.json  # 查看回放
  python main.py test --component adb   # 测试ADB连接
  python main.py test --component yolo  # 测试YOLO模型
  python main.py test --component ocr   # 测试OCR
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # run 子命令
    run_parser = subparsers.add_parser("run", help="运行AI指挥")
    run_parser.add_argument(
        "--config", "-c", default="config/settings.yaml", help="配置文件路径"
    )
    run_parser.add_argument(
        "--launch", "-l", action="store_true", help="自动启动游戏"
    )

    # calibrate 子命令
    cal_parser = subparsers.add_parser("calibrate", help="UI校准")
    cal_parser.add_argument("--host", default="192.168.1.100", help="设备IP")
    cal_parser.add_argument("--port", type=int, default=5555, help="ADB端口")

    # replay 子命令
    replay_parser = subparsers.add_parser("replay", help="查看回放")
    replay_parser.add_argument("replay_file", help="回放文件路径")

    # test 子命令
    test_parser = subparsers.add_parser("test", help="测试组件")
    test_parser.add_argument(
        "--config", "-c", default="config/settings.yaml", help="配置文件路径"
    )
    test_parser.add_argument(
        "--component", "-m", choices=["adb", "yolo", "ocr"],
        default="adb", help="测试组件"
    )

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "calibrate":
        cmd_calibrate(args)
    elif args.command == "replay":
        cmd_replay(args)
    elif args.command == "test":
        cmd_test(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()