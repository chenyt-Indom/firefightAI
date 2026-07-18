"""端到端测试: 截图 → 颜色检测 → MuMuManager触控 → 验证

测试流程:
  1. 截图当前游戏画面
  2. 颜色检测找到所有蓝色(友军)单位
  3. 选中一个单位 (tap)
  4. 滑动移动单位 (swipe to new position)
  5. 再次截图验证变化
"""

import sys
import os
import time
import subprocess
import argparse
from pathlib import Path

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np

from src.execution.mumu_manager import MuMuManagerTouch

# 路径配置
ADB_PATH = r"D:\MuMuPlayer\nx_device\12.0\shell\adb.exe"
DEVICE = "127.0.0.1:7555"
OUTPUT_DIR = r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\test_output"

# HSV 颜色范围 (#58A5F3 blue = OpenCV H~105)
ALLY_HSV_LOW = np.array([95, 100, 100], dtype=np.uint8)
ALLY_HSV_HIGH = np.array([115, 255, 255], dtype=np.uint8)
ENEMY_HSV_LOW_1 = np.array([0, 100, 100], dtype=np.uint8)
ENEMY_HSV_HIGH_1 = np.array([10, 255, 255], dtype=np.uint8)
ENEMY_HSV_LOW_2 = np.array([170, 100, 100], dtype=np.uint8)
ENEMY_HSV_HIGH_2 = np.array([180, 255, 255], dtype=np.uint8)


def screenshot(name: str) -> str:
    """通过 ADB 截图并保存"""
    path = os.path.join(OUTPUT_DIR, f"e2e_{name}.png")
    with open(path, "wb") as f:
        subprocess.run(
            [ADB_PATH, "-s", DEVICE, "exec-out", "screencap", "-p"],
            stdout=f,
            timeout=10,
        )
    return path


def find_units_by_color(img: np.ndarray, label: str) -> list[dict]:
    """通过颜色检测找到所有单位的中心坐标

    Returns:
        [{x, y, area}, ...] 按面积降序
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    if label == "ally":
        mask = cv2.inRange(hsv, ALLY_HSV_LOW, ALLY_HSV_HIGH)
    else:  # enemy
        mask1 = cv2.inRange(hsv, ENEMY_HSV_LOW_1, ENEMY_HSV_HIGH_1)
        mask2 = cv2.inRange(hsv, ENEMY_HSV_LOW_2, ENEMY_HSV_HIGH_2)
        mask = cv2.bitwise_or(mask1, mask2)

    # 连通组件分析
    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)

    units = []
    for i in range(1, n_labels):
        area = stats[i, 4]  # cv2.CC_STAT_AREA
        if area < 30:  # 过滤噪点
            continue
        cx, cy = int(centroids[i, 0]), int(centroids[i, 1])
        units.append({"x": cx, "y": cy, "area": area})

    units.sort(key=lambda u: u["area"], reverse=True)
    return units


def draw_units(img: np.ndarray, units: list[dict], color: tuple, label: str) -> np.ndarray:
    """在图上标注单位位置"""
    result = img.copy()
    for i, u in enumerate(units):
        x, y = u["x"], u["y"]
        cv2.circle(result, (x, y), 8, color, 2)
        cv2.putText(result, f"{label}{i+1}", (x + 10, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
    return result


def compare_images(before: str, after: str) -> dict:
    """比较两张截图的变化"""
    img_b = cv2.imread(before)
    img_a = cv2.imread(after)
    if img_b is None or img_a is None:
        return {"error": "无法读取图片"}

    gray = cv2.cvtColor(cv2.absdiff(img_b, img_a), cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 20, 255, cv2.THRESH_BINARY)

    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(thresh, 8)

    big_blobs = []
    for i in range(1, n_labels):
        # cv2.CC_STAT_* 常量兼容 (新版 vs 旧版 OpenCV)
        area = stats[i, 4]  # cv2.CC_STAT_AREA
        if area > 30:
            x, y = stats[i, 0], stats[i, 1]
            w, h = stats[i, 2], stats[i, 3]
            big_blobs.append({"area": area, "x": x, "y": y, "w": w, "h": h,
                              "cx": x + w // 2, "cy": y + h // 2})

    big_blobs.sort(key=lambda b: b["area"], reverse=True)

    total_changed = int(np.count_nonzero(thresh))
    return {
        "changed_pixels": total_changed,
        "blobs": big_blobs[:5],
        "total_blobs": len(big_blobs),
    }


def main():
    parser = argparse.ArgumentParser(description="Firefight AI 端到端测试")
    parser.add_argument("--dry-run", action="store_true", help="只检测不操作")
    parser.add_argument("--target", type=int, default=0, help="选第几个单位 (0=最大)")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    touch = MuMuManagerTouch()

    if not touch.is_connected:
        print("ERROR: MuMuManager.exe 不存在!")
        return

    # -------------------------------------------
    # Step 1: 截图 + 颜色检测
    # -------------------------------------------
    print("\n" + "=" * 60)
    print("Step 1: 截图并检测友军单位...")
    print("=" * 60)

    before_path = screenshot("before_select")
    img = cv2.imread(before_path)
    if img is None:
        print("ERROR: 截图失败!")
        return

    h, w = img.shape[:2]
    print(f"截图分辨率: {w}x{h}")

    allies = find_units_by_color(img, "ally")
    enemies = find_units_by_color(img, "enemy")

    print(f"检测到 {len(allies)} 个友军单位, {len(enemies)} 个敌军单位")

    if not allies:
        print("没找到友军单位, 无法测试!")
        return

    # 显示前5个友军
    for i, u in enumerate(allies[:5]):
        print(f"  友军{i+1}: ({u['x']}, {u['y']}) area={u['area']}")

    # 保存标注图
    annotated = draw_units(img, allies, (0, 255, 0), "A")
    annotated = draw_units(annotated, enemies, (0, 0, 255), "E")
    detect_path = os.path.join(OUTPUT_DIR, "e2e_detected.png")
    cv2.imwrite(detect_path, annotated)
    print(f"标注图: {detect_path}")

    if args.dry_run:
        print("\n(dry-run 模式, 跳过实际操作)")
        return

    # -------------------------------------------
    # Step 2: 选中单位 (tap 单位中心)
    # -------------------------------------------
    target_unit = allies[min(args.target, len(allies) - 1)]
    tx, ty = target_unit["x"], target_unit["y"]
    print(f"\n{'=' * 60}")
    print(f"Step 2: 选中友军单位 ({tx}, {ty})...")
    print(f"{'=' * 60}")

    success = touch.tap(tx, ty)
    print(f"tap ({tx}, {ty}) -> {'OK' if success else 'FAIL'}")
    time.sleep(0.5)

    # -------------------------------------------
    # Step 3: 移动单位 (swipe to new position)
    # -------------------------------------------
    # 选一个不同的目标位置 (偏移 200 像素)
    dest_x = min(tx + 200, w - 50)
    dest_y = ty
    print(f"\n{'=' * 60}")
    print(f"Step 3: 滑动到 ({dest_x}, {dest_y})...")
    print(f"{'=' * 60}")

    # Swipe = touch down at unit + move to dest
    # 使用 long_press 选中 (500ms) 然后 swipe 可能不对
    # 对于 RTS 游戏，通常: tap 选单位, tap 命令按钮, swipe/tap 目标
    # 简化测试: 直接拖拽单位
    success = touch.swipe(tx, ty, dest_x, dest_y, duration_ms=500)
    print(f"swipe ({tx},{ty}) -> ({dest_x},{dest_y}) -> {'OK' if success else 'FAIL'}")
    time.sleep(1.0)

    # -------------------------------------------
    # Step 4: 截图验证
    # -------------------------------------------
    print(f"\n{'=' * 60}")
    print("Step 4: 截图验证...")
    print(f"{'=' * 60}")

    after_path = screenshot("after_move")
    result = compare_images(before_path, after_path)

    print(f"变化像素: {result['changed_pixels']}")
    print(f"变化区块数: {result['total_blobs']}")

    for i, b in enumerate(result["blobs"]):
        print(f"  区块{i+1}: area={b['area']}, pos=({b['cx']},{b['cy']}), size={b['w']}x{b['h']}")

    if result["changed_pixels"] > 500:
        print("\n✅ 检测到显著画面变化 - 触控注入生效!")
    else:
        print("\n⚠️ 画面变化较小 - 请肉眼确认游戏是否响应")

    print(f"\n截图文件:")
    print(f"  操作前: {before_path}")
    print(f"  操作后: {after_path}")
    print(f"  检测图: {detect_path}")


if __name__ == "__main__":
    main()
