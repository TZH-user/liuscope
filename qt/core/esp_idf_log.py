# -*- coding: utf-8 -*-
"""ESP-IDF 日志行解析与崩溃特征识别（纯函数，离屏可单测）。

覆盖三件事：
- 常规日志行 `I (2812) wifi: msg`（等级 I/W/D/E/V，容忍前导 ANSI 色码）
- 复位横幅 `rst:0x1 (POWERON_RESET)`——看门狗循环/异常重启的标志
- panic 行 `Guru Meditation Error: Core 0 panic'ed (StoreProhibited).`
  与 backtrace 行 `backtrace:0x400814ad:0x3ffb5e50 ...`（addr2line 解析素材）
"""
from __future__ import annotations

import re

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
_IDF_LINE_RE = re.compile(
    r'^([IWDVE])\s*\((\d+)\)\s*([^:\s][^:]*?):\s?(.*)$', re.DOTALL)
_RST_RE = re.compile(r'\brst:0x([0-9a-fA-F]+)\s*\(([^)]+)\)')
_PANIC_RE = re.compile(
    r"Guru\s+Meditation\s+Error:\s*Core\s+(\d+)\s+panic'ed\s+\(([^)]+)\)", re.IGNORECASE)
_BT_ADDR_RE = re.compile(r'0x[0-9a-fA-F]{4,}')

# 空闲落显/格式切换落显的后缀（主窗给残行打标记，解析前剥掉）
FLUSH_SUFFIXES = (' (空闲落显)', ' (格式切换落显)')


def strip_ansi(line: str) -> str:
    """去 ANSI 色码（ESP-IDF 日志可带颜色转义）。"""
    return _ANSI_RE.sub('', line)


def parse_idf_line(line: str) -> dict | None:
    """`I (2812) tag: msg` → dict(level, ts_ms, tag, msg)；不匹配返回 None。"""
    m = _IDF_LINE_RE.match(strip_ansi(line))
    if not m:
        return None
    return {'level': m.group(1), 'ts_ms': int(m.group(2)),
            'tag': m.group(3).strip(), 'msg': m.group(4)}


def is_reset_banner(line: str) -> tuple[int, str] | None:
    """复位横幅 → (reason_code, reason_str)；否则 None。

    常见：0x1 POWERON_RESET / 0x3 SW_RESET / 0x8 TG1WDT_SYS_RESET /
    0x10 RTCWDT_RTC_RESET / 0x15 USB_UART_CHIP_RESET …（原因名原样返回）
    """
    m = _RST_RE.search(strip_ansi(line))
    if not m:
        return None
    return int(m.group(1), 16), m.group(2).strip()


def is_panic(line: str) -> tuple[int, str] | None:
    """Guru Meditation panic 行 → (core, exc_name)；否则 None。"""
    m = _PANIC_RE.search(strip_ansi(line))
    if not m:
        return None
    return int(m.group(1)), m.group(2).strip()


def is_backtrace_line(line: str) -> bool:
    return 'backtrace' in strip_ansi(line).lower() and '0x' in strip_ansi(line).lower()


def extract_backtrace_addrs(line: str) -> list[str]:
    """从 backtrace 行提取 pc 地址序列（保序）。

    经典格式 `backtrace:0x400814ad:0x3ffb5e50 0x40082319:0x3ffb5e70` 是
    pc:sp 交替——只取偶数位（pc 列），sp 喂 addr2line 只会出垃圾行。
    新版 `-|/-` elided 形态同样按 pc:sp 交替处理，取得到多少算多少。
    """
    text = strip_ansi(line)
    if 'backtrace' not in text.lower():
        return []
    all_addrs = _BT_ADDR_RE.findall(text)
    return all_addrs[0::2]


def normalize_for_parse(line: str) -> str:
    """剥落显后缀与 ANSI 后的可解析文本（主窗 RX 行先过这里）。"""
    text = line
    for suffix in FLUSH_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return strip_ansi(text)
