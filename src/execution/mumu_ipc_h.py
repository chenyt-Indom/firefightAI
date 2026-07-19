"""MuMu IPC 触控辅助 - 封装 finger touch API"""
import ctypes
from loguru import logger

_DLL = None
_HANDLE = 0

def ipc_connect() -> bool:
    global _DLL, _HANDLE
    if _DLL and _HANDLE > 0:
        return True
    try:
        _DLL = ctypes.windll.LoadLibrary(
            r"D:\MuMuPlayer\nx_device\12.0\shell\sdk\external_renderer_ipc.dll"
        )
        _DLL.nemu_connect.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
        _DLL.nemu_connect.restype = ctypes.c_int
        _HANDLE = _DLL.nemu_connect(ctypes.c_wchar_p(r"D:\MuMuPlayer"), 0)
        if _HANDLE <= 0:
            logger.warning(f"IPC连接失败: {_HANDLE}")
            return False
        return True
    except Exception as e:
        logger.warning(f"IPC初始化异常: {e}")
        return False

def ipc_touch_down(x: int, y: int, fid: int = 0) -> int:
    global _DLL, _HANDLE
    if not _DLL or _HANDLE <= 0:
        return -1
    try:
        _DLL.nemu_input_event_finger_touch_down.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int
        ]
        _DLL.nemu_input_event_finger_touch_down.restype = ctypes.c_int
        return _DLL.nemu_input_event_finger_touch_down(_HANDLE, 0, x, y, fid)
    except Exception:
        return -1

def ipc_touch_up(fid: int = 0) -> int:
    global _DLL, _HANDLE
    if not _DLL or _HANDLE <= 0:
        return -1
    try:
        _DLL.nemu_input_event_finger_touch_up.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int
        ]
        _DLL.nemu_input_event_finger_touch_up.restype = ctypes.c_int
        return _DLL.nemu_input_event_finger_touch_up(_HANDLE, 0, fid)
    except Exception:
        return -1
