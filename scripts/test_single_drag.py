"""单单位拖拽测试：尝试不同策略找到正确的移动方式"""
import cv2, numpy as np, subprocess, time, os

ADB = r"D:\MuMuPlayer\nx_main\adb.exe"
MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
OUT = r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\screenshots"

def capture():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

def detect_allies(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95,100,100]), np.array([115,255,255]))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pts = []
    for c in contours:
        area = cv2.contourArea(c)
        if 50 < area < 2000:
            M = cv2.moments(c)
            if M["m00"] > 0:
                pts.append((int(M["m10"]/M["m00"]), int(M["m01"]/M["m00"])))
    return pts

def mumu(cmd):
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
                   capture_output=True, text=True, timeout=5)

# 取第一个单位
before = capture()
units = detect_allies(before)
if not units:
    print("无友军!")
    exit(1)

ux, uy = units[0]
print(f"目标单位: ({ux}, {uy})")

# ── 策略1: 长按圆圈再拖拽 (swipe 1000ms) ──
print("\n--- 策略1: swipe 1000ms, 上移200px ---")
mumu(f"input tap {ux} {uy}")
time.sleep(0.6)
# 尝试不同偏移: (30,61), (0,50), (0,40), (0,30)
for label, dx, dy in [("A", 30, 61), ("B", 0, 50), ("C", 0, 40), ("D", 0, 60)]:
    # 每次重新截图确保状态
    before_test = capture()
    cx, cy = ux + dx, uy + dy
    ty = max(10, uy - 200)
    mumu(f"input tap {ux} {uy}")
    time.sleep(0.5)
    mumu(f"input swipe {cx} {cy} {ux} {ty} 1000")
    time.sleep(1.0)
    after_test = capture()
    
    # 蓝色变化
    roi_b = before_test[uy-20:uy+120, ux-60:ux+60]
    roi_a = after_test[uy-20:uy+120, ux-60:ux+60]
    hsv_b = cv2.cvtColor(roi_b, cv2.COLOR_BGR2HSV)
    hsv_a = cv2.cvtColor(roi_a, cv2.COLOR_BGR2HSV)
    m_b = cv2.inRange(hsv_b, np.array([95,100,100]), np.array([130,255,255]))
    m_a = cv2.inRange(hsv_a, np.array([95,100,100]), np.array([130,255,255]))
    blue_b = np.count_nonzero(m_b)
    blue_a = np.count_nonzero(m_a)
    
    diff = cv2.absdiff(before_test, after_test)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
    change_pct = np.count_nonzero(thresh) / (before_test.shape[0]*before_test.shape[1]) * 100
    
    print(f"  {label}) offset=({dx},{dy}): 蓝线{blue_b}→{blue_a}({'+' if blue_a>=blue_b else ''}{blue_a-blue_b}), 像素变化{change_pct:.2f}%", end="")
    if blue_a - blue_b > 100:
        print(" ⭐")
    elif blue_a - blue_b > 30:
        print(" ✓")
    elif change_pct > 2:
        print(" (地图平移?)")
    else:
        print()

# ── 策略2: 双击圆圈 + 点目标 (类似RTS操作) ──
print("\n--- 策略2: 先tap圆圈再tap目标 ---")
for label, dx, dy in [("A", 30, 61), ("B", 0, 50)]:
    before_test = capture()
    cx, cy = ux + dx, uy + dy
    ty = max(10, uy - 200)
    mumu(f"input tap {ux} {uy}")  # 选中
    time.sleep(0.5)
    mumu(f"input tap {cx} {cy}; input tap {ux} {ty}")  # 点圆圈→点目标
    time.sleep(1.0)
    after_test = capture()
    
    roi_b = before_test[uy-20:uy+120, ux-60:ux+60]
    roi_a = after_test[uy-20:uy+120, ux-60:ux+60]
    hsv_b = cv2.cvtColor(roi_b, cv2.COLOR_BGR2HSV)
    hsv_a = cv2.cvtColor(roi_a, cv2.COLOR_BGR2HSV)
    m_b = cv2.inRange(hsv_b, np.array([95,100,100]), np.array([130,255,255]))
    m_a = cv2.inRange(hsv_a, np.array([95,100,100]), np.array([130,255,255]))
    blue_b = np.count_nonzero(m_b)
    blue_a = np.count_nonzero(m_a)
    
    diff = cv2.absdiff(before_test, after_test)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
    change_pct = np.count_nonzero(thresh) / (before_test.shape[0]*before_test.shape[1]) * 100
    
    print(f"  {label}) 蓝线{blue_b}→{blue_a}, 像素变化{change_pct:.2f}%", end="")
    if blue_a - blue_b > 30:
        print(" ✓")
    else:
        print()
