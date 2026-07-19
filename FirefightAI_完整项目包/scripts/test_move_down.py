"""测试向下方拖拽——之前单位都移到屏幕上方了"""
import sys, time, subprocess, math
import cv2, numpy as np

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB  = r"D:\MuMuPlayer\nx_main\adb.exe"

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def detect_and_cluster(img, eps=50):
    h, w = img.shape[:2]
    small = cv2.resize(img, (w//2, h//2))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    n, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    pts = []
    for i in range(1, n):
        if stats[i,4] >= 10:
            pts.append((int(centroids[i][0])*2, int(centroids[i][1])*2))
    if len(pts) <= 1:
        return pts
    clusters, used = [], set()
    for i, p in enumerate(pts):
        if i in used: continue
        c = [p]; used.add(i)
        for j, q in enumerate(pts):
            if j not in used and math.hypot(p[0]-q[0], p[1]-q[1]) < eps:
                c.append(q); used.add(j)
        cx = sum(t[0] for t in c)//len(c)
        cy = sum(t[1] for t in c)//len(c)
        clusters.append((cx, cy, len(c)))
    clusters.sort(key=lambda x: x[0])  # 按X排序
    return clusters

print("📸 截图...")
before = capture()
h, w = before.shape[:2]
units = detect_and_cluster(before)
print(f"检测到 {len(units)} 个聚类单位")
for i, (cx, cy, n) in enumerate(units):
    print(f"  单位#{i+1}: ({cx:4d}, {cy:4d}) 成员={n}")

# 取中间5个单位，向下移动
target_units = units[:5]
print(f"\n🎯 向下移动 {len(target_units)} 个单位")

# 【复制 test_move_correct.py 的精确格式】
cmds = []
for i, (ux, uy, n) in enumerate(target_units):
    circle_y = uy + 35
    tx = ux
    ty = uy + 300  # 向下300px
    cmds.append(f"input tap {ux} {uy}")
    cmds.append(f"input swipe {ux} {circle_y} {tx} {ty} 500")
    print(f"  #{i+1}: tap({ux},{uy}) → swipe({ux},{circle_y})→({tx},{ty})")

batch = "; ".join(cmds)
t0 = time.time()
r = subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", batch],
                   capture_output=True, text=True, timeout=10)
ok = "errcode: 0" in r.stdout
print(f"  发送 {len(cmds)} 条: {'✅' if ok else '❌'} | {(time.time()-t0)*1000:.0f}ms")

time.sleep(2.5)
after = capture()

cv2.imwrite("screenshots/move_down_before.png", before)
cv2.imwrite("screenshots/move_down_after.png", after)

diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
changed = np.count_nonzero(gray > 30)
pct = changed / gray.size * 100
print(f"像素变化: {pct:.2f}%")

# 检查单位位置是否变化
after_units = detect_and_cluster(after)
print(f"\n📊 位置对比:")
for i in range(min(len(units), 8)):
    bx, by = units[i][0], units[i][1]
    ax, ay = after_units[i][0] if i < len(after_units) else (0,0)
    if i < len(after_units):
        dist = math.hypot(bx-ax, by-ay)
        print(f"  单位#{i+1}: ({bx},{by}) → ({ax},{ay}) | 位移: {dist:.0f}px")
    else:
        print(f"  单位#{i+1}: 消失?")

print("\n请到游戏里确认——单位向下移动了吗?")
