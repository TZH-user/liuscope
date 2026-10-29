# -*- coding: utf-8 -*-
"""鲁班猫启动流采集（配合远程 reboot）：LiuScope 离屏连 COM19@1500000 录完整启动。

时序：先启动本脚本（连串口等流）→ SSH 触发 sudo reboot → 脚本录满 LIVE_WAIT 秒后
断言+截图+落盘 RX 全文。UART 无回补，必须先连后重启。
用法：QT_QPA_FONTDIR=C:/Windows/Fonts QT_QPA_PLATFORM=offscreen python _verify/lubancat_boot_capture.py
产物：_verify/evidence/lubancat_boot_live.png + lubancat_boot_rx.txt
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'qt'))

from PySide6 import QtCore, QtWidgets  # noqa: E402

import ui.main_window as mw  # noqa: E402

PORT = os.environ.get('LIVE_PORT', 'COM19')
BAUD = os.environ.get('LIVE_BAUD', '1500000')
WAIT_S = float(os.environ.get('LIVE_WAIT', '100'))


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = mw.MainWindow()
    win._port_watch_timer.stop()
    win.chk_auto_save.setChecked(False)
    win.clear_log()

    idx = win.combo_port.findText(PORT, QtCore.Qt.MatchStartsWith)
    assert idx >= 0, f'端口下拉里找不到 {PORT}'
    win.combo_port.setCurrentIndex(idx)
    win.combo_baud.setCurrentText(BAUD)
    win.open_connection()
    assert win.worker is not None, 'open_connection 未创建 worker'
    result = []
    win.worker.opened.connect(lambda: result.append('opened'))
    win.worker.open_failed.connect(lambda msg: result.append(f'failed: {msg}'))
    end = time.monotonic() + 5
    while time.monotonic() < end and not result:
        app.processEvents()
        time.sleep(0.05)
    assert result and result[0] == 'opened', \
        f'串口未真正打开: {result or "5s 超时"}——COM 口多半被占用（VOFA+ 等），先释放'
    print(f'connected {PORT} @ {BAUD}, capturing {WAIT_S:.0f}s ...', flush=True)

    end = time.monotonic() + WAIT_S
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()

    rx = [e for e in win.message_entries if e['direction'] == 'RX']
    ansi_lines = [e['text'] for e in rx if '\x1b[' in e['text']]
    print(f'captured {len(rx)} RX lines, {len(ansi_lines)} with ANSI SGR')

    out_dir = os.path.join(os.path.dirname(__file__), 'evidence')
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'lubancat_boot_rx.txt'), 'w',
              encoding='utf-8') as f:
        f.write('\n'.join(e['text'] for e in rx))
    assert win.log.grab().save(os.path.join(out_dir, 'lubancat_boot_live.png')), '截图失败'
    print(f'saved evidence/lubancat_boot_live.png + lubancat_boot_rx.txt '
          f'({len(rx)} lines, {len(ansi_lines)} ANSI)')

    for e in rx[:8]:
        print('  HEAD', repr(e['text'][:110]))
    print('  ...')
    for e in rx[-12:]:
        print('  TAIL', repr(e['text'][:110]))

    win._teardown_worker()
    win.close()
    app.processEvents()
    return 0


if __name__ == '__main__':
    sys.exit(main())
