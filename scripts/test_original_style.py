"""完全复制 test_move_correct.py 的格式，只改目标方向为向下"""
import sys, os, time, subprocess
import numpy as np, cv2

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB  = r"D:\MuMuPlayer\nx_main\adb.exe"
DEVICE = "127.0.0.1:7555"

def screencap():
    result = subprocess.run(
        [ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
        capture_output=True, timeout=5
    )
    frame = np.frombuffer(result.stdout, dtype=np.uint8)
    return cv2.imdecode(frame, cv2.IMREAD_COLOR)

def detect_allies(frame):
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (w // 2, h // 2))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([95, 100, 100]), np.array([115, 255, 255]))
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    units = []
    for i in range(1, n):
        area = stats[i, 4]
        if area < 10:
            continue
        units.append({
            "x": int(centroids[i, 0] * 2),
            "y": int(centroids[i, 1] * 2),
        })
    return units

def send_touch(commands: list[str]) -> bool:
    if not commands:
        return True
    batch = "; ".join(commands)
    result = subprocess.run(
        [MUMU, "control", "-v", "0", "tool", "cmd", "-c", batch],
        capture_output=True, text=True, timeout=10,
    )
    return "errcode: 0" in result.stdout or result.returncode == 0

# ================================================================
print("=" * 60)
print("🎮 原始格式测试 (向下移动)")

# --- 1. 截图 ---
print("\n📸 截图...")
frame = screencap()
h, w = frame.shape[:2]
print(f"   分辨率: {w}x{h}")

# --- 2. 检测 (不聚类，原始方式) ---
print("🔍 检测友军...")
allies = detect_allies(frame)
print(f"   检测到 {len(allies)} 个友军 (原始点)")

# 过滤：取屏幕上半部分、稍微分散的单位
# 每30px取一个点，避免重复选同一班组
filtered = []
used_x = set()
for a in sorted(allies, key=lambda u: u["x"]):
    bx = a["x"] // 50  # 50px分桶
    if bx not in used_x:
        filtered.append(a)
        used_x.add(bx)
print(f"   过滤后 {len(filtered)} 个分散单位")

for i, a in enumerate(filtered[:8]):
    print(f"   单位#{i+1}: ({a['x']}, {a['y']})")

if not filtered:
    print("⚠️ 无友军检测到")
    sys.exit(1)

# --- 3. 保存 ---
os.makedirs("screenshots", exist_ok=True)
cv2.imwrite("screenshots/before_down.png", frame)

# --- 4. 发送指令 (完全按原始格式) ---
print(f"\n🎯 发送指令: 选中 → 向下拖拽")
target_y = int(h * 0.80)  # 屏幕下方80%处

cmds = []
test_units = filtered[:6]  # 测试6个

for i, unit in enumerate(test_units):
    ux, uy = unit["x"], unit["y"]
    circle_y = uy + 35  # 原始偏移

    # 目标点稍微分散
    tx = min(w - 30, max(30, ux + (i - 2) * 50))
    ty = target_y

    cmds.append(f"input tap {ux} {uy}")
    cmds.append(f"input swipe {ux} {circle_y} {tx} {ty} 500")
    print(f"   单位#{i+1}: 选中({ux},{uy}) → 拖拽({ux},{circle_y})→({tx},{ty})")

# 发送所有命令
ok = send_touch(cmds)
print(f"\n   发送 {len(cmds)} 条指令 (1次调用): {'✅ OK' if ok else '⚠️ '}")

# --- 5. 等2.5秒 ---
time.sleep(2.5)

# --- 6. 截图对比 ---
frame2 = screencap()
cv2.imwrite("screenshots/after_down.png", frame2)

# 像素对比
diff = cv2.absdiff(frame, frame2)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
changed = np.count_nonzero(gray > 30)
pct = changed / gray.size * 100
print(f"\n📊 像素变化: {pct:.2f}%")

# 单位位置对比
after_allies = detect_allies(frame2)
print(f"   后帧友军: {len(after_allies)} 个")
if after_allies:
    # 找对应单位
    before_positions = [(u["x"], u["y"]) for u in test_units]
    for i, (bx, by) in enumerate(before_positions):
        # 找最近的后帧单位
        nearest = min(after_allies, key=lambda a: (a["x"]-bx)**2 + (a["y"]-by)**2)
        dist = ((nearest["x"]-bx)**2 + (nearest["y"]-by)**2)**0.5
        marker = "✅" if dist > 60 else "⚠️" if dist > 20 else "❌"
        print(f"   单位#{i+1}: ({bx},{by}) → (~{nearest['x']},{nearest['y']}) {dist:.0f}px {marker}")

print("\n" + "=" * 60)
print("请确认: 单位是否向下移动了?")
print("截图: screenshots/before_down.png vs after_down.png")
