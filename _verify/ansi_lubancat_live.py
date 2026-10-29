# -*- coding: utf-8 -*-
"""鲁班猫真板 ANSI 验证（一次性）：LiuScope 离屏开 COM19(1500000) 收板子调试串口流。

前置：板端已用 SSH 向 /dev/ttyFIQ0 写 ANSI 测试行（写 /dev/console 无效——用户态
输出被路由到 tty1 帧缓冲，只有内核 printk 落串口，2026-10-29 实测）。
产物：_verify/evidence/ansi_lubancat_live.png + 控制台断言输出。
用法：QT_QPA_FONTDIR=C:/Windows/Fonts QT_QPA_PLATFORM=offscreen python _verify/ansi_lubancat_live.py
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'qt'))

from PySide6 import QtWidgets  # noqa: E402

import ui.main_window as mw  # noqa: E402

PORT = os.environ.get('LIVE_PORT', 'COM19')
BAUD = os.environ.get('LIVE_BAUD', '1500000')
WAIT_S = float(os.environ.get('LIVE_WAIT', '25'))


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = mw.MainWindow()
    win._port_watch_timer.stop()
    win.chk_auto_save.setChecked(False)
    win.clear_log()

    idx = win.combo_port.findText(PORT, __import__('PySide6').QtCore.Qt.MatchStartsWith)
    assert idx >= 0, f'端口下拉里找不到 {PORT}: {[win.combo_port.itemText(i) for i in range(win.combo_port.count())]}'
    win.combo_port.setCurrentIndex(idx)
    win.combo_baud.setCurrentText(BAUD)
    win.open_connection()
    assert win.worker is not None, f'{PORT} 连接失败'
    print(f'connected {PORT} @ {BAUD}')

    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
        rx = [e for e in win.message_entries if e['direction'] == 'RX']
        if len(rx) >= 3:
            break
    app.processEvents()

    rx = [e for e in win.message_entries if e['direction'] == 'RX']
    print(f'captured {len(rx)} RX entries:')
    ansi_lines = []
    for e in rx[-40:]:
        print(f'  RX {e["text"]!r}')
        if '\x1b[' in e['text']:
            ansi_lines.append(e['text'])

    assert ansi_lines, '未收到含 ANSI 转义的行'
    print(f'ANSI lines: {len(ansi_lines)}')

    # 转义码不可见于显示文档（渲染开启时）
    assert '\x1b' not in win.log.toPlainText(), '渲染开启时转义码不应出现在文档'

    out = os.path.join(os.path.dirname(__file__), 'evidence', 'ansi_lubancat_live.png')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    assert win.log.grab().save(out), '截图失败'
    print(f'saved {out}')

    win._teardown_worker()
    win.close()
    app.processEvents()
    return 0


if __name__ == '__main__':
    sys.exit(main())
