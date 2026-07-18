#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Win32 鼠标注入测试

绕过 MuMu IPC DLL 的问题，直接用 Win32 API
向 MuMu 模拟器窗口发送鼠标事件。

原理: SDL2 在 Windows 上透过 Win32 读鼠标事件，
向模拟器窗口发送的 WM_LBUTTONDOWN/UP 会最终到达游戏。
"""

import ctypes
import time
import sys
from ctypes import wintypes

# Win32 常量
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
MK_LBUTTON = 0x0001

# User32 函数
user32 = ctypes.windll.user32

# FindWindowW
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND

# SetForegroundWindow
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

# GetWindowRect
class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

# GetClientRect
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL

# ClientToScreen
class POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]

user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ClientToScreen.restype = wintypes.BOOL

# ScreenToClient
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ScreenToClient.restype = wintypes.BOOL

# SendMessageW
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_longlong

# PostMessageW
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

# SetCursorPos
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL

# mouse_event (from user32)
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
user32.mouse_event.restype = None

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000

# EnumChildWindows
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL

# GetClassNameW
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

# GetWindowTextW
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int


def make_lparam(x: int, y: int) -> int:
    """将 x, y 打包为 lParam"""
    return (y << 16) | (x & 0xFFFF)


def find_mumu_windows():
    """找到所有 MuMu 相关的窗口"""
    windows = []

    def enum_callback(hwnd, lparam):
        class_buf = ctypes.create_unicode_buffer(256)
        text_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buf, 256)
        user32.GetWindowTextW(hwnd, text_buf, 256)
        
        class_name = class_buf.value
        title = text_buf.value
        
        if any(kw in (class_name + title).lower() for kw in 
               ['mumu', 'nemu', 'render', 'subwin', 'sdl']):
            rect = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            windows.append({
                'hwnd': hwnd,
                'class': class_name,
                'title': title,
                'rect': (rect.left, rect.top, rect.right, rect.bottom),
                'size': (w, h),
            })
        return True

    # 枚举顶级窗口
    user32.EnumChildWindows(None, WNDENUMPROC(enum_callback), 0)
    
    # 也尝试直接通过类名查找
    class_names_to_try = [
        "NemuPlayer", "Qt5QWindowIcon", "SDL_app",
        "MuMuPlayer", "MuMu", "RenderWindow",
        "subWin", "NemuWin", "NemuRender"
    ]
    
    for cn in class_names_to_try:
        hwnd = user32.FindWindowW(cn, None)
        if hwnd:
            rect = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            text_buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, text_buf, 256)
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            windows.append({
                'hwnd': hwnd,
                'class': cn,
                'title': text_buf.value,
                'rect': (rect.left, rect.top, rect.right, rect.bottom),
                'size': (w, h),
            })

    return windows


def tap_screen(x: int, y: int, delay_ms: int = 50):
    """
    用 mouse_event 发送点击 - 这模拟硬件鼠标事件
    
    注意：这会移动用户的实际鼠标指针！
    """
    # 保存当前鼠标位置 (如果需要)
    # 移动鼠标到目标位置
    user32.SetCursorPos(x, y)
    time.sleep(0.01)
    
    # 按下
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(delay_ms / 1000.0)
    
    # 释放
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
    time.sleep(0.01)


def tap_window(hwnd, x_in_window: int, y_in_window: int, delay_ms: int = 50):
    """
    向指定窗口发送鼠标消息 (SendMessage/PostMessage)
    
    这不会移动用户的实际鼠标！但是游戏可能不吃消息。
    """
    lparam = make_lparam(x_in_window, y_in_window)
    
    # 发送按下
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(delay_ms / 1000.0)
    
    # 发送释放
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
    time.sleep(0.01)


def tap_via_sendinput(x: int, y: int, delay_ms: int = 50):
    """
    使用 SendInput API 发送鼠标事件
    
    这是最低层的注入方式，游戏更难拦截。
    """
    # INPUT 结构
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        class _INPUT_UNION(ctypes.Union):
            _fields_ = [
                ("mi", MOUSEINPUT),
            ]
        _anonymous_ = ("u",)
        _fields_ = [
            ("type", wintypes.DWORD),
            ("u", _INPUT_UNION),
        ]

    INPUT_MOUSE = 0
    
    # SendInput
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT

    # 创建鼠标移动 + 按下事件
    events = []
    
    # 按下
    inp_down = INPUT()
    inp_down.type = INPUT_MOUSE
    inp_down.mi.dx = x
    inp_down.mi.dy = y
    inp_down.mi.mouseData = 0
    inp_down.mi.dwFlags = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
    inp_down.mi.time = 0
    events.append(inp_down)
    
    # 释放
    inp_up = INPUT()
    inp_up.type = INPUT_MOUSE
    inp_up.mi.dx = x
    inp_up.mi.dy = y
    inp_up.mi.mouseData = 0
    inp_up.mi.dwFlags = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE
    inp_up.mi.time = 0
    events.append(inp_up)

    # 创建 INPUT 数组
    inp_array = (INPUT * len(events))(*events)
    user32.SendInput(len(events), inp_array, ctypes.sizeof(INPUT))
    time.sleep(delay_ms / 1000.0)


# ============================================================
# 主测试
# ============================================================

print("=" * 70)
print("  Win32 鼠标注入测试")
print("=" * 70)

# 1. 找到 MuMu 窗口
print("\n[1] 查找 MuMu 窗口...")
windows = find_mumu_windows()

if not windows:
    print("    未找到 MuMu 相关窗口!")
    print("    尝试枚举子窗口...")
    
    # 枚举所有顶级窗口的子窗口
    def enum_all_toplevel():
        results = []
        def callback(hwnd, lparam):
            class_buf = ctypes.create_unicode_buffer(256)
            text_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            user32.GetWindowTextW(hwnd, text_buf, 256)
            rect = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            # 只显示有实际大小的窗口
            if w > 100 and h > 100:
                results.append({
                    'hwnd': hwnd,
                    'class': class_buf.value,
                    'title': text_buf.value,
                    'rect': (rect.left, rect.top, rect.right, rect.bottom),
                    'size': (w, h),
                })
            return True
        
        user32.EnumChildWindows(None, WNDENUMPROC(callback), 0)
        return results
    
    all_windows = enum_all_toplevel()
    for w in all_windows:
        print(f"    [{w['class']}] \"{w['title']}\" {w['size']} @ {w['rect']}")
    
    # 也尝试通过已知的 MuMu 窗口标题查找
    for title_hint in ["MuMu", "Nemu", "模拟器", "Player"]:
        hwnd = user32.FindWindowW(None, title_hint)
        if hwnd:
            rect = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            print(f"    [by title '{title_hint}'] class={class_buf.value} size={rect.right-rect.left}x{rect.bottom-rect.top}")
else:
    for w in windows:
        print(f"    [{w['class']}] \"{w['title']}\"")
        print(f"    位置: {w['rect']}, 大小: {w['size']}")
        print(f"    HWND: {w['hwnd']}")

print("\n" + "=" * 70)
print("  现在将尝试 3 种注入方式 (每种测试 1 次点击)")
print("  ⚠️  方式1 (mouse_event) 会移动你的鼠标指针!")
print("  请确保游戏画面可见")
print("=" * 70)

# 找到最适合的窗口 (通常是最大的 MuMu 相关窗口)
# 或子窗口 RenderWindow
target_hwnd = None
target_rect = None

# 在所有窗口中找到最大的候选
all_candidates = []
for w in windows:
    w_area = w['size'][0] * w['size'][1]
    all_candidates.append((w_area, w))

# 也加入刚才枚举的所有大窗口
if 'all_windows' in dir():
    pass  # already printed

candidates = []
# 优先找 MuMu 渲染窗口
for w in windows:
    if 'render' in w['class'].lower() or 'subwin' in w['class'].lower():
        candidates.append(w)
        print(f"\n  候选渲染窗口: [{w['class']}] size={w['size']}")

# 如果没有渲染窗口，找主窗口
if not candidates and windows:
    candidates = sorted(windows, key=lambda w: w['size'][0] * w['size'][1], reverse=True)

if candidates:
    target = candidates[0]
    target_hwnd = target['hwnd']
    target_rect = target['rect']
    print(f"\n  选用窗口: [{target['class']}] \"{target['title']}\"")
    print(f"  窗口大小: {target['size'][0]}x{target['size'][1]}")
    print(f"  窗口位置: ({target['rect'][0]}, {target['rect'][1]})")
else:
    print("\n  找不到合适的窗口进行测试!")
    print("  请手动确认 MuMu 模拟器正在运行且游戏在前台")
    sys.exit(1)

# 计算点击目标
# 测试1: 游戏暂停按钮位置 (屏幕坐标 → 窗口坐标)
# 屏幕 1920x1080, 暂停按钮在 (1824, 54)
# 窗口内坐标 = 屏幕坐标 (因为窗口渲染区应该和屏幕一样大)

# 使用窗口的绝对坐标 + 屏内坐标来得到屏幕坐标
click_screen_x = target['rect'][0] + 960   # 屏幕中央
click_screen_y = target['rect'][1] + 540
click_window_x = 960
click_window_y = 540

print(f"\n  测试目标: 窗口内 ({click_window_x}, {click_window_y})")
print(f"  对应屏幕: ({click_screen_x}, {click_screen_y})")

# 先激活窗口
print(f"\n  激活窗口...")
user32.SetForegroundWindow(target_hwnd)
time.sleep(0.3)

# --- 方式1: mouse_event ---
print(f"\n{'='*50}")
print(f"  [方式1] mouse_event 测试")
print(f"  点击窗口中央 ({click_window_x}, {click_window_y})")
print(f"  ⚠️  鼠标指针会移动!")
print(f"{'='*50}")

tap_screen(click_screen_x, click_screen_y, delay_ms=100)
print(f"  完成! 请观察游戏是否有点击反应")
time.sleep(0.5)

# --- 方式2: SendInput ---
print(f"\n{'='*50}")
print(f"  [方式2] SendInput 测试")
print(f"  点击屏幕右上角暂停区域 (约 1824, 54)")
print(f"{'='*50}")

# SendInput 使用绝对坐标 (0-65535)
# 将屏幕坐标转为绝对坐标
screen_w = 65535  # 使用当前主显示器
screen_h = 65535

# 直接用 SetCursorPos 移动到暂停按钮位置
pause_screen_x = target['rect'][0] + 1824
pause_screen_y = target['rect'][1] + 54
user32.SetCursorPos(pause_screen_x, pause_screen_y)
time.sleep(0.1)

# mouse_event 按下/释放
user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
time.sleep(0.05)
user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
print(f"  完成! 请观察游戏是否有点击反应")
time.sleep(0.5)

# 再点一次恢复
user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
time.sleep(0.05)
user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
time.sleep(0.5)

# --- 方式3: PostMessage ---
print(f"\n{'='*50}")
print(f"  [方式3] PostMessage 测试 (不移动鼠标)")
print(f"  发送 WM_LBUTTONDOWN/UP 到窗口")
print(f"{'='*50}")

tap_window(target_hwnd, click_window_x, click_window_y, delay_ms=100)
print(f"  完成! 请观察游戏是否有点击反应")
time.sleep(0.5)

# --- 方式4: SendInput + ABSOLUTE ---
print(f"\n{'='*50}")
print(f"  [方式4] SendInput 绝对坐标测试")
print(f"  使用 SendInput API 底层注入")
print(f"{'='*50}")

tap_via_sendinput(click_screen_x, click_screen_y, delay_ms=100)
print(f"  完成! 请观察游戏是否有点击反应")

# ============================================================
print(f"\n{'='*70}")
print(f"  测试完成!")
print(f"\n  结果总结:")
print(f"    如果 mouse_event 有效: 可以用此方案完全替代 MuMu IPC")
print(f"    如果 PostMessage 有效: 最佳方案 (不干扰用户鼠标)")
print(f"    如果 SendInput 有效: 中间方案")
print(f"    如果都无效: 需要其他方案 (scrcpy / 重启 MuMu)")
print(f"{'='*70}")
