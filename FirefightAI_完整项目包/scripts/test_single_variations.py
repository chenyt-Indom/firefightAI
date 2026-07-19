"""单单位测试: 对比不同拖拽参数

每次只操作1个单位 (避免多单位混淆), 依次测试:
  A: 从单位中心向上 300px, 500ms
  B: 从单位下方 35px 向上 300px, 500ms  
  C: 从单位下方 35px 向上 300px, 1000ms (长按)
  D: 从单位下方 35px 向上 600px, 500ms (长距离)

请在每次测试后观察游戏画面, 告诉我哪个有效。
"""
import sys, os, time, math, subprocess
import cv2, numpy as np

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB  = r"D:\MuMuPlayer\nx_main\adb.exe"

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def detect_one_ally(img):
    """只取最后一个检测到的友军单位 (屏幕最下方, 方便观察)"""
    h, w = img.shape[:2]
    small = cv2.resize(img, (w//2, h//2))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    pts = []
    for i in range(1, len(stats)):
        if stats[i,4] >= 10:
            pts.append((int(centroids[i][0])*2, int(centroids[i][1])*2, stats[i,4]))
    if not pts:
        return None
    # 取最下方的单位
    pts.sort(key=lambda p: p[1], reverse=True)
    return pts[0]  # (x, y, area)

def try_method(label, tap_x, tap_y, swipe_x1, swipe_y1, swipe_x2, swipe_y2, duration):
    """测试一种拖拽方式, 返回像素变化"""
    before = capture()
    cv2.imwrite(f"screenshots/test_{label}_before.png", before)

    # tap 选中
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", f"input tap {tap_x} {tap_y}"],
                   capture_output=True, text=True, timeout=5)
    time.sleep(0.3)

    # swipe 拖拽
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c",
                    f"input swipe {swipe_x1} {swipe_y1} {swipe_x2} {swipe_y2} {duration}"],
                   capture_output=True, text=True, timeout=5)

    time.sleep(2.0)
    after = capture()
    cv2.imwrite(f"screenshots/test_{label}_after.png", after)

    diff = cv2.absdiff(before, after)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    changed = np.count_nonzero(gray > 30)
    pct = changed / gray.size * 100
    return pct

# ── 主流程 ──
print("=" * 60)
print("🧪 单单位拖拽参数对比")
os.makedirs("screenshots", exist_ok=True)

# 找一个单位
img = capture()
unit = detect_one_ally(img)
if unit is None:
    print("❌ 没检测到友军!")
    sys.exit(1)

ux, uy, area = unit
print(f"\n测试单位: ({ux}, {uy}) 面积={area}")
target_y = max(10, uy - 300)

results = []

# A: 从中心拖, 500ms
print(f"\n{'='*40}")
print(f"测试A: 从单位中心({ux},{uy}) 拖到 ({ux},{target_y}), 500ms")
print(f"请观察: 蓝线是否出现? 单位是否向上移动?")
input("准备好后按 Enter...")
pct = try_method("A", ux, uy, ux, uy, ux, target_y, 500)
results.append(("A: 中心→上300, 500ms", pct))
print(f"像素变化: {pct:.2f}%")

# B: 从下方35, 500ms
print(f"\n{'='*40}")
print(f"测试B: 从下方({ux},{uy+35}) 拖到 ({ux},{target_y}), 500ms")
input("准备好后按 Enter...")
pct = try_method("B", ux, uy, ux, uy+35, ux, target_y, 500)
results.append(("B: 下方35→上300, 500ms", pct))
print(f"像素变化: {pct:.2f}%")

# C: 从下方35, 1000ms
print(f"\n{'='*40}")
print(f"测试C: 从下方({ux},{uy+35}) 拖到 ({ux},{target_y}), 1000ms")
input("准备好后按 Enter...")
pct = try_method("C", ux, uy, ux, uy+35, ux, target_y, 1000)
results.append(("C: 下方35→上300, 1000ms", pct))
print(f"像素变化: {pct:.2f}%")

# D: 长距离
target_far = max(10, uy - 600)
print(f"\n{'='*40}")
print(f"测试D: 从下方({ux},{uy+35}) 拖到 ({ux},{target_far}), 500ms (长距离)")
input("准备好后按 Enter...")
pct = try_method("D", ux, uy, ux, uy+35, ux, target_far, 500)
results.append(("D: 下方35→上600, 500ms", pct))
print(f"像素变化: {pct:.2f}%")

# ── 总结 ──
print(f"\n{'='*60}")
print("📊 结果汇总:")
for label, pct in results:
    bar = "█" * int(pct * 5) if pct > 0 else ""
    print(f"  {label}: {pct:.2f}% {bar}")
print("\n哪个测试让单位移动了? 告诉我编号 (A/B/C/D)")
print("=" * 60)
