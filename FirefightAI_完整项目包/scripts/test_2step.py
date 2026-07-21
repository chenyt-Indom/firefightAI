"""两步触控：先选所有单位 → 等500ms → 再全部拖拽"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2, numpy as np, subprocess, time, math

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB = r"D:\MuMuPlayer\nx_main\adb.exe"
OFFSET = 50
EPS = 50

def mumu(cmd):
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
                   capture_output=True, text=True, timeout=30)

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def simple_cluster(points, eps=50):
    if len(points) == 0:
        return []
    points = np.array(points)
    clusters = []
    used = set()
    for i in range(len(points)):
        if i in used:
            continue
        cluster = [points[i]]
        used.add(i)
        queue = [i]
        while queue:
            cur = queue.pop(0)
            for j in range(len(points)):
                if j not in used:
                    dist = math.hypot(points[cur][0]-points[j][0], points[cur][1]-points[j][1])
                    if dist < eps:
                        cluster.append(points[j])
                        used.add(j)
                        queue.append(j)
        clusters.append(np.array(cluster))
    return clusters

def detect_and_cluster(img, eps=50):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    points = []
    for i in range(1, min(200, len(stats))):
        if 50 < stats[i,4] < 2000:
            points.append([centroids[i][0], centroids[i][1]])
    if len(points) == 0:
        return []
    clusters = simple_cluster(points, eps=eps)
    centers = []
    for cluster in clusters:
        avg = np.mean(cluster, axis=0)
        best_pt = min(cluster, key=lambda p: math.hypot(p[0]-avg[0], p[1]-avg[1]))
        centers.append((int(best_pt[0]), int(best_pt[1]), len(cluster)))
    return centers

# === 主流程 ===
print("截图...")
before = capture()
clusters = detect_and_cluster(before, eps=EPS)
print(f"检测到 {len(clusters)} 个聚类")

# === 第1步：只点击选中所有单位 ===
print("\n[Step 1] 选中所有单位...")
for i in range(0, len(clusters), 10):
    chunk = clusters[i:i+10]
    cmds = [f"input tap {cx} {cy}" for cx, cy, _ in chunk]
    batch = "; ".join(cmds)
    mumu(batch)
    print(f"  选中 {len(chunk)} 个: {batch[:80]}...")

# === 等游戏注册选中状态 ===
print("\n[Step 2] 等待 800ms 让游戏注册选中...")
time.sleep(0.8)

# === 第3步：从圆圈位置向上拖拽 ===
print("\n[Step 3] 拖拽所有单位向上...")
for i in range(0, len(clusters), 10):
    chunk = clusters[i:i+10]
    cmds = []
    for cx, cy, _ in chunk:
        circle_y = cy + OFFSET
        target_y = max(10, circle_y - 300)
        cmds.append(f"input swipe {cx} {circle_y} {cx} {target_y} 800")
    batch = "; ".join(cmds)
    mumu(batch)
    print(f"  拖拽 {len(chunk)} 个: {batch[:80]}...")

print("\n等待 1 秒看效果...")
time.sleep(1.0)
after = capture()

# 对比
diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
_, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
changed = np.sum(thresh > 0)
print(f"\n像素变化: {changed/thresh.size*100:.2f}%")

cv2.imwrite("screenshots/before_2step.png", before)
cv2.imwrite("screenshots/after_2step.png", after)

# 标注
for cx, cy, count in clusters:
    cv2.circle(before, (cx, cy), 8, (0, 255, 255), 2)
cv2.imwrite("screenshots/clusters_2step.png", before)
print("截图已保存")
