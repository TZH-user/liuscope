# -*- coding: utf-8 -*-
"""serialscope Qt 前端验证原型（qt-frontend-spike 分支）。

目的：验证 PySide6 技术栈在本机跑通（串口枚举/收发/行缓冲/深色 QSS/离屏自测），
不是功能完整的移植。对应 PowerShell 版主线的最小闭环：
端口枚举 → 连接 → 行缓冲 + 空闲落显 + 积压显示 → 发送回显。

依赖：python -m pip install PySide6-Essentials pyserial
自测（无显示器环境亦可）：python serialscope_qt.py --selftest
运行：python serialscope_qt.py
"""
import sys
import time

from PySide6 import QtCore, QtWidgets

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

DARK_QSS = """
QMainWindow, QWidget { background: #1E2430; color: #E1E6F0; font-family: 'Microsoft YaHei UI'; font-size: 9pt; }
QComboBox, QLineEdit, QPlainTextEdit { background: #262D3A; color: #E1E6F0; border: 1px solid #3E546A; border-radius: 4px; padding: 3px 6px; }
QComboBox QAbstractItemView { background: #262D3A; color: #E1E6F0; selection-background-color: #0E639C; }
QPushButton { background: #0E639C; color: white; border: none; border-radius: 4px; padding: 6px 14px; }
QPushButton:hover { background: #1177BB; }
QPushButton:disabled { background: #3E546A; }
QLabel#backlog { color: #969EAC; }
QLabel#status_ok { color: #4EC9B0; }
QLabel#status_err { color: #F14C4C; }
"""

IDLE_FLUSH_MS = 400  # 空闲落显阈值（与 PS 版一致）


class SerialReader(QtCore.QThread):
    got_bytes = QtCore.Signal(bytes)

    def __init__(self, port):
        super().__init__()
        self.port = port
        self._run = True

    def run(self):
        while self._run:
            try:
                n = self.port.in_waiting
                if n:
                    self.got_bytes.emit(self.port.read(n))
                else:
                    self.msleep(10)
            except Exception:
                break

    def stop(self):
        self._run = False
        self.wait(1500)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('serialscope-qt spike')
        self.resize(900, 560)
        self.port = None
        self.reader = None
        self.line_buffer = ''
        self.backlog_bytes = 0
        self.last_byte_at = 0.0

        top = QtWidgets.QHBoxLayout()
        self.combo_port = QtWidgets.QComboBox()
        for p in (list_ports.comports() if list_ports else []):
            self.combo_port.addItem(f'{p.device} — {p.description}')
        self.combo_baud = QtWidgets.QComboBox()
        self.combo_baud.setEditable(True)
        self.combo_baud.addItems(['1500000', '115200', '921600'])
        self.btn_connect = QtWidgets.QPushButton('连接')
        self.btn_connect.clicked.connect(self.toggle_connect)
        self.lbl_status = QtWidgets.QLabel('未连接')
        self.lbl_status.setObjectName('status_err')
        top.addWidget(self.combo_port, 2)
        top.addWidget(self.combo_baud, 1)
        top.addWidget(self.btn_connect)
        top.addWidget(self.lbl_status, 2)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)

        bottom = QtWidgets.QHBoxLayout()
        self.edit_send = QtWidgets.QLineEdit()
        self.edit_send.returnPressed.connect(self.send)
        self.btn_send = QtWidgets.QPushButton('发送')
        self.btn_send.clicked.connect(self.send)
        self.lbl_backlog = QtWidgets.QLabel('积压 0 B')
        self.lbl_backlog.setObjectName('backlog')
        bottom.addWidget(self.edit_send, 3)
        bottom.addWidget(self.btn_send)
        bottom.addWidget(self.lbl_backlog, 1)

        central = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(central)
        v.addLayout(top)
        v.addWidget(self.log, 1)
        v.addLayout(bottom)
        self.setCentralWidget(central)

        self.flush_timer = QtCore.QTimer(self)
        self.flush_timer.timeout.connect(self.on_flush_tick)
        self.flush_timer.start(200)

    # ---------- RX 管线（行缓冲 + 空闲落显 + 积压，与 PS 版同构） ----------

    def ingest_bytes(self, data: bytes):
        self.last_byte_at = time.monotonic()
        self.line_buffer += data.decode('utf-8', errors='replace')
        *done, self.line_buffer = self.line_buffer.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        for line in done:
            self.log.appendPlainText(f'[RX] {line}')

    def on_flush_tick(self):
        # 先空闲落显再统计：否则落显当拍标签仍显示旧积压，滞后一拍
        if self.line_buffer and (time.monotonic() - self.last_byte_at) * 1000 >= IDLE_FLUSH_MS:
            self.log.appendPlainText(f'[RX] {self.line_buffer}  (空闲落显)')
            self.line_buffer = ''
        self.backlog_bytes = len(self.line_buffer.encode('utf-8', errors='replace'))
        self.lbl_backlog.setText(f'积压 {self.backlog_bytes} B')

    # ---------- 连接 / 发送 ----------

    def toggle_connect(self):
        if self.port:
            self.reader.stop()
            self.port.close()
            self.port = None
            self.btn_connect.setText('连接')
            self.lbl_status.setText('未连接')
            self.lbl_status.setObjectName('status_err')
            return
        if serial is None:
            self.lbl_status.setText('未装 pyserial')
            return
        device = self.combo_port.currentText().split(' — ')[0]
        if not device:
            return
        try:
            self.port = serial.Serial(device, int(self.combo_baud.currentText()), timeout=0)
        except Exception as exc:
            self.lbl_status.setText(f'打开失败: {exc}')
            return
        self.reader = SerialReader(self.port)
        self.reader.got_bytes.connect(self.ingest_bytes)
        self.reader.start()
        self.btn_connect.setText('断开')
        self.lbl_status.setText(f'已连接 {device}')
        self.lbl_status.setObjectName('status_ok')
        self.lbl_status.style().unpolish(self.lbl_status)
        self.lbl_status.style().polish(self.lbl_status)

    def send(self):
        text = self.edit_send.text()
        if not text:
            return
        if self.port:
            self.port.write((text + '\n').encode('utf-8'))
        self.log.appendPlainText(f'[TX] {text}')
        self.edit_send.clear()


def run_selftest() -> int:
    """离屏自测：构造窗口 → 灌三条帧（含无换行残行）→ 验证行缓冲/空闲落显/积压计数。"""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    win.ingest_bytes(b'boot v1.0\n')
    win.ingest_bytes(b'temp=41.5\nload=23\n')
    win.ingest_bytes(b'no_newline_tail')  # 无换行：只进缓冲
    assert '[RX] boot v1.0' in win.log.toPlainText()
    assert '[RX] temp=41.5' in win.log.toPlainText()
    assert 'no_newline_tail' not in win.log.toPlainText(), '残行不该立即显示'
    assert win.line_buffer == 'no_newline_tail'
    win.on_flush_tick()  # 尚未过空闲阈值：只更新积压
    assert win.backlog_bytes == len(b'no_newline_tail')
    win.last_byte_at = 0.0  # 模拟静默超阈值
    win.on_flush_tick()
    assert 'no_newline_tail  (空闲落显)' in win.log.toPlainText(), '空闲落显未触发'
    assert win.line_buffer == '' and win.backlog_bytes == 0
    print('QT_SPIKE_SELFTEST_OK')
    return 0


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(run_selftest())
    sys.exit(main())
