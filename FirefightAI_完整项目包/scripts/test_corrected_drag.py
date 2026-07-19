"""验证修正后的圆圈偏移 (dx=30, dy=61)：选中 → 拖拽"""
import cv2, numpy as np, subprocess, time, os, math

ADB = r"D:\MuMuPlayer\nx_main\adb.exe"
MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
OUT = r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\screenshots"

OFFSET_X, OFFSET_Y = 30, 61

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def detect_allies(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    pts = []
    for i in range(1, min(200, len(stats))):
        if 50 < stats[i,4] < 2000:
            pts.append((int(centroids[i][0]), int(centroids[i][1])))
    return pts

def cluster_simple(pts, eps=50):
    """简易距离聚类"""
    if not pts:
        return []
    clusters = []
    used = set()
    for i, p in enumerate(pts):
        if i in used:
            continue
        cluster = [p]
        used.add(i)
        for j, q in enumerate(pts):
            if j not in used and math.hypot(p[0]-q[0], p[1]-q[1]) < eps:
                cluster.append(q)
                used.add(j)
        # 用最近点
        cx = int(np.mean([t[0] for t in cluster]))
        cy = int(np.mean([t[1] for t in cluster]))
        best = min(cluster, key=lambda t: math.hypot(t[0]-cx, t[1]-cy))
        clusters.append(best)
    return clusters

def mumu(cmd):
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
                   capture_output=True, text=True, timeout=5)

# ── 1. 截图 + 检测 ──
print("1. 截图...")
before = capture()
cv2.imwrite(os.path.join(OUT, "drag_before.png"), before)

allies = cluster_simple(detect_allies(before), eps=50)
print(f"   聚类后: {len(allies)} 个单位")

# 取前8个
targets = allies[:8]

# ── 2. 两步触控 ──
# 第一步: 选中
tap_cmds = [f"input tap {ux} {uy}" for ux, uy in targets]
mumu("; ".join(tap_cmds))
print(f"2. 选中 {len(tap_cmds)} 个单位...")
time.sleep(0.5)

# 第二步: 从圆圈拖到正上方200px
swipe_cmds = []
for ux, uy in targets:
    circle_x = ux + OFFSET_X
    circle_y = uy + OFFSET_Y
    target_y = max(10, uy - 200)
    swipe_cmds.append(f"input swipe {circle_x} {circle_y} {ux} {target_y} 500")

mumu("; ".join(swipe_cmds))
print(f"3. 拖拽 {len(swipe_cmds)} 个单位 (dx={OFFSET_X}, dy={OFFSET_Y})...")
time.sleep(0.8)

# ── 3. 对比 ──
after = capture()
cv2.imwrite(os.path.join(OUT, "drag_after.png"), after)

# 蓝线检测: 选中后应出现蓝线
# 检测after中单位区域的新增蓝色像素
def blue_in_rect(img, cx, cy, w=60, h=100):
    x1, y1 = max(0, cx-w//2), max(0, cy)
    x2, y2 = min(img.shape[1], cx+w//2), min(img.shape[0], cy+h)
    roi = img[y1:y2, x1:x2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([130,255,255]))
    return np.count_nonzero(mask)

blue_before = sum(blue_in_rect(before, ux, uy) for ux, uy in targets)
blue_after = sum(blue_in_rect(after, ux, uy) for ux, uy in targets)

print(f"\n4. 蓝色像素总计:")
print(f"   操作前: {blue_before}")
print(f"   操作后: {blue_after}")
print(f"   变化: {'+' if blue_after >= blue_before else ''}{blue_after - blue_before}")

# 全局像素变化
diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
_, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
changed = np.count_nonzero(thresh)
total = before.shape[0] * before.shape[1]
print(f"   全局变化: {changed/total*100:.2f}%")

if changed/total > 2:
    print("\n✅ 画面大幅变化 — 可能成功! (或地图平移)")
else:
    print(f"\n{'✅' if blue_after > blue_before + 50 else '❌'} 蓝线{'有' if blue_after > blue_before else '无'}变化")
