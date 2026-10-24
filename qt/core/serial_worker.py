# -*- coding: utf-8 -*-
"""串口读取线程：QThread 内持有 SerialPort，读到的字节经信号送回 UI 线程。

要点：端口在工作线程里打开（避免跨线程句柄）；停止 = 置标志 + join，
UI 侧不直接 touch 串口对象。
"""
from __future__ import annotations

from PySide6 import QtCore

try:
    import serial
except ImportError:  # pragma: no cover - pyserial 缺失时 UI 会提示
    serial = None


class SerialWorker(QtCore.QThread):
    got_bytes = QtCore.Signal(bytes)
    opened = QtCore.Signal()
    open_failed = QtCore.Signal(str)
    lost = QtCore.Signal(str)

    def __init__(self, device: str, baud: int, parent=None):
        super().__init__(parent)
        self._device = device
        self._baud = baud
        self._running = True
        self._port = None

    def run(self):  # pragma: no cover - 需要真实串口，离屏自测不覆盖
        if serial is None:
            self.open_failed.emit('未安装 pyserial：python -m pip install pyserial')
            return
        try:
            self._port = serial.Serial(self._device, self._baud, timeout=0)
        except Exception as exc:
            self.open_failed.emit(str(exc))
            return
        self.opened.emit()
        while self._running:
            try:
                waiting = self._port.in_waiting
                if waiting:
                    self.got_bytes.emit(self._port.read(waiting))
                else:
                    self.msleep(10)
            except Exception as exc:
                if self._running:
                    self.lost.emit(str(exc))
                break

    def write_bytes(self, data: bytes) -> bool:
        if not self._port:
            return False
        try:
            self._port.write(data)
            return True
        except Exception:
            return False

    def write_line(self, text: str) -> bool:
        return self.write_bytes((text + '\n').encode('utf-8'))

    def stop(self):
        self._running = False
        self.wait(1500)
        if self._port:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None
