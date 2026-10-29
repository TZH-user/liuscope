# -*- coding: utf-8 -*-
"""Stage C(P2) 多会话窗口 + 广播发送证据。

离屏开两个会话窗口（fake 环回 worker），窗口 2 勾选广播发送，窗口 1 收到
（广播）TX 条目；双窗截图拼合落盘。
用法: python _verify/display_stage_c_shot.py
产物: _verify/evidence/display_stage_c_multi.png
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'qt'))


def _make_worker_cls():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'qt'))
    from selftest import _fake_worker_cls
    return _fake_worker_cls()


def main() -> int:
    from PySide6 import QtGui, QtWidgets

    import ui.main_window as mw

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    mw.SerialWorker = _make_worker_cls()

    win1 = mw.MainWindow()
    win1._port_watch_timer.stop()
    win1.show()
    win1.open_connection()

    win2 = win1._open_new_session_window()
    win2._port_watch_timer.stop()
    win2.open_connection()

    for w in (win1, win2):
        w.combo_tx_format.setCurrentText('ASCII')
        w.combo_newline.setCurrentText('LF')

    win2.btn_broadcast.setChecked(True)
    win2.edit_send.setText('AT+VERSION?')
    win2.send()
    app.processEvents()

    assert any('（广播）' in e['text'] for e in win1.message_entries
               if e['direction'] == 'TX'), '窗口 1 应收到广播条目'
    assert any('已广播到 1' in e['text'] for e in win2.message_entries
               if e['direction'] == 'SYS')

    # 双窗横向拼图
    p1, p2 = win1.grab(), win2.grab()
    canvas = QtGui.QPixmap(p1.width() + p2.width() + 8,
                               max(p1.height(), p2.height()))
    canvas.fill()
    painter = QtGui.QPainter(canvas)
    painter.drawPixmap(0, 0, p1)
    painter.drawPixmap(p1.width() + 8, 0, p2)
    painter.end()

    out = os.path.join(os.path.dirname(__file__), 'evidence', 'display_stage_c_multi.png')
    canvas.save(out)
    print('STAGE C EVIDENCE ->', out)

    win2.btn_broadcast.setChecked(False)   # 不污染 ini（广播勾选即时写设置）
    for w in (win1, win2):
        w._port_watch_timer.stop()
        w.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
