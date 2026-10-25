# -*- coding: utf-8 -*-
"""串口扫描与自动连接选口：对齐主线 serial_io.ps1 的口径。

- 展示文本「COM6 — 描述」；描述自带 (COM6) 尾巴时去掉防重复
- CH340/CH341（WCH 芯片，鲁班猫板载串口）在多个新设备中优先自动连接
- 首次扫描只建基线，已插着的设备不触发自动连接
"""
from __future__ import annotations

import re

try:
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - 无 pyserial 的裸环境
    list_ports = None

_PORT_TAIL_RE = re.compile(r'\s*\(COM\d+\)\s*$', re.IGNORECASE)
_COM_RE = re.compile(r'COM\d+', re.IGNORECASE)


def scan_port_infos() -> list[tuple[str, str]]:
    """返回 [(device, description)]；无 pyserial 时返回空。"""
    infos: list[tuple[str, str]] = []
    if list_ports is None:
        return infos
    for p in list_ports.comports():
        infos.append((p.device, p.description or ''))
    return infos


def port_display_text(device: str, description: str) -> str:
    if not description.strip():
        return device
    desc = _PORT_TAIL_RE.sub('', description.strip())
    if not desc or desc == device:
        return device
    return f'{device} — {desc}'


def port_name_from_display(text: str) -> str:
    """从展示文本取 COM 口名；取不到就原样返回（主线同口径）。"""
    text = (text or '').strip()
    if not text:
        return ''
    m = _COM_RE.search(text)
    return m.group(0).upper() if m else text


def is_ch340_description(description: str) -> bool:
    return bool(re.search(r'CH34[01]', description or ''))


def pick_auto_connect_port(new_devices: list[tuple[str, str]]) -> str:
    """多个新端口时优先选 CH340 设备；没有则取第一项。"""
    if not new_devices:
        return ''
    for device, desc in new_devices:
        if is_ch340_description(desc):
            return device
    return new_devices[0][0]
