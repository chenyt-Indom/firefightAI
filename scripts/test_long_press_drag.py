"""根据APK分析: "Tap and hold screen to select"
游戏需要一次连续手势: touch_down→hold→move→up
不能用 input tap + input swipe 两次分离的手势!
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
print("🧪 单次长按拖拽: input swipe 起点=单位, duration=3000ms")
print("   模拟: touch_down → hold → drag → up (一次完整手势)")

before = capture()
h, w = before.shape[:2]
unit = get_one_unit(before)
if not unit:
    print("❌ 无单位"); sys.exit(1)
ux, uy = unit
print(f"目标单位: ({ux}, {uy})")

# 从单位下方35px拖到下方300px, 持续3秒 (模拟长按+拖拽)
start_y = uy + 35
target_y = min(h - 30, uy + 250)

cmd = f"input swipe {ux} {start_y} {ux} {target_y} 3000"
print(f"\n指令: {cmd}")
print("这模拟: 手指按住单位下方35px 3秒 → 慢慢往下拖250px → 松开")

subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
               capture_output=True, text=True, timeout=10)

print("等待游戏响应 (3秒)...")
time.sleep(3.0)

after = capture()
cv2.imwrite("screenshots/long_press_before.png", before)
cv2.imwrite("screenshots/long_press_after.png", after)

diff = cv2.absdiff(before, after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
changed = np.count_nonzero(gray > 30)
pct = changed / gray.size * 100
print(f"像素变化: {pct:.2f}%")

# 也测试从单位中心开始
time.sleep(1.0)
print("\n--- 第二轮: 从单位中心开始 ---")
unit2 = get_one_unit(after)  # 找当前画面的单位
if unit2:
    ux2, uy2 = unit2
    target_y2 = min(h - 30, uy2 + 250)
    before2 = after
    cmd2 = f"input swipe {ux2} {uy2} {ux2} {target_y2} 3000"
    print(f"指令: {cmd2}")
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd2],
                   capture_output=True, text=True, timeout=10)
    time.sleep(3.0)
    after2 = capture()
    cv2.imwrite("screenshots/long_press2_after.png", after2)
    diff2 = cv2.absdiff(before2, after2)
    gray2 = cv2.cvtColor(diff2, cv2.COLOR_BGR2GRAY)
    pct2 = np.count_nonzero(gray2 > 30) / gray2.size * 100
    print(f"像素变化: {pct2:.2f}%")

print(f"\n请确认: 单位是在移动还是镜头在平移?")
