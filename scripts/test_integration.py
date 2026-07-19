#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Firefight AI 集成测试脚本

测试流程:
  第1步: 连接 MuMu 模拟器 (ADB)
  第2步: 连接 MuMu IPC 触控 (external_renderer_ipc.dll)
  第3步: ADB 截图 + YOLO 检测 + 颜色敌我识别
  第4步: MuMu IPC 触控测试 (tap 暂停按钮)
  第5步: 完整命令链路测试 (框选 → 移动指令 → 点击目标)

用法:
  python scripts/test_integration.py
  python scripts/test_integration.py --no-touch   # 跳过触控, 仅测试检测
  python scripts/test_integration.py --debug      # 保存调试图片
"""

import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.execution.adb_utils import ADBUtils
from src.execution.mumu_ipc import MuMuTouchController
from src.vision.detector import UnitDetector


# ============================================================
# 配置
# ============================================================

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
OUTPUT_DIR = Path(__file__).parent.parent / "test_output"
SCREENSHOT_PATH = OUTPUT_DIR / "test_screenshot.png"
DETECT_PATH = OUTPUT_DIR / "test_detect.png"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def draw_detections(frame: np.ndarray, units: list) -> np.ndarray:
    """在帧上绘制检测结果"""
    vis = frame.copy()
    for u in units:
        x1, y1, x2, y2 = u.bbox
        color = (0, 255, 0) if u.team.value == "ally" else (0, 0, 255)
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)
        label = f"{u.unit_type.value} #{u.track_id}"
        cv2.putText(vis, label, (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    return vis


def find_blue_units_by_color(frame: np.ndarray,
                             min_area: int = 50,
                             max_area: int = 5000) -> list[tuple[int, int, int, int]]:
    """纯颜色检测定位蓝色友军单位标记

    不依赖YOLO, 直接在画面中搜索 #58A5F3 蓝色像素,
    用连通组件分析得到每个蓝色标记的位置和大小。

    OpenCV HSV 范围: H 0-180, S 0-255, V 0-255
    #58A5F3 (BGR=243,165,88) -> H≈105, S≈165, V≈243

    Args:
        frame: BGR 图像
        min_area: 最小标记面积(像素), 过滤小噪点
        max_area: 最大标记面积, 过滤大块天空

    Returns:
        列表: [(cx, cy, w, h), ...] 蓝色单位标记的位置和大小
    """
    # 友军蓝色 HSV 范围 (来自 mod.txt <friend>#58A5F3</friend>)
    ally_low = np.array([95, 100, 100], dtype=np.uint8)
    ally_high = np.array([115, 255, 255], dtype=np.uint8)

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, ally_low, ally_high)

    # 形态学去噪
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    # 找连通组件
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )

    units = []
    for i in range(1, num_labels):  # 跳过背景 (label=0)
        x, y, w, h, area = stats[i]
        if min_area <= area <= max_area:
            cx, cy = centroids[i]
            units.append((int(cx), int(cy), int(w), int(h)))

    # 按 y 坐标排序（视觉上从上到下）
    units.sort(key=lambda u: u[1])
    return units


def draw_color_units(frame: np.ndarray,
                     units: list[tuple[int, int, int, int]]) -> np.ndarray:
    """绘制颜色检测到的蓝色单位"""
    vis = frame.copy()
    for cx, cy, w, h in units:
        x1, y1 = cx - w // 2, cy - h // 2
        x2, y2 = cx + w // 2, cy + h // 2
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 255), 2)
        cv2.putText(vis, f"({cx},{cy})", (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
    return vis


# ============================================================
# 测试步骤
# ============================================================

def step1_adb_connect(cfg: dict) -> ADBUtils:
    """第1步: 连接 ADB"""
    print("\n" + "=" * 60)
    print("  [1/5] 连接 MuMu 模拟器 (ADB)")
    print("=" * 60)

    device_cfg = cfg["device"]
    active = device_cfg.get("active", "mumu")
    info = device_cfg.get(active, {})

    adb = ADBUtils(
        host=info.get("adb_host", "127.0.0.1"),
        port=info.get("adb_port", 7555),
    )

    if not adb.connect():
        print("  [FAIL] ADB 连接失败! 请确认 MuMu 模拟器已启动")
        return None

    print(f"  [OK] ADB 已连接: {adb.device_addr}")

    # 检查当前前台应用
    activity = adb.get_current_activity()
    print(f"  [INFO] 当前 Activity: {activity}")

    pkg = cfg["game"]["package_name"]
    if pkg not in (activity or ""):
        print(f"  [WARN] 游戏未在前台! 当前包名不匹配 {pkg}")
    else:
        print(f"  [OK] 游戏正在运行: {pkg}")

    return adb


def step2_mumu_ipc(cfg: dict):
    """第2步: 连接 MuMu IPC"""
    print("\n" + "=" * 60)
    print("  [2/5] 连接 MuMu IPC 触控注入")
    print("=" * 60)

    mumu_cfg = cfg.get("mumu_ipc", {})
    game_cfg = cfg["game"]

    touch = MuMuTouchController(
        package_name=game_cfg["package_name"],
        instance_index=mumu_cfg.get("instance_index", 0),
        app_index=mumu_cfg.get("app_index", 0),
        dll_path=mumu_cfg.get("dll_path"),
    )

    if not touch.connect():
        print("  [FAIL] MuMu IPC 连接失败! 将使用 ADB input 回退")
        return None

    print(f"  [OK] MuMu IPC 已连接")
    print(f"       handle={touch._handle}, display_id={touch._display_id}")
    return touch


def step3_detect(adb: ADBUtils, cfg: dict, debug: bool = False):
    """第3步: 截图 + YOLO 检测 + 颜色敌我识别"""
    print("\n" + "=" * 60)
    print("  [3/5] 截图 + YOLO检测 + 颜色敌我识别")
    print("=" * 60)

    # 截图
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not adb.screenshot(str(SCREENSHOT_PATH)):
        print("  [FAIL] ADB 截图失败!")
        return None, None

    print(f"  [OK] 截图已保存: {SCREENSHOT_PATH}")

    frame = cv2.imread(str(SCREENSHOT_PATH))
    if frame is None:
        print("  [FAIL] 读取截图失败!")
        return None, None

    h, w = frame.shape[:2]
    print(f"  [INFO] 截图分辨率: {w}x{h}")

    # 加载 YOLO 模型
    yolo_cfg = cfg["yolo"]
    team_cfg = cfg["team_detection"]

    detector = UnitDetector(
        model_path=yolo_cfg["model_path"],
        fallback_model_path=yolo_cfg["fallback_model_path"],
        confidence_threshold=yolo_cfg["confidence_threshold"],
        iou_threshold=yolo_cfg["iou_threshold"],
        image_size=yolo_cfg["image_size"],
        device=yolo_cfg["device"],
        color_roi_ratio=team_cfg.get("color_roi_ratio", 0.3),
        color_match_threshold=team_cfg.get("color_match_threshold", 0.15),
    )

    if not detector.load_model():
        print("  [FAIL] YOLO 模型加载失败!")
        return None, None

    print(f"  [OK] YOLO 模型已加载 (类型: {detector.model_type})")

    # 检测
    units = detector.predict(frame)

    if not units:
        print("  [WARN] 未检测到任何单位! (可能模型未训练或场景无单位)")
        # 尝试纯颜色检测作为备份
        print("  [INFO] 启用纯颜色检测备份方案...")
        color_units = find_blue_units_by_color(frame)
        if color_units:
            print(f"  [OK] 纯颜色检测找到 {len(color_units)} 个蓝色标记:")
            for i, (cx, cy, w, h) in enumerate(color_units[:15]):
                print(f"       标记#{i+1}: ({cx},{cy}) 尺寸={w}x{h}")
            vis = draw_color_units(frame, color_units)
            cv2.imwrite(str(DETECT_PATH), vis)
            print(f"  [OK] 颜色检测可视化已保存: {DETECT_PATH}")
        else:
            print("  [WARN] 颜色检测也未找到蓝色标记")
    else:
        allies = [u for u in units if u.team.value == "ally"]
        enemies = [u for u in units if u.team.value == "enemy"]
        unknowns = [u for u in units if u.team.value == "unknown"]

        print(f"  [OK] 检测到 {len(units)} 个单位:")
        print(f"       友军(蓝): {len(allies)}")
        print(f"       敌军(红): {len(enemies)}")
        print(f"       未识别:   {len(unknowns)}")

        # 列出前10个单位详情
        for u in units[:10]:
            team_label = {"ally": "蓝", "enemy": "红", "unknown": "?"}[u.team.value]
            print(f"       #{u.track_id} {u.unit_type.value} "
                  f"({team_label}) @({u.x},{u.y}) conf={u.confidence:.2f}")

    # 绘制并保存检测图
    vis = draw_detections(frame, units)
    cv2.imwrite(str(DETECT_PATH), vis)
    print(f"  [OK] 检测可视化已保存: {DETECT_PATH}")

    # 颜色检测统计
    if debug and units:
        ally_color = team_cfg.get("ally_color", {})
        enemy_color = team_cfg.get("enemy_color", {})
        print(f"\n  [DEBUG] 颜色配置:")
        print(f"       友军: {ally_color.get('hex', '#58A5F3')} "
              f"HSV({ally_color.get('hsv_low')}~{ally_color.get('hsv_high')})")
        print(f"       敌军: {enemy_color.get('hex', '#FD8177')} "
              f"HSV({enemy_color.get('hsv_low')}~{enemy_color.get('hsv_high')})")

    return units, frame


def step4_tap_test(touch, adb: ADBUtils, skip: bool = False):
    """第4步: 触控测试"""
    print("\n" + "=" * 60)
    print("  [4/5] MuMu IPC 触控测试")
    print("=" * 60)

    if skip:
        print("  [SKIP] 跳过触控测试")
        return True

    if touch is None:
        print("  [WARN] MuMu IPC 未连接, 使用 ADB input 测试")
        # ADB tap 暂停按钮 (1920x1080 的右上角)
        test_x, test_y = 1824, 54
        ok = adb.tap(test_x, test_y)
        print(f"  ADB tap ({test_x},{test_y}): {'[OK]' if ok else '[FAIL]'}")
        return ok

    # MuMu IPC tap 测试
    print("  [TEST] 测试1: tap 暂停按钮 (1824, 54)")

    # 先确认游戏画面正常
    ok = touch.tap(1824, 54, delay_ms=50)
    time.sleep(0.3)
    if ok:
        print("  [OK] tap1 成功")
    else:
        print("  [FAIL] tap1 失败")
        return False

    # 再点一下恢复
    time.sleep(1)
    ok = touch.tap(1824, 54, delay_ms=50)
    print(f"  [TEST] 恢复: {'[OK]' if ok else '[FAIL]'}")

    # 测试中间点击
    print("  [TEST] 测试2: 点击屏幕中央 (960, 540)")
    ok = touch.tap(960, 540, delay_ms=100)
    print(f"  [{'OK' if ok else 'FAIL'}] tap2 完成")

    return True


def step5_command_test(touch, adb: ADBUtils, skip: bool = False):
    """第5步: 完整命令链路测试"""
    print("\n" + "=" * 60)
    print("  [5/5] 完整命令链路: 框选 → MOVE → 目标")
    print("=" * 60)

    if skip:
        print("  [SKIP] 跳过命令链路测试")
        return

    def tap(x, y, label=""):
        """内部 tap 封装"""
        if touch and touch.is_connected:
            ok = touch.tap(x, y, delay_ms=50)
        else:
            ok = adb.tap(x, y)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] tap {label} ({x},{y})")
        return ok

    def swipe(x1, y1, x2, y2, dur, label=""):
        if touch and touch.is_connected:
            ok = touch.swipe(x1, y1, x2, y2, duration_ms=dur)
        else:
            ok = adb.swipe(x1, y1, x2, y2, duration_ms=dur)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {label}")
        return ok

    # 注意: 以下坐标基于 1080x1920 竖屏
    # 实际按钮位置需要根据游戏 UI 微调

    # --- 步骤A: 框选单位 ---
    print("\n  --- A. 框选前线单位 ---")
    # 横屏 1920x1080: 从战场左侧中下到右上来框选
    # 蓝单位在中下区域(大约 y=400-600), 我们框选他们
    swipe(192, 700, 1152, 400, dur=200, label="框选 (192,700)->(1152,400)")
    time.sleep(0.3)

    # --- 步骤B: 点击 MOVE 按钮 ---
    print("\n  --- B. 点击 MOVE 按钮 ---")
    # 按钮在控制面板区域 (基于横屏比例)
    btn_move = (96, 961)  # 5%x, 89%y
    tap(*btn_move, label="MOVE 按钮")
    time.sleep(0.2)

    # --- 步骤C: 点击目标位置 ---
    print("\n  --- C. 点击目标位置 ---")
    # 目标: 屏幕中央偏上(前进方向)
    target = (960, 400)
    tap(*target, label=f"目标 ({target[0]},{target[1]})")
    time.sleep(0.3)

    print("\n  [OK] 命令链路测试完成!")
    print("  [INFO] 请观察游戏画面确认单位是否移动")


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Firefight AI 集成测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--no-touch", action="store_true",
                        help="跳过所有触控操作,仅测试检测")
    parser.add_argument("--debug", action="store_true",
                        help="输出调试信息并保存中间图片")
    args = parser.parse_args()

    print("=" * 60)
    print("  Firefight AI 集成测试")
    print(f"  配置: {CONFIG_PATH}")
    print(f"  MuMu IPC: {args.no_touch and '禁用' or '启用'}")
    print("=" * 60)

    # 加载配置
    cfg = load_config()
    print(f"  [INFO] 游戏: {cfg['game']['package_name']}")
    print(f"  [INFO] 分辨率: {cfg['game']['screen_width']}x{cfg['game']['screen_height']}")
    print(f"  [INFO] 敌我识别: {cfg['team_detection']['method']}")

    ok = True

    # 第1步: ADB
    adb = step1_adb_connect(cfg)
    if adb is None:
        print("\n[ABORT] ADB 连接失败, 无法继续")
        return 1

    # 第2步: MuMu IPC
    touch = None
    if not args.no_touch:
        touch = step2_mumu_ipc(cfg)

    # 第3步: 检测
    units, frame = step3_detect(adb, cfg, debug=args.debug)

    # 第4步: 触控
    if not step4_tap_test(touch, adb, skip=args.no_touch):
        print("\n[ABORT] 触控测试失败")
        ok = False

    # 第5步: 命令链路
    step5_command_test(touch, adb, skip=args.no_touch)

    # 清理
    if touch:
        touch.disconnect()
    if adb:
        adb.disconnect()

    print("\n" + "=" * 60)
    print(f"  测试{'成功' if ok else '部分失败'}")
    print(f"  输出目录: {OUTPUT_DIR}")
    if units:
        print(f"  检测到 {len(units)} 个单位")
        allies = [u for u in units if u.team.value == "ally"]
        enemies = [u for u in units if u.team.value == "enemy"]
        print(f"    友军: {len(allies)}, 敌军: {len(enemies)}")
    print("=" * 60)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
