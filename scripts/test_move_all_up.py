"""测试: 命令所有友军向正上方移动

验证 MuMuManager 触控能否真正控制游戏单位
跳过AI决策, 直接: 截图 → 检测 → 向上移动
"""
import sys
import time
import subprocess
import numpy as np
sys.path.insert(0, "C:/Users/19853/WorkBuddy/2026-07-18-07-52-25/firefightAI")

# --- 1. 截图 ---
print("=" * 60)
print("📸 截图...")
t0 = time.time()
result = subprocess.run(
    [r"d:\firefight\adb\adb.exe", "-s", "127.0.0.1:7555",
     "exec-out", "screencap", "-p"],
    capture_output=True, timeout=5
)
frame = np.frombuffer(result.stdout, dtype=np.uint8)
frame = np.frombuffer(result.stdout, dtype=np.uint8)
import cv2
frame = cv2.imdecode(frame, cv2.IMREAD_COLOR)
if frame is None:
    print("❌ 截图失败!")
    sys.exit(1)

h, w = frame.shape[:2]
print(f"   分辨率: {w}x{h} | 耗时: {(time.time()-t0)*1000:.0f}ms")

# --- 2. 检测友军 ---
print("🔍 检测友军...")
t1 = time.time()
small = cv2.resize(frame, (w // 2, h // 2))
hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

ALLY_LOW = np.array([95, 100, 100])
ALLY_HIGH = np.array([115, 255, 255])
ally_mask = cv2.inRange(hsv, ALLY_LOW, ALLY_HIGH)

n, labels, stats, centroids = cv2.connectedComponentsWithStats(ally_mask, 8)
allies = []
for i in range(1, n):
    area = stats[i, 4]
    if area < 10:
        continue
    allies.append({
        "x": int(centroids[i, 0] * 2),  # 还原到原始分辨率
        "y": int(centroids[i, 1] * 2),
        "area": area,
    })

allies.sort(key=lambda u: u["y"])  # 从上到下排序
print(f"   检测到 {len(allies)} 个友军 | 耗时: {(time.time()-t1)*1000:.0f}ms")

if len(allies) == 0:
    print("⚠️ 没有检测到友军, 仅发送测试点击")
    allies = [{"x": w // 2, "y": h // 2}]

# 打印前5个单位坐标
for i, a in enumerate(allies[:5]):
    print(f"   单位#{i+1}: ({a['x']}, {a['y']}) 面积={a['area']}")

# --- 3. 保存截图(前) ---
before_path = "C:/Users/19853/WorkBuddy/2026-07-18-07-52-25/firefightAI/screenshots/before_move_up.png"
import os
os.makedirs(os.path.dirname(before_path), exist_ok=True)
cv2.imwrite(before_path, frame)
print(f"\n📷 截图已保存: {before_path}")

# --- 4. 向正上方移动 ---
print("\n🎮 发送移动指令: 所有单位 → 正上方")
MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"

# 目标: 屏幕正上方中央 (x=w/2, y=h*0.1)
target_y = int(h * 0.10)

cmds = []
# 策略: 取前10个友军 (避免命令太长)
for a in allies[:10]:
    ux, uy = a["x"], a["y"]
    # 框选该单位 (小范围swipe模拟选择)
    x1 = max(0, ux - 40)
    y1 = max(0, uy - 40)
    x2 = min(w, ux + 40)
    y2 = min(h, uy + 40)
    # 每个单位分开一点, 避免堆叠
    tx = min(w - 20, max(20, w // 2 + (i - len(allies)//2) * 60))
    ty = target_y
    cmds.append(f"input swipe {x1} {y1} {x2} {y2} 100")
    cmds.append(f"input tap {tx} {ty}")

batch_cmd = "; ".join(cmds)

t2 = time.time()
result = subprocess.run(
    [MUMU, "control", "-v", "0", "tool", "cmd", "-c", batch_cmd],
    capture_output=True, text=True, timeout=10,
)

elapsed = (time.time() - t2) * 1000
ok = "errcode: 0" in result.stdout or result.returncode == 0
print(f"   命令: {len(cmds)}条指令")
print(f"   结果: {'✅ OK' if ok else '❌ FAIL'} | {elapsed:.0f}ms")
if not ok:
    print(f"   stdout: {result.stdout[:200]}")
    print(f"   stderr: {result.stderr[:200]}")

# --- 5. 等待一下再截图(后) ---
time.sleep(1.5)

print("\n📸 截图(后)...")
result2 = subprocess.run(
    [r"d:\firefight\adb\adb.exe", "-s", "127.0.0.1:7555",
     "exec-out", "screencap", "-p"],
    capture_output=True, timeout=5
)
frame2 = np.frombuffer(result2.stdout, dtype=np.uint8)
frame2 = cv2.imdecode(frame2, cv2.IMREAD_COLOR)

after_path = "C:/Users/19853/WorkBuddy/2026-07-18-07-52-25/firefightAI/screenshots/after_move_up.png"
cv2.imwrite(after_path, frame2)
print(f"   已保存: {after_path}")

# --- 6. 像素对比 ---
diff = cv2.absdiff(frame, frame2)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
changed = np.count_nonzero(gray > 30)
total = gray.size
pct = changed / total * 100
print(f"\n📊 像素变化: {changed:,} / {total:,} = {pct:.2f}%")

# 分析变化区域
_, thresh = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
large_regions = [c for c in contours if cv2.contourArea(c) > 500]
print(f"   显著变化区域 (>{500}px²): {len(large_regions)}个")

if pct > 1.0 and len(large_regions) > 3:
    print("\n✅ 画面有明显变化, 触控很可能生效了!")
elif pct > 0.3:
    print("\n⚠️ 画面有轻微变化, 可能部分生效")
else:
    print("\n❌ 画面几乎无变化, 触控未生效或游戏暂停中")

print("=" * 60)
print("请检查游戏画面, 确认单位是否向上移动")
print(f"对比截图: {before_path} vs {after_path}")
