# -*- coding: utf-8 -*-
"""产出浅色主题截图：docs/screenshots/main_light.png / settings_light.png（离屏可跑）。

构图与既有深色截图 main_dark.png / settings_dark.png 对齐：
- 终端页 2000x1000 整窗：FakeWorker 替身让状态「已连接 COM9 @ 115200」，
  clear_log 后铺 RX/TX/SYS 日志行（先清空：离屏下往建窗已有内容的文档追加
  的行 grab 画不出来，与审计/自测同款规避）；
- 设置页直接 grab settings_page 控件（无菜单/页签 chrome，同 settings_dark）。

用法：python _verify/make_light_screenshots.py
自动保存日志全程取消勾选（AutoLogWriter 懒创建，不往 logs/ 落任何文件）；
结束时还原被改动的设置键并把主题切回深色。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QT_DIR = os.path.join(ROOT, 'qt')
DOCS_DIR = os.path.join(ROOT, 'docs', 'screenshots')
if QT_DIR not in sys.path:
    sys.path.insert(0, QT_DIR)

from PySide6 import QtCore, QtWidgets  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402

from core.settings import read_setting, write_setting  # noqa: E402
from ui import theme  # noqa: E402

# 收尾还原的设置键（连接区 closeEvent 会回写，主题/CLI 路径本脚本会动）
_SETTING_KEYS = ('ui/theme', 'stm32_cli_path', 'update/last_check',
                 'connect/port', 'connect/baud', 'connect/auto_link',
                 'connect/rx_format', 'connect/tx_format', 'connect/newline')


class FakeWorker(QtCore.QObject):
    """替身串口线程类（信号面同 SerialWorker；start() 同步发 opened）。"""

    got_bytes = QtCore.Signal(bytes)
    opened = QtCore.Signal()
    open_failed = QtCore.Signal(str)
    lost = QtCore.Signal(str)

    def __init__(self, device, baud, parent=None):
        super().__init__(parent)
        self.device, self.baud = device, baud

    def start(self):
        self.opened.emit()

    def stop(self):
        pass

    def write_bytes(self, data):
        return True


def build_log_lines(win) -> None:
    """clear_log 后铺与深色截图同构的 RX/TX/SYS 样例行。"""
    win.clear_log()
    win._append_log('SYS', 'AI 控制通道已开启 tcp://127.0.0.1:9101 （JSON 行协议, '
                           'ping/help/status/send/read_lines[+follow]/wave_snapshot/'
                           'spectrum_snapshot/xy_snapshot/modbus_recent/stm32_status/'
                           'settings_*，命令表发 {"cmd":"help"}，详见 AI_INTERFACE.md）')
    for i, temp in enumerate((23.50, 23.80, 24.10, 24.40, 24.70, 25.00,
                              25.30, 25.60, 25.90, 26.20, 26.50, 26.80)):
        win._append_log('RX', f'temp={temp:.2f},hum={55 + i % 8},vbat=3.87')
    win._append_log('RX', 'boot v1.2.0')
    win._append_log('SYS', '[STM32] 探测完成: 1 个 (FW V2J46S7)')
    win._append_log('TX', 'AT+VERSION?')
    win._append_log('RX', 'LiuScope V6 v1.2.0')
    win._append_log('TX', 'AT+RESET')
    win._append_log('RX', 'ready')
    win._append_log('RX', '[Modbus] 01 03 08 01 90 00 50 00 C8 00 64 84 0B')
    win._append_log('SYS', '[STM32] 实际已连接: STM32H743 (Flash 2 MBytes / 内核 Cortex-M7 / 3.27V)')


def main() -> int:
    import ui.main_window as mw

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\msyh.ttc')

    saved = {k: read_setting(k, '') for k in _SETTING_KEYS}
    write_setting('update/last_check', '9999999999')   # 屏蔽启动 3s 自动更新检查
    os.makedirs(DOCS_DIR, exist_ok=True)

    old_worker_cls, old_scan = mw.SerialWorker, mw.scan_port_infos
    mw.SerialWorker = FakeWorker
    mw.scan_port_infos = lambda: [('COM9', 'USB-SERIAL CH340 (COM9)')]
    try:
        write_setting(theme.THEME_SETTING_KEY, theme.LIGHT_KEY)  # 建窗会按 ini 恢复
        theme.set_theme(theme.LIGHT_KEY)
        assert theme.current_theme() == theme.LIGHT_KEY
        win = mw.MainWindow()
        win.show()
        win._port_watch_timer.stop()
        win.chk_auto_save.setChecked(False)   # 不往 logs/ 落自动日志
        win.chk_auto_link.setChecked(False)

        # ---- 终端页整窗 2000x1000（同 main_dark.png）----
        win.resize(2000, 1000)
        win.combo_baud.setCurrentText('115200')
        win.combo_rx_format.setCurrentText('ASCII')
        win.btn_connect.click()               # FakeWorker 同步 opened → 已连接
        assert win.lbl_status.text().startswith('已连接'), win.lbl_status.text()
        build_log_lines(win)
        app.processEvents()
        out_main = os.path.join(DOCS_DIR, 'main_light.png')
        win.grab().save(out_main)
        print('已保存', out_main)

        # ---- 设置页（同 settings_dark.png：直接抓页签内容控件）----
        win.resize(2560, 1400)
        win.tabs.setCurrentIndex(5)           # 设置
        app.processEvents()
        out_settings = os.path.join(DOCS_DIR, 'settings_light.png')
        win.settings_page.grab().save(out_settings)
        print('已保存', out_settings)

        win._port_watch_timer.stop()
        win.close()
    finally:
        mw.SerialWorker, mw.scan_port_infos = old_worker_cls, old_scan
        for key, value in saved.items():
            write_setting(key, value)         # 空串 = read_setting 视同缺省
        theme.set_theme(theme.DARK_KEY)
    return 0


if __name__ == '__main__':
    sys.exit(main())
