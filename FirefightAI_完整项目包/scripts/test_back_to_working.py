"""回到最初成功的方法：MuMuManager单次调用+分号分隔batch，使用校准后的圆圈偏移"""
import cv2, numpy as np, subprocess, time, os

ADB = r'D:\MuMuPlayer\nx_main\adb.exe'
MUMU = r'D:\MuMuPlayer\nx_main\MuMuManager.exe'
OUT = r'C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\screenshots'

# 校准参数
DX = 30   # 圆圈在单位右侧30px
DY = 61   # 圆圈在单位下方61px

def capture():
    r = subprocess.run([ADB, '-s', '127.0.0.1:7555', 'exec-out', 'screencap', '-p'],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def detect_allies(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    units = []
    for i in range(1, min(200, len(stats))):
        area = stats[i, 4]
        if 30 < area < 3000:  # 滤掉太小(噪声)和太大(连片)
            cx, cy = int(centroids[i][0]), int(centroids[i][1])
            units.append((cx, cy))
    return cluster(units, eps=50)

def cluster(points, eps=50):
    """简单距离聚类，返回每组的最近实际点"""
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
                group.append(p2)
                used.add(j)
        # 取聚类中离几何中心最近的点（保证点在真实士兵上）
        avg = np.mean(group, axis=0)
        best = min(group, key=lambda p: np.hypot(p[0]-avg[0], p[1]-avg[1]))
        groups.append((int(best[0]), int(best[1]), len(group)))
    return groups

def mumu_batch(cmds):
    """单次 MuMuManager 调用执行所有命令（分批，每批最多10条避免超时）"""
    total_ok = True
    total_elapsed = 0
    batch_size = 10  # 5个单位 = 10条命令
    for i in range(0, len(cmds), batch_size):
        batch = "; ".join(cmds[i:i+batch_size])
        t0 = time.time()
        r = subprocess.run([MUMU, 'control', '-v', '0', 'tool', 'cmd', '-c', batch],
                           capture_output=True, text=True, timeout=15)
        elapsed = (time.time() - t0) * 1000
        ok = "errcode: 0" in r.stdout
        total_ok = total_ok and ok
        total_elapsed += elapsed
    return total_ok, total_elapsed

print("=" * 60)
print("回到最初成功方法: MuMuManager 单次batch (tap选中 + swipe拖拽)")
print(f"圆圈偏移: dx={DX}, dy={DY}")
print("=" * 60)

# 截图
before = capture()
units = detect_allies(before)
print(f"检测到 {len(units)} 个聚类单位")

if not units:
    print("无单位!")
    exit()

# 构建命令：每个单位 tap选中 → swipe拖拽到上方250px
cmds = []
for i, (cx, cy, n) in enumerate(units):
    circle_x = cx + DX
    circle_y = cy + DY
    target_y = max(10, circle_y - 250)
    cmds.append(f"input tap {cx} {cy}")
    cmds.append(f"input swipe {circle_x} {circle_y} {circle_x} {target_y} 800")
    
    if i < 5:
        print(f"  单位{i+1}: tap({cx},{cy}) → swipe({circle_x},{circle_y})→({circle_x},{target_y})")

# 单次执行
print(f"\n执行 {len(units)} 个单位, {len(cmds)} 条命令...")
ok, elapsed = mumu_batch(cmds)
print(f"执行: {'OK' if ok else 'FAIL'} ({elapsed:.0f}ms)")

# 等命令生效
time.sleep(2.0)

# 截图对比
after = capture()

# 分析
diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
_, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
change_pct = np.count_nonzero(thresh) / (before.shape[0] * before.shape[1]) * 100

print(f"\n画面变化: {change_pct:.2f}%")

# 保存对比图
mb = before.copy()
ma = after.copy()
for cx, cy, n in units:
    circle_x = cx + DX
    circle_y = cy + DY
    target_y = max(10, circle_y - 250)
    cv2.circle(mb, (cx, cy), 8, (0, 255, 255), 2)
    cv2.circle(mb, (circle_x, circle_y), 5, (255, 0, 0), 2)
    cv2.circle(ma, (cx, cy), 8, (0, 255, 255), 2)
    cv2.circle(ma, (circle_x, circle_y), 5, (255, 0, 0), 2)

cv2.imwrite(os.path.join(OUT, 'back_before.png'), mb)
cv2.imwrite(os.path.join(OUT, 'back_after.png'), ma)

print("截图: back_before.png / back_after.png")
print("\n黄圈=单位, 蓝点=圆圈位置")
print("看一下单位有没有向上移动!")
