# -*- coding: utf-8 -*-
"""Screenshot helper: find LiuScope window, bring to front, grab via PIL.ImageGrab."""
import ctypes
import sys
import time

from PIL import ImageGrab

user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
IsWindowVisible = user32.IsWindowVisible
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
SetForegroundWindow = user32.SetForegroundWindow
ShowWindow = user32.ShowWindow
GetWindowRect = user32.GetWindowRect

SW_RESTORE = 9


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def find_windows(substr):
    hits = []
    buf = ctypes.create_unicode_buffer(512)

    @EnumWindowsProc
    def cb(hwnd, _):
        if IsWindowVisible(hwnd):
            n = GetWindowTextLengthW(hwnd)
            if n:
                GetWindowTextW(hwnd, buf, 512)
                t = buf.value
                if substr.lower() in t.lower():
                    r = RECT()
                    GetWindowRect(hwnd, ctypes.byref(r))
                    hits.append((hwnd, t, (r.left, r.top, r.right, r.bottom)))
        return True
    EnumWindows(cb, None)
    return hits


def shot(out_path, title_substr="LiuScope", window=True):
    hits = find_windows(title_substr)
    if not hits:
        print("NO WINDOW MATCHING", title_substr)
        return None
    hwnd, title, rect = hits[0]
    ShowWindow(hwnd, SW_RESTORE)
    try:
        SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.6)
    if window:
        img = ImageGrab.grab(bbox=rect, all_screens=True)
    else:
        img = ImageGrab.grab(all_screens=True)
    img.save(out_path)
    print("shot:", out_path, "title:", title, "rect:", rect, "size:", img.size)
    return out_path


if __name__ == "__main__":
    out = sys.argv[1]
    sub = sys.argv[2] if len(sys.argv) > 2 else "LiuScope"
    full = len(sys.argv) > 3 and sys.argv[3] == "full"
    shot(out, sub, window=not full)
