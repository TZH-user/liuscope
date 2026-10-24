# -*- coding: utf-8 -*-
"""主窗体：M1 闭环 = 连接/断开 + RX/TX 日志（着色）+ 行缓冲/空闲落显/积压常显 + 发送。"""
from __future__ import annotations

import html
import time

from PySide6 import QtCore, QtWidgets

try:
    from serial.tools import list_ports
except ImportError:
    list_ports = None

from core import APP_NAME, APP_VERSION, REPO_URL
from core.rx_buffer import RxBuffer
from core.serial_worker import SerialWorker
from ui.theme import RX_COLOR, SYS_COLOR, TIME_COLOR, TX_COLOR
from ui.wave_window import WaveWindow


def _stamp() -> str:
    return time.strftime('%H:%M:%S')


class MainWindow(QtWidgets.QMainWindow):
    FLUSH_INTERVAL_MS = 200

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'{APP_NAME} v{APP_VERSION}')
        self.resize(980, 620)

        self.buffer = RxBuffer()
        self.worker: SerialWorker | None = None
        self._max_log_blocks = 20000

        # ---- 顶部连接行 ----
        self.combo_port = QtWidgets.QComboBox()
        self.combo_baud = QtWidgets.QComboBox()
        self.combo_baud.setEditable(True)
        self.combo_baud.addItems(['1500000', '921600', '460800', '115200'])
        self.btn_connect = QtWidgets.QPushButton('连接')
        self.btn_connect.clicked.connect(self.toggle_connect)
        self.btn_refresh = QtWidgets.QPushButton('刷新串口')
        self.btn_refresh.clicked.connect(self.refresh_ports)
        self.lbl_status = QtWidgets.QLabel('未连接')
        self.lbl_status.setObjectName('status_err')

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel('串口'))
        top.addWidget(self.combo_port, 2)
        top.addWidget(self.btn_refresh)
        top.addWidget(QtWidgets.QLabel('波特率'))
        top.addWidget(self.combo_baud, 1)
        top.addWidget(self.btn_connect)
        top.addWidget(self.lbl_status, 2)

        # ---- 日志区 ----
        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)

        # ---- 底部发送行 ----
        self.edit_send = QtWidgets.QLineEdit()
        self.edit_send.setPlaceholderText('输入要发送的内容，Enter 发送')
        self.edit_send.returnPressed.connect(self.send)
        self.btn_send = QtWidgets.QPushButton('发送')
        self.btn_send.clicked.connect(self.send)
        self.lbl_backlog = QtWidgets.QLabel('积压 0 B')
        self.lbl_backlog.setObjectName('backlog')

        bottom = QtWidgets.QHBoxLayout()
        bottom.addWidget(self.edit_send, 3)
        bottom.addWidget(self.btn_send)
        bottom.addWidget(self.lbl_backlog, 1)

        terminal = QtWidgets.QWidget()
        terminal_layout = QtWidgets.QVBoxLayout(terminal)
        terminal_layout.setContentsMargins(0, 0, 0, 0)
        terminal_layout.addLayout(top)
        terminal_layout.addWidget(self.log, 1)
        terminal_layout.addLayout(bottom)

        # ---- 页签：终端 / 波形（后续插件页也挂这里） ----
        self.wave = WaveWindow()
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(terminal, '终端')
        self.tabs.addTab(self.wave, '波形')
        self.tabs.setObjectName('main_tabs')
        self.setCentralWidget(self.tabs)

        self.flush_timer = QtCore.QTimer(self)
        self.flush_timer.timeout.connect(self.on_flush_tick)
        self.flush_timer.start(self.FLUSH_INTERVAL_MS)
        self.refresh_ports()

    # ---------- 串口 ----------

    def refresh_ports(self):
        self.combo_port.clear()
        for p in (list_ports.comports() if list_ports else []):
            self.combo_port.addItem(f'{p.device} — {p.description}')

    def toggle_connect(self):
        if self.worker:
            self._teardown_worker()
            self.set_status('未连接', 'status_err')
            return
        device = self.combo_port.currentText().split(' — ')[0].strip()
        if not device:
            self.set_status('没有可用串口', 'status_err')
            return
        try:
            baud = int(self.combo_baud.currentText())
        except ValueError:
            self.set_status('波特率必须是数字', 'status_err')
            return
        self.worker = SerialWorker(device, baud, self)
        self.worker.got_bytes.connect(self.ingest_bytes)
        self.worker.open_failed.connect(lambda msg: (self._teardown_worker(),
                                                     self.set_status(f'打开失败: {msg}', 'status_err')))
        self.worker.lost.connect(lambda msg: (self._teardown_worker(),
                                              self.set_status(f'连接丢失: {msg}', 'status_err')))
        self.worker.opened.connect(lambda: self.set_status(f'已连接 {device} @ {baud}', 'status_ok'))
        self.worker.start()

    def _teardown_worker(self):
        if not self.worker:
            return
        self.worker.stop()
        self.worker.deleteLater()
        self.worker = None

    def set_status(self, text: str, obj_name: str):
        self.lbl_status.setText(text)
        self.lbl_status.setObjectName(obj_name)
        self.lbl_status.style().unpolish(self.lbl_status)
        self.lbl_status.style().polish(self.lbl_status)

    def closeEvent(self, event):  # noqa: N802 (Qt 命名)
        self.wave.stop_timers()
        self._teardown_worker()
        super().closeEvent(event)

    # ---------- RX 管线 ----------

    def ingest_bytes(self, data: bytes):
        now = time.monotonic()
        for line in self.buffer.feed(data, now):
            self._append_log('RX', line)
        self.wave.consume_bytes(data)
        self._update_backlog()

    def on_flush_tick(self):
        now = time.monotonic()
        tail = self.buffer.poll_idle_flush(now)
        if tail is not None:
            self._append_log('RX', f'{tail} (空闲落显)')
        self._update_backlog()

    def _update_backlog(self):
        self.lbl_backlog.setText(f'积压 {self.buffer.backlog_bytes} B')

    # ---------- 日志 / 发送 ----------

    def _append_log(self, direction: str, text: str):
        color = {'RX': RX_COLOR, 'TX': TX_COLOR}.get(direction, SYS_COLOR)
        safe = html.escape(text)
        self.log.append(
            f'<span style="color:{TIME_COLOR}">[{_stamp()}]</span> '
            f'<b style="color:{color}">{direction}</b> '
            f'<span style="color:{color}">{safe}</span>'
        )
        if self.log.document().blockCount() > self._max_log_blocks:
            cursor = self.log.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(cursor.MoveOperation.NextBlock,
                                cursor.MoveMode.KeepAnchor, self._max_log_blocks // 10)
            cursor.removeSelectedText()

    def send(self):
        text = self.edit_send.text().strip()
        if not text:
            return
        sent = bool(self.worker and self.worker.write_line(text))
        self._append_log('TX', text + ('' if sent else '  (未连接，仅入日志)'))
        self.edit_send.clear()
