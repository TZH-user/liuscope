# -*- coding: utf-8 -*-
"""Stage A(P0) 显示增强证据：关键词告警 + 字号缩放 + 信号线标签。

离屏主窗喂三类 RX 行（错误级/关注级/普通），截图验证 alert 整行红、watch
整行琥珀、普通行默认色；另把日志放大到 14pt 展示缩放效果。
用法: python _verify/display_stage_a_shot.py
产物: _verify/evidence/display_stage_a.png
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'qt'))


def main() -> int:
    from PySide6 import QtWidgets

    import ui.main_window as mw
    from core.settings import write_setting

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    write_setting('log/kw_alert', 'ERROR, PANIC, HardFault')
    write_setting('log/kw_watch', 'retry, timeout')
    win = mw.MainWindow()
    win._port_watch_timer.stop()
    win.resize(980, 640)
    win.show()
    app.processEvents()

    win._append_log('RX', 'boot ok, loading app v1.0')
    win._append_log('RX', '\x1b[33m[warn] sensor read timeout\x1b[0m')
    win._append_log('RX', 'send retry #3 to 192.168.2.100')
    win._append_log('RX', 'I (2812) wifi: connected, rssi=-52')
    win._append_log('RX', '\x1b[0;32mI (2900) app: task started\x1b[0m')
    win._append_log('RX', 'E (2910) i2c: ERROR nack on addr 0x3C')
    win._append_log('SYS', 'Guru Meditation Error: Core 0 panic\'ed (StoreProhibited)')
    app.processEvents()

    # 字号放大到 14pt（模拟 Ctrl+滚轮连按 5 次；先复位清掉残留记忆值）
    win.log.zoom(0)
    for _ in range(5):
        win.log.zoom(1)
    app.processEvents()
    assert win.log.font_pt() == 14

    out = os.path.join(os.path.dirname(__file__), 'evidence', 'display_stage_a.png')
    win.grab().save(out)
    print('STAGE A EVIDENCE ->', out)

    win._port_watch_timer.stop()
    win.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
