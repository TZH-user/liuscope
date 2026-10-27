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

# VID/PID → 芯片标注（ESP32 生态常见串口芯片；v1.3.0 ESP32 支持）
# USB-Serial/JTAG 是 ESP32-S2/S3/C3 内置口：无经典 DTR/RTS 复位时序，
# 也不适合高频二进制流（esp-csi 官方同样建议用外挂 UART 口收流）。
ESP32_PORT_LABELS = {
    (0x10C4, 0xEA60): 'CP210x',
    (0x1A86, 0x7523): 'CH340',
    (0x1A86, 0x5523): 'CH341',
    (0x0403, 0x6001): 'FT232',
    (0x303A, 0x1001): 'ESP32 JTAG',
    (0x303A, 0x0002): 'ESP32-S2 CDC',
}


def chip_label_for(vid, pid) -> str:
    """VID/PID → 简短芯片标注；未知或缺失返回空串。"""
    if vid is None or pid is None:
        return ''
    return ESP32_PORT_LABELS.get((vid, pid), '')


def scan_port_infos() -> list[tuple[str, str]]:
    """返回 [(device, description)]；无 pyserial 时返回空。"""
    infos: list[tuple[str, str]] = []
    if list_ports is None:
        return infos
    for p in list_ports.comports():
        infos.append((p.device, p.description or ''))
    return infos


def scan_port_infos_full() -> list[tuple[str, str, str]]:
    """返回 [(device, description, chip_label)]，chip_label 由 VID/PID 映射（未知为空）。"""
    infos: list[tuple[str, str, str]] = []
    if list_ports is None:
        return infos
    for p in list_ports.comports():
        label = chip_label_for(getattr(p, 'vid', None), getattr(p, 'pid', None))
        infos.append((p.device, p.description or '', label))
    return infos


def port_display_text(device: str, description: str) -> str:
    if not description.strip():
        return device
    desc = _PORT_TAIL_RE.sub('', description.strip())
    if not desc or desc == device:
        return device
    return f'{device} — {desc}'


def port_display_text_full(device: str, description: str, label: str) -> str:
    """展示文本追加芯片标注（描述里已含同名芯片词则不重复）。"""
    base = port_display_text(device, description)
    if label and label.lower() not in base.lower():
        return f'{base} · {label}'
    return base


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
