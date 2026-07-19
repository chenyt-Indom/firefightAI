"""测试不同移动方式：选中后→点目标 / 拖拽 / 长按拖拽"""
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
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return []
    # 取中间位置的蓝点
    mid = len(xs) // 2
    idx = np.argsort(ys)
    return [(xs[idx[mid]], ys[idx[mid]])]

def mumu(cmd):
    subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
                   capture_output=True, text=True, timeout=5)

def adb_shell(cmd):
    subprocess.run([ADB, "-s", "127.0.0.1:7555", "shell", cmd],
                   capture_output=True, text=True, timeout=5)

def compare_blue(before, after, unit_x, unit_y):
    """对比前后蓝色像素变化"""
    w, h = 120, 140
    x1, y1 = max(0, unit_x-w//2), max(0, unit_y-20)
    x2, y2 = min(before.shape[1], unit_x+w//2), min(before.shape[0], unit_y+h)
    
    for img, name in [(before, "前"), (after, "后")]:
        roi = img[y1:y2, x1:x2]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv, np.array([95,100,100]), np.array([130,255,255]))
        if img is before:
            b_before = np.count_nonzero(m)
        else:
            b_after = np.count_nonzero(m)
    
    diff = cv2.absdiff(before, after)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
    change = np.count_nonzero(thresh) / (before.shape[0]*before.shape[1]) * 100
    
    return b_before, b_after, change

before = capture()
units = detect_allies(before)
if not units:
    print("无友军!")
    exit(1)

ux, uy = units[0]
ty_above = max(10, uy - 200)  # 上方200px
ty_below = min(before.shape[0]-10, uy + 200)  # 下方200px

print(f"单位: ({ux}, {uy}), 目标上={ty_above}, 目标下={ty_below}")

# ── 策略A: 选中 → tap 目标 (不拖拽) ──
print("\n--- A) 选中→tap目标 (RTS风格) ---")
before_a = capture()
mumu(f"input tap {ux} {uy}")
time.sleep(0.5)
mumu(f"input tap {ux} {ty_above}")
time.sleep(1.0)
after_a = capture()
bb, ba, ch = compare_blue(before_a, after_a, ux, uy)
print(f"  蓝线{bb}→{ba}({'+' if ba>=bb else ''}{ba-bb}), 像素变化{ch:.2f}%")

# ── 策略B: ADB touch事件模拟长按拖拽 ──  
print("\n--- B) ADB sendevent 模拟长按拖拽 ---")
before_b = capture()
mumu(f"input tap {ux} {uy}")
time.sleep(0.5)
# 使用 getevent 获取坐标映射
ads = [f"input motionevent DOWN {ux} {uy+50}",
       f"input motionevent MOVE {ux} {ty_above}",
       f"input motionevent UP {ux} {ty_above}"]
for cmd in ads:
    adb_shell(cmd)
    time.sleep(0.1)
time.sleep(1.0)
after_b = capture()
bb, ba, ch = compare_blue(before_b, after_b, ux, uy)
print(f"  蓝线{bb}→{ba}({'+' if ba>=bb else ''}{ba-bb}), 像素变化{ch:.2f}%")

# ── 策略C: 选中→长tap圆圈→再tap目标 (两步tap) ──
print("\n--- C) 选中→长按圆圈→点目标 ---")
before_c = capture()
mumu(f"input tap {ux} {uy}")
time.sleep(0.5)
mumu(f"input swipe {ux} {uy+50} {ux} {uy+50} 1000")  # 长按圆圈1秒
time.sleep(0.2)
mumu(f"input tap {ux} {ty_above}")  # 点目标
time.sleep(1.0)
after_c = capture()
bb, ba, ch = compare_blue(before_c, after_c, ux, uy)
print(f"  蓝线{bb}→{ba}({'+' if ba>=bb else ''}{ba-bb}), 像素变化{ch:.2f}%")

# ── 策略D: ADB 原生 input swipe (不是MuMuManager) ──
print("\n--- D) ADB 原生 swipe ---")
before_d = capture()
adb_shell(f"input tap {ux} {uy}")
time.sleep(0.5)
adb_shell(f"input swipe {ux} {uy+50} {ux} {ty_above} 1000")
time.sleep(1.5)
after_d = capture()
bb, ba, ch = compare_blue(before_d, after_d, ux, uy)
print(f"  蓝线{bb}→{ba}({'+' if ba>=bb else ''}{ba-bb}), 像素变化{ch:.2f}%")

# ── 策略E: 选中→拖到很远 (不一定是上方) ──
print("\n--- E) 选中→swipe到屏幕中央 ---")
before_e = capture()
mumu(f"input tap {ux} {uy}")
time.sleep(0.5)
mumu(f"input swipe {ux} {uy+50} 960 540 1000")  # 拖到屏幕中心
time.sleep(1.5)
after_e = capture()
bb, ba, ch = compare_blue(before_e, after_e, ux, uy)
print(f"  蓝线{bb}→{ba}({'+' if ba>=bb else ''}{ba-bb}), 像素变化{ch:.2f}%")
