# -*- coding: utf-8 -*-
"""input_inject.py — 键鼠注入（设计稿 §2/§4：PC 事件批经控制通道下行 → uinput 注入）。

python-uinput 仅 Linux 可用：import 放 try 块，失败时优雅降级——is_available=False，
apply_events 只记日志、不抛异常（Windows 单测 / 无 uinput 的板均不崩，M3 装 udev 规则后生效）。

键值映射：LiuScope PC 端下发 Qt.Key_* 数值 → Linux input 事件码。
映射表是纯数据（不依赖 uinput import 成功），可在任何平台单测校验。
事件批格式（与设计稿 §4 对齐）：{"cmd":"input","events":[
    {"t":"key","key":<Qt键码>,"down":true|false},
    {"t":"mouse_move","dx":N,"dy":N},
    {"t":"mouse_button","btn":"left|right|middle","down":true|false},
    {"t":"wheel","dx":N,"dy":N}, ...]}
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

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

DEVICE_NAME = "liuscope-server-uinput"


def _default_device_factory(events, name):
    """真实 uinput 设备工厂（events 为 ("EV_KEY"/"EV_REL", code) 元组列表）。"""
    return uinput.Device(events, name=name)


class InputInjector:
    """uinput 虚拟键鼠：懒创建设备；device_factory 可注入（测试用 fake 记录 emit）。"""

    def __init__(self, device_factory=None):
        self._factory = device_factory or _default_device_factory
        self._injected_factory = device_factory is not None
        self._device = None
        self._warned = False

    @property
    def is_available(self) -> bool:
        """uinput 可导入，或测试注入了自定义设备工厂。"""
        return _UINPUT_OK or self._injected_factory

    def _ensure_device(self):
        if self._device is not None:
            return self._device
        if not self.is_available:
            if not self._warned:  # 只告警一次，避免日志刷屏
                log.warning("uinput 不可用：键鼠注入降级为仅日志（需 python-uinput + /dev/uinput 权限）")
                self._warned = True
            return None
        events = sorted({*_KEY_MAP.values(),
                         *((EV_KEY, c) for c in _MOUSE_BTN.values()),
                         (EV_REL, REL_X), (EV_REL, REL_Y),
                         (EV_REL, REL_WHEEL), (EV_REL, REL_HWHEEL)})
        self._device = self._factory(events, name=DEVICE_NAME)
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
