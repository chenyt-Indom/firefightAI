"""双指缩放触控录制 — 多次录制，学习模式

用法: python scripts/record_zoom.py [次数默认3]

每次录制5秒，在模拟器上做双指缩放（放大或缩小）。
记录多次后自动分析共通模式并生成回放脚本。
"""

import subprocess, time, json
from pathlib import Path

ADB = r"D:\MuMuPlayer\nx_device\12.0\shell\adb.exe"
DEV = "127.0.0.1:7555"
EVENT_DEV = "/dev/input/event4"
PROJECT = Path(__file__).parent.parent


def record_session(session_id: int, label: str) -> list[str]:
    """录制一次缩放手势，返回事件列表"""
    print(f"\n{'='*50}")
    print(f"📡 录制 #{session_id}: {label}")
    print(f"{'='*50}")
    print(f"⏰ 5秒后开始，请在模拟器上做双指缩放...")
    for i in range(5, 0, -1):
        print(f"   {i}...", end="\r")
        time.sleep(1)
    print("   🚀 录制中！现在做手势！       ")

    # 用 timeout 命令限制 getevent 只跑5秒
    cmd = f'timeout 5 getevent -l {EVENT_DEV} 2>/dev/null'
    r = subprocess.run([ADB, "-s", DEV, "shell", cmd], capture_output=True, timeout=10)
    raw = r.stdout.decode("latin-1")

    events = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        if any(k in line for k in ["ABS_MT", "BTN_TOUCH", "SYN_REPORT"]):
            events.append(line)

    print(f"   ✅ 捕获 {len(events)} 个事件")
    return events


def analyze_events(events: list[str]) -> dict:
    """分析手势特征"""
    fingers = set()
    xs, ys = [], []
    frames_parsed = []

    current = {}
    slot = None
    for line in events:
        parts = line.split()
        if len(parts) < 3:
            continue
        key = parts[1]

        if key == "SYN_REPORT":
            if current.get("slot") is not None:
                frames_parsed.append(dict(current))
                current = {}
            continue
        elif key == "ABS_MT_SLOT":
            slot = parts[2]
            current["slot"] = slot
        elif key == "ABS_MT_TRACKING_ID":
            tid_val = parts[2]
            current[f"tid_{slot}"] = tid_val
            try:
                tid = int(tid_val, 16)
                if tid != 0xFFFFFFFF:
                    fingers.add(tid)
            except:
                pass
        elif key == "ABS_MT_POSITION_X":
            try:
                val = int(parts[2], 16)
                current[f"x_{slot}"] = val
                xs.append(val)
            except:
                pass
        elif key == "ABS_MT_POSITION_Y":
            try:
                val = int(parts[2], 16)
                current[f"y_{slot}"] = val
                ys.append(val)
            except:
                pass

    return {
        "event_count": len(events),
        "frames": len(frames_parsed),
        "fingers": len(fingers),
        "finger_ids": sorted(fingers),
        "x_range": [min(xs), max(xs)] if xs else [],
        "y_range": [min(ys), max(ys)] if ys else [],
        "x_delta": max(xs) - min(xs) if xs else 0,
        "y_delta": max(ys) - min(ys) if ys else 0,
    }


def generate_replay_script(all_sessions: list[dict]) -> str:
    """从所有录制数据中生成最优回放脚本"""
    # 找到X、Y位移最大的那个session（最清晰的缩放手势）
    best = max(all_sessions, key=lambda s: s["analysis"]["x_delta"] + s["analysis"]["y_delta"])

    analysis = best["analysis"]
    events = best["events"]

    print(f"\n{'='*50}")
    print(f"📊 选择最佳手势: #{best['id']} ({best['label']})")
    print(f"   手指数: {analysis['fingers']}")
    print(f"   帧数: {analysis['frames']}")
    print(f"   X范围: {analysis['x_range']} Δ={analysis['x_delta']}")
    print(f"   Y范围: {analysis['y_range']} Δ={analysis['y_delta']}")

    # 保存原始数据
    raw_path = PROJECT / "data" / "zoom_recorded_best.json"
    with open(raw_path, "w") as f:
        json.dump({"analysis": analysis, "events": events}, f, indent=2)
    print(f"   📁 原始数据: {raw_path}")

    # 生成Python回放脚本
    replay_code = _build_replay_script(events, analysis)
    replay_path = PROJECT / "data" / "zoom_replay.py"
    with open(replay_path, "w") as f:
        f.write(replay_code)
    print(f"   📁 回放脚本: {replay_path}")

    return str(replay_path)


def _build_replay_script(events: list[str], analysis: dict) -> str:
    """从原始事件生成可执行的回放脚本"""
    lines = ['"""自动生成的缩放回放脚本"""', "import subprocess, time", "",
             'ADB = r"D:\\MuMuPlayer\\nx_device\\12.0\\shell\\adb.exe"',
             'DEV = "127.0.0.1:7555"',
             'EVENT = "/dev/input/event4"',
             "",
             "def send(tp, code, val):",
             '    subprocess.run([ADB, "-s", DEV, "shell", ',
             '        f"sendevent {EVENT} {tp} {code} {val}"],',
             "        capture_output=True, timeout=2)",
             "",
             "def syn():",
             "    send(0, '0x00', '0x00')",
             "",
             "def replay():",
             "    print('回放缩放手势...')"]

    # 将事件转换为sendevent命令
    for ev in events:
        parts = ev.split()
        if len(parts) < 3:
            continue
        tp = parts[0]  # EV_ABS or EV_SYN or EV_KEY
        key = parts[1]
        val = parts[2]

        if tp == "EV_SYN":
            lines.append("    syn()")
        elif tp == "EV_ABS":
            # 需要映射 key name 到 code
            code_map = {
                "ABS_MT_SLOT": "0x2f",
                "ABS_MT_TRACKING_ID": "0x39",
                "ABS_MT_POSITION_X": "0x35",
                "ABS_MT_POSITION_Y": "0x36",
            }
            code = code_map.get(key)
            if code:
                lines.append(f"    send(3, '{code}', '0x{val}')")
        elif tp == "EV_KEY" and key == "BTN_TOUCH":
            if val == "DOWN":
                lines.append("    send(1, '0x14a', '0x1')")
            elif val == "UP":
                lines.append("    send(1, '0x14a', '0x0')")

    lines.extend(["    time.sleep(1)", "    print('完成')", "", "if __name__ == '__main__':", "    replay()"])
    return "\n".join(lines)


def main():
    import sys

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"\n🔥 双指缩放录制器 — 共 {count} 次")
    print(f"   每次5秒录制，请在模拟器窗口上用鼠标做双指缩放")
    print(f"   录完后自动分析+生成回放脚本\n")

    sessions = []
    labels = ["放大", "缩小", "放大"]

    for i in range(count):
        label = labels[i] if i < len(labels) else "缩放"
        events = record_session(i + 1, label)
        analysis = analyze_events(events)
        sessions.append({
            "id": i + 1,
            "label": label,
            "events": events,
            "analysis": analysis,
        })

        print(f"\n   分析 #{i+1}:")
        print(f"   手指数: {analysis['fingers']}")
        print(f"   X位移: {analysis['x_delta']}px")
        print(f"   Y位移: {analysis['y_delta']}px")
        print(f"   总帧数: {analysis['frames']}")

    # 汇总
    print(f"\n{'='*50}")
    print(f"📊 汇总分析:")
    for s in sessions:
        a = s["analysis"]
        print(f"   #{s['id']} {s['label']}: 手指{a['fingers']} ΔX={a['x_delta']} ΔY={a['y_delta']} 帧{a['frames']}")

    # 生成回放脚本
    replay_path = generate_replay_script(sessions)

    print(f"\n✅ 完成！运行回放脚本测试:")
    print(f"   python {replay_path}")


if __name__ == "__main__":
    main()
