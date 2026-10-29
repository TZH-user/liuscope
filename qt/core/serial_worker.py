# -*- coding: utf-8 -*-
"""串口读取线程：QThread 内持有 SerialPort，读到的字节经信号送回 UI 线程。

要点：端口在工作线程里打开（避免跨线程句柄）；停止 = 置标志 + join，
UI 侧不直接 touch 串口对象（DTR/RTS 实时切换与复位时序经 ctrl_* 方法跨线程
下发——pyserial 的线控只是寄存器写，跨线程调用安全，异常由调用方容错）。

ESP32 支持（v1.3.0）：
- dtr_rts_neutral：打开后立刻松开 DTR/RTS——自动下载电路把 DTR/RTS 映射到
  IO0/EN，pyserial 打开端口时默认拉线即复位 ESP32（用户第一痛点）
- auto_reconnect：读循环异常后不立刻放弃，每 2s 重试原参数重开（烧录/复位
  后 COM 重枚举、板子重启不断线），port_lost 通知一次、成功发 reconnected
"""
from __future__ import annotations

from PySide6 import QtCore

from core import esp32_control

try:
    import serial
except ImportError:  # pragma: no cover - pyserial 缺失时 UI 会提示
    serial = None

RECONNECT_INTERVAL_S = 2.0
RECONNECT_POLL_MS = 200   # 重连等待按 200ms 分片轮询，保证 stop() 及时响应


def apply_open_policy(port, dtr_rts_neutral: bool):
    """打开后应用 DTR/RTS 策略。中性 = 两者都松开（ESP32 防复位）。

    虚拟端口（loop:// 等）或 USB-Serial/JTAG 不支持线控时静默忽略。
    """
    if not dtr_rts_neutral:
        return
    try:
        port.dtr = False
        port.rts = False
    except Exception:
        pass


class SerialWorker(QtCore.QThread):
    got_bytes = QtCore.Signal(bytes)
    opened = QtCore.Signal()
    open_failed = QtCore.Signal(str)
    lost = QtCore.Signal(str)
    port_lost = QtCore.Signal(str)      # auto_reconnect 路径：连接丢失（仍在重连）
    reconnected = QtCore.Signal()       # auto_reconnect 路径：重开成功

    def __init__(self, device: str, baud: int, parent=None,
                 dtr_rts_neutral: bool = False, auto_reconnect: bool = False):
        super().__init__(parent)
        self._device = device
        self._baud = baud
        self._running = True
        self._port = None
        self._neutral = bool(dtr_rts_neutral)
        self._auto_reconnect = bool(auto_reconnect)

    @property
    def baud(self) -> int:
        return self._baud

    @property
    def device(self) -> str:
        return self._device

    def _open_port(self):
        # serial_for_url：裸 COM 名走默认本地端口；rfc2217://、socket://、loop://
        # 等 pyserial URL 直通（v2 M1 串口网络化：ser2net / Moxa NPort / 远程板卡）
        self._port = serial.serial_for_url(self._device, self._baud, timeout=0)
        apply_open_policy(self._port, self._neutral)

    def run(self):  # pragma: no cover - 需要真实串口，离屏自测不覆盖
        if serial is None:
            self.open_failed.emit('未安装 pyserial：python -m pip install pyserial')
            return
        try:
            self._open_port()
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
                if not self._running:
                    break
                if not self._auto_reconnect:
                    self.lost.emit(str(exc))
                    break
                if not self._reconnect_loop(str(exc)):
                    break

    def _reconnect_loop(self, reason: str) -> bool:
        """自动重连：关旧口 → 每 2s 重试原参数，成功回 True（回主循环）。

        只在首丢时发一次 port_lost；重连期间随 stop() 及时退出。
        """
        try:
            self._port.close()
        except Exception:
            pass
        self._port = None
        self.port_lost.emit(reason)
        while self._running:
            for _ in range(int(RECONNECT_INTERVAL_S * 1000) // RECONNECT_POLL_MS):
                if not self._running:
                    return False
                self.msleep(RECONNECT_POLL_MS)
            try:
                self._open_port()
            except Exception:
                continue   # 设备还没回来（重枚举中），下一轮再试
            self.reconnected.emit()
            return True
        return False

    # ---------- ESP32 线控（UI 线程调用，寄存器写跨线程安全） ----------

    def ctrl_reset(self) -> bool:
        """运行模式复位（EN 脉冲）。"""
        return self._ctrl(lambda p: esp32_control.pulse_reset(p))

    def ctrl_download(self) -> bool:
        """进 ROM 下载模式（esptool 经典时序）。"""
        return self._ctrl(lambda p: esp32_control.enter_download_mode(p))

    def _ctrl(self, fn) -> bool:
        if not self._port:
            return False
        try:
            fn(self._port)
            return True
        except Exception:
            return False

    def set_dtr(self, on: bool) -> bool:
        return self._ctrl(lambda p: setattr(p, 'dtr', bool(on)))

    def set_rts(self, on: bool) -> bool:
        return self._ctrl(lambda p: setattr(p, 'rts', bool(on)))

    def send_break(self) -> bool:
        """发送串口 BREAK（部分板子进 bootloader/唤醒用；虚拟口静默忽略）。"""
        return self._ctrl(lambda p: p.send_break())

    def line_signals(self) -> dict | None:
        """读 modem 状态线（跨线程寄存器读，同 DTR/RTS 跨线程口径）。

        loop:// 等虚拟口/不支持线控的驱动返回 None，调用方按未知处理。
        """
        if not self._port:
            return None
        try:
            return {'cts': bool(self._port.getCTS()), 'dsr': bool(self._port.getDSR()),
                    'ri': bool(self._port.getRI()), 'cd': bool(self._port.getCD())}
        except Exception:
            return None

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
