#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Win32 鼠标注入测试 v2

改进:
1. 枚举 MuMu 主窗口的所有子窗口，找到真正的渲染窗口
2. 考虑窗口缩放 (1714x1021 窗口 → 1920x1080 游戏)
3. 用 ADB 截图对比验证点击是否生效
"""

import ctypes
import subprocess
import time
import sys
import os
from ctypes import wintypes

# ============================================================
# Win32 常量
# ============================================================
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 正确的 c_long
c_long = ctypes.c_long

# ============================================================
# 类型定义
# ============================================================
class RECT(ctypes.Structure):
    _fields_ = [("left", c_long), ("top", c_long), ("right", c_long), ("bottom", c_long)]

class POINT(ctypes.Structure):
    _fields_ = [("x", c_long), ("y", c_long)]

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# ============================================================
# Win32 函数签名
# ============================================================
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL

user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL

user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL

user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
user32.mouse_event.restype = None

user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

# ============================================================
# 枚举窗口
# ============================================================

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "test_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# MuMu ADB
MUADB = r"D:\MuMuPlayer\nx_device\12.0\shell\adb.exe"
ADB_DEVICE = "127.0.0.1:7555"


def adb_screenshot(path: str) -> bool:
    """用 MuMu 自带的 adb 截图"""
    try:
        subprocess.run(
            [MUADB, "-s", ADB_DEVICE, "exec-out", "screencap", "-p"],
            stdout=open(path, "wb"), stderr=subprocess.DEVNULL,
            timeout=10, check=True
        )
        return os.path.getsize(path) > 1000
    except Exception as e:
        print(f"   ADB 截图失败: {e}")
        return False


def enum_children(hwnd):
    """枚举所有子窗口"""
    children = []

    def callback(h, _):
        cn = ctypes.create_unicode_buffer(256)
        wt = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cn, 256)
        user32.GetWindowTextW(h, wt, 256)
        r = RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        cr = RECT()
        user32.GetClientRect(h, ctypes.byref(cr))
        children.append({
            'hwnd': h,
            'class': cn.value,
            'title': wt.value,
            'screen_rect': (r.left, r.top, r.right, r.bottom),
            'client_size': (cr.right, cr.bottom),
            'width': r.right - r.left,
            'height': r.bottom - r.top,
        })
        # 递归枚举子窗口的子窗口
        user32.EnumChildWindows(h, WNDENUMPROC(callback), 0)
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(callback), 0)
    return children


# ============================================================
# 查找 MuMu 窗口
# ============================================================

print("=" * 70)
print("  Win32 鼠标注入测试 v2")
print("=" * 70)

# 找到主窗口
main_hwnd = user32.FindWindowW("Qt5156QWindowIcon", "MuMu安卓设备")
if not main_hwnd:
    print("\n未找到 MuMu 窗口! 请确认模拟器正在运行")
    sys.exit(1)

main_rect = RECT()
user32.GetWindowRect(main_hwnd, ctypes.byref(main_rect))
print(f"\n主窗口: MuMu安卓设备 (HWND={main_hwnd})")
print(f"  屏幕位置: ({main_rect.left},{main_rect.top})-({main_rect.right},{main_rect.bottom})")
print(f"  窗口大小: {main_rect.right-main_rect.left}x{main_rect.bottom-main_rect.top}")

# 枚举所有子窗口
print(f"\n枚举子窗口 (递归)...")
children = enum_children(main_hwnd)

# 找渲染窗口
render_candidates = []
for c in children:
    area = c['width'] * c['height']
    # 渲染窗口特征: 足够大, 类名可能包含 render/sub/qt
    if area > 100000:  # > 316x316
        render_candidates.append((area, c))
    # 打印所有子窗口
    label = " *** 候选渲染窗口" if area > 100000 else ""
    print(f"  [{c['class']}] \"{c['title']}\" {c['width']}x{c['height']} "
          f"@({c['screen_rect'][0]},{c['screen_rect'][1]}){label}")

if not render_candidates:
    print("\n未找到大的渲染子窗口! 使用主窗口")
    target_hwnd = main_hwnd
    target_rect = main_rect
else:
    # 选最大的
    render_candidates.sort(key=lambda x: x[0], reverse=True)
    target = render_candidates[0][1]
    target_hwnd = target['hwnd']
    target_rect = RECT()
    target_rect.left = target['screen_rect'][0]
    target_rect.top = target['screen_rect'][1]
    target_rect.right = target['screen_rect'][2]
    target_rect.bottom = target['screen_rect'][3]
    print(f"\n选中渲染窗口: [{target['class']}] {target['width']}x{target['height']}")

# ============================================================
# 坐标映射
# ============================================================

# 游戏内部分辨率 1920x1080, 窗口实际大小
game_w, game_h = 1920, 1080
wnd_w = target_rect.right - target_rect.left
wnd_h = target_rect.bottom - target_rect.top

scale_x = wnd_w / game_w
scale_y = wnd_h / game_h

print(f"\n坐标映射: 游戏{game_w}x{game_h} → 窗口{wnd_w}x{wnd_h}")
print(f"  缩放: x{scale_x:.3f}, y{scale_y:.3f}")


def game_to_screen(gx: int, gy: int) -> tuple:
    """游戏坐标 → 屏幕绝对坐标"""
    sx = int(target_rect.left + gx * scale_x)
    sy = int(target_rect.top + gy * scale_y)
    return sx, sy


def click_screen(gx: int, gy: int, label="", delay_ms=50):
    """用 mouse_event 点击游戏坐标"""
    sx, sy = game_to_screen(gx, gy)
    print(f"  点击 {label}: 游戏({gx},{gy}) → 屏幕({sx},{sy})")
    user32.SetCursorPos(sx, sy)
    time.sleep(0.02)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(delay_ms / 1000.0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)


# ============================================================
# 截图对比
# ============================================================

def take_screenshot(name: str) -> str:
    p = os.path.join(OUTPUT_DIR, f"win32_{name}.png")
    ok = adb_screenshot(p)
    if ok:
        sz = os.path.getsize(p)
        print(f"  截图: {name} ({sz//1024}KB)")
    return p if ok else None


# ============================================================
# 测试流程
# ============================================================

print(f"\n{'='*70}")
print(f"  开始测试 - 请紧盯游戏画面")
print(f"{'='*70}")

# 激活窗口
user32.SetForegroundWindow(main_hwnd)
time.sleep(0.3)

# 截图前
take_screenshot("before_test")

# --- 测试1: 暂停按钮 (右上角) ---
print(f"\n[测试1] 暂停按钮 ({int(1920*0.95)}, {int(1080*0.05)})")
click_screen(1824, 54, "暂停按钮", delay_ms=100)
time.sleep(0.5)
# 再点一下恢复
click_screen(1824, 54, "恢复", delay_ms=100)
time.sleep(0.5)

# --- 测试2: 更明显的测试 - 点击编组 (屏幕下部数字面板) ---
print(f"\n[测试2] 编组切换 - 点击编组按钮区域")
# 编组数字在屏幕底部约 y=1040 区域, 大约是左下角
# 横屏菜单在底部有队形/命令/下车等按钮
# 尝试点一个应该能看到 UI 反应的位置

# 点屏幕中央偏下 - 如果点到单位会有选中效果
click_screen(960, 600, "屏幕中央偏下(选单位)", delay_ms=100)
time.sleep(0.3)

take_screenshot("after_center_click_v2")

# --- 测试3: 尝试编组按钮 ---
print(f"\n[测试3] 测试编组按钮区域")
# 之前的突破验证中, 点击 (1075, 540) 成功切换了编组
# 这次用正确的坐标映射再试
click_screen(1075, 540, "编组切换", delay_ms=100)
time.sleep(0.5)

take_screenshot("after_group_click_v2")

# --- 测试4: 尝试更大幅度的操作 ---
print(f"\n[测试4] 底部控制面板")
# 尝试点一些控制按钮 (可能在屏幕底部)
for y_ratio in [0.85, 0.90, 0.95]:
    gx = int(960)
    gy = int(1080 * y_ratio)
    click_screen(gx, gy, f"底部 y={y_ratio:.0%}", delay_ms=50)
    time.sleep(0.2)

take_screenshot("after_bottom_test")

# ============================================================
print(f"\n{'='*70}")
print(f"  测试完成!")
print(f"  截图保存于: {OUTPUT_DIR}")
print(f"  请告诉我:")
print(f"    1) 游戏画面有没有任何反应?")
print(f"    2) 如果有点击反应，是哪个测试步骤?")
print(f"    3) 有没有看到绿色选中框/菜单弹出/其他UI变化?")
print(f"{'='*70}")
