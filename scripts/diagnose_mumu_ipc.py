#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MuMu IPC 全面诊断脚本

尝试多种方式诊断和修复触控注入问题：
1. nemu_connect 连接测试
2. nemu_get_display_id（多个 app_index）
3. nemu_capture_display 验证连通性
4. 尝试多个 display_id 发送触控
5. try finger_touch 变体 API
6. 尝试重启连接
"""

import ctypes
import os
import sys
import time
from pathlib import Path

# 添加项目目录
sys.path.insert(0, str(Path(__file__).parent.parent))

# ============================================================
# DLL 加载
# ============================================================

DLL_PATH = r"D:\MuMuPlayer\nx_device\12.0\shell\sdk\external_renderer_ipc.dll"
INSTALL_PATH = r"D:\MuMuPlayer"
PACKAGE_NAME = b"com.windowsgames.firefightbw"

print("=" * 70)
print("  MuMu IPC 全面诊断")
print("=" * 70)

# 检查 DLL
print(f"\n[1] DLL 检查: {DLL_PATH}")
print(f"    存在: {os.path.exists(DLL_PATH)}")

# 加载 DLL
try:
    dll = ctypes.windll.LoadLibrary(DLL_PATH)
    print(f"    加载成功")
except Exception as e:
    print(f"    加载失败: {e}")
    sys.exit(1)

# ============================================================
# 列出所有导出函数
# ============================================================

print(f"\n[2] 尝试列出导出函数...")
# nemu_connect 已知签名
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

# ============================================================
# 测试 nemu_connect
# ============================================================

print(f"\n[3] nemu_connect 测试")
print(f"    安装路径: {INSTALL_PATH}")

for idx in range(3):
    handle = dll.nemu_connect(ctypes.c_wchar_p(INSTALL_PATH), ctypes.c_int(idx))
    print(f"    instance_index={idx}: handle={handle}")
    if handle > 0:
        dll.nemu_disconnect(ctypes.c_int(handle))

# ============================================================
# 主连接 + 多 display_id 测试
# ============================================================

print(f"\n[4] 连接并尝试获取 display_id")

handle = dll.nemu_connect(ctypes.c_wchar_p(INSTALL_PATH), ctypes.c_int(0))
print(f"    nemu_connect(instance=0): handle={handle}")

if handle <= 0:
    print("    连接失败，终止诊断")
    sys.exit(1)

# 测试不同的 app_index
for app_idx in range(3):
    display_id = dll.nemu_get_display_id(
        ctypes.c_int(handle),
        ctypes.c_char_p(PACKAGE_NAME),
        ctypes.c_int(app_idx),
    )
    print(f"    app_index={app_idx}: display_id={display_id}")

# ============================================================
# 测试 nemu_capture_display
# ============================================================

print(f"\n[5] 测试 nemu_capture_display (截图验证连通性)")

# 签名推断：nemu_capture_display(handle, display_id, buffer_size, width, height)
# 返回可能是 buffer 指针或 int
try:
    dll.nemu_capture_display.argtypes = [
        ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    ]
    dll.nemu_capture_display.restype = ctypes.c_int

    buf_size = ctypes.c_int(0)
    width = ctypes.c_int(0)
    height = ctypes.c_int(0)

    for display_id in [0, 1, 2]:
        ret = dll.nemu_capture_display(
            ctypes.c_int(handle), ctypes.c_int(display_id),
            ctypes.c_int(0),
            ctypes.byref(buf_size), ctypes.byref(width), ctypes.byref(height)
        )
        print(f"    display_id={display_id}: ret={ret}, buf_size={buf_size.value}, "
              f"size={width.value}x{height.value}")
except Exception as e:
    print(f"    nemu_capture_display 调用失败: {e}")
    print("    尝试简化签名...")
    try:
        dll.nemu_capture_display.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        dll.nemu_capture_display.restype = ctypes.c_int
        for display_id in [0, 1, 2]:
            ret = dll.nemu_capture_display(
                ctypes.c_int(handle), ctypes.c_int(display_id),
                ctypes.c_int(0), ctypes.c_int(0), ctypes.c_int(0)
            )
            print(f"    display_id={display_id}: ret={ret}")
    except Exception as e2:
        print(f"    简化签名也失败: {e2}")

# ============================================================
# 触控测试 - 所有 display_id
# ============================================================

print(f"\n[6] 触控测试 - 尝试所有 display_id (0-4)")

# 测试坐标：屏幕中央
test_x, test_y = 960, 540

for display_id in range(5):
    try:
        ret_down = dll.nemu_input_event_touch_down(
            ctypes.c_int(handle), ctypes.c_int(display_id),
            ctypes.c_int(test_x), ctypes.c_int(test_y)
        )
        time.sleep(0.05)
        ret_up = dll.nemu_input_event_touch_up(
            ctypes.c_int(handle), ctypes.c_int(display_id)
        )
        time.sleep(0.05)
        print(f"    display_id={display_id}: touch_down={ret_down}, touch_up={ret_up}")
    except Exception as e:
        print(f"    display_id={display_id}: 异常 - {e}")

# ============================================================
# 尝试 finger_touch API
# ============================================================

print(f"\n[7] 尝试 nemu_input_event_finger_touch API")

for func_name in [
    "nemu_input_event_finger_touch_down",
    "nemu_input_event_finger_touch_up",
]:
    try:
        func = getattr(dll, func_name)
        print(f"    找到: {func_name}")
        if "down" in func_name:
            func.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            func.restype = ctypes.c_int
            for display_id in [0, 1]:
                ret = func(
                    ctypes.c_int(handle), ctypes.c_int(display_id),
                    ctypes.c_int(test_x), ctypes.c_int(test_y)
                )
                print(f"      display_id={display_id}: ret={ret}")
        else:
            func.argtypes = [ctypes.c_int, ctypes.c_int]
            func.restype = ctypes.c_int
            for display_id in [0, 1]:
                ret = func(ctypes.c_int(handle), ctypes.c_int(display_id))
                print(f"      display_id={display_id}: ret={ret}")
    except AttributeError:
        print(f"    未找到: {func_name} (DLL 未导出)")
    except Exception as e:
        print(f"    调用 {func_name} 异常: {e}")

# ============================================================
# 测试 key 事件
# ============================================================

print(f"\n[8] 尝试 key 事件 (nemu_input_event_key_down/up)")

for func_name in [
    "nemu_input_event_key_down",
    "nemu_input_event_key_up",
]:
    try:
        func = getattr(dll, func_name)
        print(f"    找到: {func_name}")
        if "down" in func_name:
            func.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        else:
            func.argtypes = [ctypes.c_int, ctypes.c_int]
        func.restype = ctypes.c_int
    except AttributeError:
        print(f"    未找到: {func_name}")

# ============================================================
# 断开连接
# ============================================================

print(f"\n[9] 清理")
dll.nemu_disconnect(ctypes.c_int(handle))
print(f"    已断开连接")

print(f"\n" + "=" * 70)
print(f"  诊断完成")
print(f"=" * 70)
print(f"\n[建议]")
print(f"  如果所有 display_id 都没反应:")
print(f"  1. 完全关闭并重启 MuMu 模拟器")
print(f"  2. 关闭 MuMu 的'强制保活'功能")
print(f"  3. 确保游戏在前台运行")
print(f"  4. 关闭 MuMu 的键鼠映射功能")
