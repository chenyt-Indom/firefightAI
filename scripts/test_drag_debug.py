"""拖拽调试脚本 — 纯MuMuManager方案 + YOLO蓝条验证

流程：
  1. 截图 → YOLO 检测 goto bar 位置
  2. input tap 选中单位 → 截图 + YOLO 验证
  3. 双击中圈(input tap×2) + swipe 拖到目标
  4. 像素对比: 相机平移 vs 单位移动

用法: python scripts/test_drag_debug.py [--unit-x X] [--unit-y Y] [--target-x X] [--target-y Y]
"""

from __future__ import annotations

import argparse, subprocess, time
from pathlib import Path
import cv2, numpy as np
from ultralytics import YOLO

# ── 配置 ──
PROJECT_ROOT = Path(__file__).parent.parent
MODEL_PATH = Path(r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\runs\detect\goto_bar17\weights\best.pt")
ADB = r"D:\MuMuPlayer\nx_device\12.0\shell\adb.exe"
DEVICE = "127.0.0.1:7555"
MUMU_MGR = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"


# ================================================================
# 工具
# ================================================================

def screenshot(filename: str) -> np.ndarray | None:
    """ADB截图"""
    t0 = time.time()
    r = subprocess.run(
        [ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
        capture_output=True, timeout=8,
    )
    if r.returncode != 0:
        print(f"  ❌ 截图失败 rc={r.returncode}")
        return None
    img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
    cv2.imwrite(str(PROJECT_ROOT / "screenshots" / filename), img)
    print(f"  📸 {filename} ({img.shape[1]}x{img.shape[0]}, {time.time()-t0:.2f}s)")
    return img


def pixel_diff(before: np.ndarray, after: np.ndarray) -> dict:
    """像素差异 + 相机平移判定"""
    if before is None or after is None:
        return {"pct": 0, "camera_shift": False}
    diff = cv2.absdiff(before, after)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    changed = np.sum(gray > 25)
    pct = round(changed / gray.size * 100, 2)
    # 行均值互相关 → 检测垂直平移
    rows = np.mean(gray, axis=1)
    corr = np.correlate(rows, rows, mode='full')
    mid = len(corr) // 2
    shift = np.argmax(corr[mid-50:mid+50]) - 50
    return {
        "pct": pct, "changed_px": int(changed),
        "camera_shift": abs(shift) > 3,
        "shift_est": int(shift) if abs(shift) > 3 else 0,
    }


def mumu_cmd(cmd: str, timeout: int = 5) -> bool:
    """MuMuManager.exe 命令"""
    try:
        r = subprocess.run(
            [MUMU_MGR, "control", "-v", "0", "tool", "cmd", "-c", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0 or "errcode: 0" in r.stdout
    except Exception as e:
        print(f"  ❌ MuMuManager: {e}")
        return False


def detect_goto_bar(model: YOLO, img: np.ndarray, conf_thresh: float = 0.15) -> list[dict]:
    """YOLO检测goto bar, 用低模型阈值+Python二次过滤"""
    results = model(img, verbose=False, conf=0.01)  # 极低模型阈值
    bars: list[dict] = []
    for r in results:
        if r.boxes is None:
            continue
        for box in r.boxes:
            if int(box.cls[0]) != 0:
                continue
            conf = float(box.conf[0])
            if conf < conf_thresh:
                continue
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
            bars.append({
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2, "conf": conf,
            })
    return sorted(bars, key=lambda b: b["conf"], reverse=True)


# ================================================================
# 主流程
# ================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit-x", type=int, default=1040)
    parser.add_argument("--unit-y", type=int, default=530)
    parser.add_argument("--target-x", type=int, default=600)
    parser.add_argument("--target-y", type=int, default=530)
    args = parser.parse_args()

    print("=" * 70)
    print("🔥 Firefight 拖拽调试 (MuMuManager + YOLO)")
    print(f"   单位: ({args.unit_x}, {args.unit_y})")
    print(f"   目标: ({args.target_x}, {args.target_y})")
    print("=" * 70)

    # 加载 YOLO
    model = YOLO(str(MODEL_PATH))
    # RTX 5070 Ti sm_120 不兼容当前PyTorch, 强制CPU
    model.to("cpu")
    print(f"✅ YOLO 已加载: goto_bar17 (CPU模式)")

    # ── 0. 初始截图 ──
    print("\n── 步骤0: 初始截图 ──")
    before = screenshot("drag_debug_0_before.png")
    if before is None:
        print("❌ 截图失败，模拟器开了吗？")
        return
    bars_before = detect_goto_bar(model, before, conf_thresh=0.15)
    print(f"  蓝条: {len(bars_before)}个")
    for b in bars_before:
        print(f"  [{b['conf']:.2f}] cx={b['cx']} cy={b['cy']} box=({b['x1']},{b['y1']})-({b['x2']},{b['y2']})")

    # ── 1. Tap 选中 ──
    print("\n── 步骤1: Tap选中 ──")
    ok = mumu_cmd(f"input tap {args.unit_x} {args.unit_y}")
    print(f"  tap({args.unit_x},{args.unit_y}) {'✅' if ok else '❌'}")
    time.sleep(0.6)
    after_tap = screenshot("drag_debug_1_selected.png")
    bars_tap = detect_goto_bar(model, after_tap, conf_thresh=0.15) if after_tap is not None else []
    print(f"  选中后蓝条: {len(bars_tap)}个")
    for b in bars_tap:
        print(f"  [{b['conf']:.2f}] cx={b['cx']} cy={b['cy']}")

    # ── 2. 双击中圈 + 拖拽 ──
    print("\n── 步骤2: 双击中圈 + 拖拽 ──")
    if bars_tap:
        bar = bars_tap[0]
        bx, by = bar["cx"], bar["cy"]
        print(f"  YOLO蓝条中心: ({bx}, {by})")
    else:
        bx, by = args.unit_x, args.unit_y + 43
        print(f"  YOLO未检出, 估测中心: ({bx}, {by})")

    tx, ty = args.target_x, args.target_y
    print(f"  → 目标: ({tx}, {ty})  dx={tx-bx} dy={ty-by}")

    # 双击中圈: tap×2
    mumu_cmd(f"input tap {bx} {by}")
    time.sleep(0.05)
    mumu_cmd(f"input tap {bx} {by}")
    time.sleep(0.1)

    # 拖拽 swipe 2秒
    mumu_cmd(f"input swipe {bx} {by} {tx} {ty} 2000")
    print(f"  ✅ swipe 2000ms 中")
    time.sleep(2.5)

    # ── 3. 结果截图 ──
    print("\n── 步骤3: 结果 ──")
    time.sleep(1.0)
    after = screenshot("drag_debug_2_result.png")

    # ── 4. 对比分析 ──
    if after is not None:
        diff = pixel_diff(before, after)
        print(f"  变化: {diff['pct']}% ({diff['changed_px']}px)")
        bar_label = "🎥 镜头平移" if diff["camera_shift"] else \
                    ("⚠️ 基本无变化" if diff["pct"] < 0.3 else "✅ 可能是单位/蓝条移动")
        print(f"  判定: {bar_label}")

        bars_after = detect_goto_bar(model, after, conf_thresh=0.15)
        print(f"  最终蓝条: {len(bars_after)}个")
        for b in bars_after:
            print(f"  [{b['conf']:.2f}] cx={b['cx']} cy={b['cy']}")

        # 蓝条位置变化: unit移动的话, 蓝条也在动的
        if bars_before and bars_after:
            dx = bars_after[0]["cx"] - bars_before[0]["cx"]
            dy = bars_after[0]["cy"] - bars_before[0]["cy"]
            print(f"  蓝条位移: dx={dx} dy={dy}")

    print(f"\n📁 screenshots/drag_debug_0/1/2.png")


if __name__ == "__main__":
    main()
