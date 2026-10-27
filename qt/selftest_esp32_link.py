# -*- coding: utf-8 -*-
"""ESP32 连接层自测组(DTR/RTS 策略、复位/下载模式时序、自动重连、VID/PID 标注)。

约定:本文件提供 groups() 返回测试函数列表;由 qt/selftest.py 的 run_selftest 挂载。
所有测试离屏可跑、无真机、无网络、不起 sys.executable 子进程(冻结冒烟安全)。
"""
from __future__ import annotations


class _SeqPort:
    """记录 dtr/rts 赋值顺序的假端口(时序断言用)。"""

    def __init__(self):
        self.calls = []
        self._dtr = True
        self._rts = True

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, v):
        self.calls.append(('dtr', bool(v)))
        self._dtr = bool(v)

    @property
    def rts(self):
        return self._rts

    @rts.setter
    def rts(self, v):
        self.calls.append(('rts', bool(v)))
        self._rts = bool(v)


class _BoomPort:
    """线控一碰就抛的假端口(策略容错断言用)。"""

    dtr = property(lambda self: (_ for _ in ()).throw(RuntimeError('no line ctrl')))
    rts = property(lambda self: (_ for _ in ()).throw(RuntimeError('no line ctrl')))


def test_esp32_control_sequences():
    """复位/下载模式时序与 esptool ClassicReset 语义逐拍一致,sleep 可替换。"""
    from core import esp32_control

    port = _SeqPort()
    old_sleep = esp32_control.sleep
    esp32_control.sleep = lambda s: port.calls.append(('sleep', s))
    try:
        esp32_control.pulse_reset(port)
        assert port.calls == [('rts', True), ('sleep', 0.1), ('rts', False)], port.calls

        port.calls.clear()
        esp32_control.enter_download_mode(port)
        assert port.calls == [('dtr', False), ('rts', True), ('sleep', 0.1),
                              ('dtr', True), ('rts', False), ('sleep', 0.05),
                              ('dtr', False)], port.calls
    finally:
        esp32_control.sleep = old_sleep
    print('ESP32 CONTROL SEQUENCES OK')


def test_dtr_rts_neutral_policy():
    """中性策略:启用=两线都松开;未启用=不动;线控不支持时静默容错。"""
    from core.serial_worker import apply_open_policy

    port = _SeqPort()
    apply_open_policy(port, False)
    assert port.calls == [], '未启用中性策略不应碰线'

    apply_open_policy(port, True)
    assert port.calls == [('dtr', False), ('rts', False)], port.calls
    assert port.dtr is False and port.rts is False

    apply_open_policy(_BoomPort(), True)   # USB-Serial/JTAG 等不支持 → 吞掉不抛
    print('DTR/RTS NEUTRAL POLICY OK')


def test_auto_reconnect_loop():
    """自动重连全链:loop:// 真开 → 强制底层口失效 → port_lost → 2s 后重开 → reconnected。"""
    from PySide6 import QtWidgets

    from core.serial_worker import SerialWorker
    from selftest import _pump_until

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    events = []
    worker = SerialWorker('loop://', 115200, dtr_rts_neutral=True, auto_reconnect=True)
    worker.opened.connect(lambda: events.append('opened'))
    worker.port_lost.connect(lambda msg: events.append(('lost', msg)))
    worker.reconnected.connect(lambda: events.append('reconnected'))
    worker.got_bytes.connect(lambda data: events.append(('rx', bytes(data))))
    worker.start()
    try:
        assert _pump_until(app, lambda: bool(events) and events[0] == 'opened'), \
            f'loop:// 应打开成功: {events}'
        port = worker._port
        assert port is not None and port.dtr is False and port.rts is False, \
            '中性策略应在打开后生效'
        assert port.write(b'PING') == 4
        assert _pump_until(app, lambda: any(
            isinstance(e, tuple) and e[0] == 'rx' and e[1] == b'PING' for e in events)), \
            f'回环写应回读: {events}'
        # 强制丢线:底层口关闭后 in_waiting 抛 SerialException → 走重连分支
        port.close()
        assert _pump_until(app, lambda: any(e == 'reconnected' for e in events), timeout_s=8), \
            f'关闭底层口后应自动重连成功: {events}'
        losts = [e for e in events if isinstance(e, tuple) and e[0] == 'lost']
        assert len(losts) == 1, f'port_lost 只应通知一次: {losts}'
        assert worker._port is not None and worker._port.is_open, '重连后应有新端口'
    finally:
        worker.stop()
    print('AUTO RECONNECT LOOP OK')


def test_vid_pid_labels():
    """VID/PID → 芯片标注映射 + 主窗端口下拉追加标注(描述已含则不重复)。"""
    from PySide6 import QtWidgets

    import ui.main_window as mw
    from core.ports import chip_label_for, port_name_from_display
    from selftest import _pump_until

    assert chip_label_for(0x1A86, 0x7523) == 'CH340'
    assert chip_label_for(0x10C4, 0xEA60) == 'CP210x'
    assert chip_label_for(0x303A, 0x1001) == 'ESP32 JTAG'
    assert chip_label_for(0x303A, 0x0002) == 'ESP32-S2 CDC'
    assert chip_label_for(0x1234, 0x5678) == '', '未知 VID/PID 不标注'
    assert chip_label_for(None, None) == '', 'URL 端口无 VID/PID 不标注'

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    old_scan = mw.scan_port_infos_full
    mw.scan_port_infos_full = lambda: [
        ('COM9', 'USB-SERIAL CH340 (COM9)', 'CH340'),      # 描述已含 CH340 → 不重复
        ('COM5', 'USB Serial/JTAG', 'ESP32 JTAG'),         # 描述未含 → 追加标注
        ('COM7', '虚拟口', '')]
    win = None
    try:
        win = mw.MainWindow()
        texts = [win.combo_port.itemText(i) for i in range(win.combo_port.count())]
        assert 'COM9 — USB-SERIAL CH340' in texts, texts
        assert 'COM5 — USB Serial/JTAG · ESP32 JTAG' in texts, texts
        assert 'COM7 — 虚拟口' in texts, texts
        # 标注不破坏取口:下拉文本仍能解析出 COM 名
        assert port_name_from_display('COM9 — USB-SERIAL CH340') == 'COM9'
        win.combo_port.setCurrentText('COM9 — USB-SERIAL CH340')
        assert port_name_from_display(win.combo_port.currentText()) == 'COM9'
    finally:
        mw.scan_port_infos_full = old_scan
        if win is not None:
            win._port_watch_timer.stop()
            win.close()
    print('VID/PID LABELS OK')


def test_baud_presets_and_esp32_buttons():
    """波特率预设含 ESP32 常用档;线控按钮未连接置灰、连接 loop:// 后可用,
    且「打开串口不拉 DTR/RTS」设置经连接链路真实生效。"""
    from PySide6 import QtWidgets

    import ui.main_window as mw
    from core.settings import write_setting
    from selftest import _CONNECT_KEYS, _pump_until, _restore_settings, _snapshot_settings

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    saved = _snapshot_settings(_CONNECT_KEYS + ('connect/dtr_rts_neutral',))
    win = None
    try:
        win = mw.MainWindow()
        bauds = {win.combo_baud.itemText(i) for i in range(win.combo_baud.count())}
        assert {'74880', '921600', '2000000'} <= bauds, f'缺 ESP32 波特率档: {sorted(bauds)}'
        for btn in (win.btn_mc_reset, win.btn_download, win.btn_dtr, win.btn_rts):
            assert not btn.isEnabled(), '未连接时 ESP32 线控按钮应置灰'

        write_setting('connect/dtr_rts_neutral', '1')
        win.combo_port.setCurrentText('loop://')
        win.combo_baud.setCurrentText('115200')
        win.btn_connect.click()
        assert _pump_until(app, lambda: win.worker is not None
                           and win._connected_device == 'loop://'), 'loop:// 应连上'
        for btn in (win.btn_mc_reset, win.btn_download, win.btn_dtr, win.btn_rts):
            assert btn.isEnabled(), '连接后 ESP32 线控按钮应可用'
        assert win.worker._port.dtr is False and win.worker._port.rts is False, \
            '中性策略设置应经连接链路生效(打开后两线松开)'

        win.btn_dtr.setChecked(True)
        assert _pump_until(app, lambda: win.worker._port.dtr is True), 'DTR 按钮应实时切线'
        win.btn_dtr.setChecked(False)
        win.btn_mc_reset.click()          # loop:// 支持线控,不应弹错误状态
        assert '复位失败' not in win.lbl_status.text()
    finally:
        if win is not None:
            win._port_watch_timer.stop()
            win.close()
        _restore_settings(saved)
    print('BAUD PRESETS + ESP32 BUTTONS OK')


def groups():
    return [test_esp32_control_sequences,
            test_dtr_rts_neutral_policy,
            test_auto_reconnect_loop,
            test_vid_pid_labels,
            test_baud_presets_and_esp32_buttons]
