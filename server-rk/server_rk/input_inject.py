# -*- coding: utf-8 -*-
"""input_inject.py — 键鼠注入（设计稿 §2/§4：PC 事件批经控制通道下行 → uinput 注入）。

python-uinput 仅 Linux 可用：import 放 try 块，失败时优雅降级——is_available=False，
apply_events 只记日志、不抛异常（Windows 单测 / 无 uinput 的板均不崩，M3 装 udev 规则后生效）。

键值映射：LiuScope PC 端下发 Qt.Key_* 数值 → Linux input 事件码。
映射表是纯数据（不依赖 uinput import 成功），可在任何平台单测校验。
事件批格式（与设计稿 §4 对齐）：{"cmd":"input","events":[
    {"t":"key","key":<Qt键码>,"down":true|false},
    {"t":"mouse_move","dx":N,"dy":N},
    {"t":"mouse_abs","x":N,"y":N},          ← 绝对定位（跟手镜像；量程=板屏分辨率）
    {"t":"mouse_button","btn":"left|right|middle","down":true|false},
    {"t":"wheel","dx":N,"dy":N}, ...]}
"""
from __future__ import annotations

import logging
import os
import re
import struct
import time

log = logging.getLogger(__name__)

try:
    import fcntl           # POSIX 专用：Windows 单测环境无此模块，走优雅降级
    _FCNTL_OK = True
except Exception:
    fcntl = None
    _FCNTL_OK = False

try:
    import uinput  # python-uinput（仅 Linux；板上需 /dev/uinput 权限，见 README）
    _UINPUT_OK = True
except Exception:
    uinput = None
    _UINPUT_OK = False

# Linux input 事件码（include/uapi/linux/input-event-codes.h）
EV_KEY, EV_REL = 0x01, 0x02
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0x00, 0x01, 0x06, 0x08
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112

# ---- Qt.Key_* → (ev_type, code) 映射（先覆盖常用键；未知键记日志忽略，后续按需扩充）----
_KEY_MAP = {0x41 + i: (EV_KEY, 30 + i) for i in range(26)}   # 'A'..'Z' → KEY_A..KEY_Z（顺序天然对齐）
for _i in range(1, 10):
    _KEY_MAP[0x30 + _i] = (EV_KEY, 1 + _i)                   # '1'..'9' → KEY_1..KEY_9
_KEY_MAP[0x30] = (EV_KEY, 11)                                # '0' → KEY_0
_KEY_MAP.update({
    0x20:       (EV_KEY, 57),    # Space
    0x01000000: (EV_KEY, 1),     # Escape
    0x01000001: (EV_KEY, 15),    # Tab
    0x01000003: (EV_KEY, 14),    # Backspace
    0x01000004: (EV_KEY, 28),    # Return（主回车）
    0x01000005: (EV_KEY, 96),    # Enter（小键盘）
    0x01000006: (EV_KEY, 110),   # Insert
    0x01000007: (EV_KEY, 111),   # Delete
    0x01000010: (EV_KEY, 102),   # Home
    0x01000011: (EV_KEY, 107),   # End
    0x01000012: (EV_KEY, 105),   # Left
    0x01000013: (EV_KEY, 103),   # Up
    0x01000014: (EV_KEY, 106),   # Right
    0x01000015: (EV_KEY, 108),   # Down
    0x01000016: (EV_KEY, 104),   # PageUp
    0x01000017: (EV_KEY, 109),   # PageDown
    0x01000020: (EV_KEY, 42),    # Shift
    0x01000021: (EV_KEY, 29),    # Ctrl
    0x01000022: (EV_KEY, 125),   # Meta
    0x01000023: (EV_KEY, 56),    # Alt
})
_MOUSE_BTN = {"left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE}

# ABS 绝对定位鼠标（跟手的关键：相对移动会漂移，绝对坐标与画面一一对应）
EV_ABS = 0x03
ABS_X, ABS_Y = 0x00, 0x01

DEVICE_NAME = "liuscope-server-uinput"


# X 指针子进程：本板 libX11 的线程实现有缺陷——XWarpPointer 在任何非主线程
# 段错误（XInitThreads 也救不了，主线程单发无恙）。故把 X 连接整个搬进一个
# 专用子进程：它的主线程守着 stdin 循环 warp（管道 8 字节一条，40Hz 毫无压力）。
_X_CHILD_SRC = """
import os, struct, sys
sys.path.insert(0, os.environ.get('PYTHONPATH', '/home/cat').split(':')[0])
from server_rk.input_inject import XPointer
p = XPointer(sys.argv[1], os.environ.get('XAUTHORITY') or None)
while True:
    data = sys.stdin.buffer.read(8)     # 阻塞读；EOF（父进程关闭）即退出
    if len(data) < 8:
        break
    x, y = struct.unpack('ii', data)
    p.warp(x, y)
"""


class XWarpChannel:
    """XPointer 子进程通道：warp(x, y) 写 8 字节进管道，子进程主线程执行。"""

    def __init__(self, display: str, auth: str | None):
        import subprocess
        import sys as _sys
        env = {**os.environ}
        if auth:
            env["XAUTHORITY"] = auth
        self._proc = subprocess.Popen(
            [_sys.executable, "-c", _X_CHILD_SRC, display],
            stdin=subprocess.PIPE, env=env)
        self._w = self._proc.stdin

    def warp(self, x: int, y: int):
        self._w.write(struct.pack("ii", int(x), int(y)))
        self._w.flush()

    def alive(self) -> bool:
        return self._proc.poll() is None

    def close(self):
        try:
            self._w.close()
        except OSError:
            pass


class XPointer:
    """X11 真指针绝对定位（libX11 XWarpPointer，ctypes 零依赖）。

    为什么不用 uinput ABS 直接驱动 X：ABS 指针设备无 BTN_TOUCH 会被 X/libinput
    归类为非指针（触摸屏需要接触态才动光标），真板实测光标纹丝不动；而 X 对
    REL 指针类设备只认相对量，绝对"跟手"无从谈起。XWarpPointer 是 X 服务端
    原生移指针（VNC/xdotool 同族机制），x11grab 画面里的光标立刻跟着走。
    uinput 保留负责按键/滚轮/点击（REL 指针类设备的按钮/滚轮 X 认）。
    """

    def __init__(self, display: str, auth: str | None):
        import ctypes
        self._x11 = ctypes.CDLL("libX11.so.6")
        # Xlib 缺省非线程安全：X 连接活在控制线程（非主线程）——不先 XInitThreads
        # 真板实测在非主线程 XOpenDisplay/warp 直接段错误（主线程单发无恙）
        self._x11.XInitThreads.restype = ctypes.c_int
        self._x11.XInitThreads()
        self._x11.XOpenDisplay.restype = ctypes.c_void_p
        self._x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        if auth:
            os.environ["XAUTHORITY"] = auth    # Xlib 在 open 时读进程环境
        self._dpy = self._x11.XOpenDisplay(display.encode())
        if not self._dpy:
            raise OSError(f"XOpenDisplay({display!r}) 失败（会话/授权不可用）")
        # 根窗口：dest_w 必须是根，dest_x/dest_y 才是根坐标系的绝对坐标
        #（dest_w=None 时 XWarpPointer 是相对移动——真板实测偏移叠加+钳边）
        self._x11.XRootWindow.restype = ctypes.c_uint64
        self._x11.XRootWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._root = self._x11.XRootWindow(self._dpy, 0)
        self._x11.XWarpPointer.restype = ctypes.c_int
        # (dpy, src_w=None(不限), dest_w=root, src 0,0,0,0, dest_x, dest_y)
        self._warp = self._x11.XWarpPointer
        self._warp.argtypes = [ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint,
                               ctypes.c_uint, ctypes.c_int, ctypes.c_int]

    def warp(self, x: int, y: int):
        self._warp(self._dpy, 0, self._root, 0, 0, 0, 0, int(x), int(y))
        self._x11.XFlush(self._dpy)   # Xlib 输出缓冲：不 flush 小请求会滞留不生效

    def close(self):
        self._x11.XCloseDisplay(self._dpy)

# 板端屏幕尺寸（uinput ABS 轴量程）：PC 端把画面坐标换算成板屏像素后发绝对值。
# 可用环境变量 SERVER_RK_SCREEN=WxH 覆盖（默认鲁班猫 5 寸屏 1024x600）。
_SCREEN_DEFAULT = (1024, 600)


def screen_size() -> tuple[int, int]:
    raw = os.environ.get("SERVER_RK_SCREEN", "").lower()
    m = re.match(r"^(\d+)x(\d+)$", raw) if raw else None
    return (int(m.group(1)), int(m.group(2))) if m else _SCREEN_DEFAULT


class UinputRawDevice:
    """零依赖 uinput 写入器（python-uinput 装不上时的兜底；仅 Linux + root）。

    板上 Debian 开 python-uinput 要编译 C 扩展（python3-dev + gcc），PEP 668 又
    挡系统 pip——直接走 /dev/uinput ioctl（UI_SET_*BIT + uinput_user_dev + 写
    input_event），daemon 以 root 跑天然有权限。events 形如 ("EV_KEY"/"EV_REL"/
    "EV_ABS", code, absinfo|None)。
    """

    # ioctl 请求号（linux/uinput.h，64 位 sizeof(unsigned int) 编码）
    _UI_SET_EVBIT = 0x40045564
    _UI_SET_KEYBIT = 0x40045565
    _UI_SET_RELBIT = 0x40045566
    _UI_SET_ABSBIT = 0x40045567
    _UI_DEV_CREATE = 0x5501
    _UI_DEV_DESTROY = 0x5502
    _ABS_CNT = 64                    # ABS_MAX+1
    _EVT = struct.Struct("qqHHi")    # struct input_event（64 位 timeval）

    # 事件规格兼容两种写法：("EV_KEY", code)（字符串名，仅测试手写）与
    # (0x01, code)（int 类型码，_ensure_device 设备表的真实形态）
    _TYPE_VALS = {"EV_KEY": 0x01, "EV_REL": 0x02, "EV_ABS": 0x03}
    _TYPE_BITS = {0x01: _UI_SET_KEYBIT, 0x02: _UI_SET_RELBIT, 0x03: _UI_SET_ABSBIT}

    @classmethod
    def _ev_type(cls, ev) -> int:
        return ev[0] if isinstance(ev[0], int) else cls._TYPE_VALS[ev[0]]

    def __init__(self, events, name=DEVICE_NAME):
        self._fd = os.open("/dev/uinput", os.O_RDWR)
        try:
            seen_types = set()
            absmax = [0] * self._ABS_CNT
            absmin = [0] * self._ABS_CNT
            for ev in events:
                tval, code = self._ev_type(ev), int(ev[1])
                if tval not in seen_types:      # 类型位只设一次（重复设置无害但省略）
                    fcntl.ioctl(self._fd, self._UI_SET_EVBIT, tval)
                    seen_types.add(tval)
                fcntl.ioctl(self._fd, self._TYPE_BITS[tval], code)
                if tval == 0x03 and len(ev) >= 4:        # ABS 附带量程 (t,code,min,max,…)
                    absmin[code], absmax[code] = int(ev[2]), int(ev[3])
            # uinput_user_dev：name[80] + id[4]u16 + ff_effects_max + absmin/absmax/
            # absfuzz/absflat 各 ABS_CNT 个 s32（内核头顺序：max 在前）
            blob = struct.pack(f"80s4Hi{self._ABS_CNT}i{self._ABS_CNT}i"
                               f"{self._ABS_CNT}i{self._ABS_CNT}i",
                               name.encode()[:80], 0x06, 1, 1, 1, 0,
                               *absmax, *absmin, *([0] * self._ABS_CNT) * 2)
            os.write(self._fd, blob)
            fcntl.ioctl(self._fd, self._UI_DEV_CREATE)
            self._name = name
        except Exception:
            os.close(self._fd)
            raise

    def emit(self, ev, value: int):
        """单事件落设备（语义对齐 python-uinput：每 emit 自带 SYN_REPORT——
        X/evdev 只在 SYN 边界取事件，漏了 SYN 事件会积在内核缓冲永不生效）。"""
        tval = self._ev_type(ev)
        now = time.time()
        sec, usec = int(now), int(now * 1e6 % 1_000_000)
        self._fd_write(self._EVT.pack(sec, usec, tval, int(ev[1]), value))
        self._fd_write(self._EVT.pack(sec, usec, 0x00, 0x00, 0))   # EV_SYN SYN_REPORT

    def _fd_write(self, blob: bytes):
        os.write(self._fd, blob)

    def close(self):
        try:
            fcntl.ioctl(self._fd, self._UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self._fd)


def _default_device_factory(events, name):
    """真实 uinput 设备工厂（events 为 ("EV_KEY"/"EV_REL", code) 元组列表）。"""
    return uinput.Device(events, name=name)


class InputInjector:
    """uinput 虚拟键鼠：懒创建设备；device_factory 可注入（测试用 fake 记录 emit）。

    绝对定位走 XPointer（XWarpPointer，pointer_env 由控制侧从视频管线喂
    DISPLAY/XAUTHORITY——uinput ABS 驱不动 X 光标，见 XPointer 注）；X 不可用时
    mouse_abs 退回 uinput ABS（控制台/无 X 场景）。
    """

    def __init__(self, device_factory=None):
        self._factory = device_factory or _default_device_factory
        self._injected_factory = device_factory is not None
        self._device = None
        self._warned = False
        self._xptr = None
        self._xptr_failed = False
        self.pointer_env: dict | None = None   # {'DISPLAY':…, 'XAUTHORITY':…}，控制侧喂

    def _ensure_xptr(self):
        """懒建 X 指针子进程通道；失败只记一次（后续 mouse_abs 落 uinput ABS 兜底）。

        不直接在本线程用 XPointer：本板 libX11 非主线程 warp 必段错误（见
        XWarpChannel 注）。子进程死了自动重拉一次，再失败才降级。
        """
        ch = self._xptr
        if ch is not None:
            if ch.alive():
                return ch
            try:
                ch.close()
            except Exception:
                pass
            self._xptr = None
            self._xptr_retries = getattr(self, "_xptr_retries", 0) + 1
            if self._xptr_retries > 3:      # 连挂 3 次：X 环境有问题，别再折腾
                self._xptr_failed = True
                return None
        if self._xptr_failed:
            return None
        env = self.pointer_env or {}
        display = env.get("DISPLAY") or os.environ.get("DISPLAY")
        if not display:
            self._xptr_failed = True
            return None
        try:
            ch = XWarpChannel(display, env.get("XAUTHORITY"))
            self._xptr = ch
            log.info("X 指针子进程已起（display=%s）", display)
            return ch
        except Exception as exc:
            log.info("X11 指针通道不可用（%s），mouse_abs 落 uinput ABS", exc)
            self._xptr_failed = True
            return None

    @property
    def is_available(self) -> bool:
        """python-uinput 可导入 / raw ioctl 可用（root + /dev/uinput）/ 测试注入工厂。"""
        return (_UINPUT_OK or self._injected_factory
                or (_FCNTL_OK and os.path.exists("/dev/uinput")))

    def _ensure_device(self):
        if self._device is not None:
            return self._device
        if not self.is_available:
            if not self._warned:  # 只告警一次，避免日志刷屏
                log.warning("uinput 不可用：键鼠注入降级为仅日志（需 python-uinput 或 root+/dev/uinput）")
                self._warned = True
            return None
        w, h = screen_size()
        events = sorted({*_KEY_MAP.values(),
                         *((EV_KEY, c) for c in _MOUSE_BTN.values()),
                         (EV_REL, REL_X), (EV_REL, REL_Y),
                         (EV_REL, REL_WHEEL), (EV_REL, REL_HWHEEL),
                         (EV_ABS, ABS_X, 0, w - 1, 0, 0),
                         (EV_ABS, ABS_Y, 0, h - 1, 0, 0)})
        try:
            self._device = self._factory(events, name=DEVICE_NAME)
        except Exception as exc:
            # python-uinput 没装/装不了：零依赖 raw ioctl 兜底（root + /dev/uinput）
            if _FCNTL_OK and os.path.exists("/dev/uinput"):
                log.info("python-uinput 不可用（%s），改用 /dev/uinput raw ioctl", exc)
                self._device = UinputRawDevice(events, name=DEVICE_NAME)
            else:
                raise
        return self._device

    def apply_events(self, events):
        """应用一批事件，返回 (applied, skipped)。单条失败只记日志，不影响整批。"""
        applied = skipped = 0
        for ev in events or []:
            try:
                if self._apply_one(ev):
                    applied += 1
                else:
                    skipped += 1
            except Exception as exc:
                log.warning("注入事件失败 %r: %s", ev, exc)
                skipped += 1
        return applied, skipped

    def _apply_one(self, ev: dict) -> bool:
        """应用单条事件；返回是否真正生效（降级/未知键 = False 计入 skipped）。"""
        dev = self._ensure_device()
        t = ev.get("t")
        if t == "key":
            qt = ev.get("key", ev.get("code"))
            mapped = _KEY_MAP.get(qt)
            if mapped is None:
                log.warning("未知键值 Qt=0x%X（已忽略；可在 _KEY_MAP 扩充）", qt or 0)
                return False
            if dev is None:
                log.info("注入降级：key Qt=0x%X down=%s 仅记录", qt, ev.get("down", True))
                return False
            dev.emit(mapped, 1 if ev.get("down", True) else 0)
            return True
        if t == "mouse_move":
            dx, dy = int(ev.get("dx", 0)), int(ev.get("dy", 0))
            if dev is None:
                log.info("注入降级：mouse_move dx=%d dy=%d 仅记录", dx, dy)
                return False
            if dx:
                dev.emit((EV_REL, REL_X), dx)
            if dy:
                dev.emit((EV_REL, REL_Y), dy)
            return True
        if t == "mouse_abs":
            # 绝对定位（跟手镜像）：PC 端把画面坐标换算成板屏像素发来，这里只钳边。
            # 首选 X11 真指针（XWarpPointer——uinput ABS 驱不动 X 光标，真板实测）；
            # X 不可用退 uinput ABS（无 X 会话/控制台场景）
            x, y = int(ev.get("x", 0)), int(ev.get("y", 0))
            w, h = screen_size()
            x = max(0, min(w - 1, x))
            y = max(0, min(h - 1, y))
            xptr = self._ensure_xptr()
            if xptr is not None:
                xptr.warp(x, y)
                return True
            if dev is None:
                log.info("注入降级：mouse_abs x=%d y=%d 仅记录", x, y)
                return False
            dev.emit((EV_ABS, ABS_X), x)
            dev.emit((EV_ABS, ABS_Y), y)
            return True
        if t == "mouse_button":
            code = _MOUSE_BTN.get(str(ev.get("btn", "left")).lower())
            if code is None:
                log.warning("未知鼠标键 %r", ev.get("btn"))
                return False
            if dev is None:
                log.info("注入降级：mouse_button %s down=%s 仅记录", ev.get("btn"), ev.get("down", True))
                return False
            dev.emit((EV_KEY, code), 1 if ev.get("down", True) else 0)
            return True
        if t == "wheel":
            dx, dy = int(ev.get("dx", 0)), int(ev.get("dy", 0))
            if dev is None:
                log.info("注入降级：wheel dx=%d dy=%d 仅记录", dx, dy)
                return False
            if dy:
                dev.emit((EV_REL, REL_WHEEL), 1 if dy > 0 else -1)
            if dx:
                dev.emit((EV_REL, REL_HWHEEL), 1 if dx > 0 else -1)
            return True
        log.warning("未知事件类型 %r: %r", t, ev)
        return False
