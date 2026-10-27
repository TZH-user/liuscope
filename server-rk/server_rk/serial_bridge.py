# -*- coding: utf-8 -*-
"""serial_bridge.py — 板端串口透传（设计稿 §2：控制通道 serial_* → 本地串口）。

直接用 pyserial 的 serial_for_url 打开任意 URL：/dev/ttyS3、/dev/ttyUSB0、
rfc2217:// 等 pyserial 支持的地址通吃；Windows 单测用内建 loop:// 验证读写闭环。
M1 的 ser2net 方案与本模块并存，M2+ 用本模块把串口收进同一鉴权控制面
（免装 ser2net、配对码统一鉴权，见设计稿 §2）。
"""
from __future__ import annotations

import logging
import threading

import serial  # pyserial（仓库既有依赖）

log = logging.getLogger(__name__)

DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT = 0.1


class SerialBridge:
    """控制通道 serial_open/write/read 的板端落地：单口占用，重开即换口/换波特率。"""

    def __init__(self, opener=serial.serial_for_url):
        self._opener = opener          # 可注入（测试可用 fake；默认 pyserial）
        self._port = None
        self._lock = threading.Lock()
        self.url: str | None = None
        self.baud: int | None = None

    @property
    def is_open(self) -> bool:
        return self._port is not None and getattr(self._port, "is_open", True)

    def open(self, url: str, baud: int = DEFAULT_BAUD, timeout: float = DEFAULT_TIMEOUT) -> dict:
        """打开串口 URL；重复 open = 先关再开（换 URL/波特率）。"""
        with self._lock:
            self._close_locked()
            self._port = self._opener(url, baudrate=int(baud), timeout=float(timeout))
            self.url, self.baud = url, int(baud)
            log.info("串口已打开 %s @%d", url, self.baud)
            return {"url": self.url, "baud": self.baud}

    def write(self, data: bytes) -> int:
        """写原始字节，返回写入数；未打开抛 RuntimeError（控制层翻译成错误响应）。"""
        if not self.is_open:
            raise RuntimeError("串口未打开（先 serial_open）")
        with self._lock:
            n = self._port.write(bytes(data))
            self._port.flush()
            return int(n or 0)

    def read(self, size: int = 4096, timeout: float | None = None) -> bytes:
        """读至多 size 字节；timeout=None 用 open 时端口默认超时。"""
        if not self.is_open:
            raise RuntimeError("串口未打开（先 serial_open）")
        with self._lock:
            if timeout is not None:
                old, self._port.timeout = self._port.timeout, float(timeout)
                try:
                    return self._port.read(int(size))
                finally:
                    self._port.timeout = old
            return self._port.read(int(size))

    def close(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                log.exception("串口关闭异常（忽略）")
        self._port = None
        self.url = None
        self.baud = None

    def describe(self) -> dict:
        return {"open": self.is_open, "url": self.url, "baud": self.baud}
