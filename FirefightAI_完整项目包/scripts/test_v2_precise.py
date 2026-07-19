"""最简验证: 只操作1个单位, 不看像素, 直接对比坐标"""
import sys, time, subprocess, math
import cv2, numpy as np

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB  = r"D:\MuMuPlayer\nx_main\adb.exe"

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def find_biggest_unit(img):
    """找画面中间最大的蓝色单位"""
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    best, best_area = None, 0
    for i in range(1, len(stats)):
        area = stats[i,4]
        cx, cy = int(centroids[i][0]), int(centroids[i][1])
        if area > best_area and 100 < cy < 900 and 100 < cx < 1800:
            best = (cx, cy)
            best_area = area
    return best

def find_circle_offset(before, after, ux, uy):
    """对比前后, 找新增蓝像素位置 → 圆圈坐标"""
    x1 = max(0, ux - 80)
    y1 = max(0, uy - 20)
    x2 = min(before.shape[1], ux + 80)
    y2 = min(before.shape[0], uy + 100)
    
    def blue_mask(img):
        roi = img[y1:y2, x1:x2]
        return cv2.inRange(roi, np.array([80,40,0]), np.array([255,255,80]))
    
    bmask = blue_mask(before)
    amask = blue_mask(after)
    new = cv2.subtract(amask, bmask)
    ys, xs = np.where(new > 0)
    
    if len(xs) < 3:
        return None
    
    # 找最大联通区域
    n, _, cstats, ccentroids = cv2.connectedComponentsWithStats(new, 8)
    if n <= 1:
        return None
    
    # 最大区域
    labels = list(range(1, n))
    labels.sort(key=lambda i: -cstats[i, 4])
    ccx = int(ccentroids[labels[0]][0]) + x1
    ccy = int(ccentroids[labels[0]][1]) + y1
    return (ccx, ccy), len(xs)

def find_unit_position(img, ux, uy, search_radius=120):
    """在一帧中找到某个单位的位置 (最近蓝色像素重心)"""
    h, w = img.shape[:2]
    x1 = max(0, ux - search_radius)
    y1 = max(0, uy - search_radius)
    x2 = min(w, ux + search_radius)
    y2 = min(h, uy + search_radius)
    
    roi = img[y1:y2, x1:x2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    ys, xs = np.where(mask > 0)
    if len(xs) < 3:
        return None
    return (int(np.mean(xs)) + x1, int(np.mean(ys)) + y1)

print("=" * 60)
print("🧪 单单位精确控制验证")

# 1. 找单位
before = capture()
unit = find_biggest_unit(before)
if not unit:
    print("❌ 无单位"); sys.exit(1)
ux, uy = unit
w, h = before.shape[1], before.shape[0]
print(f"目标单位: ({ux}, {uy}) | 屏幕 {w}x{h}")

# 2. 点击选单位 + 截图查圆圈
subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", f"input tap {ux} {uy}"],
               capture_output=True, text=True, timeout=5)
time.sleep(0.8)
after_tap = capture()
cv2.imwrite("screenshots/v2_after_tap.png", after_tap)

# 3. 找圆圈
result = find_circle_offset(before, after_tap, ux, uy)
if not result:
    print("❌ 没找到圆圈 (蓝线没出现?)")
    print("   请检查 after_tap.png，看蓝线是否出现")
    sys.exit(1)
(cx, cy), n_pixels = result
print(f"✅ 找到圆圈: ({cx}, {cy}) 偏移(dx={cx-ux:+d}, dy={cy-uy:+d}) 新增{n_pixels}蓝像素")

# 4. 记录 tap 后单位位置
pos_before = find_unit_position(after_tap, ux, uy)
print(f"   当前位置: {pos_before}")

# 5. 拖拽: 从圆圈拖到下方200px
target_y = min(h - 30, cy + 200)
subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c",
                f"input swipe {cx} {cy} {cx} {target_y} 1000"],
               capture_output=True, text=True, timeout=10)
time.sleep(2.5)

# 6. 验证: 单位坐标变了吗?
after_move = capture()
cv2.imwrite("screenshots/v2_after_move.png", after_move)

pos_after = find_unit_position(after_move, pos_before[0], pos_before[1]) if pos_before else None

print(f"\n📊 结果:")
print(f"   拖拽: ({cx},{cy}) → ({cx},{target_y})")
print(f"   单位位置(前): {pos_before}")
print(f"   单位位置(后): {pos_after}")

if pos_before and pos_after:
    dist = math.hypot(pos_after[0]-pos_before[0], pos_after[1]-pos_before[1])
    if dist > 50:
        print(f"   ✅ 位移: {dist:.0f}px — 单位移动了!")
    elif dist > 20:
        print(f"   ⚠️ 位移: {dist:.0f}px — 微小移动")
    else:
        print(f"   ❌ 位移: {dist:.0f}px — 没移动")
else:
    print("   ❌ 找不到单位 (可能移动出搜索范围或已被消灭)")

print(f"\n截图: v2_after_tap.png (选中后蓝线), v2_after_move.png (拖拽后)")
