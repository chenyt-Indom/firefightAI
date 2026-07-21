"""两步法终极版: 
1. tap选中 → 等0.8s 
2. 截图检测蓝线(Goto标记)精确位置
3. 从蓝线位置拖拽2000ms慢速 → 确保进入DRAGGING_GOTO_MARKER
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
        if 50 < st[i,4] < 3000 and 100 < ct[i,1]*2 < 900:
            return int(ct[i,0]*2), int(ct[i,1]*2)
    return None

def find_new_blue(before, after, ux, uy):
    """在单位周围找新增的蓝色像素 (蓝线/圆圈)"""
    x1 = max(0, ux - 100)
    y1 = max(0, uy - 20)
    x2 = min(before.shape[1], ux + 100)
    y2 = min(before.shape[0], uy + 140)
    
    def blue_mask(img):
        roi = img[y1:y2, x1:x2]
        # 检测新增的蓝色 (BGR: B>G, B>R 且总亮度改变)
        b = roi[:,:,0].astype(int)
        g = roi[:,:,1].astype(int)
        r = roi[:,:,2].astype(int)
        return ((b > g + 20) & (b > r + 20)).astype(np.uint8) * 255
    
    bm = blue_mask(before)
    am = blue_mask(after)
    new = cv2.subtract(am, bm)
    ys, xs = np.where(new > 100)
    
    if len(xs) < 5:
        return None
    
    # 最大联通区域
    n, _, cstats, ccentroids = cv2.connectedComponentsWithStats(new, 8)
    if n <= 1:
        return None
    
    labels = list(range(1, n))
    labels.sort(key=lambda i: -cstats[i, 4])
    cx = int(ccentroids[labels[0]][0]) + x1
    cy = int(ccentroids[labels[0]][1]) + y1
    return (cx, cy), len(xs), cstats[labels[0], 4]

print("=" * 60)
print("🧪 两步法终极版: tap选中 → 检测蓝线 → 慢速拖拽")

# Step 1: 截图找单位
before = cap()
unit = find_unit(before)
if not unit:
    print("❌ 无单位")
    exit(1)
ux, uy = unit
h, w = before.shape[:2]
print(f"目标: ({ux}, {uy})")

# Step 2: tap 选中
subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c",
                f"input tap {ux} {uy}"],
               capture_output=True, text=True, timeout=5)
time.sleep(0.8)  # 等待蓝线出现

# Step 3: 截图检测蓝线
after_tap = cap()
cv2.imwrite("screenshots/final_tap.png", after_tap)

result = find_new_blue(before, after_tap, ux, uy)
if not result:
    print("❌ 未检测到蓝线! 扩大搜索...")
    # 放宽条件重试
    def find_blue_wide(img, ux, uy):
        h, w = img.shape[:2]
        x1, y1 = max(0, ux-150), max(0, uy-50)
        x2, y2 = min(w, ux+150), min(h, uy+200)
        roi = img[y1:y2, x1:x2]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv, np.array([95,80,80]), np.array([130,255,255]))
        ys, xs = np.where(m > 0)
        if len(xs) < 5:
            return None
        return (int(np.mean(xs))+x1, int(np.mean(ys))+y1)

    blue_pos = find_blue_wide(after_tap, ux, uy)
    if not blue_pos:
        print("❌ 仍然没找到蓝线")
        exit(1)
    bx, by = blue_pos
    n_pixels = 0
else:
    (bx, by), n_pixels, area = result
    print(f"✅ 检测到蓝线: ({bx}, {by}) 偏移(dx={bx-ux:+d},dy={by-uy:+d}) {n_pixels}像素")

# Step 4: 从蓝线位置慢速拖拽 (2000ms)
target_y = max(30, min(h-30, by + 200))
cmd = f"input swipe {bx} {by} {bx} {target_y} 2000"
print(f"🔄 拖拽: {cmd}")

subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
               capture_output=True, text=True, timeout=10)

time.sleep(2.5)
after = cap()
cv2.imwrite("screenshots/final_after.png", after)

diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
pct = np.count_nonzero(gray > 30) / gray.size * 100
print(f"📊 像素变化: {pct:.2f}%")
print(f"截图: final_tap.png (蓝线), final_after.png (拖拽后)")
print(f"\n请确认: 单位移动了还是镜头平移了?")
