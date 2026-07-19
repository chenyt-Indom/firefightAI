"""录制→解析→复现 用户拖拽手势
1. 截图(前) → 2. 录制getevent 5秒 → 3. 截图(后) → 4. 解析 → 5. 复现
"""
import subprocess, time, re, os, sys
import cv2, numpy as np

ADB = r"D:\MuMuPlayer\nx_main\adb.exe"
MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
EVENT_DEV = "/dev/input/event4"
OUT = r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\screenshots"
os.makedirs(OUT, exist_ok=True)

def cap():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

# ── 1. 截图前 ──
print("=" * 60)
print("🎬 录制准备")
before = cap()
cv2.imwrite(f"{OUT}/record_before.png", before)
print("📸 截图(前) 已保存")

# ── 2. 倒计时 ──
for i in [3, 2, 1]:
    print(f"   {i}...")
    time.sleep(1)

# ── 3. 录制 ──
print("🔴 开始录制! 现在拖拽一个单位移动!")
print("   (5秒后自动停止)")

# 先清空事件缓冲区
subprocess.run([ADB, "-s", "127.0.0.1:7555", "shell", "pkill", "-9", "getevent"],
               capture_output=True, timeout=3)
time.sleep(0.5)

proc = subprocess.Popen(
    [ADB, "-s", "127.0.0.1:7555", "shell", "getevent", "-lt", EVENT_DEV],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, bufsize=1
)

time.sleep(5.5)

# 杀掉 getevent
subprocess.run([ADB, "-s", "127.0.0.1:7555", "shell", "pkill", "-9", "getevent"],
               capture_output=True, timeout=3)
proc.terminate()

try:
    stdout, stderr = proc.communicate(timeout=3)
except:
    stdout, stderr = proc.stdout.read() if proc.stdout else "", ""

print(f"⏹ 录制完成 ({len(stdout)} 字符)")

# ── 4. 截图后 ──
after = cap()
cv2.imwrite(f"{OUT}/record_after.png", after)
print("📸 截图(后) 已保存")

if not stdout.strip():
    print("❌ 没录到事件！重试...")
    sys.exit(1)

# ── 5. 解析事件 ──
events = []
for line in stdout.strip().split('\n'):
    m = re.match(r'\[\s*(\d+\.\d+)\]\s+\S+\s+event4:\s+(.+)', line)
    if m:
        events.append((float(m.group(1)), m.group(2)))

if not events:
    print("❌ 无法解析事件")
    sys.exit(1)

print(f"📝 解析到 {len(events)} 个事件, 跨度 {events[-1][0]-events[0][0]:.3f}s")

# 提取所有 ABS_MT_POSITION 坐标
positions = []
for t, rest in events:
    xm = re.search(r'ABS_MT_POSITION_X\s+([0-9a-f]+)', rest)
    ym = re.search(r'ABS_MT_POSITION_Y\s+([0-9a-f]+)', rest)
    if xm and ym:
        positions.append((int(xm.group(1), 16), int(ym.group(1), 16), t))

if len(positions) < 3:
    print(f"❌ 坐标点不足 ({len(positions)}), 手势可能太短")
    sys.exit(1)

# 找 touch down (第一个坐标) 和 最后的坐标
down_x, down_y = positions[0][0], positions[0][1]
up_x, up_y = positions[-1][0], positions[-1][1]
total_duration = positions[-1][2] - positions[0][2]

# 检测初始停顿 (按住不动的那段)
hold_end_idx = 0
for i in range(1, len(positions)):
    dx = abs(positions[i][0] - positions[0][0])
    dy = abs(positions[i][1] - positions[0][1])
    if dx > 5 or dy > 5:  # 移动超过5px才算开始拖拽
        hold_end_idx = i
        break

hold_duration = positions[hold_end_idx][2] - positions[0][2] if hold_end_idx > 0 else 0
drag_duration = total_duration - hold_duration

print(f"\n🎯 手势分析:")
print(f"   down: ({down_x}, {down_y})")
print(f"   up:   ({up_x}, {up_y})")
print(f"   位移: dx={up_x-down_x:+d}, dy={up_y-down_y:+d}")
print(f"   总时长: {total_duration:.3f}s")
print(f"   按住不动: {hold_duration:.3f}s (选中等候)")
print(f"   拖拽时长: {drag_duration:.3f}s")
print(f"   轨迹点数: {len(positions)}")

# ── 6. 用 input swipe 复现 - 完全复制时长和距离 ──
duration_ms = int(total_duration * 1000)
cmd = f"input swipe {down_x} {down_y} {up_x} {up_y} {duration_ms}"
print(f"\n🔄 复现: {cmd}")

print("等待 2 秒后自动复现...")
time.sleep(2)

subprocess.run(
    [MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
    capture_output=True, text=True, timeout=10
)

# 截取复现后画面
time.sleep(2)
replay_after = cap()
cv2.imwrite(f"{OUT}/replay_after.png", replay_after)

diff = cv2.absdiff(before, replay_after)
gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
pct = np.count_nonzero(gray > 30) / gray.size * 100
print(f"\n✅ 复现执行完成!")
print(f"   像素变化: {pct:.2f}%")
print(f"   截图: record_before.png → replay_after.png")
print(f"   请确认: 复现的手势是否让单位移动了? (非镜头平移)")
