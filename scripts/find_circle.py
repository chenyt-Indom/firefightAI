"""精确定位圆圈位置：选中一个单位后，分析蓝线/圆圈像素坐标"""
import cv2, numpy as np, subprocess, time, os

ADB = r"D:\MuMuPlayer\nx_main\adb.exe"
MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
OUT = r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\screenshots"

def capture():
    result = subprocess.run(
        [ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
        capture_output=True, timeout=10
    )
    arr = np.frombuffer(result.stdout, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)

def detect_allies(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    units = []
    for c in contours:
        area = cv2.contourArea(c)
        if 30 < area < 3000:
            M = cv2.moments(c)
            if M["m00"] > 0:
                cx, cy = int(M["m10"]/M["m00"]), int(M["m01"]/M["m00"])
                units.append((cx, cy, area))
    return units

def mumu(cmd):
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
                   capture_output=True, text=True, timeout=5)

# ── 1. 截图 → 检测一个单位 ──
print("1. 截图...")
before = capture()
units = detect_allies(before)
if not units:
    print("未检测到友军!")
    exit(1)

# 找一个孤立单位（避免干扰）
units.sort(key=lambda x: x[2])  # 按面积排序，小的可能是步兵个体
target = units[0]
ux, uy = target[0], target[1]
print(f"选中目标单位: ({ux}, {uy}), 面积={target[2]}")

cv2.imwrite(os.path.join(OUT, "circle_before.png"), before)

# ── 2. 点击选中 ──
print(f"2. 点击选中 ({ux}, {uy})...")
mumu(f"input tap {ux} {uy}")
time.sleep(0.8)

# ── 3. 截图分析 ──
after = capture()
cv2.imwrite(os.path.join(OUT, "circle_after.png"), after)

# 分析: 在单位下方区域找新增的蓝色像素
h, w = before.shape[:2]
search_y1 = max(0, uy)
search_y2 = min(h, uy + 120)
search_x1 = max(0, ux - 60)
search_x2 = min(w, ux + 60)

# 提取前后蓝色像素
def blue_pixels(img, x1, x2, y1, y2):
    roi = img[y1:y2, x1:x2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([130,255,255]))
    ys, xs = np.where(mask > 0)
    return [(x + x1, y + y1) for x, y in zip(xs, ys)]

blue_before = set(blue_pixels(before, search_x1, search_x2, search_y1, search_y2))
blue_after = set(blue_pixels(after, search_x1, search_x2, search_y1, search_y2))
new_blue = blue_after - blue_before
lost_blue = blue_before - blue_after

print(f"\n3. 蓝色像素变化:")
print(f"   搜索区域: [{search_x1}:{search_x2}, {search_y1}:{search_y2}] ({search_x2-search_x1}x{search_y2-search_y1})")
print(f"   选中前: {len(blue_before)}px")
print(f"   选中后: {len(blue_after)}px")
print(f"   新增蓝: {len(new_blue)}px")
print(f"   消失蓝: {len(lost_blue)}px")

if new_blue:
    # 计算新增蓝点的中心和范围
    xs = [p[0] for p in new_blue]
    ys = [p[1] for p in new_blue]
    cx_new = int(np.mean(xs))
    cy_new = int(np.mean(ys))
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    
    offset_x = cx_new - ux
    offset_y = cy_new - uy
    
    print(f"\n4. 新增蓝色区域 (蓝线+圆圈):")
    print(f"   中心: ({cx_new}, {cy_new})")
    print(f"   范围: [{x_min}:{x_max}, {y_min}:{y_max}]")
    print(f"   相对于单位偏移: dx={offset_x}, dy={offset_y}")
    print(f"   大小: {x_max-x_min}x{y_max-y_min}")
    
    # 判断形状
    aspect = (x_max - x_min) / max(1, y_max - y_min)
    print(f"   宽高比: {aspect:.2f} ({'横向线' if aspect > 1.5 else '圆形/方形' if 0.7 < aspect < 1.5 else '竖向线'})")
    
    # 在after图上画标记
    marked = after.copy()
    cv2.circle(marked, (ux, uy), 8, (0, 255, 255), 2)  # 单位中心-黄圈
    cv2.circle(marked, (cx_new, cy_new), 8, (0, 255, 0), 2)  # 新增蓝点中心-绿圈
    cv2.rectangle(marked, (x_min, y_min), (x_max, y_max), (255, 0, 0), 1)  # 蓝线范围框
    cv2.rectangle(marked, (search_x1, search_y1), (search_x2, search_y2), (0, 0, 255), 1)  # 搜索区域框
    cv2.imwrite(os.path.join(OUT, "circle_analyzed.png"), marked)
    print(f"\n   截图已保存: circle_analyzed.png (黄=单位中心, 绿=新增蓝中心, 蓝框=蓝线范围)")
else:
    # 可能没选中，扩大搜索
    print("\n4. 未在近处找到新增蓝点! 扩大搜索...")
    # 全图搜索新增蓝色
    full_new = set(blue_pixels(after, 0, w, 0, h)) - set(blue_pixels(before, 0, w, 0, h))
    if full_new:
        xs = [p[0] for p in full_new]
        ys = [p[1] for p in full_new]
        print(f"   全图新增蓝色: {len(full_new)}px, 中心=({np.mean(xs):.0f}, {np.mean(ys):.0f})")
    else:
        print("   全图无新增蓝色 — 选中失败!")
