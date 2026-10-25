# -*- coding: utf-8 -*-
"""日志行悬停格式互转（移植主线 get_line_alt_format_tooltip 口径）。

HEX 行（HEX 接收模式的日志）→ 解码成 文本/十进制/float32(小端)；
文本行（ASCII 接收模式）→ 给出 UTF-8 HEX。预览字节超 64 截断加省略号。
"""
from __future__ import annotations

import re

from core.hexutil import bytes_to_hex_text, try_parse_hex_text

# 整行都是 2 位十六进制对才算 HEX 行；单个 2 字符 token 当文本处理
# （"OK"/"AB" 之类短词做 hex 误判率太高）
_HEX_LINE_RE = re.compile(r'^[0-9A-Fa-f]{2}(?:[\s,;]+[0-9A-Fa-f]{2})+$')
_PREVIEW_LIMIT = 64


def _preview_suffix(total: int, shown: int) -> str:
    return ' …' if total > shown else ''


def _hex_line_tooltip(line: str) -> str:
    try:
        data = try_parse_hex_text(line)
    except ValueError:
        return ''
    if not data:
        return ''
    shown = data[:_PREVIEW_LIMIT]
    chars: list[str] = []
    for b in shown:
        if 0x20 <= b <= 0x7E:
            chars.append(chr(b))
        elif b == 0x0D:
            chars.append('<CR>')
        elif b == 0x0A:
            chars.append('<LF>')
        else:
            chars.append('·')
    suffix = _preview_suffix(len(data), len(shown))
    lines = [f'文本(UTF-8): {"".join(chars)}{suffix}',
             f'十进制: {" ".join(str(b) for b in shown)}{suffix}']
    if len(data) % 4 == 0:
        import struct
        count = min(len(data) // 4, 16)
        floats = [f'{struct.unpack_from("<f", data, i * 4)[0]:.6g}' for i in range(count)]
        lines.append(f'float32(小端): {", ".join(floats)}')
    return '\n'.join(lines)


def alt_format_tooltip(line: str) -> str:
    if not line or not line.strip():
        return ''
    trimmed = line.strip()
    if len(trimmed) >= 5 and _HEX_LINE_RE.match(trimmed):
        return _hex_line_tooltip(trimmed)
    data = trimmed.encode('utf-8')
    if not data:
        return ''
    shown = data[:_PREVIEW_LIMIT]
    suffix = _preview_suffix(len(data), len(shown))
    return f'HEX: {bytes_to_hex_text(shown)}{suffix}'
