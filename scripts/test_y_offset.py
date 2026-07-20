"""猜测: 颜色检测到的蓝点在单位图标上方, 但单位实体在下方。
尝试在不同 Y 偏移位置 tap 和 swipe。
"""
import sys, time, subprocess
import cv2, numpy as np

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB  = r"D:\MuMuPlayer\nx_main\adb.exe"

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def get_one_unit(img):
    h, w = img.shape[:2]
    small = cv2.resize(img, (w//2, h//2))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    for i in range(1, len(stats)):
        if 50 < stats[i,4] < 3000:
            return int(centroids[i][0]*2), int(centroids[i][1]*2)
    return None

print("=" * 60)
print("🧪 测试不同 tap 偏移位置")
before = capture()
h, w = before.shape[:2]
unit = get_one_unit(before)
if not unit:
    print("❌ 无单位"); sys.exit(1)
ux, uy = unit
print(f"检测到蓝色标记中心: ({ux}, {uy})")

# 测试 5 种偏移
offsets = [
    ("+0(原始)",  0),
    ("+35",      35),
    ("+60",      60),
    ("+90",      90),
    ("+120",    120),
]

for label, offset in offsets:
    ty = uy + offset
    target_y = max(10, ty - 150)

    # 单独的 tap, 然后用单独的 swipe
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c",
                    f"input tap {ux} {ty}"],
                   capture_output=True, text=True, timeout=5)
    time.sleep(0.6)

    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c",
                    f"input swipe {ux} {ty+20} {ux} {target_y} 800"],
                   capture_output=True, text=True, timeout=5)

    time.sleep(1.5)

    after = capture()
    diff = cv2.absdiff(before, after)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    changed = np.count_nonzero(gray > 30)
    pct = changed / gray.size * 100

    cv2.imwrite(f"screenshots/offset_{offset}_after.png", after)
    print(f"\ntap偏移 {label} @({ux},{ty}): 像素变化 {pct:.2f}%")

    before = after  # 用当前帧做下一次的参考

print(f"\n请确认哪一次偏移量让单位真正移动了（不是镜头平移）")
print("截图: screenshots/offset_*_after.png")
