# -*- coding: utf-8 -*-
"""XMODEM/YMODEM 传输自测组(CRC 向量 + 仿真对端整环 + 传输引擎容错)。

约定:本文件提供 groups() 返回测试函数列表;由 qt/selftest.py 的 run_selftest 挂载。
所有测试离屏可跑、无真机、无网络、不起 sys.executable 子进程(冻结冒烟安全)。
"""
from __future__ import annotations

import sys
import time as _t

from core.xmodem import (ACK, CAN, CRC_CHR, EOT, NAK, SOH, X_BLOCK, Y_BLOCK,
                         XmodemSender, YmodemSender, crc16_ccitt)


def _crc_vector():
    """CRC16-CCITT(XMODEM) 标准校验向量：b'123456789' → 0x31C3。"""
    assert crc16_ccitt(b'123456789') == 0x31C3
    assert crc16_ccitt(b'') == 0
    print('XFER CRC VECTOR OK')


def _run_sender(sender, device, timeout_s: float = 10):
    """整环驱动：device 应答逐字节喂回 sender，直到 done/error/超时。"""
    deadline = _t.monotonic() + timeout_s
    while not sender.done and not sender.error and _t.monotonic() < deadline:
        for b in device.take_output():
            sender.on_rx(b)
        for _ in range(5):    # tick 推进但远小于超时上限（30），防锁步被误判停机
            sender.tick()
    assert not sender.error, sender.error
    assert sender.done, '发送端未在期限内完成'


class _FakeXReceiver:
    """仿真 XMODEM 接收端：起手 'C'/NAK → 校验每块 → ACK/NAK → EOT ACK。"""

    def __init__(self, start_byte: int = CRC_CHR, nak_block: int | None = None):
        self.nak_block = nak_block     # 对该块号先 NAK 一次（重发测试）
        self.crc_mode = start_byte == CRC_CHR
        self.out = bytearray([start_byte])
        self.received = bytearray()
        self.naked = False
        self.done = False
        self._buf = bytearray()

    def take_output(self) -> bytes:
        data = bytes(self.out)
        self.out.clear()
        return data

    def feed(self, data: bytes):
        """解析 PC 发来的块帧（CRC 2 字节尾 / checksum 1 字节尾），应答写 self.out。"""
        self._buf.extend(data)
        trailer = 2 if self.crc_mode else 1
        while len(self._buf) >= 3 + X_BLOCK + trailer:
            head = self._buf[0]
            if head != SOH:   # 非块帧（不应出现）丢弃
                del self._buf[0]
                continue
            no, nno = self._buf[1], self._buf[2]
            assert no == (~nno) & 0xFF, '块号/反码校验失败'
            payload = bytes(self._buf[3:3 + X_BLOCK])
            if self.crc_mode:
                ok = (self._buf[3 + X_BLOCK] << 8) | self._buf[4 + X_BLOCK]
                want = crc16_ccitt(payload)
            else:
                ok = self._buf[3 + X_BLOCK]
                want = sum(payload) & 0xFF
            del self._buf[:3 + X_BLOCK + trailer]
            if no == self.nak_block and not self.naked:
                self.naked = True
                self.out.append(NAK)
                continue
            if ok != want:
                self.out.append(NAK)
                continue
            self.received.extend(payload)
            self.out.append(ACK)


class _FakeXDeviceEot:
    """把 EOT 帧也送进解析的包装（XMODEM EOT 是裸字节，feed 外单独处理）。"""

    @staticmethod
    def feed_eot(dev: _FakeXReceiver):
        dev.out.append(ACK)
        dev.done = True


def test_xmodem_loopback():
    """XMODEM：CRC/校验和两种模式 + NAK 重发 + 对端 CAN 取消 + 超时。"""
    data = bytes(range(256)) * 2 + b'tail'   # 516 字节 → 5 块，末块 0x1A 填充
    expect = data + bytes([0x1A]) * (X_BLOCK * 5 - len(data))

    # CRC 模式整环
    dev = _FakeXReceiver()
    tx = XmodemSender(data, lambda b: (dev.feed(b), _FakeXDeviceEot.feed_eot(dev)
                                       if b == bytes([EOT]) else None))
    _run_sender(tx, dev)
    assert bytes(dev.received) == expect
    assert dev.done

    # checksum 模式（老 bootloader NAK 起手）：逐字节等价验证
    dev2 = _FakeXReceiver(NAK)
    tx2 = XmodemSender(data[:100], lambda b: (dev2.feed(b),
                                              _FakeXDeviceEot.feed_eot(dev2)
                                              if b == bytes([EOT]) else None))
    _run_sender(tx2, dev2)
    assert bytes(dev2.received) == data[:100] + bytes([0x1A]) * (X_BLOCK - 100)

    # NAK 重发：第 3 块先 NAK 一次
    dev3 = _FakeXReceiver(CRC_CHR, nak_block=3)

    def write3(b: bytes):
        dev3.feed(b)
        if b == bytes([EOT]):
            _FakeXDeviceEot.feed_eot(dev3)

    tx3 = XmodemSender(data, write3)
    _run_sender(tx3, dev3)
    assert bytes(dev3.received) == expect
    assert dev3.naked

    # 对端 CAN → 发送端报错
    tx4 = XmodemSender(data, lambda b: None)
    tx4.on_rx(CRC_CHR)   # 进入 blocks 态
    tx4.on_rx(CAN)
    assert tx4.error and '取消' in tx4.error

    # 超时：无对端应答 tick 耗尽
    tx5 = XmodemSender(b'x', lambda b: None)
    for _ in range(40):
        tx5.tick()
    assert tx5.error and '超时' in tx5.error
    print('XMODEM LOOPBACK OK')


class _FakeYReceiver:
    """仿真 YMODEM 接收端（AN3155 风格）：C→块0→ACK+C→数据→ACK→EOT→C→空块。"""

    def __init__(self):
        self.out = bytearray(b'C')     # 起手请求 CRC
        self.filename = b''
        self.filesize = -1
        self.received = bytearray()
        self.done = False
        self._buf = bytearray()
        self._got_eot = False

    def take_output(self) -> bytes:
        data = bytes(self.out)
        self.out.clear()
        return data

    def feed(self, data: bytes):
        self._buf.extend(data)
        while True:
            if not self._buf:
                return
            head = self._buf[0]
            if head == EOT:
                del self._buf[0]
                self.out.extend(bytes([ACK, CRC_CHR]))   # ACK + 'C'（请求结束块）
                continue
            size = 128 if head == SOH else Y_BLOCK
            if len(self._buf) < 3 + size + 2:
                return
            no, nno = self._buf[1], self._buf[2]
            assert no == (~nno) & 0xFF
            payload = bytes(self._buf[3:3 + size])
            crc = (self._buf[3 + size] << 8) | self._buf[4 + size]
            del self._buf[:3 + size + 2]
            assert crc == crc16_ccitt(payload), 'YMODEM 块 CRC 不符'
            if no == 0:
                name, _, rest = payload.partition(b'\0')
                self.filename = name
                self.filesize = int(rest.partition(b'\0')[0])
                self.out.extend(bytes([ACK, CRC_CHR]))   # ACK + 'C'（标准接收端行为）
            elif head == SOH and set(payload) == {0}:
                self.out.extend(bytes([ACK]))    # 结束空块（全 0）→ 完成
                self.done = True
            else:
                self.received.extend(payload)
                self.out.extend(bytes([ACK]))


def test_ymodem_loopback():
    """YMODEM 整环：块0 文件名/大小、1024 数据块、EOT、空块收尾。"""
    data = bytes((i * 7) & 0xFF for i in range(2500))   # 2500 → 3 个 1024 块
    dev = _FakeYReceiver()
    tx = YmodemSender(data, 'firmware.bin', dev.feed)
    _run_sender(tx, dev)
    assert dev.filename == b'firmware.bin'
    assert dev.filesize == len(data)
    expect = data + bytes([0x1A]) * (Y_BLOCK * 3 - len(data))
    assert bytes(dev.received) == expect
    print('YMODEM LOOPBACK OK')


def test_proto_send_ui():
    """主窗协议发送集成：combo=YMODEM → 文件对话框桩 → 环回 worker 整环跑完。"""
    import tempfile
    import time as _t

    from PySide6 import QtWidgets

    import ui.main_window as mw
    from selftest import (_CONNECT_KEYS, _fake_worker_cls, _restore_settings,
                          _snapshot_settings)

    data = bytes((i * 13) & 0xFF for i in range(2100))   # 3 个 1024 块
    dev = _FakeYReceiver()

    class LoopWorker(_fake_worker_cls()):
        """写进 fake 设备、应答经 0ms 定时器回灌 got_bytes（事件驱动不递归，
        贴近真实 worker 的跨线程 queued 语义）。"""

        def write_bytes(self, d):
            from PySide6 import QtCore
            dev.feed(d)
            QtCore.QTimer.singleShot(0, lambda: self.got_bytes.emit(dev.take_output()))
            return True

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    saved = _snapshot_settings(_CONNECT_KEYS)
    orig_dialog = QtWidgets.QFileDialog.getOpenFileName
    mw.SerialWorker = LoopWorker
    win = None
    try:
        win = mw.MainWindow()
        win._port_watch_timer.stop()
        win.open_connection()
        assert win.worker is not None

        with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as f:
            f.write(data)
            path = f.name
        QtWidgets.QFileDialog.getOpenFileName = \
            lambda *a, **k: (path, '固件 (*.bin)')

        win.combo_file_proto.setCurrentText('YMODEM')
        win.btn_file.click()   # → _start_proto_send（等 'C'）
        assert win._xfer is not None and win.btn_file.text() == '停止'
        win.worker.got_bytes.emit(dev.take_output())   # 设备起手 'C' 递给引擎

        deadline = _t.monotonic() + 5
        while win._xfer is not None and _t.monotonic() < deadline:
            reply = dev.take_output()
            if reply:
                win.worker.got_bytes.emit(reply)
            app.processEvents()
        assert win._xfer is None, '协议发送应在期限内完成'
        assert win.btn_file.text() == '文件发送'
        expect = data + bytes([0x1A]) * (1024 * 3 - len(data))
        assert bytes(dev.received) == expect
        assert any('完成' in e['text'] and 'YMODEM发送' in e['text']
                   for e in win.message_entries if e['direction'] == 'SYS')
        assert any('YMODEM发送' in e['text'] for e in win.message_entries
                   if e['direction'] == 'TX')
    finally:
        QtWidgets.QFileDialog.getOpenFileName = orig_dialog
        if win is not None:
            win._port_watch_timer.stop()
            win._stop_file_send('测试收尾')
            win.close()
        _restore_settings(saved)
    print('PROTO SEND UI OK')


def groups():
    return [_crc_vector, test_xmodem_loopback, test_ymodem_loopback,
            test_proto_send_ui]
