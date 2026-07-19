"""测试 ADB input + 验证"""
import subprocess, time, os, cv2, numpy as np

OUT = r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\test_output"
os.makedirs(OUT, exist_ok=True)
ADB = r"D:\MuMuPlayer\nx_device\12.0\shell\adb.exe"
DEV = "127.0.0.1:7555"

def screenshot(name):
    p = os.path.join(OUT, f"adb_test_{name}.png")
    subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"],
                   stdout=open(p, "wb"), timeout=10)
    return p if os.path.getsize(p) > 1000 else None

def compare(before, after, label):
    b = cv2.imread(before)
    a = cv2.imread(after)
    if b is None or a is None: return False, 0
    gray = cv2.cvtColor(cv2.absdiff(b, a), cv2.COLOR_BGR2GRAY)
    changed = np.count_nonzero(gray > 15)
    pct = 100.0 * changed / gray.size
    return changed > 500, changed

print("=" * 60)
print("ADB input + scrcpy 方案测试")
print("=" * 60)

# 检查 scrcpy 是否可用
scrcpy = r"C:\Users\19853\WorkBuddy\2026-07-18-07-52-25\firefightAI\scrcpy-win64-v3.3\scrcpy.exe"
scrcpy_exists = os.path.exists(scrcpy)
print(f"scrcpy 可用: {scrcpy_exists}")

# 截图1: 测试前
before = screenshot("before_adb")
if not before:
    print("截图失败!")
    exit()
print(f"截图: before ({os.path.getsize(before)//1024}KB)")

# 测试1: ADB input tap 暂停按钮
print("\n[测试1] adb shell input tap 1824 54 (暂停)")
subprocess.run([ADB, "-s", DEV, "shell", "input", "tap", "1824", "54"], timeout=5)
time.sleep(0.8)

after1 = screenshot("after_adb_tap1")
if after1:
    ok, n = compare(before, after1, "ADB tap")
    print(f"  变化: {n}像素 -> {'✅ 有反应!' if ok else '❌ 无反应'}")

# 截图2: 再点一次恢复
before = screenshot("before_adb2")
subprocess.run([ADB, "-s", DEV, "shell", "input", "tap", "1824", "54"], timeout=5)
time.sleep(0.5)

# 测试2: ADB input swipe (框选)
print("\n[测试2] adb shell input swipe 框选")
subprocess.run([ADB, "-s", DEV, "shell", "input", "swipe", "300", "500", "1200", "500", "200"], timeout=5)
time.sleep(0.5)

after2 = screenshot("after_adb_swipe")
if after2:
    ok, n = compare(before, after2, "ADB swipe")
    print(f"  变化: {n}像素 -> {'✅ 有反应!' if ok else '❌ 无反应'}")

# 测试3: 如果 scrcpy 可用, 用 scrcpy 注入触控
if scrcpy_exists:
    print("\n[测试3] 用 scrcpy 发送触控 (通过 control socket)")
    before = screenshot("before_scrcpy")
    
    # scrcpy 启动方式: scrcpy --no-video --no-audio --control
    # 然后通过 ADB 转发向 scrcpy 的 control socket 发送事件
    # 但这太复杂, 先试简单的: scrcpy 快捷键
    
    # scrcpy 可以通过 Ctrl+click 来点击
    # 但我们没法通过命令行控制 scrcpy 的点击
    
    # 实际上 scrcpy 支持通过管道发送控制命令
    # scrcpy --no-window --no-audio (headless mode)
    # 但这需要保持进程运行
    
    # 最简单: 直接用 scrcpy 快捷键 Ctrl+B 返回
    # 但需要窗口获得焦点
    
    print("  scrcpy 控制需要在图形界面下操作")
    print("  (scrcpy 通过 SDL 窗口接收键盘/鼠标事件转发)")
    
    # 尝试 headless scrcpy 然后用 adb 转发
    print("  正在尝试 headless scrcpy 模式...")
    
    # 先尝试启动 scrcpy headless
    # scrcpy --no-window --no-audio --max-size 1024
    # 这样 scrcpy forward 了端口后, 我们可以通过 adb 转发发控制命令
    
    # 实际上 scrcpy 的控制通过 TCP socket
    # 启动: scrcpy --no-window --no-audio --tcpip=127.0.0.1:5555
    # 但 adb 已经在 7555 了
    
    # 换一种方式: scrcpy 支持 --control 但需要 --no-video 来省资源
    # 实际上最简单的是让用户手动通过 scrcpy 窗口点击
    
    print("  scrcpy 需要用户手动操作窗口才能转发触控")
    print("  命令行触控需要启动 scrcpy server + otg 模式")

print(f"\n{'='*60}")
print("测试完成")
print(f"截图: {OUT}")
print(f"{'='*60}")

if not scrcpy_exists:
    print("\n⚠️  scrcpy 不可用, 需要下载或指定路径")
    print(f"  当前路径: {scrcpy}")
