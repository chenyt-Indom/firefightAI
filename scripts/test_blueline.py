"""蓝线检测测试：选中一个单位 → 截图看蓝线 → 下达移动 → 看蓝线位置是否变化"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2, numpy as np, subprocess, time

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB = r"D:\MuMuPlayer\nx_main\adb.exe"

def mumu(cmd):
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
                   capture_output=True, text=True, timeout=10)

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def detect_unit_region(img, cx, cy, size=120):
    """裁剪单位周围区域"""
    h, w = img.shape[:2]
    x1 = max(0, cx - size//2)
    y1 = max(0, cy - 20)
    x2 = min(w, cx + size//2)
    y2 = min(h, cy + size)
    return img[y1:y2, x1:x2], (x1, y1)

def find_blue_elements(img_region):
    """在截取区域中找蓝色元素（非单位本身的蓝色标记"""
    hsv = cv2.cvtColor(img_region, cv2.COLOR_BGR2HSV)
    # 放宽蓝色范围，包含更亮的蓝线
    mask1 = cv2.inRange(hsv, np.array([95, 80, 80]), np.array([130, 255, 255]))
    # 也尝试找高饱和度的蓝
    mask2 = cv2.inRange(hsv, np.array([100, 150, 150]), np.array([120, 255, 255]))
    mask = cv2.bitwise_or(mask1, mask2)
    return mask

# === 测试 ===
print("=" * 60)
print("蓝线检测 - 单单位精确测试")
print("=" * 60)

# 1. 截图
before = capture()
h, w = before.shape[:2]
print(f"截图: {w}x{h}")

# 2. 找一个小区域内的单位来测试（屏幕中央偏下，避开边缘）
# 用颜色检测
hsv = cv2.cvtColor(before, cv2.COLOR_BGR2HSV)
mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
_, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)

# 找面积最大的单位（可能是车辆，容易观察）
largest = None
largest_area = 0
for i in range(1, min(200, len(stats))):
    area = stats[i,4]
    if area > largest_area and 50 < area < 2000:
        cx, cy = int(centroids[i][0]), int(centroids[i][1])
        # 避开屏幕顶部边缘
        if cy > 200 and cy < h - 100:
            largest_area = area
            largest = (cx, cy, area)

if largest is None:
    print("没找到合适的测试单位")
    exit()

cx, cy, area = largest
print(f"\n测试单位: ({cx}, {cy}) 面积={area}")

# 3. 截取区域 before
region_before, offset = detect_unit_region(before, cx, cy)
blue_before = find_blue_elements(region_before)
blue_px_before = np.sum(blue_before > 0)
print(f"选中前蓝色像素: {blue_px_before}")

# 4. 点击选中
print(f"\n点击 ({cx}, {cy}) 选中单位...")
mumu(f"input tap {cx} {cy}")
time.sleep(0.5)

after_select = capture()
region_after_select, _ = detect_unit_region(after_select, cx, cy)
blue_after_select = find_blue_elements(region_after_select)
blue_px_select = np.sum(blue_after_select > 0)
print(f"选中后蓝色像素: {blue_px_select} (+{blue_px_select - blue_px_before})")

# 保存选中前后的区域截图
cv2.imwrite("screenshots/region_before.png", region_before)
cv2.imwrite("screenshots/region_selected.png", region_after_select)

# 标记蓝色检测结果
blue_viz = cv2.cvtColor(blue_after_select, cv2.COLOR_GRAY2BGR)
cv2.imwrite("screenshots/region_blue_mask.png", blue_viz)

# 5. 拖拽向正上方
circle_y = cy + 50
target_y = max(10, circle_y - 300)
print(f"\n拖拽: ({cx}, {circle_y}) → ({cx}, {target_y})")
mumu(f"input swipe {cx} {circle_y} {cx} {target_y} 500")
time.sleep(0.8)

after_move = capture()
region_after_move, _ = detect_unit_region(after_move, cx, cy)
blue_after_move = find_blue_elements(region_after_move)
blue_px_move = np.sum(blue_after_move > 0)
print(f"移动后蓝色像素: {blue_px_move} (vs 选中后 {blue_px_select})")

cv2.imwrite("screenshots/region_moved.png", region_after_move)

# 6. 在全局图上标注
cv2.circle(before, (cx, cy), 10, (0, 255, 255), 2)
cv2.putText(before, "TARGET", (cx+15, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,255), 1)
cv2.imwrite("screenshots/unit_marked.png", before)

print(f"\n{'='*60}")
print("结论:")
if blue_px_select > blue_px_before + 50:
    print(f"  ✅ 选中成功！蓝色元素增加了 {blue_px_select - blue_px_before} 像素")
else:
    print(f"  ❌ 选中可能失败，蓝色像素无明显变化 ({blue_px_select - blue_px_before})")

if blue_px_move != blue_px_select:
    print(f"  ✅ 蓝线位置有变化! (蓝线像素从{blue_px_select}变到{blue_px_move})")
else:
    print(f"  ❌ 蓝线位置无变化，拖拽命令可能未生效")

print(f"\n文件已保存:")
print(f"  screenshots/region_before.png     - 选中前")
print(f"  screenshots/region_selected.png   - 选中后（看有没有蓝线出现）")
print(f"  screenshots/region_moved.png      - 拖拽后（看蓝线是否移位）")
print(f"  screenshots/unit_marked.png       - 全局标记")
