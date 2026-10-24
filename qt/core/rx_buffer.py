# -*- coding: utf-8 -*-
"""接收行缓冲：纯逻辑、无 Qt 依赖，可独立单测。

与 PowerShell 主线同构的三件事：
1. 行缓冲重组（CRLF/CR 统一成 \n，只有成行才交给 UI）；
2. 空闲落显（无换行残行静默超过 idle_flush_ms 自动吐出，短消息不再等 8KB）；
3. 积压计数（backlog_bytes = 已接收但还没显示的字节数，即「收到但看不见」的量）。

时间由调用方注入（time.monotonic()），测试可用假时钟精确推进。
"""
from __future__ import annotations


class RxBuffer:
    def __init__(self, idle_flush_ms: int = 400):
        self.idle_flush_ms = idle_flush_ms
        self._buf = ''
        self._last_byte_at = 0.0
        self._has_byte = False

    def feed(self, data: bytes, now: float) -> list[str]:
        """喂入原始字节，返回本次凑成的完整行（不含行尾）。"""
        self._last_byte_at = now
        self._has_byte = True
        self._buf += data.decode('utf-8', errors='replace')
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

    @property
    def backlog_bytes(self) -> int:
        return len(self._buf.encode('utf-8', errors='replace'))

    def clear(self) -> None:
        self._buf = ''
        self._has_byte = False
