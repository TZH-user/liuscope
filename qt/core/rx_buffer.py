# -*- coding: utf-8 -*-
"""接收行缓冲：纯逻辑、无 Qt 依赖，可独立单测。

与 PowerShell 主线同构的三件事：
1. 行缓冲重组（CRLF/CR 统一成 \n，只有成行才交给 UI）；
2. 空闲落显（无换行残行静默超过 idle_flush_ms 自动吐出，短消息不再等 8KB）；
3. 积压计数（backlog_bytes = 已接收但还没显示的字节数，即「收到但看不见」的量）。

解码对齐主线 serial_io.ps1 的 UTF8Encoding.GetDecoder()：增量解码器把被 USB
分包切开的半个多字节字符留在内部状态里，下一块到齐才输出——按块独立解码
会把跨块中文变成 U+FFFD 乱码。

时间由调用方注入（time.monotonic()），测试可用假时钟精确推进。
"""
from __future__ import annotations

import codecs


def _new_decoder():
    return codecs.getincrementaldecoder('utf-8')(errors='replace')


class RxBuffer:
    def __init__(self, idle_flush_ms: int = 400):
        self.idle_flush_ms = idle_flush_ms
        self._decoder = _new_decoder()
        self._buf = ''
        self._last_byte_at = 0.0
        self._has_byte = False

    def feed(self, data: bytes, now: float) -> list[str]:
        """喂入原始字节，返回本次凑成的完整行（不含行尾）。"""
        self._last_byte_at = now
        self._has_byte = True
        self._buf += self._decoder.decode(data)  # 半个字符留在解码器里等下一块
        parts = self._buf.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        self._buf = parts.pop()
        return parts

    def poll_idle_flush(self, now: float) -> str | None:
        """静默超过阈值时吐出残行；正常返回 None。"""
        if not self._buf or not self._has_byte:
            return None
        if (now - self._last_byte_at) * 1000.0 < self.idle_flush_ms:
            return None
        tail, self._buf = self._buf, ''
        return tail

    def drain(self) -> str | None:
        """无条件吐出残行（切显示格式等场景），没有则返回 None。"""
        if not self._buf:
            return None
        tail, self._buf = self._buf, ''
        return tail

    @property
    def backlog_bytes(self) -> int:
        return len(self._buf.encode('utf-8', errors='replace'))

    def clear(self) -> None:
        self._buf = ''
        self._has_byte = False
        self._decoder = _new_decoder()  # 丢弃悬着的半个字符，重新开始
