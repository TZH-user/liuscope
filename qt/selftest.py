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
    win.wave.state.enabled = False  # 本测试只看终端管线，波形侧不参与积压
    # 设置记忆会恢复本机 ini 的收发格式；终端管线用例统一按 ASCII 口径来
    win.combo_rx_format.setCurrentText('ASCII')
    win.combo_tx_format.setCurrentText('ASCII')

    # 完整行立即吐出，残行留在缓冲
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


def test_terminal_chrome():
    """A1/A2：清空记录 + 收发字节统计 + 全时间戳 + 日志条目列表。"""
    from PySide6 import QtWidgets

    from ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    win.wave.state.enabled = False
    win.combo_rx_format.setCurrentText('ASCII')

    # 全时间戳：条目与日志头行都带日期（Ctrl+F 时间定位的数据基础）
    win.ingest_bytes(b'boot v1.0\n')
    entry = win.message_entries[-1]
    assert entry['direction'] == 'RX' and entry['text'] == 'boot v1.0'
    import re as _re
    assert _re.search(r'\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\] RX boot v1\.0',
                      win.log.toPlainText()), '日志头行必须是全时间戳'
    assert win.rx_bytes_total == len(b'boot v1.0\n')
    assert 'RX 0.0 KiB | TX 0.0 KiB' in win.lbl_stats.text()

    # 清空记录：显示/条目/残行缓冲全清，字节统计保留（连接周期量）
    win.ingest_bytes(b'temp=41.5\n')
    win.btn_clear_log.click()
    assert len(win.message_entries) == 1
    assert win.message_entries[0]['text'] == '消息记录已清空。'
    assert win.buffer.backlog_bytes == 0
    assert win.rx_bytes_total > 0, '字节统计不清零'
    assert '消息记录已清空。' in win.log.toPlainText()

    # 条目上限裁剪：>上限丢最老（注入小上限避免真灌 2 万条）
    win.message_entries.clear()
    import ui.main_window as mw
    old_max, old_trim = mw.MAX_LOG_ENTRIES, mw.MAX_LOG_TRIM
    mw.MAX_LOG_ENTRIES, mw.MAX_LOG_TRIM = 10, 4
    for i in range(12):
        win._append_log('RX', f'LINE-{i}')
    mw.MAX_LOG_ENTRIES, mw.MAX_LOG_TRIM = old_max, old_trim
    assert len(win.message_entries) == 8
    assert win.message_entries[0]['text'] == 'LINE-4', '应丢最老的 4 条'

    win.close()
    print('TERMINAL CHROME OK')


def test_ports_watch():
    """A3/A4：端口展示文本 / CH340 优选 / 热插拔自动连接 / 设置记忆。"""
    from PySide6 import QtCore, QtWidgets

    import ui.main_window as mw
    from core.ports import (is_ch340_description, pick_auto_connect_port,
                            port_display_text, port_name_from_display)
    from core.settings import read_setting, write_setting

    # 展示文本与取口名（描述自带 (COMx) 尾巴时去重）
    assert port_display_text('COM6', 'USB-SERIAL CH340 (COM6)') == 'COM6 — USB-SERIAL CH340'
    assert port_display_text('COM6', 'COM6') == 'COM6'
    assert port_name_from_display('COM6 — USB-SERIAL CH340') == 'COM6'
    assert port_name_from_display('COM33') == 'COM33'

    # CH340 优选（多新端口选 CH340，无 CH340 取第一项）
    assert pick_auto_connect_port([('COM6', '蓝牙串行'), ('COM8', 'USB-SERIAL CH340')]) == 'COM8'
    assert pick_auto_connect_port([('COM3', ''), ('COM4', 'USB 设备')]) == 'COM3'
    assert pick_auto_connect_port([]) == ''
    assert is_ch340_description('USB-SERIAL CH341A (COM5)')
    assert not is_ch340_description('蓝牙链接上的标准串行 (COM6)')

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = mw.MainWindow()
    win.show()
    win.wave.state.enabled = False

    created = []

    class FakeWorker(QtCore.QObject):
        got_bytes = QtCore.Signal(bytes)
        opened = QtCore.Signal()
        open_failed = QtCore.Signal(str)
        lost = QtCore.Signal(str)

        def __init__(self, device, baud, parent=None):
            super().__init__(parent)
            self.device, self.baud = device, baud
            created.append(self)

        def start(self):
            pass

        def stop(self):
            pass

        def write_bytes(self, data):
            return True

    old_worker_cls, old_scan = mw.SerialWorker, mw.scan_port_infos
    try:
        mw.SerialWorker = FakeWorker
        mw.scan_port_infos = lambda: [('COM3', '蓝牙链接上的标准串行 (COM3)'),
                                      ('COM8', 'USB-SERIAL CH340 (COM8)')]
        win.refresh_ports()
        assert win.combo_port.count() == 2

        # 勾选自动连接 + COM8 是基线外新设备 → 自动连接并优选 CH340
        win.chk_auto_link.setChecked(True)
        win._known_port_names = ['COM3']
        win._port_watch_tick()
        assert len(created) == 1 and created[0].device == 'COM8', '新设备应自动连接且优选 CH340'
        assert '检测到新串口设备 COM8' in win.log.toPlainText()
        assert win._connected_device == 'COM8'
        assert read_setting('connect/port', '') == 'COM8'

        # 已连着同一口：设备列表再变也不重复连
        n_created = len(created)
        win._known_port_names = ['COM3']
        win._port_watch_tick()
        assert len(created) == n_created

        # 未勾选自动连接：新设备只刷新列表不连
        win.chk_auto_link.setChecked(False)
        win._teardown_worker()
        win._known_port_names = ['COM3']
        mw.scan_port_infos = lambda: [('COM3', ''), ('COM8', 'USB-SERIAL CH340 (COM8)'),
                                      ('COM9', 'USB-SERIAL CH340 (COM9)')]
        win._port_watch_tick()
        assert len(created) == n_created
        assert win.combo_port.count() == 3, '端口列表应刷新出 COM9'
    finally:
        mw.SerialWorker, mw.scan_port_infos = old_worker_cls, old_scan
        win._port_watch_timer.stop()
    win.close()

    # 连接周期记忆：先写设置再重建窗口（必须在 win.close() 之后写——
    # closeEvent 会把当时的组合框状态存回 ini）
    write_setting('connect/rx_format', 'HEX')
    write_setting('connect/tx_format', 'HEX')
    write_setting('connect/newline', 'CRLF')
    write_setting('connect/baud', '115200')

    win2 = mw.MainWindow()
    try:
        assert win2.combo_rx_format.currentText() == 'HEX'
        assert win2.combo_tx_format.currentText() == 'HEX'
        assert win2.combo_newline.currentText() == 'CRLF'
        assert win2.combo_baud.currentText() == '115200'
    finally:
        # 组合框先复位再关窗：closeEvent 会把当前状态存回 ini，顺手还原默认
        win2.combo_rx_format.setCurrentText('ASCII')
        win2.combo_tx_format.setCurrentText('ASCII')
        win2.combo_newline.setCurrentText('LF')
        win2.combo_baud.setCurrentText('1500000')
        win2.chk_auto_link.setChecked(False)
        win2._port_watch_timer.stop()
        win2.close()
    print('PORT WATCH OK')


def test_find_time_mode():
    """A5：时间戳查询识别/解析 + 头行定位（对齐主线 find_log_timestamp_jump）。"""
    from datetime import datetime

    from PySide6 import QtWidgets

    from core.log_find import (collect_timestamp_headers, find_timestamp_jump,
                               is_timestamp_query, parse_timestamp)
    from ui.find_bar import FindBar

    # 查询形态识别
    assert is_timestamp_query('20:57')
    assert is_timestamp_query('10:03:22.5')
    assert is_timestamp_query('2026-10-24 20:57:38.500')
    assert is_timestamp_query('2026-10-24')
    assert not is_timestamp_query('hello')
    assert not is_timestamp_query('')
    # 非法时间解析失败 → 回退文本搜索
    assert parse_timestamp('25:99') is None
    assert parse_timestamp('10:03') == datetime(1900, 1, 1, 10, 3)

    # 头行收集与定位：3 条头行 10:00 / 10:05 / 10:10
    text = ('[2026-10-24 10:00:00.000] RX\nboot ok\n'
            '[2026-10-24 10:05:00.000] RX\ntemp=1\n'
            '[2026-10-24 10:10:00.000] SYS\ndone\n')
    hits = collect_timestamp_headers(text)
    assert len(hits) == 3 and hits[0][0] == 0

    # 只给时刻：日期取首条头行的日期；向前 = 首条 ≥ 时刻
    r = find_timestamp_jump(hits, '10:03', backward=False)
    assert r['found'] and r['ordinal'] == 2 and r['time'].strftime('%H:%M') == '10:05'
    assert text[r['pos']:r['pos'] + r['length']] == '[2026-10-24 10:05:00.000]'
    # 精确命中取自身
    r = find_timestamp_jump(hits, '10:10:00', backward=False)
    assert r['ordinal'] == 3
    # 时刻早于所有条目 → 回绕到第一条
    r = find_timestamp_jump(hits, '09:00', backward=False)
    assert r['ordinal'] == 1
    # 向后 = 末条 < 时刻
    r = find_timestamp_jump(hits, '10:10', backward=True, sel_start=10 ** 9)
    assert r['ordinal'] == 2, '选区在末尾时向后应取 10:05 这条'
    # 无 ≥ 该时刻条目
    r = find_timestamp_jump(hits, '23:00', backward=False, sel_start=10 ** 9, sel_end=10 ** 9)
    assert r is not None and not r['found'] and r['total'] == 3

    # FindBar 集成：时间查询直接跳头行并显示「时刻 第 i/N 处」
    edit = QtWidgets.QTextEdit()
    edit.setPlainText(text)
    bar = FindBar(edit)
    bar.edit_find.setText('10:03')
    assert '时刻 第 2/3 处' in bar.lbl_count.text()
    cursor = edit.textCursor()
    assert '10:05' in cursor.selectedText()
    bar.edit_find.setText('hello')
    assert not bar._time_mode and bar.lbl_count.text() == '无匹配'
    print('FIND TIME OK')


def test_auto_log_export():
    """B6/B7：自动日志落盘（懒建文件/SYS 不落/勾选关闭丢弃）+ 日志导出。"""
    import os
    import tempfile

    from PySide6 import QtWidgets

    import ui.main_window as mw
    from core.auto_log import AutoLogWriter, build_log_export_text, format_entry_block
    from datetime import datetime

    # 纯逻辑：块格式与主线同构
    block = format_entry_block(datetime(2026, 10, 24, 10, 0, 0, 123000), 'RX', 'boot ok')
    assert block == '[2026-10-24 10:00:00.123] RX\r\nboot ok\r\n\r\n'

    tmp_dir = tempfile.mkdtemp(prefix='qt_auto_log_')
    writer = AutoLogWriter(tmp_dir, now_fn=lambda: 1761271200.0)  # 固定文件名时刻
    assert not writer.active and writer.path is None
    writer.enqueue(format_entry_block(datetime(2026, 10, 24, 10, 0, 1), 'RX', 'line-1'))
    assert writer.pending_count() == 1
    assert writer.flush()
    assert writer.active and 'serial_auto_log_' in os.path.basename(writer.path)
    writer.enqueue(format_entry_block(datetime(2026, 10, 24, 10, 0, 2), 'TX', 'AT'))
    writer.close()
    with open(writer.path, encoding='utf-8', newline='') as f:
        content = f.read()
    assert 'line-1' in content and '[2026-10-24 10:00:02.000] TX\r\nAT\r\n\r\n' in content

    # 导出文本构建：头行 + 正文 + 空行
    entries = [
        {'ts': datetime(2026, 10, 24, 10, 0, 0), 'direction': 'RX', 'text': 'hello'},
        {'ts': datetime(2026, 10, 24, 10, 0, 1), 'direction': 'SYS', 'text': 'note'},
    ]
    export_text = build_log_export_text(entries)
    assert export_text == ('[2026-10-24 10:00:00.000] RX\nhello\n\n'
                           '[2026-10-24 10:00:01.000] SYS\nnote\n\n')

    # 主窗体集成：注入临时日志目录
    old_get_log_dir = mw.get_log_dir
    mw.get_log_dir = lambda: tmp_dir
    try:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        win = mw.MainWindow()
        win.show()
        win.wave.state.enabled = False
        win.combo_rx_format.setCurrentText('ASCII')
        win.chk_auto_save.setChecked(True)
        before = os.listdir(tmp_dir)
        win.ingest_bytes(b'log-me\n')          # RX 入队
        win._append_log('SYS', 'sys-note')      # SYS 不入队
        assert win._auto_log.pending_count() == 1, 'SYS 不该进自动日志'
        win.on_flush_tick()                     # 落盘 + 标签更新
        assert '日志: ' in win.lbl_log_path.text()
        after = os.listdir(tmp_dir)
        assert len(after) == len(before) + 1
        latest = max(os.path.join(tmp_dir, n) for n in after)
        with open(latest, encoding='utf-8', newline='') as f:
            assert 'log-me' in f.read()

        # 勾选关闭：积压直接丢，不落盘
        win.chk_auto_save.setChecked(False)
        win.ingest_bytes(b'dropped\n')
        win._flush_auto_log()
        assert win._auto_log.pending_count() == 0

        # 导出日志（直接调内部方法避免文件对话框）
        export_path = os.path.join(tmp_dir, 'export_test.txt')
        win._export_log_to(export_path)
        with open(export_path, encoding='utf-8') as f:
            exported = f.read()
        assert 'log-me' in exported, '导出文本应包含 RX 条目'
        assert '日志已导出到' in win.log.toPlainText(), '完成提示进窗口日志（导出文件先写后记，不含自身）'
        win._port_watch_timer.stop()
        win.close()
    finally:
        mw.get_log_dir = old_get_log_dir
    print('AUTO LOG OK')


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


def test_hexutil():
    from core.hexutil import bytes_to_hex_text, try_parse_hex_text

    assert bytes_to_hex_text(b'\xDE\xAD\x01') == 'DE AD 01'
    assert bytes_to_hex_text(b'') == ''
    assert try_parse_hex_text('DE AD 01') == b'\xde\xad\x01'
    assert try_parse_hex_text('de,ad;be:ef') == b'\xde\xad\xbe\xef'
    assert try_parse_hex_text('0xDE 0x0A') == b'\xde\x0a'
    assert try_parse_hex_text('0X2 A') == b'\x02\n'  # 0X 大小写不敏感、单 token 1~2 位
    assert try_parse_hex_text('  ') == b''
    try:
        try_parse_hex_text('GG')
        raise AssertionError('GG 应当抛 ValueError')
    except ValueError as exc:
        assert 'HEX 输入无效: GG' in str(exc)
    print('HEXUTIL OK')


def test_main_window_m3():
    """M3 端到端：HEX 收发 / 换行模式 / 发送历史 / 文件发送状态机 / 查找条。"""
    from PySide6 import QtWidgets

    from ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()

    # HEX 接收：按块落显，残行先落显
    win.combo_rx_format.setCurrentText('HEX')
    win.ingest_bytes(b'\xde\xad\xbe\xef')
    assert 'RX DE AD BE EF' in win.log.toPlainText()
    # 切回 ASCII：HEX 模式下没进行缓冲，积压为 0
    win.combo_rx_format.setCurrentText('ASCII')
    assert win.buffer.backlog_bytes == 0

    # HEX 发送：未连接仅入日志；非法输入置状态不发送
    win.combo_tx_format.setCurrentText('HEX')
    win.edit_send.setText('01 0x2, A')
    win.send()
    assert 'TX 01 02 0A' in win.log.toPlainText()
    win.edit_send.setText('GG')
    win.send()
    assert 'HEX 输入无效: GG' in win.lbl_status.text()

    # ASCII 发送 + 换行模式：展示层 <CR>/<LF> 可视化
    win.combo_tx_format.setCurrentText('ASCII')
    win.combo_newline.setCurrentText('CRLF')
    win.edit_send.setText('AT+RST')
    win.send()
    assert 'TX AT+RST<CR><LF>' in win.log.toPlainText()

    # 发送历史：⬆ 回翻、⬇ 走到底恢复草稿、连续重复不入栈
    win.edit_send.setText('draft-x')
    win.edit_send.recall(-1)
    assert win.edit_send.text() == 'AT+RST', '⬆ 应取最近一条历史'
    win.edit_send.recall(+1)
    assert win.edit_send.text() == 'draft-x', '⬇ 走到底应恢复草稿'
    before = len(win.edit_send._history)
    win.edit_send.push_history('HIST-UNIQUE-1')
    win.edit_send.push_history('HIST-UNIQUE-1')
    assert len(win.edit_send._history) == before + 1, '连续重复只入栈一次'

    # 文件发送状态机：假 worker 4KiB 分块，20ms 定时器驱到完成
    sent = bytearray()

    class FakeWorker:
        def write_bytes(self, data: bytes) -> bool:
            sent.extend(data)
            return True

    win.worker = FakeWorker()
    import tempfile
    import os
    payload = bytes(range(256)) * 40  # 10240 字节 → 3 块（4096+4096+2048）
    fd, path = tempfile.mkstemp(suffix='.bin')
    os.write(fd, payload)
    os.close(fd)
    try:
        win._file_send = {'name': 't.bin', 'bytes': payload, 'pos': 0}
        win.btn_file.setText('停止')
        win.file_timer.start()
        for _ in range(10):
            win._file_send_tick()
            if win._file_send is None:
                break
        assert bytes(sent) == payload, '文件必须原样字节流发完'
        assert win._file_send is None
        assert '文件发送完成: t.bin' in win.lbl_status.text()

        # 中途停止：已发字节如实入账
        win.worker = FakeWorker()
        win._file_send = {'name': 't2.bin', 'bytes': payload, 'pos': 0}
        win._file_send_tick()
        assert win._file_send['pos'] == 4096
        win._stop_file_send('手动停止')
        assert win._file_send is None
        assert '[文件发送中断] t2.bin: 手动停止（已发 4096 / 10240 字节）' in win.log.toPlainText()
        assert win.btn_file.text() == '文件发送'
    finally:
        os.unlink(path)

    # 查找条：实时高亮 + 计数 + 循环跳转
    win.find_bar.open_and_focus()
    win.find_bar.edit_find.setText('AT+RST')
    assert win.find_bar.lbl_count.text().startswith('第 1/')
    total = int(win.find_bar.lbl_count.text().split('/')[1].split(' ')[0])
    win.find_bar.navigate(+1)
    assert win.find_bar.lbl_count.text() == f'第 {2 if total > 1 else 1}/{total} 处'
    win.find_bar.navigate(-1)
    assert win.find_bar.lbl_count.text() == f'第 1/{total} 处'
    win.find_bar.hide_and_clear()
    assert not win.log.extraSelections(), '关闭查找条应清掉高亮'

    win.worker = None
    win.close()
    print('MAINWINDOW M3 OK')


def test_modbus_core():
    from core.modbus import (WatchItem, build_modbus_request, decode_modbus_registers,
                             get_modbus_crc16, merge_watch_groups, parse_modbus_response,
                             slice_watch_value, test_modbus_crc, watch_flash_tone)

    # CRC16 经典向量：01 03 00 00 00 02 → C4 0B（低字节在前）
    assert get_modbus_crc16(bytes.fromhex('010300000002')) == bytes.fromhex('C40B')
    frame, error = build_modbus_request(1, 3, 0, 2)
    assert error == '' and frame == bytes.fromhex('010300000002C40B')
    assert test_modbus_crc(frame) and not test_modbus_crc(frame[:-2] + b'\x00\x00')

    # 组帧边界：03 数量 126 拒绝；06 写值 0x2000；05 置位 0xFF00；16 列表
    _f, e = build_modbus_request(1, 3, 0, 126)
    assert '1~125' in e
    f6, e6 = build_modbus_request(1, 6, 0x10, 1, value_text='0x2000')
    assert f6[:6] == bytes.fromhex('010600102000') and test_modbus_crc(f6)
    f5, _ = build_modbus_request(1, 5, 4, 1, value_text='on')
    assert f5[4:6] == b'\xff\x00'
    f16, e16 = build_modbus_request(2, 16, 0, 0, value_list=['100', '0x2000', '-5'])
    assert e16 == '' and f16[6] == 6 and f16[4:6] == b'\x00\x03'
    # -5 按 0xFFFB 收（band 0xFFFF，与主线一致）
    assert f16[7:13] == bytes.fromhex('0064') + bytes.fromhex('2000') + bytes.fromhex('FFFB')

    # 响应解析：2 寄存器 0x000A 0x0102 → 十进制 '10, 258'
    body = bytes.fromhex('010304000A0102')
    resp = body + get_modbus_crc16(body)
    parsed = parse_modbus_response(resp, 3, 1, 2)
    assert parsed['ok'] and parsed['reg_bytes'] == bytes.fromhex('000A0102')
    assert decode_modbus_registers(parsed['reg_bytes'], '十进制') == '10, 258'
    assert decode_modbus_registers(parsed['reg_bytes'], 'HEX') == '0x000A, 0x0102'
    assert decode_modbus_registers(bytes.fromhex('FFFF'), '有符号') == '-1'
    # float32(ABCD)：寄存器 0x42F6 0x0000 → 123.0
    assert decode_modbus_registers(bytes.fromhex('42F60000'), 'float32(ABCD)') == '123'

    # 异常帧：01 83 02 + CRC
    exc_body = bytes.fromhex('018302')
    exc = exc_body + get_modbus_crc16(exc_body)
    parsed = parse_modbus_response(exc, 3, 1, 2)
    assert not parsed['ok'] and '02 非法数据地址' in parsed['error']
    # CRC 坏帧
    parsed = parse_modbus_response(resp[:-2] + b'\x00\x00', 3, 1, 2)
    assert parsed['error'] == 'CRC 校验失败'

    # Watch 合并：间隙≤4 并区间，超上限不并
    items = [WatchItem(1, 3, 0, 2), WatchItem(1, 3, 4, 3), WatchItem(1, 3, 100, 1)]
    groups = merge_watch_groups(items)
    assert len(groups) == 2
    assert (groups[0]['start'], groups[0]['count']) == (0, 7)
    # 切片：合并响应里地址 4、数量 3 的项取 00 01 00 02 00 03 → '1, 2, 3'
    text, raw = slice_watch_value(bytes.fromhex('000A01020064000F000100020003'), 3, 0, items[1])
    assert text == '1, 2, 3' and raw == '00 01 00 02 00 03'
    # 线圈切片：fc1，字节 0b00000101 → 位 0,2 = 1
    bit_item = WatchItem(1, 1, 0, 3)
    text, _ = slice_watch_value(b'\x05', 1, 0, bit_item)
    assert text == '1,0,1'
    # 闪烁语义
    assert watch_flash_tone('10', '11') == 'up'
    assert watch_flash_tone('10', '9') == 'down'
    assert watch_flash_tone('10', '10') == 'same'
    assert watch_flash_tone('ok', '10') == 'changed'
    print('MODBUS CORE OK')


def test_modbus_panel():
    from PySide6 import QtWidgets

    from core.modbus import get_modbus_crc16
    from ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    panel = win.modbus

    # 假从站：03 读保持寄存器 → 固定回 2 寄存器 0x0064 0x00C8（100,200）
    def fake_slave(frame: bytes, timeout_ms: int = 600) -> bytes:
        assert frame[-2:] == get_modbus_crc16(frame[:-2])
        if frame[1] == 3:
            body = bytes([frame[0], 3, 4, 0, 100, 0, 200])
            return body + get_modbus_crc16(body)
        body = bytes([frame[0], frame[1]]) + frame[2:3] + b'\x02'
        return (body + get_modbus_crc16(body))[:5]  # 异常响应占位（本测试不会走到）

    panel.transport = fake_slave
    win_modbus_logs = []

    panel.spin_slave.setValue(1)
    panel.combo_fc.setCurrentIndex(2)   # 03
    panel.spin_start.setValue(0)
    panel.spin_count.setValue(2)
    panel.run_transaction()
    assert '寄存器[2] 十进制: 100, 200' in panel.result_box.toPlainText()
    assert panel.lbl_frame.text() == '发送: 01 03 00 00 00 02 C4 0B'

    # 事务回写主日志（TX/RX [Modbus] 条目，Ctrl+F 可检索）
    panel.log_fn = lambda d, t: win_modbus_logs.append((d, t))
    panel.run_transaction()
    assert ('TX', '[Modbus] 01 03 00 00 00 02 C4 0B') in win_modbus_logs
    assert any(d == 'RX' and t.startswith('[Modbus] 01 03 04') for d, t in win_modbus_logs)

    # 监视：加两项（同从站同功能码、地址近邻）→ 合并成一轮读，行值回填
    panel.spin_start.setValue(0)
    panel.spin_count.setValue(1)
    panel.edit_value.setText('温度')
    panel.add_watch()
    panel.spin_start.setValue(1)
    panel.add_watch()
    assert len(panel.watch_items) == 2
    panel.chk_watch.setChecked(True)
    panel.run_watch_cycle()
    assert panel.watch_table.item(0, 4).text() == '100'
    assert panel.watch_table.item(1, 4).text() == '200'
    assert '无响应' not in panel.watch_table.item(0, 5).text()

    # 未连接提示（transport 置空）
    panel.transport = None
    panel.run_transaction()
    assert '串口未连接' in panel.result_box.toPlainText()

    win.close()
    print('MODBUS PANEL OK')


def test_update_check():
    import io
    import json

    from core.update_check import compare_app_versions, get_version_tuple, check_app_update

    assert get_version_tuple('v1.2.10') == (1, 2, 10)
    assert get_version_tuple('1.2') == (1, 2, 0)
    assert get_version_tuple('0.2.0-qt') == (0, 2, 0)
    assert get_version_tuple('v1.x.3') == (1, 0, 3)
    assert compare_app_versions('1.1.0', 'v1.0.0') == 1
    assert compare_app_versions('1.1.0', '1.1.0') == 0
    assert compare_app_versions('0.9.9', '1.0.0') == -1
    assert compare_app_versions('1.1', '1.1.0') == 0

    def fake_opener_ok(request, timeout=10):
        return io.BytesIO(json.dumps({'tag_name': 'v9.9.9', 'body': '新版说明'}).encode())

    result = check_app_update('1.1.0', opener=fake_opener_ok)
    assert result['ok'] and result['has_update'] and result['latest'] == 'v9.9.9'
    assert result['note'] == '新版说明' and result['current'] == '1.1.0'

    def fake_opener_old(request, timeout=10):
        return io.BytesIO(json.dumps({'tag_name': 'v0.0.1'}).encode())
    result = check_app_update('1.1.0', opener=fake_opener_old)
    assert result['ok'] and not result['has_update']

    def fake_opener_dead(request, timeout=10):
        raise OSError('network unreachable')

    result = check_app_update('1.1.0', opener=fake_opener_dead)
    assert not result['ok'] and 'network unreachable' in result['note']
    print('UPDATE CHECK OK')


def test_update_ui():
    from PySide6 import QtWidgets

    from ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()

    win._on_update_result({'ok': True, 'has_update': True, 'latest': 'v9.9.9',
                           'page_url': 'https://example.com', 'note': ''}, manual=False)
    assert '发现新版本 v9.9.9' in win.log.toPlainText()
    assert '有新版本 v9.9.9' in win.lbl_status.text()

    win._on_update_result({'ok': True, 'has_update': False}, manual=True)
    assert '已是最新版本' in win.log.toPlainText()

    win._on_update_result({'ok': False, 'has_update': False, 'note': '超时'}, manual=True)
    assert '检查更新失败: 超时' in win.log.toPlainText()

    win._on_update_result({'ok': False, 'has_update': False, 'note': '静默'}, manual=False)
    assert '静默' not in win.log.toPlainText(), '自动检查失败不打扰'

    win.close()
    print('UPDATE UI OK')


def test_spectrum_dsp():
    import math

    import numpy as np

    from core.dsp import (color_map, magnitude_spectrum, max_bin, to_db,
                          update_ema, update_max_hold, waterfall_row)

    # 1000 点、fs=1000：50 Hz 正弦（25 个周期）峰在 bin 50
    n = 1000
    sig = [3.0 * math.sin(2 * math.pi * 50.0 * i / 1000.0) for i in range(n)]
    mag, used = magnitude_spectrum(sig)
    assert used == 512 and mag.size == 256
    peak = max_bin(to_db(mag), 1)
    assert abs(peak * 1000.0 / used - 50.0) < 1000.0 / used, f'peak bin {peak}'
    # 满量程正弦（频率对齐 bin）→ 汉宁窗相干增益 0.5 → -6.02 dB（主线 C# 同样不补偿）
    sig2 = [math.sin(2 * math.pi * 32 * i / 512.0) for i in range(512)]
    mag2, _ = magnitude_spectrum(sig2)
    db2 = to_db(mag2)
    assert abs(db2[32] + 6.02) < 0.5, f'bin 幅度应≈-6dB，实际 {db2[32]}'
    # 底噪下限 -140
    assert to_db(np.array([0.0]))[0] == -140.0
    # 短输入返回空
    short_mag, short_used = magnitude_spectrum([1.0] * 10)
    assert short_used == 0 and short_mag.size == 0

    # 峰值保持 / EMA
    hold = np.full(4, -140.0)
    update_max_hold(np.array([-120.0, -130.0, -110.0, -100.0]), hold)
    assert list(hold) == [-120.0, -130.0, -110.0, -100.0]
    update_max_hold(np.array([-125.0, -125.0, -125.0, -125.0]), hold)
    assert list(hold) == [-120.0, -125.0, -110.0, -100.0], '保持只升不降'
    avg = np.array([0.0, 0.0])
    update_ema(np.array([10.0, -10.0]), avg, 0.25)
    assert list(avg) == [2.5, -2.5]

    # 瀑布配色端点：t=0 → 暗蓝(0,0,60)，t=1 → 白(255,255,255)
    row = waterfall_row(np.array([-100.0, 0.0]), -100.0, 0.0, 1.0)
    assert row.shape == (2, 3) and tuple(row[0]) == (0, 0, 60) and tuple(row[1]) == (255, 255, 255)
    assert color_map(0.4)[1] > color_map(0.1)[1], '中段应比暗端绿'
    print('SPECTRUM DSP OK')


def test_spectrum_panel():
    import math

    import numpy as np
    from PySide6 import QtWidgets

    from ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    panel = win.spectrum

    # 喂 FireWater 单通道 50Hz 正弦（fs=1000 → 帧率 1000/秒，模拟数据 20ms/帧太慢，直接喂数据）
    phase = 0.0
    for _ in range(600):
        phase += 2 * math.pi * 50.0 / 1000.0
        win.wave.state.consume_bytes(f'{math.sin(phase):.6f}\n'.encode())
    panel.combo_ch.setCurrentIndex(0)
    panel.combo_pts.setCurrentText('512')
    panel.edit_fs.setText('1000')

    result = panel.analyze()
    assert result is not None and result['used'] == 512
    assert abs(result['df'] - 1000.0 / 512) < 1e-9
    assert abs(result['rbw'] - 1.5 * 1000.0 / 512) < 1e-9

    # 峰值保持积累：重绘两帧后 max_hold 不低于当前谱
    panel.chk_peak.setChecked(True)
    panel.repaint_spectrum()
    panel.repaint_spectrum()
    assert panel._max_hold is not None and panel._max_hold.size == 256
    # 主频标注
    assert '主频:' in panel.lbl_peak.text() and '--' not in panel.lbl_peak.text()
    # 瀑布在积累且行数受限
    assert 1 <= len(panel._waterfall) <= 120

    # 冻结：底层继续进数据，面板不更新
    panel.btn_freeze.setChecked(True)
    rows_at_freeze = len(panel._waterfall)
    win.wave.state.consume_bytes(b'0.5\n')
    panel.repaint_spectrum()
    assert len(panel._waterfall) == rows_at_freeze, '冻结时瀑布不进新行'
    panel.btn_freeze.setChecked(False)

    # 瀑布方向：新行在顶（图像第 0 行 == 最新一行，旧行被顶向下）
    panel.chk_wf.setChecked(True)
    panel.repaint_spectrum()
    top_row = panel._waterfall[0]
    panel.repaint_spectrum()  # 数据没变，但再推进一帧序列
    from core.dsp import waterfall_row as _wf_row
    scale = panel._read_scale()
    expected = _wf_row(panel.analyze()['db'], scale[2], scale[0], 1.0)
    assert np.array_equal(panel._wf_image.image[0], expected), '图像顶部必须是最新谱行'

    win.close()
    print('SPECTRUM PANEL OK')


def run_selftest() -> int:
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    test_rx_buffer()
    test_hexutil()
    test_main_window()
    test_terminal_chrome()
    test_ports_watch()
    test_find_time_mode()
    test_auto_log_export()
    test_wave_protocols()
    test_wave_window()
    test_main_window_m3()
    test_modbus_core()
    test_modbus_panel()
    test_update_check()
    test_update_ui()
    test_spectrum_dsp()
    test_spectrum_panel()
    print('QT_SPIKE_SELFTEST_OK')
    return 0


if __name__ == '__main__':
    sys.exit(run_selftest())
