"""双指缩放触控录制工具 v2 — 修复编码和ADB路径

用法: python scripts/record_pinch.py [in|out]
  在5秒内做双指缩放手势
"""

import subprocess, time, json, sys
from pathlib import Path

ADB = r"D:\MuMuPlayer\nx_device\12.0\shell\adb.exe"
DEVICE = "127.0.0.1:7555"
PROJECT = Path(__file__).parent.parent

def adb_raw(cmd: str, timeout=5):
    """ADB命令, 返回字节(raw output)"""
    r = subprocess.run(
        [ADB, "-s", DEVICE, "shell"] + cmd.split(),
        capture_output=True, timeout=timeout,
    )
    return r.stdout

def main():
    direction = sys.argv[1] if len(sys.argv) > 1 else input("缩放方向 [in/out]: ").strip() or "in"
    print(f"\n📡 检测触控设备...")

    # 先找出有触控数据的设备
    devices = []
    for dev in ["/dev/input/event4", "/dev/input/event5", "/dev/input/event6", "/dev/input/event7", "/dev/input/event8"]:
        out = adb_raw(f"getevent -p {dev}", timeout=3)
        if b"ABS_MT_POSITION" in out:
            x_min, x_max = 0, 9999
            for line in out.decode('latin-1').split('\n'):
                if 'ABS_MT_POSITION_X' in line and 'max' in line:
                    x_max = int(line.strip().split()[-1])
            devices.append((dev, x_max))
            print(f"  ✅ {dev} (X_max={x_max})")
        else:
            print(f"  ❌ {dev}")

    if not devices:
        print("❌ 没有找到多点触控设备!")
        return

    # 用X范围最大的设备
    devices.sort(key=lambda d: d[1], reverse=True)
    touch_dev = devices[0][0]
    print(f"\n📱 使用: {touch_dev}")

    print(f"\n{'='*50}")
    print(f"⏰ 5秒后开始录制双指{direction=='in' and '放大' or '缩小'}手势")
    print(f"   在模拟器窗口上用鼠标双指缩放!")
    print(f"{'='*50}")
    for i in range(5, 0, -1):
        print(f"   {i}...", end="\r")
        time.sleep(1)
    print("   🚀 录制中! 现在做手势!    ")

    # 录制6秒
    raw_lines = []
    t_end = time.time() + 5
    while time.time() < t_end:
        try:
            out = adb_raw(f"getevent -l {touch_dev}", timeout=1)
            if out:
                for line in out.decode('latin-1').split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    # 只保留触控相关事件
                    if any(k in line for k in ['ABS_MT', 'BTN_TOUCH', 'SYN_REPORT']):
                        raw_lines.append(line)
        except Exception:
            pass
        time.sleep(0.03)

    print(f"\n✅ 捕获 {len(raw_lines)} 个事件")

    if not raw_lines:
        print("❌ 没有事件! 你是否在5秒内做了双指手势?")
        return

    # 分析
    fingers = set()
    xs, ys = [], []
    for line in raw_lines:
        if "POSITION_X" in line:
            try:
                xs.append(int(line.strip().split()[-1], 16))
            except:
                pass
        elif "POSITION_Y" in line:
            try:
                ys.append(int(line.strip().split()[-1], 16))
            except:
                pass
        elif "TRACKING_ID" in line:
            try:
                tid = int(line.strip().split()[-1], 16)
                if tid < 0xFFFF0000:
                    fingers.add(tid)
            except:
                pass

    print(f"  手指数: {len(fingers)}")
    if xs:
        print(f"  X范围: {min(xs)}-{max(xs)} (Δ={max(xs)-min(xs)})")
    if ys:
        print(f"  Y范围: {min(ys)}-{max(ys)} (Δ={max(ys)-min(ys)})")

    # 保存
    path = PROJECT / "data" / "pinch_gesture.json"
    data = {
        "direction": direction,
        "device": touch_dev,
        "fingers": list(fingers),
        "x_range": [min(xs), max(xs)] if xs else [],
        "y_range": [min(ys), max(ys)] if ys else [],
        "event_count": len(raw_lines),
        "events": raw_lines,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n📁 已保存: {path}")
    print(f"   事件数: {len(raw_lines)} | 手指: {len(fingers)}")


if __name__ == "__main__":
    main()
