"""核心测试: tap后等0.5s再swipe — 防止被当成镜头平移"""
import sys, time, subprocess
import cv2, numpy as np

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB  = r"D:\MuMuPlayer\nx_main\adb.exe"

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def detect_allies(frame):
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (w//2, h//2))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    pts = []
    for i in range(1, len(stats)):
        if stats[i,4] >= 10:
            pts.append((int(centroids[i][0])*2, int(centroids[i][1])*2))
    return pts

print("=" * 60)
print("🧪 tap→等→swipe 分离测试")
before = capture()
allies = detect_allies(before)
print(f"检测到 {len(allies)} 友军点")

# 取中间4个分散点
filtered = []
buckets = set()
for a in sorted(allies, key=lambda p: p[0]):
    b = a[0] // 80
    if b not in buckets:
        filtered.append(a)
        buckets.add(b)
units = filtered[:4]
for i, (x, y) in enumerate(units):
    print(f"  #{i+1}: ({x}, {y})")

h, w = before.shape[:1]

# === 方案: 先批量tap选所有 → 等0.5s → 再批量swipe ===
print("\n[1/3] 批量选中所有单位...")
tap_cmds = [f"input tap {x} {y}" for x, y in units]
batch_tap = "; ".join(tap_cmds)
subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", batch_tap],
               capture_output=True, text=True, timeout=5)
print(f"  发送 {len(tap_cmds)} 条 tap")

print("[2/3] 等待 0.5s 让游戏注册选中...")
time.sleep(0.5)

print("[3/3] 批量向下拖拽...")
swipe_cmds = []
for i, (ux, uy) in enumerate(units):
    target_x = ux + (i - 1) * 60
    target_y = min(h - 30, uy + 200)
    swipe_cmds.append(f"input swipe {ux} {uy+35} {target_x} {target_y} 500")
batch_swipe = "; ".join(swipe_cmds)
subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", batch_swipe],
               capture_output=True, text=True, timeout=5)
print(f"  发送 {len(swipe_cmds)} 条 swipe")

time.sleep(2.5)
after = capture()

cv2.imwrite("screenshots/delayed_before.png", before)
cv2.imwrite("screenshots/delayed_after.png", after)

diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
changed = np.count_nonzero(gray > 30)
pct = changed / gray.size * 100
print(f"\n📊 像素变化: {pct:.2f}%")

print("\n请确认: 单位移动了还是镜头平移了?")
