"""
触控注入对比测试 - 验证 sendevent 方案是否能绕过游戏触控检测

测试流程:
  1. 用 ADB input tap 点击游戏按钮 (旧方案, 已知被拦截)
  2. 用 sendevent 硬件级注入点击同一按钮 (新方案)
  3. 对比截图, 看哪个方案能触发游戏响应

使用方法:
  python scripts/test_touch_inject.py
  (需要 MuMu 模拟器已启动并运行 Firefight 游戏)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.execution.adb_utils import ADBUtils
from scripts.hardware_touch import HardwareTouchInjector
from loguru import logger

# 配置 logging
logger.remove()
logger.add(sys.stderr, level="INFO", format="<level>{level}</level> | {message}")


def screenshot(adb: ADBUtils, name: str) -> str:
    """截图并保存"""
    path = f"test_screenshots/{name}_{int(time.time())}.png"
    Path("test_screenshots").mkdir(exist_ok=True)
    adb._run_s("screencap -p /sdcard/test_cap.png", timeout=5)
    adb._run_adb(["pull", "/sdcard/test_cap.png", path], timeout=5)
    adb._run_s("rm /sdcard/test_cap.png", timeout=3)
    logger.info(f"截图保存: {path}")
    return path


def test_adb_input_tap(adb: ADBUtils, x: int, y: int, label: str):
    """测试 ADB input tap (旧方案)"""
    logger.info(f"[旧方案] ADB input tap ({x},{y}) - {label}")
    before = screenshot(adb, f"adb_before_{label}")
    adb.tap(x, y)
    time.sleep(1.0)
    after = screenshot(adb, f"adb_after_{label}")
    return before, after


def test_sendevent_tap(adb: ADBUtils, injector: HardwareTouchInjector, x: int, y: int, label: str):
    """测试 sendevent tap (新方案)"""
    logger.info(f"[新方案] sendevent tap ({x},{y}) - {label}")
    before = screenshot(adb, f"send_before_{label}")
    injector.tap(x, y, hold_ms=80)
    time.sleep(1.0)
    after = screenshot(adb, f"send_after_{label}")
    return before, after


def test_sendevent_swipe(adb: ADBUtils, injector: HardwareTouchInjector,
                         x1: int, y1: int, x2: int, y2: int, label: str):
    """测试 sendevent swipe (新方案)"""
    logger.info(f"[新方案] sendevent swipe ({x1},{y1})→({x2},{y2}) - {label}")
    before = screenshot(adb, f"swipe_before_{label}")
    injector.swipe(x1, y1, x2, y2, duration_ms=800, steps=20)
    time.sleep(1.0)
    after = screenshot(adb, f"swipe_after_{label}")
    return before, after


def main():
    print("=" * 60)
    print("  触控注入对比测试")
    print("  ADB input (旧) vs sendevent (新)")
    print("=" * 60)

    # 连接 MuMu
    adb = ADBUtils(
        host="127.0.0.1",
        port=7555,
        command_timeout=10,
        retry_count=3,
    )

    if not adb.connect():
        print("❌ ADB 连接失败！请确认 MuMu 模拟器已启动")
        print("   MuMu 12 默认端口: 7555 或 16384")
        sys.exit(1)

    print("✅ ADB 连接成功")

    # 获取 root 权限 (sendevent 需要 root)
    print("获取 root 权限...")
    adb._run_adb(["root"], timeout=10)
    time.sleep(2)

    # 初始化硬件触控注入器
    injector = HardwareTouchInjector(adb, screen_size=(1080, 1920))
    if not injector.connect():
        print("❌ 触控设备探测失败")
        print("  尝试手动运行: adb shell getevent -p")
        sys.exit(1)

    print(f"\n触控设备: {injector.touch_device}")
    print(f"X范围: {injector.x_min}~{injector.x_max}")
    print(f"Y范围: {injector.y_min}~{injector.y_max}")
    print()

    while True:
        print("\n" + "=" * 60)
        print("测试选项:")
        print("  1. 对比测试 - 点击屏幕中央")
        print("  2. 对比测试 - 点击 UI 按钮 (需输入坐标)")
        print("  3. sendevent 滑动测试 (单位移动)")
        print("  4. sendevent 长按测试")
        print("  5. sendevent 双指缩放测试")
        print("  6. 列出所有 input 设备 (调试)")
        print("  7. 录制真实触控事件 (参考)")
        print("  0. 退出")
        print("=" * 60)

        choice = input("选择 [0-7]: ").strip()

        if choice == "0":
            break

        elif choice == "1":
            # 屏幕中央
            cx, cy = 540, 960
            print("\n--- 测试 1: ADB input tap ---")
            test_adb_input_tap(adb, cx, cy, "center")
            print("\n--- 测试 2: sendevent tap ---")
            test_sendevent_tap(adb, injector, cx, cy, "center")
            print("\n对比两张 after 截图，看哪个触发了游戏响应")

        elif choice == "2":
            coord = input("输入坐标 (x y, 如 110 680): ").strip()
            try:
                x, y = map(int, coord.split())
            except:
                print("格式错误")
                continue
            print("\n--- 测试 1: ADB input tap ---")
            test_adb_input_tap(adb, x, y, "button")
            print("\n--- 测试 2: sendevent tap ---")
            test_sendevent_tap(adb, injector, x, y, "button")

        elif choice == "3":
            # 模拟单位移动: 从单位位置滑动到目标位置
            print("单位移动测试 - 从起点滑到终点")
            start = input("起点坐标 (x y, 如 540 1200): ").strip()
            end = input("终点坐标 (x y, 如 540 400): ").strip()
            try:
                x1, y1 = map(int, start.split())
                x2, y2 = map(int, end.split())
            except:
                print("格式错误")
                continue
            print(f"\n--- ADB swipe (旧) ---")
            logger.info(f"[旧方案] ADB swipe ({x1},{y1})→({x2},{y2})")
            before1 = screenshot(adb, "adb_swipe_before")
            adb.swipe(x1, y1, x2, y2, duration_ms=800)
            time.sleep(1.0)
            after1 = screenshot(adb, "adb_swipe_after")

            print(f"\n--- sendevent swipe (新) ---")
            test_sendevent_swipe(adb, injector, x1, y1, x2, y2, "unit_move")

        elif choice == "4":
            coord = input("长按坐标 (x y): ").strip()
            try:
                x, y = map(int, coord.split())
            except:
                print("格式错误")
                continue
            print(f"\n--- sendevent long press ({x},{y}) 2秒 ---")
            before = screenshot(adb, "longpress_before")
            injector.long_press(x, y, duration_ms=2000)
            time.sleep(0.5)
            after = screenshot(adb, "longpress_after")

        elif choice == "5":
            print("双指缩放测试 (放大)")
            cx, cy = 540, 960
            before = screenshot(adb, "zoom_before")
            injector.pinch_zoom(cx, cy, start_distance=200, end_distance=600, duration_ms=600)
            time.sleep(0.5)
            after = screenshot(adb, "zoom_after")

        elif choice == "6":
            print("\n所有 input 设备:")
            result = adb._run_s("getevent -p", timeout=10)
            print(result[:3000])

        elif choice == "7":
            print("\n录制真实触控事件 (5秒) - 请在模拟器上用鼠标点击/滑动")
            print("开始录制...")
            import subprocess
            cmd = [adb.adb_path, "-s", adb.device_addr, "shell", "getevent", "-lt"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(5)
            proc.terminate()
            out, _ = proc.communicate(timeout=3)
            print("\n录制的触控事件 (前2000字符):")
            print(out[:2000])
            # 保存完整日志
            Path("test_screenshots").mkdir(exist_ok=True)
            with open("test_screenshots/real_touch_events.txt", "w") as f:
                f.write(out)
            print(f"\n完整日志: test_screenshots/real_touch_events.txt")

    print("\n测试结束")
    adb.disconnect()


if __name__ == "__main__":
    main()
