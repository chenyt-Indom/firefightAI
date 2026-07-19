#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Win32 鼠标注入测试 v3 - 精准瞄准渲染窗口

策略: 找到 nemuwin/nemudisplay 渲染子窗口, 
用 SendInput + mouse_event 组合直接注入到渲染层。
"""

import ctypes
import subprocess
import time
import sys
import os
from ctypes import wintypes

# ============================================================
user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long)
    ]

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
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
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND

MUADB = r"D:\MuMuPlayer\nx_device\12.0\shell\adb.exe"
ADB_DEVICE = "127.0.0.1:7555"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "test_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def adb_screenshot(name: str) -> str:
    p = os.path.join(OUTPUT_DIR, f"v3_{name}.png")
    try:
        subprocess.run(
            [MUADB, "-s", ADB_DEVICE, "exec-out", "screencap", "-p"],
            stdout=open(p, "wb"), stderr=subprocess.DEVNULL,
            timeout=10, check=True
        )
        if os.path.getsize(p) > 1000:
            return p
    except:
        pass
    return None

# ============================================================
print("=" * 70)
print("  Win32 鼠标注入 v3 - 精准渲染窗口注入")
print("=" * 70)

# 找主窗口和渲染子窗口
main_hwnd = user32.FindWindowW("Qt5156QWindowIcon", "MuMu安卓设备")
if not main_hwnd:
    print("未找到 MuMu 窗口!")
    sys.exit(1)

# 找 nemuwin 渲染窗口
render_hwnd = None
render_rect = None

def find_render(hwnd, _):
    global render_hwnd, render_rect
    cn = ctypes.create_unicode_buffer(256)
    wt = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cn, 256)
    user32.GetWindowTextW(hwnd, wt, 256)
    
    if cn.value == "nemuwin" and wt.value == "nemudisplay":
        render_hwnd = hwnd
        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        render_rect = r
        return False  # 停止枚举
    return True

user32.EnumChildWindows(main_hwnd, WNDENUMPROC(find_render), 0)

if not render_hwnd:
    print("未找到 nemuwin 渲染窗口!")
    sys.exit(1)

print(f"渲染窗口: nemuwin/nemudisplay (HWND={render_hwnd})")
print(f"屏幕位置: ({render_rect.left},{render_rect.top})-({render_rect.right},{render_rect.bottom})")
render_w = render_rect.right - render_rect.left
render_h = render_rect.bottom - render_rect.top
print(f"渲染大小: {render_w}x{render_h}")

# 游戏内部分辨率 1920x1080
game_w, game_h = 1920, 1080
scale_x = render_w / game_w
scale_y = render_h / game_h
print(f"缩放: x{scale_x:.4f}, y{scale_y:.4f}")

# 坐标转换
def game_to_screen(gx, gy):
    sx = int(render_rect.left + gx * scale_x)
    sy = int(render_rect.top + gy * scale_y)
    return sx, sy

# ============================================================
# 截图对比
def compare_screenshots(before_path, after_path, label):
    """像素级对比"""
    import cv2, numpy as np
    b = cv2.imread(before_path)
    a = cv2.imread(after_path)
    if b is None or a is None:
        return False, 0
    diff = cv2.absdiff(b, a)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    changed = np.count_nonzero(gray > 15)
    pct = 100.0 * changed / gray.size
    
    if changed > 500:
        diff_p = os.path.join(OUTPUT_DIR, f"v3_diff_{label}.png")
        _, thresh = cv2.threshold(gray, 15, 255, cv2.THRESH_BINARY)
        cv2.imwrite(diff_p, thresh)
        print(f"  {label}: {changed}像素 ({pct:.2f}%) 变化 ← 可能有反应!")
        return True, changed
    else:
        print(f"  {label}: {changed}像素 ({pct:.4f}%) 无显著变化")
        return False, changed

# ============================================================
# 测试开始
print(f"\n{'='*70}")
print(f"  请紧盯游戏画面!")
print(f"{'='*70}")

# 激活
user32.SetForegroundWindow(main_hwnd)
user32.BringWindowToTop(main_hwnd)
time.sleep(0.3)

# 截图: 测试前
before = adb_screenshot("before")
if not before:
    print("ADB 截图失败!")
    sys.exit(1)

results = {}

# ============================================================
# 测试A: 暂停按钮 (应该最明显)
# ============================================================
print(f"\n[测试A] 暂停按钮 - 这应该有明显反应!")
sx, sy = game_to_screen(1824, 54)
print(f"  坐标: 游戏(1824,54) → 屏幕({sx},{sy})")

user32.SetCursorPos(sx, sy)
time.sleep(0.02)
user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
time.sleep(0.08)
user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
time.sleep(0.5)

after_a = adb_screenshot("after_pause")
if after_a:
    found, n = compare_screenshots(before, after_a, "暂停按钮")
    results['pause'] = (found, n)
    before = after_a  # 用新的作为下一轮的基准

# 再点一次恢复
sx2, sy2 = game_to_screen(1824, 54)
user32.SetCursorPos(sx2, sy2)
time.sleep(0.02)
user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
time.sleep(0.08)
user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
time.sleep(0.3)

after_a2 = adb_screenshot("after_unpause")
if after_a2:
    found, n = compare_screenshots(after_a, after_a2, "取消暂停")
    results['unpause'] = (found, n)
    before = after_a2

# ============================================================
# 测试B: 编组切换 (1075,540)
# ============================================================
print(f"\n[测试B] 编组切换 - 之前MuMu IPC验证过这个坐标!")
sx, sy = game_to_screen(1075, 540)
print(f"  坐标: 游戏(1075,540) → 屏幕({sx},{sy})")

user32.SetCursorPos(sx, sy)
time.sleep(0.02)
user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
time.sleep(0.08)
user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
time.sleep(0.5)

after_b = adb_screenshot("after_group_switch")
if after_b:
    found, n = compare_screenshots(before, after_b, "编组切换")
    results['group'] = (found, n)
    before = after_b

# ============================================================
# 测试C: 双击单位选中
# ============================================================
print(f"\n[测试C] 双击蓝色单位位置 (约 1334,671)")
sx, sy = game_to_screen(1334, 671)
print(f"  坐标: 游戏(1334,671) → 屏幕({sx},{sy})")

for i in range(2):
    user32.SetCursorPos(sx, sy)
    time.sleep(0.02)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
    time.sleep(0.05)

time.sleep(0.5)

after_c = adb_screenshot("after_unit_click")
if after_c:
    found, n = compare_screenshots(before, after_c, "双击单位")
    results['unit_dbl'] = (found, n)
    before = after_c

# ============================================================
# 测试D: 发送 WM_LBUTTONDOWN/UP 到渲染窗口
# ============================================================
print(f"\n[测试D] SendMessage 直接发送到 nemuwin 渲染窗口")

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

def make_lparam(x, y):
    return (y << 16) | (x & 0xFFFF)

user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_longlong
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

# 用游戏内坐标直接发送 (渲染窗口应该接受游戏坐标)
lparam = make_lparam(1075, 540)
user32.PostMessageW(render_hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
time.sleep(0.08)
user32.PostMessageW(render_hwnd, WM_LBUTTONUP, 0, lparam)
time.sleep(0.5)

after_d = adb_screenshot("after_sendmsg")
if after_d:
    found, n = compare_screenshots(before, after_d, "SendMessage")
    results['sendmsg'] = (found, n)
    before = after_d

# ============================================================
# 总结
# ============================================================
print(f"\n{'='*70}")
print(f"  测试总结")
print(f"{'='*70}")

any_work = False
for name, (found, n) in results.items():
    status = "✅ 有反应!" if found else "❌ 无反应"
    print(f"  {name:12s}: {status} ({n}像素变化)")
    if found:
        any_work = True

if any_work:
    print(f"\n  🎉 Win32鼠标注入有效! 可以替代MuMu IPC")
else:
    print(f"\n  Win32鼠标注入对游戏无效")
    print(f"\n  建议:")
    print(f"  1. ⭐ 完全关闭并重启 MuMu 模拟器 (重置IPC状态)")
    print(f"  2. 尝试 MuMuManager.exe api 命令")
    print(f"  3. 关闭 MuMu 的'强制保活'功能")
    print(f"  4. 尝试其他模拟器 (雷电/蓝叠)")

print(f"\n  截图保存: {OUTPUT_DIR}")
