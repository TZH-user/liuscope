# -*- coding: utf-8 -*-
"""离屏自测：RxBuffer 纯逻辑单测 + 主窗体收发管线（QT_QPA_PLATFORM=offscreen 可跑）。"""
from __future__ import annotations

import sys


def test_rx_buffer():
    from core.rx_buffer import RxBuffer

    t = 1000.0  # 假时钟（秒）

    # 完整行立即吐出，残行留在缓冲
    buf = RxBuffer()
    assert buf.feed(b'boot v1.0\n', t) == ['boot v1.0']
    assert buf.backlog_bytes == 0

    # CRLF/CR 归一
    assert buf.feed(b'a\r\nb\rc\n', t) == ['a', 'b', 'c']

    # 无换行：进缓冲、不显示、计积压
    lines = buf.feed(b'no_newline_tail', t)
    assert lines == []
    assert buf.backlog_bytes == len(b'no_newline_tail')

    # 未过空闲阈值不落显
    assert buf.poll_idle_flush(t + 0.1) is None
    # 过阈值（400ms）落显且清零
    tail = buf.poll_idle_flush(t + 0.5)
    assert tail == 'no_newline_tail'
    assert buf.backlog_bytes == 0

    # 多字节 UTF-8 的积压按字节计（温度=6B + =41.5=5B）
    buf2 = RxBuffer()
    buf2.feed('温度=41.5'.encode('utf-8'), t)  # 11 字节无换行
    assert buf2.backlog_bytes == 11

    # 续包拼行：分片到达仍能重组
    buf3 = RxBuffer()
    assert buf3.feed(b'12.', t) == []
    assert buf3.feed(b'5\n', t) == ['12.5']

    print('RXBUFFER OK')


def test_main_window():
    from PySide6 import QtWidgets

    from ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()

    win.ingest_bytes(b'boot v1.0\n')
    win.ingest_bytes(b'temp=41.5\n')
    win.ingest_bytes(b'no_newline_tail')
    text = win.log.toPlainText()
    assert 'RX boot v1.0' in text and 'RX temp=41.5' in text
    assert 'no_newline_tail' not in text, '残行不该立即显示'
    assert win.buffer.backlog_bytes == len(b'no_newline_tail')

    # 未过阈值的 flush tick：只更新积压标签
    win.on_flush_tick()
    assert win.lbl_backlog.text() == f'积压 {len(b"no_newline_tail")} B'

    # 静默超阈值 → 空闲落显（测试直接把最后收字节的时刻拨早 10s）
    win.buffer._last_byte_at -= 10.0
    win.on_flush_tick()
    assert 'no_newline_tail (空闲落显)' in win.log.toPlainText()
    assert win.lbl_backlog.text() == '积压 0 B'

    # 发送：未连接时仅入日志
    win.edit_send.setText('AT+VERSION?')
    win.send()
    assert 'TX AT+VERSION?' in win.log.toPlainText()
    assert win.edit_send.text() == ''

    win.close()
    print('MAINWINDOW OK')


def run_selftest() -> int:
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    test_rx_buffer()
    test_main_window()
    print('QT_SPIKE_SELFTEST_OK')
    return 0


if __name__ == '__main__':
    sys.exit(run_selftest())
