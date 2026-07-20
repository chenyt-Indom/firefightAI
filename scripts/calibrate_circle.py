"""
校准圆圈偏移量：测试不同 offset 值，看哪些单位能被正确选中并拖拽
流程：先截一帧→选3个单位→分别用不同偏移量测试→截对比帧→算像素变化
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np
import subprocess
import time
import tempfile

MUMU_EXE = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"

def run_mumu(cmd: str):
    """执行 MuMuManager 命令"""
    full_cmd = f'control -v 0 tool cmd -c "{cmd}"'
    result = subprocess.run(
        [MUMU_EXE] + full_cmd.split(" "),
        capture_output=True, text=True, timeout=5
    )
    return result.stdout

ADB_PATH = r"D:\MuMuPlayer\nx_main\adb.exe"

def capture():
    """管线截图"""
    t0 = time.time()
    result = subprocess.run(
        [ADB_PATH, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
        capture_output=True, timeout=10
    )
    img = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)
    elapsed = (time.time() - t0) * 1000
    return img, elapsed

def detect_allies(img):
    """颜色检测友军（蓝色）"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95, 100, 100]), np.array([115, 255, 255]))
    # 找连通区域中心
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    units = []
    for i in range(1, min(num_labels, 200)):
        area = stats[i, 4]
        if 50 < area < 2000:
            cx, cy = centroids[i]
            units.append((int(cx), int(cy), area))
    return units

def pixel_diff(before, after):
    """计算像素差异百分比"""
    diff = cv2.absdiff(before, after)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
    changed = np.sum(thresh > 0)
    total = thresh.size
    return changed / total * 100

def test_offset(before_img, units, offset_y, n_units=3):
    """
    测试一个偏移量：选 n 个单位，分别 tap 选中 + 从(center_x, center_y+offset_y)向上拖 200px
    返回每个单位的像素变化
    """
    target_units = units[:n_units]
    
    # 依次操作每个单位（逐条执行，确保选中状态）
    cmds = []
    for cx, cy, area in target_units:
        # 1. 先点击单位中心选中它
        # 2. 从圆圈位置拖拽到上方
        start_y = cy + offset_y
        end_y = start_y - 200
        end_y = max(10, end_y)
        cmds.append(f"input tap {cx} {cy}")
        cmds.append(f"input swipe {cx} {start_y} {cx} {end_y} 500")
    
    batch = "; ".join(cmds)
    print(f"  offset={offset_y}px: {batch}")
    
    result = run_mumu(batch)
    time.sleep(0.5)  # 等游戏响应
    
    after_img, _ = capture()
    diff_pct = pixel_diff(before_img, after_img)
    
    return diff_pct, after_img

def main():
    print("=" * 60)
    print("圆圈偏移量校准测试")
    print("=" * 60)
    
    # 1. 截图 + 检测
    print("\n[1] 截图并检测友军...")
    before_img, cap_time = capture()
    print(f"  截图耗时: {cap_time:.0f}ms, 尺寸: {before_img.shape}")
    
    units = detect_allies(before_img)
    print(f"  检测到 {len(units)} 个友军单位")
    
    if len(units) < 5:
        print("  单位太少，退出")
        return
    
    # 选相距较远的 3 个单位做测试（避免互相干扰）
    selected = []
    for u in units:
        if len(selected) >= 3:
            break
        too_close = any(abs(u[0] - s[0]) < 100 and abs(u[1] - s[1]) < 100 for s in selected)
        if not too_close:
            selected.append(u)
    
    print(f"  选中 {len(selected)} 个测试单位: {selected}")
    
    # 2. 测试不同偏移量
    # 从 20px 到 80px，步进 10px
    offsets = list(range(20, 90, 10))
    
    print("\n[2] 开始测试不同偏移量...\n")
    
    results = {}
    for offset in offsets:
        # 每轮重新截图做 baseline
        baseline, _ = capture()
        diff_pct, after_img = test_offset(baseline, selected, offset, n_units=3)
        results[offset] = diff_pct
        print(f"  >>> 像素变化: {diff_pct:.2f}%")
        print()
        time.sleep(1.0)  # 间隔
    
    # 3. 总结
    print("=" * 60)
    print("测试结果汇总:")
    print(f"{'偏移量':>8}  {'像素变化':>10}")
    print("-" * 30)
    for offset, diff in sorted(results.items()):
        bar = "█" * int(diff * 10)
        print(f"{offset:>5}px  {diff:>8.2f}%  {bar}")
    
    best = max(results, key=results.get)
    print(f"\n最佳偏移量: {best}px (像素变化 {results[best]:.2f}%)")
    print("=" * 60)

if __name__ == "__main__":
    main()
