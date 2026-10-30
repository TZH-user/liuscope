# -*- coding: utf-8 -*-
"""ESP32 日志层自测组(IDF 日志解析、复位横幅/崩溃检测、backtrace 提取、端口错误提示)。

约定:本文件提供 groups() 返回测试函数列表;由 qt/selftest.py 的 run_selftest 挂载。
所有测试离屏可跑、无真机、无网络、不起 sys.executable 子进程(冻结冒烟安全)。
"""
from __future__ import annotations


def test_esp_idf_parse():
    """解析/复位横幅/panic/backtrace 提取纯逻辑(含 ANSI 样本与落显后缀)。"""
    from core import esp_idf_log

    info = esp_idf_log.parse_idf_line('I (2812) wifi: mode : sta (aa:bb)')
    assert info == {'level': 'I', 'ts_ms': 2812, 'tag': 'wifi', 'msg': 'mode : sta (aa:bb)'}

    ansi = '\x1b[0;31mE (1234) foo: boom!\x1b[0m'
    info = esp_idf_log.parse_idf_line(ansi)
    assert info and info['level'] == 'E' and info['tag'] == 'foo' and info['msg'] == 'boom!'

    assert esp_idf_log.parse_idf_line('普通串口输出不是 IDF 日志') is None

    rst = esp_idf_log.is_reset_banner('rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)')
    assert rst == (0x01, 'POWERON_RESET'), rst
    assert esp_idf_log.is_reset_banner('rst:0x8 (TG1WDT_SYS_RESET)') == (0x08, 'TG1WDT_SYS_RESET')
    assert esp_idf_log.is_reset_banner('普通输出') is None

    panic = esp_idf_log.is_panic(
        "Guru Meditation Error: Core  0 panic'ed (StoreProhibited). Exception was unhandled.")
    assert panic == (0, 'StoreProhibited'), panic
    assert esp_idf_log.is_panic('I (99) app: running fine') is None

    bt = 'backtrace:0x400814ad:0x3ffb5e50 0x40082319:0x3ffb5e70 0x40082f31:0x3ffb5e90'
    assert esp_idf_log.extract_backtrace_addrs(bt) == ['0x400814ad', '0x40082319', '0x40082f31'], \
        'pc:sp 交替应只取 pc 列'
    assert esp_idf_log.extract_backtrace_addrs('Backtrace: 0x4200e2a2:0x3fca82e0 |-| (-|)') \
        == ['0x4200e2a2']
    assert esp_idf_log.extract_backtrace_addrs('普通行 0x1234') == []
    # 落显后缀不影响识别
    assert esp_idf_log.is_reset_banner('rst:0x1 (POWERON_RESET) (空闲落显)') == \
        (0x01, 'POWERON_RESET')
    print('ESP IDF PARSE OK')


def test_port_open_hints():
    """打开失败处置提示按平台/错误文本映射。"""
    from core.port_hints import port_open_error_hint

    assert 'dialout' in port_open_error_hint(
        'PermissionError(13, "Permission denied")', 'linux')
    assert 'ModemManager' in port_open_error_hint(
        'SerialException: Device or resource busy', 'linux')
    assert '占用' in port_open_error_hint('could not open port: Access is denied (5)', 'win32')
    # winerror 31（设备未响应配置）文本同时含 PermissionError → 设备故障提示优先于笼统占用
    hint31 = port_open_error_hint(
        "Cannot configure port, something went wrong. Original message: "
        "PermissionError(13, '连到系统上的设备没有发挥作用。', None, 31)", 'win32')
    assert '错误码 31' in hint31 and '重插' in hint31
    assert port_open_error_hint('PortDeviceNotImplemented: unknown', 'win32') is None
    assert port_open_error_hint('', '') is None
    print('PORT OPEN HINTS OK')


def test_idf_log_ui():
    """主窗集成:IDF 开关着色/复位装饰行/backtrace 收集与按钮联动/开关关闭旁路。"""
    from PySide6 import QtWidgets

    import ui.main_window as mw
    from ui import theme
    from selftest import _CONNECT_KEYS, _restore_settings, _snapshot_settings

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    saved = _snapshot_settings(_CONNECT_KEYS + ('log/esp_idf_parse',))
    win = None
    try:
        win = mw.MainWindow()
        win._port_watch_timer.stop()
        assert win.chk_idf.isChecked(), 'IDF 解析默认开'
        assert not win.btn_backtrace.isEnabled(), '无地址时解析按钮置灰'

        # 复位横幅 → 装饰行(含原因) + 清空收集器
        n0 = len(win.message_entries)
        win._append_log('RX', 'rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)')
        assert any('设备复位' in e['text'] and '0x01' in e['text'] and 'POWERON_RESET' in e['text']
                   for e in win.message_entries[n0:]), '应插入复位装饰行'
        assert win._esp32_bt == []
        # 风格:RX 色 → accent(装饰行触发后 RX 行走 accent)
        color, marker, bold = win._esp32_log_style('rst:0x1 (POWERON_RESET)')
        assert color == theme.color('accent') and marker and bold

        # backtrace → 只收 pc、按钮点亮
        win._append_log('RX', 'backtrace:0x400814ad:0x3ffb5e50 0x40082319:0x3ffb5e70')
        assert win._esp32_bt == ['0x400814ad', '0x40082319'], win._esp32_bt
        assert win.btn_backtrace.isEnabled(), '收集到地址后解析按钮应可用'
        assert '0x3ffb5e50' not in win._esp32_bt, 'sp 列不应混入'

        # panic → 红色加粗并重置收集器
        color, marker, bold = win._esp32_log_style(
            "Guru Meditation Error: Core  0 panic'ed (StoreProhibited).")
        assert color == theme.color('status_err') and bold and not marker
        assert win._esp32_bt == [], 'panic 应开始新现场'
        assert not win.btn_backtrace.isEnabled()

        # 常规 IDF 等级着色(W 琥珀 / D 暗灰;I 走默认 RX 色)
        assert win._esp32_log_style('W (10) wifi: retry')[0] == theme.color('status_warn')
        assert win._esp32_log_style('D (11) http: 200')[0] == theme.color('text_dim')
        assert win._esp32_log_style('I (12) app: ok')[0] == theme.color('rx')
        # 非 IDF 行不受影响
        assert win._esp32_log_style('hello world')[0] == theme.color('rx')

        # 开关关闭 → 不再插装饰行、不收集(旁路在 _append_log 层)
        win.chk_idf.setChecked(False)
        n1 = len(win.message_entries)
        win._append_log('RX', 'rst:0x3 (SW_RESET)')
        assert len(win.message_entries) == n1 + 1, '关闭解析后只应有 RX 行本身'
        win._append_log('RX', 'backtrace:0x400814ad:0x3ffb5e50')
        assert win._esp32_bt == [] and not win.btn_backtrace.isEnabled(), \
            '关闭解析不收集地址、按钮保持置灰'

        # 清空记录联动收集器
        win.chk_idf.setChecked(True)
        win._append_log('RX', 'backtrace:0x400814ad:0x3ffb5e50')
        win.clear_log()
        assert win._esp32_bt == [] and not win.btn_backtrace.isEnabled()
    finally:
        if win is not None:
            win._port_watch_timer.stop()
            win.close()
        _restore_settings(saved)
    print('IDF LOG UI OK')


def groups():
    return [test_esp_idf_parse, test_port_open_hints, test_idf_log_ui]
