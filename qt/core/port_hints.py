# -*- coding: utf-8 -*-
"""串口打开失败的处置提示（纯函数，按平台与错误文本映射；离屏可单测）。

Linux 桌面串口两大经典坑都在这里给一句可执行的处置：
- 权限（不在 dialout 组 / udev 规则缺失）
- 端口被抢（ModemManager 对新端口探测、brltty 抢 CP210x）
"""
from __future__ import annotations

_HINT_LINUX_PERM = (
    'Linux 权限不足：sudo usermod -aG dialout $USER 后重新插拔或注销；'
    '若仍失败，检查 brltty/ModemManager 是否抢占（sudo systemctl disable --now brltty '
    'ModemManager）')
_HINT_LINUX_BUSY = (
    '端口被占用：另一程序正在使用，或 ModemManager 正在探测新端口'
    '（sudo systemctl disable --now ModemManager 后重插）')
_HINT_WIN_BUSY = (
    '串口被其他程序占用（含另一个 LiuScope 实例/串口监视器），关闭后重试')
_HINT_WIN_DEVFAIL = (
    'Windows 错误码 31（设备未响应配置）：多为串口被占用或 USB 串口适配器假死；'
    '关闭其他占用程序后重试，仍失败则重插 USB（设备管理器确认 COM 号未漂移）')


def port_open_error_hint(exc_text: str, sys_platform: str) -> str | None:
    """打开失败的异常文本 + 平台 → 一句处置提示；无匹配返回 None。

    顺序敏感：winerror 31 的文本同时含 PermissionError 与「没有发挥作用」，
    设备故障匹配必须先于笼统的 permission/占用 匹配。
    """
    text = (exc_text or '').lower()
    if ('没有发挥作用' in exc_text or 'not functioning' in text
            or 'gen_failure' in text or ', none, 31' in text):
        return _HINT_LINUX_PERM if 'linux' in (sys_platform or '').lower() else _HINT_WIN_DEVFAIL
    if 'permission' in text or 'access is denied' in text or 'errno 13' in text:
        return _HINT_LINUX_PERM if 'linux' in (sys_platform or '').lower() else _HINT_WIN_BUSY
    if 'resource busy' in text or 'busy' in text or 'errno 16' in text:
        return _HINT_LINUX_BUSY if 'linux' in (sys_platform or '').lower() else _HINT_WIN_BUSY
    if 'linux' in (sys_platform or '').lower() and 'access is denied' in text:
        return _HINT_LINUX_PERM
    return None
