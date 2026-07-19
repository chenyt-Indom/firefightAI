import ctypes, subprocess

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 检查 MuMu 窗口
buf = ctypes.create_unicode_buffer(256)

print("=" * 50)
print("MuMu 状态检查")
print("=" * 50)

# 主窗口
hwnd = user32.FindWindowW('Qt5156QWindowIcon', 'MuMu安卓设备')
if hwnd:
    user32.GetWindowTextW(hwnd, buf, 256)
    print(f"✅ 主窗口: [{buf.value}] hwnd={hwnd}")
else:
    print("❌ 主窗口未出现")

# 渲染窗口
hwnd_r = user32.FindWindowW('nemuwin', 'nemudisplay')
if hwnd_r:
    print(f"✅ 渲染窗口: nemuwin/nemudisplay hwnd={hwnd_r}")
else:
    print("❌ 渲染窗口未出现")

# ADB
print()
result = subprocess.run(
    [r'D:\MuMuPlayer\nx_device\12.0\shell\adb.exe', 'devices'],
    capture_output=True, text=True
)
devices = result.stdout.strip()
print(f"ADB: {devices}")

# 如果 ADB 在线，检查前台应用
if 'device' in devices and 'offline' not in devices:
    result2 = subprocess.run(
        [r'D:\MuMuPlayer\nx_device\12.0\shell\adb.exe', '-s', '127.0.0.1:7555',
         'shell', 'dumpsys', 'activity', 'activities'],
        capture_output=True, text=True, timeout=10
    )
    for line in result2.stdout.split('\n'):
        if 'mResumedActivity' in line or 'topResumedActivity' in line:
            print(f"前台: {line.strip()}")
            break

print()
print("=" * 50)
print("现在测试 MuMu IPC...")
print("=" * 50)

# 加载 DLL 测试
dll_path = r"D:\MuMuPlayer\nx_device\12.0\shell\sdk\external_renderer_ipc.dll"
try:
    dll = ctypes.windll.LoadLibrary(dll_path)
    dll.nemu_connect.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
    dll.nemu_connect.restype = ctypes.c_int
    dll.nemu_disconnect.argtypes = [ctypes.c_int]
    dll.nemu_disconnect.restype = None
    dll.nemu_get_display_id.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    dll.nemu_get_display_id.restype = ctypes.c_int
    dll.nemu_input_event_touch_down.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    dll.nemu_input_event_touch_down.restype = ctypes.c_int
    dll.nemu_input_event_touch_up.argtypes = [ctypes.c_int, ctypes.c_int]
    dll.nemu_input_event_touch_up.restype = ctypes.c_int

    handle = dll.nemu_connect(ctypes.c_wchar_p(r"D:\MuMuPlayer"), ctypes.c_int(0))
    print(f"nemu_connect: handle={handle}")

    if handle > 0:
        pkgn = b"com.windowsgames.firefightbw"
        display_id = dll.nemu_get_display_id(
            ctypes.c_int(handle), ctypes.c_char_p(pkgn), ctypes.c_int(0)
        )
        print(f"nemu_get_display_id: {display_id}")

        # 测试触控
        import time
        # 暂停按钮
        ret = dll.nemu_input_event_touch_down(ctypes.c_int(handle), ctypes.c_int(display_id), ctypes.c_int(1824), ctypes.c_int(54))
        print(f"touch_down(1824,54): ret={ret}")
        time.sleep(0.1)
        ret = dll.nemu_input_event_touch_up(ctypes.c_int(handle), ctypes.c_int(display_id))
        print(f"touch_up: ret={ret}")

        dll.nemu_disconnect(ctypes.c_int(handle))
        print("已断开")
except Exception as e:
    print(f"错误: {e}")
