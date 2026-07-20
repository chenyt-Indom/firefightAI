"""聚类测试：将检测到的蓝点按距离聚类成16个真正的单位"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2, numpy as np, subprocess, time, math

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB = r"D:\MuMuPlayer\nx_main\adb.exe"
OFFSET = 50  # 圆圈偏移量

def mumu(cmd):
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
                   capture_output=True, text=True, timeout=30)

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def simple_cluster(points, eps=40):
    """简单距离聚类：两个点距离 < eps 则归为一类"""
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
        
        # BFS 扩展
        queue = [i]
        while queue:
            cur = queue.pop(0)
            for j in range(len(points)):
                if j not in used:
                    dist = math.hypot(points[cur][0] - points[j][0], points[cur][1] - points[j][1])
                    if dist < eps:
                        cluster.append(points[j])
                        used.add(j)
                        queue.append(j)
        
        clusters.append(np.array(cluster))
    
    return clusters

def detect_and_cluster(img, eps=40):
    """颜色检测 + 距离聚类 → 返回聚类中心列表"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    
    # 找所有连通区域中心
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

clusters = detect_and_cluster(before, eps=50)
print(f"检测到 {len(clusters)} 个聚类 (预期 16)")
for i, (cx, cy, count) in enumerate(clusters):
    print(f"  单位{i+1}: ({cx}, {cy}) 成员数={count}")

# 分批命令：对每个聚类中心操作
CHUNK = 8
total = 0
for i in range(0, len(clusters), CHUNK):
    chunk = clusters[i:i+CHUNK]
    cmds = []
    for cx, cy, _ in chunk:
        circle_y = cy + OFFSET
        target_y = max(10, circle_y - 250)
        cmds.append(f"input tap {cx} {cy}")
        cmds.append(f"input swipe {cx} {circle_y} {cx} {target_y} 500")
    batch = "; ".join(cmds)
    print(f"  批次 {i//CHUNK+1}: {len(chunk)} 个单位... {batch[:80]}...")
    mumu(batch)
    total += len(chunk)
    time.sleep(0.3)

print(f"\n执行完成, 共 {total} 个单位")
time.sleep(1.0)
after = capture()

# 像素对比
diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
_, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
changed = np.sum(thresh > 0)
total_px = thresh.size
print(f"像素变化: {changed/total_px*100:.2f}%")

cv2.imwrite("screenshots/before_cluster.png", before)
cv2.imwrite("screenshots/after_cluster.png", after)
print("截图已保存")

# 在图上画聚类
for cx, cy, count in clusters:
    cv2.circle(before, (cx, cy), 8, (0, 255, 255), 2)
    cv2.putText(before, f"{count}", (cx+10, cy-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,255), 1)
cv2.imwrite("screenshots/clusters_labeled.png", before)
print("聚类标注图已保存: screenshots/clusters_labeled.png")
