# -*- coding: utf-8 -*-
"""ctypes SendInput-based GUI input helper (human-style black-box operation).

Coordinates: window-relative pixels of the LiuScope window (same pixel space as
screenshot.py grabs), converted to absolute screen coords at call time.
"""
import ctypes
import time

from screenshot import find_windows

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", INPUTunion)]


def _win_rect(title_substr="LiuScope"):
    hits = find_windows(title_substr)
    if not hits:
        raise RuntimeError("LiuScope window not found")
    return hits[0][2]  # (l, t, r, b)


def _send(inputs):
    n = len(inputs)
    arr = (INPUT * n)()
    for i, obj in enumerate(inputs):
        arr[i] = obj
    user32.SendInput(n, arr, ctypes.sizeof(INPUT))


def _mk_mouse(flags, ax, ay, data=0):
    mi = MOUSEINPUT(ax, ay, data, flags, 0, None)
    return INPUT(INPUT_MOUSE, INPUTunion(mi=mi))


def _mk_key(vk, scan=0, flags=0):
    ki = KEYBDINPUT(vk, scan, flags, 0, None)
    return INPUT(INPUT_KEYBOARD, INPUTunion(ki=ki))


def _to_abs(x, y):
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    ax = int((x - vx) * 65535 / (vw - 1))
    ay = int((y - vy) * 65535 / (vh - 1))
    return ax, ay


MOUSE_STEP = MOUSEEVENTF_VIRTUALDESK | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE


def _mouse(flags, sx, sy, data=0):
    ax, ay = _to_abs(sx, sy)
    _send([_mk_mouse(MOUSE_STEP | flags, ax, ay, data)])


def win_rect(title="LiuScope"):
    return _win_rect(title)


def click(rx, ry, title="LiuScope", delay=0.12, right=False):
    """Click at window-relative coords (rx, ry)."""
    l, t, r, b = _win_rect(title)
    sx, sy = l + rx, t + ry
    _mouse(0, sx, sy)
    time.sleep(0.05)
    if right:
        _mouse(MOUSEEVENTF_RIGHTDOWN, sx, sy)
        _mouse(MOUSEEVENTF_RIGHTUP, sx, sy)
    else:
        _mouse(MOUSEEVENTF_LEFTDOWN, sx, sy)
        _mouse(MOUSEEVENTF_LEFTUP, sx, sy)
    time.sleep(delay)


def double_click(rx, ry, title="LiuScope"):
    click(rx, ry, title, delay=0.05)
    click(rx, ry, title, delay=0.12)


def wheel(rx, ry, delta, title="LiuScope"):
    """delta>0 scroll up."""
    l, t, _, _ = _win_rect(title)
    sx, sy = l + rx, t + ry
    _mouse(0, sx, sy)
    ax, ay = _to_abs(sx, sy)
    _send([_mk_mouse(MOUSE_STEP | MOUSEEVENTF_WHEEL, ax, ay, int(delta * 120))])
    time.sleep(0.1)


def key(name, hold=0.03):
    VK = {"enter": 0x0D, "esc": 0x1B, "tab": 0x09, "back": 0x08, "del": 0x2E,
          "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "home": 0x24, "end": 0x23,
          "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
          "pageup": 0x21, "pagedown": 0x22, "space": 0x20,
          "f": 0x46, "a": 0x41, "c": 0x43, "v": 0x56}
    v = VK.get(name.lower())
    if v is None:
        raise ValueError("unknown key " + name)
    _send([_mk_key(v, 0, 0)])
    time.sleep(hold)
    _send([_mk_key(v, 0, KEYEVENTF_KEYUP)])
    time.sleep(0.05)


def chord(*names, hold=0.04):
    VK = {"enter": 0x0D, "esc": 0x1B, "tab": 0x09, "shift": 0x10, "ctrl": 0x11,
          "alt": 0x12, "f": 0x46, "a": 0x41, "c": 0x43, "v": 0x56}
    downs, ups = [], []
    for n in names:
        v = VK[n.lower()]
        downs.append(_mk_key(v, 0, 0))
    for n in reversed(names):
        v = VK[n.lower()]
        ups.append(_mk_key(v, 0, KEYEVENTF_KEYUP))
    _send(downs)
    time.sleep(hold)
    _send(ups)
    time.sleep(0.05)


def type_text(text):
    """Unicode typing via KEYEVENTF_UNICODE."""
    for ch in text:
        scans = [ord(ch)] if ord(ch) <= 0xFFFF else [0xD800 + ((ord(ch) - 0x10000) >> 10), 0xDC00 + ((ord(ch) - 0x10000) & 0x3FF)]
        for s in scans:
            _send([_mk_key(0, s, KEYEVENTF_UNICODE),
                   _mk_key(0, s, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)])
        time.sleep(0.01)
    time.sleep(0.05)


def focus_window(title="LiuScope"):
    import ctypes as c
    hits = find_windows(title)
    if not hits:
        raise RuntimeError("window not found")
    hwnd = hits[0][0]
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    return hits[0][2]
