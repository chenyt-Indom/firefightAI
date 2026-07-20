"""用 50px 偏移量测试：选中所有单位 + 从圆圈位置向上拖拽"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2, numpy as np, subprocess, time

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB = r"D:\MuMuPlayer\nx_main\adb.exe"
OFFSET = 50  # 圆圈在单位中心下方 50px

def mumu(cmd):
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
                   capture_output=True, text=True, timeout=30)

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def detect_allies(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    units = []
    for i in range(1, min(200, len(stats))):
        if 50 < stats[i,4] < 2000:
            units.append((int(centroids[i][0]), int(centroids[i][1])))
    return units

# === 主流程 ===
print("截图...")
before = capture()

units = detect_allies(before)
print(f"检测到 {len(units)} 个友军")

# 分批执行，每批10个单位（20条命令）
CHUNK = 10
total_sent = 0
for i in range(0, min(len(units), 30), CHUNK):
    chunk_units = units[i:i+CHUNK]
    cmds = []
    for cx, cy in chunk_units:
        circle_y = cy + OFFSET
        target_y = max(10, circle_y - 200)
        cmds.append(f"input tap {cx} {cy}")
        cmds.append(f"input swipe {cx} {circle_y} {cx} {target_y} 500")
    batch = "; ".join(cmds)
    print(f"  批次 {i//CHUNK+1}: {len(chunk_units)} 个单位...")
    mumu(batch)
    total_sent += len(chunk_units)
    time.sleep(0.3)

print(f"执行完成, 共 {total_sent} 个单位")

time.sleep(1.0)
after = capture()

# 像素对比
diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
_, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
changed = np.sum(thresh > 0)
total = thresh.size

print(f"\n像素变化: {changed/total*100:.2f}% ({changed}/{total})")

# 保存截图
cv2.imwrite("screenshots/before_50px.png", before)
cv2.imwrite("screenshots/after_50px.png", after)
print("截图已保存: screenshots/before_50px.png / after_50px.png")
