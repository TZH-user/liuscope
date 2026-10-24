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


def test_wave_protocols():
    """三协议解码 + 视图模型 + 滤波 + 快照合同（对齐主线 SelfTest 断言口径）。"""
    import struct

    from core.wave_state import WaveState, sanitize_terminal_text, try_parse_wave_frame

    assert try_parse_wave_frame('1.5,-2,3e2') == [1.5, -2.0, 300.0]
    assert try_parse_wave_frame('  12 , -0.5 , 7.25e-1 ')[2] == 0.725
    assert try_parse_wave_frame('42') == [42.0]
    assert try_parse_wave_frame('spec:abc') is None
    assert try_parse_wave_frame('1.0,NaN') is None
    assert try_parse_wave_frame('$H:1.0') is None
    assert try_parse_wave_frame('') is None
    ansi = chr(27) + '[32m1.5' + chr(27) + '[0m,2.5'
    assert try_parse_wave_frame(sanitize_terminal_text(ansi)) == [1.5, 2.5]

    # FireWater：多行一次喂入 + 残行保留
    st = WaveState()
    st.enabled = True
    st.consume_bytes(b'1,2\n3,4\n5')
    assert st.frame_total == 2
    assert st.ch_values[0] == [1.0, 3.0] and st.ch_values[1] == [2.0, 4.0]
    assert st.line_buffer == '5'

    # JustFloat：分片到达重组
    frame = struct.pack('<3f', 1.5, -2.25, 3.75) + b'\x00\x00\x80\x7f'
    st2 = WaveState()
    st2.enabled = True
    st2.protocol = 'JustFloat'
    st2.consume_bytes(frame[:6])
    st2.consume_bytes(frame[6:])
    assert st2.frame_total == 1
    assert [st2.ch_values[c][-1] for c in range(3)] == [1.5, -2.25, 3.75]

    # JustFloat：垃圾+好帧 —— 垃圾不含帧尾，整帧的尾在 index 16，
    # payload 连垃圾一起被当 4 通道帧收下（与主线逐字节扫描行为一致）
    st2.consume_bytes(b'\xde\xad\xbe\xef' + frame)
    assert st2.frame_total == 2

    # JustFloat：暂停冻结 append 但缓冲照常排空
    st3 = WaveState()
    st3.enabled = True
    st3.protocol = 'JustFloat'
    st3.paused = True
    st3.consume_bytes(frame)
    assert st3.frame_total == 0 and len(st3.jf_buffer) == 0

    # RawData：字节即采样点
    st4 = WaveState()
    st4.enabled = True
    st4.protocol = 'RawData'
    st4.consume_bytes(bytes([1, 2, 3]))
    st4.consume_bytes(bytes([255]))
    assert st4.ch_values[0] == [1.0, 2.0, 3.0, 255.0] and st4.frame_total == 4

    # 视图：跟随 → 平移历史 → 改宽度保持中心 → 拖回右端恢复跟随
    st5 = WaveState()
    st5.enabled = True
    st5.consume_bytes('\n'.join(str(i) for i in range(3000)).encode() + b'\n')
    assert st5.frame_total == 3000
    assert st5.view_range() == (2000, 3000, True)
    st5.pan_view(-100)
    assert st5.view_range() == (1900, 2900, False)
    st5.set_view_width(500)
    assert st5.view_range() == (2150, 2650, False)
    st5.set_view_window(2900, 3400)
    assert st5.view_range() == (2900, 3000, True)

    # 快照合同：越界/正常都不抛
    assert st5.snapshot(999, 100) == []
    assert st5.snapshot(0, 5) == [2995.0, 2996.0, 2997.0, 2998.0, 2999.0]

    # 滤波：滑动平均窗口语义（前缀和，窗口不足按已有样本平均）
    st6 = WaveState()
    st6.enabled = True
    st6.consume_bytes(b'0\n4\n8\n')
    st6.filter_mode = '滑动平均'
    st6.filter_param = 2
    data, offset, length = st6.view_data(0, 0, 3)
    assert length == 3 and list(data[offset:offset + length]) == [0.0, 2.0, 6.0]
    # 切回"关"立即对旧数据生效（缓存键失效）
    st6.filter_mode = '关'
    data, offset, length = st6.view_data(0, 0, 3)
    assert list(data[offset:offset + length]) == [0.0, 4.0, 8.0]

    # 切协议清缓冲 + 重置解码器
    st6.consume_bytes(b'9')  # 残行
    st6.switch_protocol('JustFloat')
    assert st6.frame_total == 0 and st6.line_buffer == '' and st6.protocol == 'JustFloat'

    # 积压提示节流（假时钟）
    notes = []
    clock = {'t': 100.0}
    st7 = WaveState(now_fn=lambda: clock['t'])
    st7.enabled = True
    st7.on_note = notes.append
    st7.consume_bytes(b'x' * 9000)
    assert len(notes) == 1 and '积压' in notes[0]
    st7.consume_bytes(b'y' * 9000)
    assert len(notes) == 1, '5 秒内重复积压只提示一次'
    clock['t'] += 6.0
    st7.consume_bytes(b'z' * 9000)
    assert len(notes) == 2

    print('WAVE PROTOCOLS OK')


def test_wave_window():
    """波形页端到端：喂字节 → 曲线数据 / 暂停冻结 / 清空 / 模拟数据 / CSV 导出。"""
    import os
    import tempfile

    from PySide6 import QtWidgets

    from ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    wave = win.wave

    # 主窗体管线分流：RX 日志 + 波形解码都吃到同一份字节
    win.ingest_bytes(b'1,2\n2,4\n3,6\n')
    wave.repaint_wave()
    assert len(wave._curves) == 2
    xs, ys = wave._curves[0].getData()
    assert list(xs) == [0.0, 1.0, 2.0] and list(ys) == [1.0, 2.0, 3.0]
    assert 'RX 1,2' in win.log.toPlainText()

    # 暂停冻结曲线（数据照常进 RX 日志）
    wave.btn_pause.setChecked(True)
    win.ingest_bytes(b'9,9\n')
    wave.repaint_wave()
    _xs, ys = wave._curves[0].getData()
    assert list(ys) == [1.0, 2.0, 3.0], '暂停时曲线不更新'
    assert 'RX 9,9' in win.log.toPlainText()
    wave.btn_pause.setChecked(False)

    # 通道开关：芯片取消勾选 → 曲线隐藏（无数据）
    wave._chips[0].setChecked(False)
    wave.repaint_wave()
    _xs, ys = wave._curves[0].getData()
    assert ys is None or ys.size == 0, '芯片关闭后该通道不绘制'
    wave._chips[0].setChecked(True)

    # 模拟数据走真实解码管线（当前协议 FireWater）
    wave.btn_sim.setChecked(True)
    wave._on_sim_tick()
    assert wave.state.frame_total > 3
    wave.btn_sim.setChecked(False)

    # CSV 导出：表头 + 行数
    fd, path = tempfile.mkstemp(suffix='.csv')
    os.close(fd)
    try:
        wave.write_csv(path)
        with open(path, 'r', encoding='utf-8-sig') as f:
            rows = f.read().strip().splitlines()
        assert rows[0] == 'frame,CH0,CH1,CH2', rows[0]  # 模拟帧是 3 通道，CH2 被扩出来
        assert len(rows) - 1 == wave.state.frame_count
    finally:
        os.unlink(path)

    # 清空归零
    wave.clear_buffers()
    wave.repaint_wave()
    assert wave.state.frame_total == 0

    win.close()
    print('WAVE WINDOW OK')


def run_selftest() -> int:
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    test_rx_buffer()
    test_main_window()
    test_wave_protocols()
    test_wave_window()
    print('QT_SPIKE_SELFTEST_OK')
    return 0


if __name__ == '__main__':
    sys.exit(run_selftest())
