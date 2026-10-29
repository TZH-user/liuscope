# -*- coding: utf-8 -*-
"""真机端到端复验：COM19 console 登录 → 触发 motd 猫画 → 离屏主窗渲染截图。

前置：鲁班猫 COM19（ttyFIQ0 @1500000）空闲；板卡已开机。若串口被其他上位机
（如用户的 GUI 实例/VOFA+）占用，SerialWorker 异步报 open_failed → 本脚本
打印跳过说明并以 0 退出（离线证据 ansi_motd_art_shot.py 已覆盖渲染对齐）。

用法: QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:/Windows/Fonts python ansi_motd_live.py
产物: _verify/evidence/ansi_motd_art_live.png
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'qt'))

PORT = 'COM19'
BAUD = '1500000'


def main() -> int:
    from PySide6 import QtCore, QtWidgets

    import ui.main_window as mw

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = mw.MainWindow()
    win._port_watch_timer.stop()
    win.resize(980, 720)
    win.show()
    app.processEvents()

    win.combo_baud.setCurrentText(BAUD)
    idx = win.combo_port.findText(PORT, QtCore.Qt.MatchStartsWith)
    assert idx >= 0, f'端口下拉里没有 {PORT}（先插板/刷新串口）'
    win.combo_port.setCurrentIndex(idx)
    win.open_connection()
    assert win.worker is not None, 'SerialWorker 未创建'

    result: list[str] = []
    win.worker.opened.connect(lambda: result.append('opened'))
    win.worker.open_failed.connect(lambda msg: result.append(f'failed: {msg}'))
    deadline = time.monotonic() + 5
    while not result and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
    if not result or result[0].startswith('failed'):
        print(f'SKIP: {PORT} 打不开（{result or "超时"}）——多半被上位机占用，离线证据已覆盖')
        win._teardown_worker()
        win.close()
        return 0
    print(f'{PORT} opened')

    def send(text: str):
        assert win.worker.write_bytes(text.encode('ascii')), 'write 失败'

    def rx_text() -> str:
        return '\n'.join(e['text'] for e in win.message_entries)

    def wait_for(needle: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.05)
            if needle in rx_text():
                return True
        return False

    send('\r\n')
    time.sleep(1.2)
    app.processEvents()
    tail = rx_text()[-500:]
    if 'login:' in tail:
        print('console 在登录提示符 → 登录')
        send('cat\r')
        wait_for('assword', 5)
        send('cat123456\r')
        time.sleep(1.5)
        app.processEvents()
    else:
        print('console 已在 shell（沿用已登录会话）')

    n0 = len(win.message_entries)
    send('sh /etc/update-motd.d/00-header\r')
    got = wait_for('Welcome to Debian', 8)
    app.processEvents()
    new_entries = win.message_entries[n0:]
    print(f'motd 触发 {"OK" if got else "超时"}，新增 {len(new_entries)} 条')

    # 断言：猫画行逐字节保真（前导 11 空格 + 行内 24 空格）
    art = ' ' * 11 + 'OOOO' + ' ' * 24 + 'OOOO'
    assert any(e['text'].endswith(art) for e in new_entries), \
        '真机猫画行未逐字节落显：' + repr([e['text'][:60] for e in new_entries[:10]])

    out = os.path.join(os.path.dirname(__file__), 'evidence', 'ansi_motd_art_live.png')
    win.grab().save(out)
    print(f'ART LIVE OK -> {out}')

    win._teardown_worker()
    win.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
