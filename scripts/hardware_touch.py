"""
硬件级触控注入模块 - 通过 sendevent 直接写 /dev/input/eventN

原理:
  ADB input tap/swipe → Android InputManager (Java框架层) → 游戏可检测合成事件
  sendevent /dev/input/eventN → Linux内核input子系统 → 事件路径与真实手指一致

事件类型 (type):
  EV_SYN  = 0x0000  (同步事件)
  EV_KEY  = 0x0001  (按键事件)
  EV_ABS  = 0x0003  (绝对坐标事件)

关键 ABS code:
  ABS_MT_SLOT         = 0x002f  (触点槽位)
  ABS_MT_TRACKING_ID  = 0x0039  (触点唯一ID, -1=抬起)
  ABS_MT_POSITION_X   = 0x0035  (X坐标)
  ABS_MT_POSITION_Y   = 0x0036  (Y坐标)
  ABS_MT_PRESSURE     = 0x002a  (压力)
  ABS_MT_TOUCH_MAJOR  = 0x0030  (触点面积)
  BTN_TOUCH           = 0x014a  (触摸按键)

使用方法:
  from scripts.hardware_touch import HardwareTouchInjector
  injector = HardwareTouchInjector(adb)
  injector.connect()           # 探测设备
  injector.tap(540, 960)       # 硬件级点击
  injector.swipe(540, 960, 200, 400, 500)  # 硬件级滑动
"""
from __future__ import annotations

import subprocess
import time
from typing import Optional

from loguru import logger


# Linux input 事件常量
EV_SYN = 0x0000
EV_KEY = 0x0001
EV_ABS = 0x0003
EV_SYN_REPORT = 0x0000  # SYN_REPORT

# ABS code
ABS_MT_SLOT = 0x002f
ABS_MT_TRACKING_ID = 0x0039
ABS_MT_POSITION_X = 0x0035
ABS_MT_POSITION_Y = 0x0036
ABS_MT_PRESSURE = 0x002a
ABS_MT_TOUCH_MAJOR = 0x0030

# KEY code
BTN_TOUCH = 0x014a

# ⚠️ 重要: sendevent 参数必须用 0x 前缀的十六进制
# 不能用 0003 002f 这种格式, 因为 sendevent 用 strtol base=0 解析
# "002f" 会被当作八进制, 但八进制不能含 f, 解析失败导致事件被丢弃
# 正确格式: sendevent /dev/input/event4 0x03 0x2f 0


class HardwareTouchInjector:
    """硬件级触控注入器 - 通过 sendevent 写内核 input 事件"""

    def __init__(self, adb_utils, screen_size: tuple[int, int] = (1080, 1920)):
        """
        Args:
            adb_utils: ADBUtils 实例
            screen_size: 屏幕分辨率 (宽, 高)
        """
        self.adb = adb_utils
        self.screen_w, self.screen_h = screen_size
        self.touch_device: Optional[str] = None
        # 触控设备坐标范围 (从 getevent -p 获取)
        self.x_min, self.x_max = 0, 0
        self.y_min, self.y_max = 0, 0
        self.x_res, self.y_res = 0, 0
        # 触点 tracking id 计数器
        self._next_tracking_id = 1
        self._connected = False

    def connect(self) -> bool:
        """探测触控设备并获取参数"""
        logger.info("开始探测触控设备...")
        try:
            result = self.adb._run_s("getevent -p", timeout=10)
            lines = result.split("\n")

            current_device = None
            found_x = False
            found_y = False

            for line in lines:
                line = line.strip()

                # 检测设备节点
                if line.startswith("add device"):
                    current_device = line.split(":")[-1].strip()
                    found_x = False
                    found_y = False
                    # 重置参数
                    self.x_min = self.x_max = 0
                    self.y_min = self.y_max = 0

                # 解析 ABS_MT_POSITION_X 参数
                if "ABS_MT_POSITION_X" in line and current_device:
                    found_x = True
                    # 格式: type 3 (EV_ABS), code 53 (ABS_MT_POSITION_X), value 0, min 0, max 32767, res 0
                    parts = line.split(",")
                    for part in parts:
                        part = part.strip()
                        if part.startswith("min"):
                            self.x_min = int(part.split()[1])
                        elif part.startswith("max"):
                            self.x_max = int(part.split()[1])
                        elif part.startswith("res"):
                            self.x_res = int(part.split()[1])
                    logger.debug(f"X参数: {line}")

                # 解析 ABS_MT_POSITION_Y 参数
                if "ABS_MT_POSITION_Y" in line and current_device:
                    found_y = True
                    parts = line.split(",")
                    for part in parts:
                        part = part.strip()
                        if part.startswith("min"):
                            self.y_min = int(part.split()[1])
                        elif part.startswith("max"):
                            self.y_max = int(part.split()[1])
                        elif part.startswith("res"):
                            self.y_res = int(part.split()[1])
                    logger.debug(f"Y参数: {line}")

                # 找到同时支持 X 和 Y 的设备
                if found_x and found_y and current_device and not self.touch_device:
                    self.touch_device = current_device
                    logger.info(f"✅ 找到触控设备: {self.touch_device}")
                    logger.info(
                        f"   X范围: {self.x_min}~{self.x_max}, "
                        f"Y范围: {self.y_min}~{self.y_max}"
                    )

            if not self.touch_device:
                # 回退：列出所有设备供用户选择
                logger.error("未找到支持 ABS_MT_POSITION_X/Y 的触控设备")
                logger.error("所有 input 设备列表:")
                for line in lines:
                    if line.startswith("add device") or "name" in line.lower():
                        logger.error(f"  {line}")
                return False

            self._connected = True
            logger.info("硬件触控注入器就绪")
            return True

        except Exception as e:
            logger.error(f"探测触控设备失败: {e}")
            return False

    def _screen_to_device_x(self, x: int) -> int:
        """屏幕坐标 → 设备坐标 X"""
        if self.x_max == self.x_min:
            return x
        return int((x / self.screen_w) * (self.x_max - self.x_min) + self.x_min)

    def _screen_to_device_y(self, y: int) -> int:
        """屏幕坐标 → 设备坐标 Y"""
        if self.y_max == self.y_min:
            return y
        return int((y / self.screen_h) * (self.y_max - self.y_min) + self.y_min)

    def _sendevent(self, type_val: int, code_val: int, value: int) -> bool:
        """执行单条 sendevent 命令 (使用 0x 十六进制格式避免八进制解析问题)"""
        cmd = (
            f"sendevent {self.touch_device} "
            f"0x{type_val:x} 0x{code_val:x} {value}"
        )
        try:
            self.adb._run_s(cmd, timeout=3)
            return True
        except Exception as e:
            logger.debug(f"sendevent 失败: {cmd} - {e}")
            return False

    def _send_batch(self, events: list[tuple[int, int, int]]) -> bool:
        """批量发送事件 (合并到一条 shell 命令减少延迟, 使用 0x 十六进制格式)

        Args:
            events: [(type, code, value), ...]
        """
        if not events or not self.touch_device:
            return False

        # 拼接成一条命令 (用 0x 前缀十六进制)
        parts = []
        for type_val, code_val, value in events:
            parts.append(
                f"sendevent {self.touch_device} "
                f"0x{type_val:x} 0x{code_val:x} {value}"
            )
        cmd = " && ".join(parts)

        try:
            self.adb._run_s(cmd, timeout=10)
            return True
        except Exception as e:
            logger.debug(f"批量 sendevent 失败: {e}")
            return False

    def tap(self, x: int, y: int, hold_ms: int = 50) -> bool:
        """硬件级点击

        Args:
            x, y: 屏幕坐标 (像素)
            hold_ms: 按住时长 (毫秒)
        """
        if not self._connected:
            logger.error("触控注入器未连接")
            return False

        dev_x = self._screen_to_device_x(x)
        dev_y = self._screen_to_device_y(y)
        tracking_id = self._next_tracking_id
        self._next_tracking_id += 1

        logger.debug(
            f"硬件 tap: screen({x},{y}) → dev({dev_x},{dev_y}), "
            f"tracking_id={tracking_id}"
        )

        # 按下 (Type B 协议)
        events_down = [
            (EV_ABS, ABS_MT_SLOT, 0),               # 槽位 0
            (EV_ABS, ABS_MT_TRACKING_ID, tracking_id),  # 分配 tracking id
            (EV_ABS, ABS_MT_POSITION_X, dev_x),     # X 坐标
            (EV_ABS, ABS_MT_POSITION_Y, dev_y),     # Y 坐标
            (EV_ABS, ABS_MT_PRESSURE, 50),          # 压力 (模拟)
            (EV_KEY, BTN_TOUCH, 1),                 # 触摸按键按下
            (EV_SYN, EV_SYN_REPORT, 0),             # 同步
        ]
        self._send_batch(events_down)

        # 按住
        time.sleep(hold_ms / 1000.0)

        # 抬起
        events_up = [
            (EV_ABS, ABS_MT_SLOT, 0),
            (EV_ABS, ABS_MT_TRACKING_ID, -1),   # -1 = 触点抬起
            (EV_KEY, BTN_TOUCH, 0),             # 触摸按键释放
            (EV_SYN, EV_SYN_REPORT, 0),         # 同步
        ]
        self._send_batch(events_up)

        return True

    def swipe(
        self,
        x1: int, y1: int,
        x2: int, y2: int,
        duration_ms: int = 300,
        steps: int = 10,
    ) -> bool:
        """硬件级滑动

        Args:
            x1, y1: 起点屏幕坐标
            x2, y2: 终点屏幕坐标
            duration_ms: 滑动总时长 (毫秒)
            steps: 插值步数 (越多越平滑)
        """
        if not self._connected:
            logger.error("触控注入器未连接")
            return False

        dev_x1 = self._screen_to_device_x(x1)
        dev_y1 = self._screen_to_device_y(y1)
        dev_x2 = self._screen_to_device_x(x2)
        dev_y2 = self._screen_to_device_y(y2)
        tracking_id = self._next_tracking_id
        self._next_tracking_id += 1

        logger.debug(
            f"硬件 swipe: screen({x1},{y1})→({x2},{y2}), "
            f"dev({dev_x1},{dev_y1})→({dev_x2},{dev_y2}), "
            f"duration={duration_ms}ms, steps={steps}"
        )

        # 1. 按下
        events_down = [
            (EV_ABS, ABS_MT_SLOT, 0),
            (EV_ABS, ABS_MT_TRACKING_ID, tracking_id),
            (EV_ABS, ABS_MT_POSITION_X, dev_x1),
            (EV_ABS, ABS_MT_POSITION_Y, dev_y1),
            (EV_ABS, ABS_MT_PRESSURE, 50),
            (EV_KEY, BTN_TOUCH, 1),
            (EV_SYN, EV_SYN_REPORT, 0),
        ]
        self._send_batch(events_down)

        # 2. 逐步移动 (线性插值)
        step_delay = duration_ms / steps / 1000.0
        for i in range(1, steps + 1):
            t = i / steps
            cur_x = int(dev_x1 + (dev_x2 - dev_x1) * t)
            cur_y = int(dev_y1 + (dev_y2 - dev_y1) * t)

            events_move = [
                (EV_ABS, ABS_MT_SLOT, 0),
                (EV_ABS, ABS_MT_POSITION_X, cur_x),
                (EV_ABS, ABS_MT_POSITION_Y, cur_y),
                (EV_ABS, ABS_MT_PRESSURE, 50),
                (EV_SYN, EV_SYN_REPORT, 0),
            ]
            self._send_batch(events_move)
            time.sleep(step_delay)

        # 3. 抬起
        events_up = [
            (EV_ABS, ABS_MT_SLOT, 0),
            (EV_ABS, ABS_MT_TRACKING_ID, -1),
            (EV_KEY, BTN_TOUCH, 0),
            (EV_SYN, EV_SYN_REPORT, 0),
        ]
        self._send_batch(events_up)

        return True

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> bool:
        """硬件级长按"""
        return self.tap(x, y, hold_ms=duration_ms)

    def multi_touch(
        self,
        points: list[tuple[int, int, int]],
        duration_ms: int = 500,
    ) -> bool:
        """硬件级多点触控 (用于双指缩放等)

        Args:
            points: [(x1, y1, slot0), (x2, y2, slot1), ...]
                    slot 是触点编号 (0, 1, ...)
            duration_ms: 持续时长
        """
        if not self._connected:
            return False

        base_tracking_id = self._next_tracking_id
        self._next_tracking_id += len(points)

        # 1. 所有手指按下
        events_down = []
        for i, (x, y, slot) in enumerate(points):
            dev_x = self._screen_to_device_x(x)
            dev_y = self._screen_to_device_y(y)
            events_down.extend([
                (EV_ABS, ABS_MT_SLOT, slot),
                (EV_ABS, ABS_MT_TRACKING_ID, base_tracking_id + i),
                (EV_ABS, ABS_MT_POSITION_X, dev_x),
                (EV_ABS, ABS_MT_POSITION_Y, dev_y),
                (EV_ABS, ABS_MT_PRESSURE, 50),
            ])
        events_down.extend([
            (EV_KEY, BTN_TOUCH, 1),
            (EV_SYN, EV_SYN_REPORT, 0),
        ])
        self._send_batch(events_down)

        time.sleep(duration_ms / 1000.0)

        # 2. 所有手指抬起
        events_up = []
        for _, _, slot in points:
            events_up.extend([
                (EV_ABS, ABS_MT_SLOT, slot),
                (EV_ABS, ABS_MT_TRACKING_ID, -1),
            ])
        events_up.extend([
            (EV_KEY, BTN_TOUCH, 0),
            (EV_SYN, EV_SYN_REPORT, 0),
        ])
        self._send_batch(events_up)

        return True

    def pinch_zoom(
        self,
        center_x: int, center_y: int,
        start_distance: int = 200,
        end_distance: int = 600,
        duration_ms: int = 500,
    ) -> bool:
        """双指缩放 (捏合/展开)

        Args:
            center_x, center_y: 缩放中心
            start_distance: 两指起始距离
            end_distance: 两指结束距离 (>start=放大, <start=缩小)
            duration_ms: 动画时长
        """
        # 两指从中心点向相反方向移动
        # 手指0: 中心向上左偏移
        # 手指1: 中心向下右偏移
        steps = 15
        step_delay = duration_ms / steps / 1000.0

        base_tracking_id = self._next_tracking_id
        self._next_tracking_id += 2

        for step in range(steps + 1):
            t = step / steps
            dist = int(start_distance + (end_distance - start_distance) * t)
            half = dist // 2

            if step == 0:
                # 第一帧：两指按下
                events = []
                for slot in [0, 1]:
                    if slot == 0:
                        x = center_x - half
                        y = center_y - half
                    else:
                        x = center_x + half
                        y = center_y + half
                    dev_x = self._screen_to_device_x(x)
                    dev_y = self._screen_to_device_y(y)
                    events.extend([
                        (EV_ABS, ABS_MT_SLOT, slot),
                        (EV_ABS, ABS_MT_TRACKING_ID, base_tracking_id + slot),
                        (EV_ABS, ABS_MT_POSITION_X, dev_x),
                        (EV_ABS, ABS_MT_POSITION_Y, dev_y),
                        (EV_ABS, ABS_MT_PRESSURE, 50),
                    ])
                events.extend([
                    (EV_KEY, BTN_TOUCH, 1),
                    (EV_SYN, EV_SYN_REPORT, 0),
                ])
            elif step == steps:
                # 最后一帧：两指抬起
                events = []
                for slot in [0, 1]:
                    events.extend([
                        (EV_ABS, ABS_MT_SLOT, slot),
                        (EV_ABS, ABS_MT_TRACKING_ID, -1),
                    ])
                events.extend([
                    (EV_KEY, BTN_TOUCH, 0),
                    (EV_SYN, EV_SYN_REPORT, 0),
                ])
            else:
                # 中间帧：移动
                events = []
                for slot in [0, 1]:
                    if slot == 0:
                        x = center_x - half
                        y = center_y - half
                    else:
                        x = center_x + half
                        y = center_y + half
                    dev_x = self._screen_to_device_x(x)
                    dev_y = self._screen_to_device_y(y)
                    events.extend([
                        (EV_ABS, ABS_MT_SLOT, slot),
                        (EV_ABS, ABS_MT_POSITION_X, dev_x),
                        (EV_ABS, ABS_MT_POSITION_Y, dev_y),
                        (EV_ABS, ABS_MT_PRESSURE, 50),
                    ])
                events.append((EV_SYN, EV_SYN_REPORT, 0))
            self._send_batch(events)
            time.sleep(step_delay)

        return True


# ============================================================
# 测试入口
# ============================================================
if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.execution.adb_utils import ADBUtils

    print("=" * 60)
    print("  硬件级触控注入测试")
    print("=" * 60)

    adb = ADBUtils(
        host="127.0.0.1",
        port=7555,
        command_timeout=10,
        retry_count=3,
    )

    if not adb.connect():
        print("❌ ADB 连接失败，请确认 MuMu 模拟器已启动")
        sys.exit(1)

    print("✅ ADB 连接成功")

    injector = HardwareTouchInjector(adb, screen_size=(1080, 1920))

    if not injector.connect():
        print("❌ 触控设备探测失败")
        sys.exit(1)

    print("\n测试菜单:")
    print("  1. 点击屏幕中央 (540, 960)")
    print("  2. 滑动 (540,1500) → (540,400) 向上滑")
    print("  3. 长按屏幕中央 1秒")
    print("  4. 双指缩放 (放大)")
    print("  5. 退出")

    while True:
        choice = input("\n选择测试 [1-5]: ").strip()
        if choice == "1":
            print("执行: 点击 (540, 960)")
            injector.tap(540, 960)
        elif choice == "2":
            print("执行: 滑动 (540,1500) → (540,400)")
            injector.swipe(540, 1500, 540, 400, duration_ms=500, steps=10)
        elif choice == "3":
            print("执行: 长按 (540, 960) 1秒")
            injector.long_press(540, 960, duration_ms=1000)
        elif choice == "4":
            print("执行: 双指放大")
            injector.pinch_zoom(540, 960, start_distance=200, end_distance=600, duration_ms=500)
        elif choice == "5":
            print("退出")
            break
        else:
            print("无效选择")

    adb.disconnect()
