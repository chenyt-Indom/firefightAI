"""LDPlayer 触控执行器 - 基于触控板录制的双手势方案

Firefight 控制手势 (用户触控板录制确认):
  手势1: tap单位 -> BTN_TOUCH DOWN+UP (180ms, TRACKING_ID=0x92)
  等待: 50ms
  手势2: 按住蓝条+拖拽 -> 新TRACKING_ID=0x93, DOWN+连续MOVE+UP

关键: 两次手势必须用不同TRACKING_ID,第一次必须正确关闭(TRACKING_ID=-1)
"""

import subprocess
import time


class LDPlayerTouch:
    """LDPlayer 硬件级触控注入器

    使用 sendevent 发送精确的多指协议,模拟真实触控板事件。
    Firefight 游戏可识别这种格式进行单位选择和移动。
    """

    DEVICE = "/dev/input/event4"
    ADB = r"D:\MuMuPlayer\nx_main\adb.exe"

    def __init__(self, device_serial="emulator-5554"):
        self.dev = device_serial
        self._tid_counter = 0x80  # 触控板通常从 0x80+ 开始

    def _next_tid(self):
        """分配唯一的手指追踪ID"""
        self._tid_counter += 1
        if self._tid_counter > 0xFF:
            self._tid_counter = 0x90
        return self._tid_counter

    def _se(self, cmd: str, timeout: int = 3):
        """发送单条 sendevent 命令"""
        full = f'shell {cmd}'
        subprocess.run(
            [self.ADB, "-s", self.dev, full],
            capture_output=True, timeout=timeout,
        )

    def _send(self, type_code: str, code: str, value: str):
        """发送单个 sendevent 事件"""
        self._se(
            f"sendevent {self.DEVICE} {type_code} {code} {value}"
        )

    def _syn(self):
        """SYN_REPORT - 同步报告"""
        self._send("0", "0", "0")

    def _down(self, tid: int, x: int, y: int):
        """手指按下"""
        self._send("3", "0x39", hex(tid))      # TRACKING_ID
        self._send("1", "0x14a", "0x1")         # BTN_TOUCH DOWN
        self._send("3", "0x35", hex(x))        # X
        self._send("3", "0x36", hex(y))        # Y
        self._syn()

    def _up(self, tid: int):
        """手指抬起"""
        self._send("3", "0x39", "0xffffffff")  # TRACKING_ID = -1 (释放)
        self._send("1", "0x14a", "0x0")         # BTN_TOUCH UP
        self._syn()

    def _move(self, tid: int, x: int, y: int):
        """手指移动"""
        # Type B 协议: 只需要更新 X, Y (不需重新发 TRACKING_ID)
        self._send("3", "0x35", hex(x))
        self._send("3", "0x36", hex(y))
        self._syn()

    def tap(self, x: int, y: int, duration_ms: int = 180):
        """单击 - 用于选中单位

        Args:
            x, y: 目标坐标 (游戏 1920x1080)
            duration_ms: 按住时长,默认180ms(用户触控板实测)
        """
        tid = self._next_tid()
        self._down(tid, x, y)
        time.sleep(duration_ms / 1000.0)
        self._up(tid)

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int,
             steps: int = 28, step_ms: int = 7):
        """按住+拖拽 - 用于拖动 Goto 标记

        Args:
            start_x, start_y: 起点 (蓝条当前位置)
            end_x, end_y: 终点 (目标位置)
            steps: 拖拽步数 (用户实测约28步)
            step_ms: 每步间隔ms (用户实测7ms)
        """
        tid = self._next_tid()
        self._down(tid, start_x, start_y)
        time.sleep(0.3)  # 按住300ms等游戏注册
        for s in range(1, steps + 1):
            t = s / steps
            x = int(start_x + (end_x - start_x) * t)
            y = int(start_y + (end_y - start_y) * t)
            self._move(tid, x, y)
            time.sleep(step_ms / 1000.0)
        self._up(tid)

    def move_unit(self, unit_x: int, unit_y: int, target_x: int, target_y: int):
        """移动单位 - 完整两步手势

        步骤:
          1. tap 单位位置 (180ms) - 选中, 蓝条出现
          2. wait 50ms
          3. drag 从蓝条位置 到 目标位置 - 设置新 Goto

        Args:
            unit_x, unit_y: 单位中心坐标
            target_x, target_y: 目标坐标
        """
        # 手势1: tap 选中
        self.tap(unit_x, unit_y, duration_ms=180)
        time.sleep(0.05)

        # 手势2: drag (从单位附近开始,到目标位置)
        # 起点从单位中心稍偏开始 (模拟点击蓝条圆圈)
        start_x = unit_x + 1
        start_y = unit_y + 1
        self.drag(start_x, start_y, target_x, target_y, steps=28, step_ms=7)


# 快速测试
if __name__ == "__main__":
    touch = LDPlayerTouch()
    print(f"LDPlayer 触控执行器已就绪")
    print(f"  设备: {touch.dev}")
    print(f"  输入设备: {touch.DEVICE}")
