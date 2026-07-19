"""
录屏+触控采集模块 - 录制用户玩游戏的全过程
同时采集: 屏幕截图 + ADB触控事件

使用方法:
  python scripts/record_session.py --adb_host 127.0.0.1 --adb_port 7555 --session my_game_1
  # 按 Ctrl+C 停止录制
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution.adb_utils import ADBUtils
from loguru import logger


class SessionRecorder:
    """游戏会话录制器 - 同时录屏和采集触控事件"""

    def __init__(
        self,
        adb: ADBUtils,
        session_name: str | None = None,
        output_dir: str = "sessions",
        capture_interval: float = 0.5,  # 截图间隔(秒)
        touch_device: str | None = None,  # 触控设备路径, 自动检测
    ):
        self.adb = adb
        self.session_name = session_name or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = Path(output_dir) / self.session_name
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.capture_interval = capture_interval
        self.touch_device = touch_device

        # 运行状态
        self._running = False
        self._touch_thread: threading.Thread | None = None
        self._capture_thread: threading.Thread | None = None
        self._touch_process: subprocess.Popen | None = None

        # 统计数据
        self.frame_count = 0
        self.touch_event_count = 0
        self.start_time: float | None = None
        self.end_time: float | None = None

        # 元数据
        self.metadata = {
            "session_name": self.session_name,
            "started_at": "",
            "ended_at": "",
            "total_frames": 0,
            "total_touch_events": 0,
            "duration_seconds": 0,
            "capture_interval": capture_interval,
            "device_addr": adb.device_addr,
            "screen_size": None,
        }

        logger.info(f"录制器初始化: session={self.session_name}, output={self.output_dir}")

    def detect_touch_device(self) -> str | None:
        """自动检测触控输入设备"""
        try:
            result = self.adb._run_s("getevent -p", timeout=5)
            # 查找包含ABS_MT_POSITION_X的设备
            lines = result.split("\n")
            current_device = None
            for line in lines:
                line = line.strip()
                if line.startswith("add device"):
                    current_device = line.split(":")[-1].strip()
                if "ABS_MT_POSITION_X" in line and current_device:
                    logger.info(f"检测到触控设备: {current_device}")
                    return current_device

            # 回退: 使用常见的触控设备路径
            for candidate in ["/dev/input/event1", "/dev/input/event2", "/dev/input/event0"]:
                try:
                    self.adb._run_s(f"getevent -p {candidate}", timeout=3)
                    logger.info(f"使用触控设备: {candidate}")
                    return candidate
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"触控设备检测失败: {e}")
        return None

    def _start_touch_capture(self) -> None:
        """后台线程: 采集ADB触控事件"""
        if not self.touch_device:
            self.touch_device = self.detect_touch_device()
        if not self.touch_device:
            logger.warning("未找到触控设备, 跳过触控采集")
            return

        touch_file = self.output_dir / "touch_events.txt"
        logger.info(f"开始触控采集 -> {touch_file}")

        try:
            cmd = [self.adb.adb_path, "-s", self.adb.device_addr, "shell", "getevent", "-lt", self.touch_device]
            self._touch_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            with open(touch_file, "w", encoding="utf-8") as f:
                f.write(f"# Session: {self.session_name}\n")
                f.write(f"# Device: {self.touch_device}\n")
                f.write(f"# Started: {datetime.now().isoformat()}\n")
                f.write("# Format: [timestamp] device: type code value\n")

                while self._running and self._touch_process and self._touch_process.poll() is None:
                    line = self._touch_process.stdout.readline()
                    if line:
                        f.write(line)
                        f.flush()
                        self.touch_event_count += 1

        except Exception as e:
            logger.error(f"触控采集异常: {e}")
        finally:
            if self._touch_process:
                self._touch_process.terminate()
                try:
                    self._touch_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._touch_process.kill()
            logger.info(f"触控采集结束, 共 {self.touch_event_count} 个事件")

    def _start_screen_capture(self) -> None:
        """后台线程: 定时截图"""
        self.metadata["started_at"] = datetime.now().isoformat()
        self.start_time = time.time()

        frames_dir = self.output_dir / "frames"
        frames_dir.mkdir(exist_ok=True)

        index_file = self.output_dir / "frame_index.jsonl"
        logger.info(f"开始截图采集 -> {frames_dir}, 间隔={self.capture_interval}s")

        with open(index_file, "w", encoding="utf-8") as idx:
            while self._running:
                try:
                    ts = time.time()
                    frame_name = f"frame_{self.frame_count:06d}.png"
                    frame_path = frames_dir / frame_name

                    # 使用ADB截图
                    self.adb._run_s("screencap -p /sdcard/firefight_ai_cap.png", timeout=5)
                    self.adb._run_adb(["pull", "/sdcard/firefight_ai_cap.png", str(frame_path)], timeout=5)
                    self.adb._run_s("rm /sdcard/firefight_ai_cap.png", timeout=3)

                    if frame_path.exists():
                        idx.write(json.dumps({
                            "frame": self.frame_count,
                            "filename": frame_name,
                            "timestamp": ts,
                            "elapsed": ts - self.start_time,
                        }) + "\n")
                        idx.flush()
                        self.frame_count += 1

                    # 计算等待时间
                    elapsed = time.time() - ts
                    sleep_time = max(0.01, self.capture_interval - elapsed)
                    time.sleep(sleep_time)

                except Exception as e:
                    logger.warning(f"截图失败: {e}")
                    time.sleep(0.5)

        self.end_time = time.time()
        self.metadata["ended_at"] = datetime.now().isoformat()
        self.metadata["total_frames"] = self.frame_count
        self.metadata["duration_seconds"] = self.end_time - self.start_time

        # 保存元数据
        meta_path = self.output_dir / "metadata.json"
        meta_path.write_text(json.dumps(self.metadata, indent=2, ensure_ascii=False), encoding="utf-8")

        logger.info(f"截图采集结束, 共 {self.frame_count} 帧, 时长 {self.metadata['duration_seconds']:.1f}s")

    def start(self) -> bool:
        """启动录制 (截图+触控双线程)"""
        if not self.adb.ensure_connected():
            logger.error("ADB未连接, 无法启动录制")
            return False

        self._running = True

        # 启动截图线程
        self._capture_thread = threading.Thread(
            target=self._start_screen_capture,
            name="screen-capture",
            daemon=True,
        )
        self._capture_thread.start()

        # 启动触控采集线程
        self._touch_thread = threading.Thread(
            target=self._start_touch_capture,
            name="touch-capture",
            daemon=True,
        )
        self._touch_thread.start()

        logger.info(f"录制启动! 按 Ctrl+C 停止")
        logger.info(f"  输出目录: {self.output_dir.absolute()}")
        logger.info(f"  截图间隔: {self.capture_interval}s")
        return True

    def stop(self) -> None:
        """停止录制"""
        logger.info("正在停止录制...")
        self._running = False

        # 等待线程结束
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=5)
        if self._touch_thread and self._touch_thread.is_alive():
            self._touch_thread.join(timeout=3)

        self.metadata["total_frames"] = self.frame_count
        self.metadata["total_touch_events"] = self.touch_event_count
        if self.start_time and self.end_time:
            self.metadata["duration_seconds"] = self.end_time - self.start_time

        meta_path = self.output_dir / "metadata.json"
        meta_path.write_text(json.dumps(self.metadata, indent=2, ensure_ascii=False), encoding="utf-8")

        logger.info(f"录制已停止")
        logger.info(f"  总帧数: {self.frame_count}")
        logger.info(f"  触控事件: {self.touch_event_count}")
        logger.info(f"  时长: {self.metadata.get('duration_seconds', 0):.1f}s")
        self.print_summary()

    def print_summary(self) -> None:
        """打印录制摘要"""
        print(f"\n{'='*50}")
        print(f"  录制会话: {self.session_name}")
        print(f"  输出目录: {self.output_dir}")
        print(f"  总帧数: {self.frame_count}")
        print(f"  触控事件: {self.touch_event_count}")
        print(f"  时长: {self.metadata.get('duration_seconds', 0):.1f}s")
        print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(
        description="Firefight AI - 游戏会话录制器 (录屏+触控)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/record_session.py                                     # 默认录制到sessions/
  python scripts/record_session.py --session my_tank_game              # 指定会话名
  python scripts/record_session.py --adb_host 127.0.0.1 --adb_port 7555  # 指定设备
  python scripts/record_session.py --interval 0.3                       # 0.3秒截图间隔
        """,
    )
    parser.add_argument("--adb_host", default="127.0.0.1", help="ADB设备IP")
    parser.add_argument("--adb_port", type=int, default=7555, help="ADB端口")
    parser.add_argument("--session", "-s", default=None, help="会话名称")
    parser.add_argument("--output", "-o", default="sessions", help="输出目录")
    parser.add_argument("--interval", "-i", type=float, default=0.5,
                        help="截图间隔(秒), 默认0.5s")
    parser.add_argument("--touch_device", default=None,
                        help="触控设备路径, 默认自动检测 (如 /dev/input/event1)")

    args = parser.parse_args()

    adb = ADBUtils(
        host=args.adb_host,
        port=args.adb_port,
        command_timeout=10,
        retry_count=2,
    )

    if not adb.connect():
        logger.error("ADB连接失败!")
        sys.exit(1)

    session_name = args.session or datetime.now().strftime("%Y%m%d_%H%M%S")

    recorder = SessionRecorder(
        adb=adb,
        session_name=session_name,
        output_dir=args.output,
        capture_interval=args.interval,
        touch_device=args.touch_device,
    )

    if not recorder.start():
        sys.exit(1)

    try:
        # 等待用户按 Ctrl+C 停止
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n")
    finally:
        recorder.stop()


if __name__ == "__main__":
    main()