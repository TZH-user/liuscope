# -*- coding: utf-8 -*-
"""序列发送自测组(脚本解析纯逻辑 + Runner 定时器语义 + 对话框/自动执行集成)。

约定:本文件提供 groups() 返回测试函数列表;由 qt/selftest.py 的 run_selftest 挂载。
所有测试离屏可跑、无真机、无网络、不起 sys.executable 子进程(冻结冒烟安全)。
"""
from __future__ import annotations

import sys

_TXSEQ_KEYS = ('txseq/script', 'txseq/auto_run', 'txseq/repeat', 'txseq/loop')


def _pump(app, runner, timeout_ms: float = 8000):
    """驱动事件循环直到 runner 结束或超时（delay 步用 1ms 档，测试不真等）。"""
    import time as _t
    deadline = _t.monotonic() + timeout_ms / 1000
    while runner.is_running() and _t.monotonic() < deadline:
        app.processEvents()
    app.processEvents()
    return runner.is_running()


def test_tx_sequence_core():
    """解析/错误定位/描述 + Runner 的重复、循环、写失败中止语义。"""
    from PySide6 import QtWidgets

    from core.tx_sequence import (TxSequenceError, TxSequenceRunner,
                                  build_send_payload, parse_script)

    assert build_send_payload('AT', 'CRLF') == b'AT\r\n'
    assert build_send_payload('AT', '无') == b'AT'

    steps = parse_script("""
# 注释与空行忽略
sendhex 01 03 00 00 00 01
delay 200
send AT+VERSION?
""")
    assert [(s.kind, s.line_no) for s in steps] == \
        [('sendhex', 3), ('delay', 4), ('send', 5)]
    assert steps[0].payload == bytes.fromhex('010300000001')
    assert steps[1].delay_ms == 200
    assert steps[2].payload == b'AT+VERSION?\n'

    for bad, frag in (('foo 1', '未知指令'), ('send', '缺内容'),
                      ('sendhex zz', 'HEX 无效'), ('delay x', '需要毫秒整数'),
                      ('# 只有注释', '没有可执行步骤')):
        try:
            parse_script(bad)
        except TxSequenceError as exc:
            assert frag in str(exc), (bad, str(exc))
        else:
            raise AssertionError(f'{bad!r} 应报错')

    # Runner：顺序 + 重复 + 写失败中止（delay 0 让出事件循环即可）
    sent: list[bytes] = []
    steps = parse_script('send A\ndelay 0\nsend B\n')
    runner = TxSequenceRunner(steps, lambda s: sent.append(s.payload) or True)
    done: list[bool] = []
    runner.finished.connect(lambda ok: done.append(ok))
    # 用 QApplication（不能用 QCoreApplication——同进程后续组要建 QWidget）
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    runner.start(2)
    _pump(app, runner)
    assert sent == [b'A\n', b'B\n', b'A\n', b'B\n'], sent
    assert done == [True]

    sent.clear()
    runner2 = TxSequenceRunner(steps, lambda s: False)   # 写失败
    done2: list[bool] = []
    runner2.finished.connect(lambda ok: done2.append(ok))
    runner2.start(5)
    _pump(app, runner2)
    assert sent == [] and done2 == [False], '首步写失败应立即中止'
    print('TX SEQUENCE CORE OK')


def test_tx_sequence_dialog():
    """对话框集成:开始/停止走主窗发送、脚本与选项记忆、连接后自动执行。"""
    from PySide6 import QtWidgets

    import ui.main_window as mw
    from selftest import (_CONNECT_KEYS, _fake_worker_cls, _restore_settings,
                          _snapshot_settings)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    saved = _snapshot_settings(_CONNECT_KEYS + _TXSEQ_KEYS)
    mw.SerialWorker = _fake_worker_cls()
    win = None
    try:
        win = mw.MainWindow()
        win._port_watch_timer.stop()

        dlg = win._open_txseq() or win._txseq_dialog
        dlg.edit_script.setPlainText('# demo\nsend A\n')
        # 未连接点开始：合法脚本、第一步写失败 → 中止
        dlg.start_clicked()
        assert dlg.btn_start.isEnabled(), '写失败中止后开始按钮应恢复'
        assert '中止' in dlg.lbl_state.text(), dlg.lbl_state.text()

        # 连接后正常跑完（fake worker 写成功）
        win.open_connection()
        dlg.edit_script.setPlainText('send A\ndelay 0\nsend B\n')
        dlg.spin_repeat.setValue(2)
        dlg.start_clicked()
        _pump(app, dlg._runner)
        assert dlg.lbl_state.text() == '完成', dlg.lbl_state.text()
        tx_texts = [e['text'] for e in win.message_entries if e['direction'] == 'TX']
        assert tx_texts.count('A') == 2 and tx_texts.count('B') == 2, tx_texts

        # 语法错误停在本地不发送
        dlg.edit_script.setPlainText('foo 1\n')
        dlg.start_clicked()
        assert '未知指令' in dlg.lbl_state.text()

        # 记忆：脚本/重复/自动执行写设置键
        dlg.edit_script.setPlainText('send X\n')
        dlg.chk_auto.setChecked(True)
        from core.settings import read_setting
        dlg.start_clicked()
        _pump(app, dlg._runner)
        assert read_setting('txseq/script') == 'send X\n'
        assert read_setting('txseq/auto_run') == '1'

        # 连接后自动执行：断开重连应自动开跑
        win._teardown_worker()
        n0 = len(win.message_entries)
        win.open_connection()
        _pump(app, win._txseq_dialog._runner)
        assert win._txseq_dialog.lbl_state.text() == '完成'
        assert any(e['direction'] == 'TX' for e in win.message_entries[n0:]), \
            '重连后应自动执行序列'
    finally:
        if win is not None:
            win._port_watch_timer.stop()
            if win._txseq_dialog is not None:
                win._txseq_dialog.close()
            win.close()
        _restore_settings(saved)
    print('TX SEQUENCE DIALOG OK')


def groups():
    return [test_tx_sequence_core, test_tx_sequence_dialog]
