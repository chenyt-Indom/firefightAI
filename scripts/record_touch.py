"""录制用户手动拖拽的触控事件，然后精确复现

原理: ADB getevent 监听 MuMu 模拟器的触控设备 (/dev/input/event4)
录制用户手动拖拽 → 解析出 down/move/up 序列 → sendevent 原样回放
"""
import subprocess, time, re, sys, os

ADB = r"D:\MuMuPlayer\nx_main\adb.exe"
DEVICE = "127.0.0.1:7555"

print("=" * 60)
print("🎬 触控录制模式")
print("=" * 60)
print()
print("我将在 3 秒后开始录制 5 秒的触控事件。")
print("在这 5 秒内，请用手指在模拟器里拖拽一个单位移动。")
print("完成后我会分析事件并尝试复现。")
print()

time.sleep(3)

print("🔴 开始录制 (5秒)...")
# 录制 getevent 输出
proc = subprocess.Popen(
    [ADB, "-s", DEVICE, "shell", "getevent", "-lt", "/dev/input/event4"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True
)
time.sleep(5.5)

# 杀掉 getevent
subprocess.run([ADB, "-s", DEVICE, "shell", "pkill", "getevent"],
               capture_output=True, timeout=3)

# 读取输出
stdout, _ = proc.communicate(timeout=2)
print(f"📝 录制完成，收到 {len(stdout)} 字符\n")

# 解析事件
events = []
current_time = 0
for line in stdout.strip().split('\n'):
    # 格式: [  12345.678901] /dev/input/event4: EV_ABS  ABS_MT_TRACKING_ID   00000000
    m = re.match(r'\[\s*(\d+\.\d+)\]\s+\S+\s+event4:\s+(.+)', line)
    if not m:
        continue
    t = float(m.group(1))
    rest = m.group(2)
    events.append((t, rest))

if not events:
    print("❌ 没有录到触控事件！请重试。")
    print("   可能原因：getevent 没权限 / 设备节点不对")
    sys.exit(1)

print(f"解析到 {len(events)} 个事件")
print(f"时间跨度: {events[-1][0] - events[0][0]:.3f}s")
print()

# 分组: 找出连续的手势 (by TRACKING_ID)
gestures = {}
current_gesture = []
last_time = events[0][0]
for t, rest in events:
    if t - last_time > 1.0 and current_gesture:  # 1秒以上间隔 = 新手势
        gestures[len(gestures)] = current_gesture
        current_gesture = []
    current_gesture.append((t, rest))
    last_time = t
if current_gesture:
    gestures[len(gestures)] = current_gesture

# 找有意义的拖拽手势 (至少包含 DOWN + MOVE + UP)
drag_gestures = []
for gid, gevents in gestures.items():
    has_down = any('BTN_TOUCH' in e[1] for e in gevents)
    has_move = any('ABS_MT_POSITION' in e[1] for e in gevents)
    has_tracking = any('ABS_MT_TRACKING_ID' in e[1] for e in gevents)
    if has_tracking or (has_move and len(gevents) > 5):
        drag_gestures.append(gid)

if not drag_gestures:
    print("❌ 未检测到拖拽手势 (需要至少包含 MOTION 事件)")
    sys.exit(1)

# 用最后一个拖拽手势 (最近完成的)
gid = drag_gestures[-1]
gevents = gestures[gid]
print(f"🎯 分析手势 #{gid} ({len(gevents)} 个事件, "
      f"{gevents[-1][0]-gevents[0][0]:.3f}s)")

# 提取关键坐标
positions = []
max_x, max_y = 0, 0
for t, rest in gevents:
    mx = re.search(r'ABS_MT_POSITION_X\s+([0-9a-f]+)', rest)
    my = re.search(r'ABS_MT_POSITION_Y\s+([0-9a-f]+)', rest)
    if mx and my:
        x = int(mx.group(1), 16)
        y = int(my.group(1), 16)
        positions.append((x, y, t))
        max_x = max(max_x, x)
        max_y = max(max_y, y)

if len(positions) < 2:
    print("❌ 坐标点不足")
    sys.exit(1)

start_x, start_y = positions[0][0], positions[0][1]
end_x, end_y = positions[-1][0], positions[-1][1]
duration = positions[-1][2] - positions[0][2]

print(f"   起点: ({start_x}, {start_y})")
print(f"   终点: ({end_x}, {end_y})")
print(f"   位移: dx={end_x-start_x:+d}, dy={end_y-start_y:+d}")
print(f"   时长: {duration:.3f}s")
print(f"   轨迹点数: {len(positions)}")
if len(positions) >= 2:
    print(f"   速度: {((end_x-start_x)**2+(end_y-start_y)**2)**0.5/duration*1000:.0f} px/s")
    # 检查是否有明显停顿
    pauses = []
    for i in range(1, len(positions)):
        dt = positions[i][2] - positions[i-1][2]
        if dt > 0.1:
            pauses.append(dt)
    if pauses:
        print(f"   停顿: {pauses} (可能是长按选中)")
    else:
        print(f"   事件密度: {len(positions)/duration:.0f} 事件/秒")

print()
print("─" * 40)
print(f"🔄 现在用 input swipe 复现这个手势...")
print(f"   input swipe {start_x} {start_y} {end_x} {end_y} {int(duration*1000)}")
print("─" * 40)

input("\n按 Enter 执行复现...")

subprocess.run(
    [r"D:\MuMuPlayer\nx_main\MuMuManager.exe", "control", "-v", "0",
     "tool", "cmd", "-c",
     f"input swipe {start_x} {start_y} {end_x} {end_y} {int(duration*1000)}"],
    capture_output=True, text=True, timeout=10
)
print("✅ 已执行，请查看游戏画面：单位移动了吗？")
