"""双击圆圈拖拽: tap → tap(hold) + drag

用户确认的控制方式:
1. 点单位选中 (蓝线+圆圈出现)
2. 再点圆圈 → 不松手 (第2次点击+hold)
3. 拖蓝线到目标 → 松手 → 单位自动寻路

关键: 两次点击必须在圆圈的同一点, 且极快连续 (模拟双击)
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

def find_circle(before, after, ux, uy, search_r=100):
    """找出选中后新增的蓝色区域 → 圆圈位置"""
    x1 = max(0, ux - search_r)
    y1 = max(0, uy - 20)
    x2 = min(before.shape[1], ux + search_r)
    y2 = min(before.shape[0], uy + search_r + 40)
    
    def blue_mask(img):
        roi = img[y1:y2, x1:x2]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        return cv2.inRange(hsv, np.array([95,80,80]), np.array([130,255,255]))
    
    bm = blue_mask(before)
    am = blue_mask(after)
    new = cv2.subtract(am, bm)
    ys, xs = np.where(new > 0)
    
    if len(xs) < 5:
        return None
    
    n, _, cst, cct = cv2.connectedComponentsWithStats(new, 8)
    if n <= 1:
        return None
    
    # 最大新增蓝色区域
    labels = list(range(1, n))
    labels.sort(key=lambda i: -cst[i,4])
    cx = int(cct[labels[0]][0]) + x1
    cy = int(cct[labels[0]][1]) + y1
    return (cx, cy), len(xs)

print("=" * 60)
print("🧪 双击圆圈拖拽测试")
before = cap()
h, w = before.shape[:2]

unit = find_unit(before)
if not unit:
    print("❌ 无单位"); exit(1)
ux, uy = unit
print(f"单位中心: ({ux}, {uy})")

# ① 点单位选中
subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c",
                f"input tap {ux} {uy}"],
               capture_output=True, text=True, timeout=5)
time.sleep(0.6)

# ② 截图找圆圈
after_tap = cap()
cv2.imwrite("screenshots/dbl_tap.png", after_tap)
result = find_circle(before, after_tap, ux, uy)

if not result:
    # 扩大搜索
    print("⚠️ 未检测到圆圈, 扩大搜索...")
    result2 = find_circle(before, after_tap, ux, uy, search_r=150)
    if result2:
        result = result2
    else:
        print("❌ 找不到圆圈, 用单位中心+35代替")
        cx, cy = ux, uy + 35
else:
    (cx, cy), n = result
    print(f"✅ 圆圈: ({cx}, {cy}) 偏移(dx={cx-ux:+d},dy={cy-uy:+d}) {n}像素")

# ③④ 双击+拖拽: tap → 极短间隔 → swipe (模拟双击hold)
target_y = max(30, min(h-30, cy + 250))
cmd = f"input tap {cx} {cy}; input swipe {cx} {cy} {cx} {target_y} 3000"
print(f"\n🔄 双击拖拽: {cmd}")
print(f"   模拟: tap圆圈 → 再tap同位置+hold → 拖3000ms到({cx},{target_y})")

subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
               capture_output=True, text=True, timeout=10)

time.sleep(3)
after = cap()
cv2.imwrite("screenshots/dbl_result.png", after)

diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
pct = np.count_nonzero(gray > 30) / gray.size * 100
print(f"📊 像素变化: {pct:.2f}%")
print(f"截图: dbl_tap.png (选中后), dbl_result.png (拖拽后)")
print(f"\n请确认: 蓝线位置是否变化了? (单位会慢慢寻路过去)")
