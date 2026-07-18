"""三重圆圈检测 + 极速双击: 
1. 点单位选中 → 蓝线出现 (3个圆圈: 左端/中间/右端)
2. 检测中间圆圈 (位置控制)
3. input tap + input swipe 间隔 <50ms (方法2)
"""
import time, subprocess, math
import cv2, numpy as np

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB  = r"D:\MuMuPlayer\nx_main\adb.exe"

def cap():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def find_unit(img):
    h, w = img.shape[:2]
    s = cv2.resize(img, (w//2, h//2))
    hsv = cv2.cvtColor(s, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    _, _, st, ct = cv2.connectedComponentsWithStats(m, 8)
    for i in range(1, len(st)):
        if 50 < st[i,4] < 3000:
            return int(ct[i,0]*2), int(ct[i,1]*2)
    return None

def find_all_circles(before, after, ux, uy, search_r=120):
    """找所有新增蓝色区域, 返回 (x, y, area, distance_from_unit)"""
    x1 = max(0, ux - search_r)
    y1 = max(0, uy - 30)
    x2 = min(before.shape[1], ux + search_r)
    y2 = min(before.shape[0], uy + search_r + 60)
    
    def blue_mask(img):
        roi = img[y1:y2, x1:x2]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        return cv2.inRange(hsv, np.array([95,70,70]), np.array([135,255,255]))
    
    bm = blue_mask(before)
    am = blue_mask(after)
    new = cv2.subtract(am, bm)
    
    n, _, cst, cct = cv2.connectedComponentsWithStats(new, 8)
    circles = []
    for i in range(1, n):
        if cst[i,4] < 3:  # 过滤极小噪点
            continue
        cx = int(cct[i][0]) + x1
        cy = int(cct[i][1]) + y1
        dist = math.hypot(cx - ux, cy - uy)
        circles.append((cx, cy, cst[i,4], dist))
    
    return sorted(circles, key=lambda c: c[2], reverse=True)

print("=" * 60)
print("🧪 三重圆圈检测 + 中圈双击拖拽")

before = cap()
h, w = before.shape[:2]
unit = find_unit(before)
if not unit:
    print("❌ 无单位"); exit(1)
ux, uy = unit
print(f"单位: ({ux}, {uy})")

# ① 点选中
subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c",
                f"input tap {ux} {uy}"],
               capture_output=True, text=True, timeout=5)
time.sleep(0.7)

# ② 截图找所有圆圈
after_tap = cap()
circles = find_all_circles(before, after_tap, ux, uy, search_r=180)
print(f"\n找到 {len(circles)} 个蓝色区域:")

if not circles:
    print("❌ 没找到!"); exit(1)

for i, (cx, cy, area, dist) in enumerate(circles[:6]):
    print(f"  #{i+1}: ({cx},{cy}) 面积={area:.0f} 距单位={dist:.0f}px 偏移(dx={cx-ux:+d},dy={cy-uy:+d})")

# ③ 取中间圆圈 (如果>=3个, 取第2个; 否则取最大的)
if len(circles) >= 3:
    # 按距离排序，取中间的那个
    sorted_by_dist = sorted(circles, key=lambda c: c[3])
    mid = sorted_by_dist[len(sorted_by_dist)//2]
    print(f"\n✅ 中间圈: ({mid[0]}, {mid[1]}) (按距离排序第{len(sorted_by_dist)//2+1}个)")
    cx, cy = mid[0], mid[1]
elif len(circles) >= 1:
    # 少于3个就取面积最大的
    cx, cy = circles[0][0], circles[0][1]
    print(f"\n⚠️ 少于3个圆, 取最大的: ({cx}, {cy})")
else:
    print("❌ 无圆圈"); exit(1)

# ④ 极速双击拖拽 (方法2: 两命令间隔极短)
# 发送 tap (第1次点) → 立即发送 swipe (第2次点+hold+拖)
target_y = min(h - 30, cy + 250)
print(f"\n🔄 极速双击: tap({cx},{cy}) → 立即 swipe({cx},{cy})→({cx},{target_y}) 2000ms")

# 用两条独立的 MuMuManager 命令, 间隔尽量短
subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c",
                f"input tap {cx} {cy}"],
               capture_output=True, text=True, timeout=3)
# 极小间隔 (<50ms)
subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c",
                f"input swipe {cx} {cy} {cx} {target_y} 2000"],
               capture_output=True, text=True, timeout=10)

time.sleep(3)
after = cap()
cv2.imwrite("screenshots/mid_circle_result.png", after)
cv2.imwrite("screenshots/mid_circle_tap.png", after_tap)

diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
pct = np.count_nonzero(gray > 30) / gray.size * 100
print(f"📊 像素变化: {pct:.2f}%")
print(f"\n请确认: 蓝线位置变了没? (注意看中间圆圈拖到哪了)")
