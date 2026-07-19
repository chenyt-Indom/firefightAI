"""对单一单位测试多种 swipe 距离和方向

目标: 找出能让单位真正移动的 swipe 参数
"""
import sys, os, time, math, subprocess
import cv2, numpy as np

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB  = r"D:\MuMuPlayer\nx_main\adb.exe"
OUT  = r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\screenshots"

os.makedirs(OUT, exist_ok=True)

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def detect_one(img):
    """找一个聚类后的单位 (取最大聚类)"""
    h, w = img.shape[:2]
    small = cv2.resize(img, (w//2, h//2))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    n, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    pts = []
    for i in range(1, n):
        if stats[i,4] >= 10:
            pts.append((int(centroids[i][0])*2, int(centroids[i][1])*2))
    if not pts:
        return None
    clusters, used = [], set()
    for i, p in enumerate(pts):
        if i in used: continue
        cluster = [p]; used.add(i)
        for j, q in enumerate(pts):
            if j not in used and math.hypot(p[0]-q[0], p[1]-q[1]) < 60:
                cluster.append(q); used.add(j)
        cx = sum(c[0] for c in cluster)//len(cluster)
        cy = sum(c[1] for c in cluster)//len(cluster)
        clusters.append((cx, cy, len(cluster)))
    clusters.sort(key=lambda c: c[2], reverse=True)
    return clusters[0] if clusters else None

def try_drag(label, ux, uy, dx, dy, duration, h, w):
    before = capture()
    start_x, start_y = ux, uy + 35
    end_x = max(20, min(w-20, ux + dx))
    end_y = max(20, min(h-20, uy + dy))
    cmd = f"input tap {ux} {uy}; input swipe {start_x} {start_y} {end_x} {end_y} {duration}"
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
                   capture_output=True, text=True, timeout=10)
    time.sleep(2.5)
    after = capture()
    cv2.imwrite(f"{OUT}/drag_{label}_b.png", before)
    cv2.imwrite(f"{OUT}/drag_{label}_a.png", after)
    diff = cv2.absdiff(before, after)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    changed = np.count_nonzero(gray > 30)
    pct = changed / gray.size * 100
    return pct, (start_x, start_y, end_x, end_y)

print("=" * 60)
print("🧪 单单位拖拽距离/方向测试")
img = capture()
h, w = img.shape[:2]
unit = detect_one(img)
if unit is None:
    print("❌ 无单位")
    sys.exit(1)
ux, uy, n = unit
print(f"测试单位: ({ux}, {uy}) 成员={n} | 屏幕 {w}x{h}")

tests = [
    ("短上80",   0,    -80,  500),
    ("上300",    0,    -300, 500),
    ("右上100",  100,  -100, 500),
    ("下100",    0,    +100, 500),
    ("短右60",   60,   0,    500),
    ("慢拖",     0,    -100, 2000),
]

print(f"\n{'测试':<10} {'方向':<10} {'距离':<8} {'时长':<6} {'像素变化':<10}")
print("-" * 60)
for label, dx, dy, dur in tests:
    pct, (sx, sy, ex, ey) = try_drag(label, ux, uy, dx, dy, dur, h, w)
    dist = ((ex-sx)**2 + (ey-sy)**2)**0.5
    print(f"{label:<10} ({dx:+4d},{dy:+4d})  {dist:>5.0f}px  {dur:>4}ms  {pct:>5.2f}%")

print("\n" + "=" * 60)
print("📷 查看 screenshots/drag_*_a.png 对比")
print("   哪个测试让单位真正移动了?")
