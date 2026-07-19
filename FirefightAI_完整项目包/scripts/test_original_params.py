"""回到原始成功参数: OFFSET=35 正下方, batch tap+swipe, 500ms swipe"""
import cv2, numpy as np, subprocess, time, os

ADB = r'D:\MuMuPlayer\nx_main\adb.exe'
MUMU = r'D:\MuMuPlayer\nx_main\MuMuManager.exe'
OUT = r'C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\screenshots'
OFFSET = 35  # 原始成功参数

def capture():
    r = subprocess.run([ADB, '-s', '127.0.0.1:7555', 'exec-out', 'screencap', '-p'],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def detect_all(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    units = []
    for i in range(1, min(200, len(stats))):
        if 30 < stats[i,4] < 3000:
            units.append((int(centroids[i][0]), int(centroids[i][1])))
    return cluster(units, eps=50)

def cluster(points, eps=50):
    if not points: return []
    used = set()
    groups = []
    for i, p1 in enumerate(points):
        if i in used: continue
        group = [p1]
        used.add(i)
        for j, p2 in enumerate(points):
            if j in used: continue
            if np.hypot(p1[0]-p2[0], p1[1]-p2[1]) < eps:
                group.append(p2); used.add(j)
        avg = np.mean(group, axis=0)
        best = min(group, key=lambda p: np.hypot(p[0]-avg[0], p[1]-avg[1]))
        groups.append((int(best[0]), int(best[1]), len(group)))
    return groups

before = capture()
units = detect_all(before)
print(f"检测到 {len(units)} 个聚类单位")

# 取前20个，避免命令太长超时
units = units[:20]
cmds = []
for cx, cy, n in units:
    circle_y = cy + OFFSET
    target_y = max(10, circle_y - 250)
    cmds.append(f"input tap {cx} {cy}")
    cmds.append(f"input swipe {cx} {circle_y} {cx} {target_y} 500")

# 分批执行（每批4条=2单位），减少失败概率
for batch_start in range(0, len(cmds), 4):
    batch = "; ".join(cmds[batch_start:batch_start+4])
    t0 = time.time()
    r = subprocess.run([MUMU, 'control', '-v', '0', 'tool', 'cmd', '-c', batch],
                       capture_output=True, text=True, timeout=15)
    e = (time.time()-t0)*1000
    ok = "errcode: 0" in r.stdout
    print(f"  批次{batch_start//4+1}: {'OK' if ok else 'FAIL'} ({e:.0f}ms) {r.stdout.strip()[:80]}")

time.sleep(2.0)
after = capture()

diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
_, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
change_pct = np.count_nonzero(thresh) / (before.shape[0]*before.shape[1]) * 100

print(f"\n画面变化: {change_pct:.2f}%")

mb = before.copy()
ma = after.copy()
for cx, cy, n in units:
    circle_y = cy + OFFSET
    target_y = max(10, circle_y - 250)
    cv2.circle(mb, (cx,cy), 6, (0,255,255), 2)
    cv2.circle(mb, (cx,circle_y), 4, (255,0,0), 1)
    cv2.circle(ma, (cx,cy), 6, (0,255,255), 2)
    cv2.circle(ma, (cx,circle_y), 4, (255,0,0), 1)
cv2.imwrite(os.path.join(OUT, 'orig_before.png'), mb)
cv2.imwrite(os.path.join(OUT, 'orig_after.png'), ma)
print("截图: orig_before.png / orig_after.png")
