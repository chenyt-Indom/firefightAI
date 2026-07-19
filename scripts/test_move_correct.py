"""测试: 正确触控方式 — 选中单位后从圆圈拖拽移动

Firefight 控制逻辑:
1. 点击单位 → 选中 (蓝线+圆圈出现在单位下方)
2. 从圆圈拖拽到目标位置 → 移动
3. 点击空地 = 平移地图 (不是移动单位!)
"""
import sys, os, time, subprocess
import numpy as np
import cv2

sys.path.insert(0, "C:/Users/19853/WorkBuddy/2026-07-18-07-52-25/firefightAI")

MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB = r"d:\firefight\adb\adb.exe"
DEVICE = "127.0.0.1:7555"

def screencap():
    """管道截图"""
    result = subprocess.run(
        [ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
        capture_output=True, timeout=5
    )
    frame = np.frombuffer(result.stdout, dtype=np.uint8)
    return cv2.imdecode(frame, cv2.IMREAD_COLOR)

def detect_allies(frame):
    """HSV颜色检测友军"""
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
    """批量发送 MuMuManager 触控指令"""
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
print("🎮 正确触控测试: 点击选中 → 从圆圈拖拽移动")

# --- 1. 截图 ---
print("\n📸 截图...")
frame = screencap()
h, w = frame.shape[:2]
print(f"   分辨率: {w}x{h}")

# --- 2. 检测 ---
print("🔍 检测友军...")
allies = detect_allies(frame)
print(f"   检测到 {len(allies)} 个友军")
for i, a in enumerate(allies[:5]):
    print(f"   单位#{i+1}: ({a['x']}, {a['y']})")

if not allies:
    print("⚠️ 无友军检测到, 退出")
    sys.exit(1)

# --- 3. 保存截图(前) ---
os.makedirs("screenshots", exist_ok=True)
cv2.imwrite("screenshots/before_correct.png", frame)

# --- 4. 正确触控: 选中+拖拽 ---
# 取前5个单位测试 (带编号标记方便观察)
print("\n🎯 发送指令: 选中 → 向正上方拖拽")
target_y = int(h * 0.12)  # 目标: 屏幕上方

cmds = []
test_units = allies[:5]  # 只测试前5个

for i, unit in enumerate(test_units):
    ux, uy = unit["x"], unit["y"]

    # 圆圈在单位下方约 25-50 像素处 (蓝线中间)
    # 尝试几个偏移量, 测试哪个最有效
    circle_y = uy + 35  # 圆圈预估在单位下方35px

    # 目标: 正上方 (稍微分散避免堆叠)
    tx = min(w - 30, max(30, ux + (i - 2) * 40))
    ty = target_y

    # 步骤1: 点击单位选中 (短暂点击)
    cmds.append(f"input tap {ux} {uy}")

    # 步骤2: 从圆圈拖拽到目标 (长按拖拽)
    cmds.append(f"input swipe {ux} {circle_y} {tx} {ty} 500")

    print(f"   单位#{i+1}: 选中({ux},{uy}) → "
          f"拖拽({ux},{circle_y})→({tx},{ty})")

# 发送所有命令
ok = send_touch(cmds)
print(f"\n   发送 {len(cmds)} 条指令: {'✅ OK' if ok else '❌ FAIL'}")

# --- 5. 等待后截图对比 ---
time.sleep(2.0)

print("\n📸 截图(后)...")
frame2 = screencap()
cv2.imwrite("screenshots/after_correct.png", frame2)

# --- 6. 像素对比 ---
diff = cv2.absdiff(frame, frame2)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
changed = np.count_nonzero(gray > 30)
pct = changed / gray.size * 100

print(f"\n📊 像素变化: {changed:,} / {gray.size:,} = {pct:.2f}%")
if pct > 2:
    print("✅ 画面有变化")
else:
    print("⚠️ 画面变化较小")

print("\n" + "=" * 60)
print("请观察游戏画面:")
print("  1. 单位是否被选中 (蓝线出现)?")
print("  2. 单位是否向上移动?")
print("  3. 如果无效, 告诉我圆圈大概在单位下方多少像素")
print(f"对比截图: screenshots/before_correct.png vs after_correct.png")
