# -*- coding: utf-8 -*-
"""证据图：状态行长报错省略（不撑宽窗口）。离屏，QT_QPA_FONTDIR 指向系统字体防豆腐块。"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
sys.path.insert(0, 'qt')

from PySide6 import QtWidgets  # noqa: E402

app = QtWidgets.QApplication(sys.argv)
import ui.main_window as mw  # noqa: E402

win = mw.MainWindow()
win.show()
win.resize(900, 620)
win._port_watch_timer.stop()
long_err = ('打开失败: Cannot configure port, something went wrong. Original message: '
            'PermissionError(13, \'连到系统上的设备没有发挥作用。\', None, 31)')
win.set_status(long_err, 'status_err')
for _ in range(30):
    app.processEvents()
    time.sleep(0.02) if False else None
app.processEvents()

pix = win.grab()
top = pix.copy(0, 0, pix.width(), 120)
top.save('_verify/evidence/status_elide_row.png')
print('tooltip 全文 =', win.lbl_status.toolTip() == long_err)
print('绘制文本 =', repr(win.lbl_status.text() if hasattr(win.lbl_status, 'text') else ''))
import PySide6.QtWidgets as QW
print('QSS 绘制文本 =', repr(QW.QLabel.text(win.lbl_status)))
win.close()
print('SAVED _verify/evidence/status_elide_row.png')
