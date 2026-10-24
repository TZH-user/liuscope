# -*- coding: utf-8 -*-
"""HEX 文本互转 —— 移植自主线 core/serial_io.ps1 的 bytes_to_hex_text / try_parse_hex_text。"""
from __future__ import annotations

import re

# 主线 -replace '0x' 大小写不敏感、全串替换
_RE_0X = re.compile(r'0x', re.IGNORECASE)
_RE_TOKEN = re.compile(r'[0-9A-Fa-f]{1,2}')
_RE_SPLIT = re.compile(r'[\s,;:-]+')


def bytes_to_hex_text(data: bytes) -> str:
    return ' '.join(f'{b:02X}' for b in data)


def try_parse_hex_text(text: str) -> bytes:
    """解析宽松 HEX 文本（空白/逗号/分号/冒号/连字符分隔，0x 前缀可带）。

    非法 token 抛 ValueError('HEX 输入无效: xxx')——与主线同语义。
    """
    if not text or not text.strip():
        return b''
    normalized = _RE_0X.sub('', text).strip()
    out = bytearray()
    for token in _RE_SPLIT.split(normalized):
        if not token:
            continue
        if not _RE_TOKEN.fullmatch(token):
            raise ValueError(f'HEX 输入无效: {token}')
        out.append(int(token, 16))
    return bytes(out)
