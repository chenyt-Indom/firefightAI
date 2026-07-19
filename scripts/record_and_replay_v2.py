"""录制→解析→复现 V2: 用设备本地文件避免pipe问题"""
import subprocess, time, re, os
import cv2, numpy as np

ADB = r"D:\MuMuPlayer\nx_main\adb.exe"
MUMU = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
OUT = r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\screenshots"
EVENTS_FILE = "/sdcard/touch_events.txt"
os.makedirs(OUT, exist_ok=True)

def cap():
    r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)

# ── 1. 截图前 ──
print("=" * 60)
print("🎬 录制准备 V2")
before = cap()
cv2.imwrite(f"{OUT}/rec_before.png", before)
print("📸 截图(前) 已保存")

# ── 2. 倒计时 ──
for i in [3, 2, 1]:
    print(f"   {i}...", flush=True)
    time.sleep(1)

# ── 3. 录制到设备文件 ──
print("🔴 开始录制! 拖拽单位! (5秒)")
subprocess.run([ADB, "-s", "127.0.0.1:7555", "shell",
                f"timeout 5 getevent -t /dev/input/event4 > {EVENTS_FILE} 2>/dev/null"],
               capture_output=True, timeout=8)
print("⏹ 录制完成")

# ── 4. 拉取事件文件 ──
r = subprocess.run([ADB, "-s", "127.0.0.1:7555", "pull", EVENTS_FILE,
                    f"{OUT}/touch_events.txt"],
                   capture_output=True, text=True, timeout=5)
with open(f"{OUT}/touch_events.txt", "r") as f:
    content = f.read()
print(f"📝 拉取到 {len(content)} 字符")

# ── 5. 截图后 ──
after = cap()
cv2.imwrite(f"{OUT}/rec_after.png", after)

if len(content) < 100:
    print(f"❌ 事件太少! ({len(content)} 字符)")
    print("   可能是屏幕没被触摸，或无权限")
    # 显示内容
    print(f"   内容: {content[:200]}")
    exit(1)

# ── 6. 解析 ──
positions = []
for line in content.strip().split('\n'):
    m = re.match(r'\[\s*(\d+\.\d+)\]\s+\S+\s+event4:\s+(.+)', line)
    if not m:
        continue
    t = float(m.group(1))
    rest = m.group(2)
    xm = re.search(r'0035\s+([0-9a-f]+)', rest)
    ym = re.search(r'0036\s+([0-9a-f]+)', rest)
    if xm and ym:
        positions.append((int(xm.group(1), 16), int(ym.group(1), 16), t))

if len(positions) < 3:
    print(f"❌ 坐标不足 ({len(positions)})")
    exit(1)

down_x, down_y = positions[0][0], positions[0][1]
up_x, up_y = positions[-1][0], positions[-1][1]
total_dur = positions[-1][2] - positions[0][2]

# 初始停顿
hold_dur = 0
for i in range(1, len(positions)):
    dx = abs(positions[i][0] - positions[0][0])
    dy = abs(positions[i][1] - positions[0][1])
    if dx > 5 or dy > 5:
        hold_dur = positions[i][2] - positions[0][2]
        break

print(f"\n🎯 手势:")
print(f"   down=({down_x},{down_y})  up=({up_x},{up_y})")
print(f"   位移: dx={up_x-down_x:+d} dy={up_y-down_y:+d}")
print(f"   总长: {total_dur:.3f}s  按住: {hold_dur:.3f}s")
print(f"   轨迹点: {len(positions)}")

# ── 7. 复现 ──
dur_ms = max(100, int(total_dur * 1000))
cmd = f"input swipe {down_x} {down_y} {up_x} {up_y} {dur_ms}"
print(f"\n🔄 复现: {cmd} ({dur_ms}ms)")

time.sleep(2)
subprocess.run([MUMU, "control", "-v", "0", "tool", "cmd", "-c", cmd],
               capture_output=True, text=True, timeout=10)
time.sleep(2)

replay = cap()
cv2.imwrite(f"{OUT}/replay_after.png", replay)
d = cv2.absdiff(before, replay)
g = cv2.cvtColor(d, cv2.COLOR_BGR2GRAY)
pct = np.count_nonzero(g > 30) / g.size * 100
print(f"✅ 复现完成! 像素变化: {pct:.2f}%")
print(f"截图: rec_before.png, rec_after.png, replay_after.png")
