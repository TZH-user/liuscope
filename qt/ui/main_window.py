# -*- coding: utf-8 -*-
"""主窗体：连接/断开 + RX/TX 日志（ASCII/HEX 双格式）+ 行缓冲/空闲落显/积压常显
+ 发送（HEX/换行模式/历史回翻）+ 文件发送（分块定时）+ 查找条 + 波形页签。

M3 复刻主线功能口径：
- HEX 发送解析同主线（0x 前缀可带、空白/逗号/分号/冒号/连字符分隔、非法 token 报错）
- 换行模式 无/LF/CR/CRLF，ASCII 与 HEX 发送都追加
- 文件发送 4 KiB/20ms 分块（≈200 KiB/s 上限），发送中再点 = 停止
"""
from __future__ import annotations

import datetime
import html
import os
import time

from PySide6 import QtCore, QtGui, QtWidgets

from core import APP_NAME, APP_VERSION, REPO_URL
from core.auto_log import AutoLogWriter, build_log_export_text, format_entry_block, get_log_dir
from core.control_channel import ControlAPI, ControlChannel, handle_control_request
from core.fault_decode import test_fault_dump_line
from core.hexutil import bytes_to_hex_text, try_parse_hex_text
from core.ports import (port_display_text, port_name_from_display,
                        pick_auto_connect_port, scan_port_infos)
from core.rx_buffer import RxBuffer
from core.serial_worker import SerialWorker
from core.session_record import SessionRecorder, load_replay_chunks
from core.settings import read_setting, write_setting
from core.update_check import RELEASES_PAGE, check_app_update
from ui.find_bar import FindBar
from ui.history_edit import HistoryLineEdit
from ui.log_hover import LogHoverTip
from ui.modbus_panel import ModbusPanel
from ui.spectrum_panel import SpectrumPanel
from ui.theme import RX_COLOR, SYS_COLOR, TIME_COLOR, TX_COLOR
from ui.wave_window import WaveWindow

NEWLINE_TEXT = {'无': '', 'LF': '\n', 'CR': '\r', 'CRLF': '\r\n'}
NEWLINE_BYTES = {'无': b'', 'LF': b'\n', 'CR': b'\r', 'CRLF': b'\r\n'}

FILE_CHUNK = 4096   # 4 KiB/20ms ≈ 上限 200 KiB/s（对齐主线）
FILE_INTERVAL_MS = 20


MAX_LOG_ENTRIES = 20000
MAX_LOG_TRIM = 4000


def _stamp() -> str:
    # 主线同款全时间戳（含毫秒）：日志导出 / 自动日志 / Ctrl+F 时间定位都靠它
    now = datetime.datetime.now()
    return f'{now:%Y-%m-%d %H:%M:%S}.{now.microsecond // 1000:03d}'


class _UpdateCheckRunnable(QtCore.QRunnable):
    """网络查询放线程池，UI 不卡（Qt 版比主线 PS 定时器方案干净的地方）。"""

    class Signals(QtCore.QObject):
        done = QtCore.Signal(dict)

    def __init__(self, check_fn, version: str):
        super().__init__()
        self._check_fn = check_fn
        self._version = version
        self.signals = self.Signals()

    def run(self):
        try:
            result = self._check_fn(self._version)
        except Exception as exc:  # 防御：check_app_update 理论上不抛
            result = {'ok': False, 'has_update': False, 'note': str(exc)}
        self.signals.done.emit(result)


class MainWindow(QtWidgets.QMainWindow):
    FLUSH_INTERVAL_MS = 200

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'{APP_NAME} v{APP_VERSION}')
        self.resize(980, 620)

        self.buffer = RxBuffer()
        self.worker: SerialWorker | None = None
        self._max_log_blocks = 20000
        self._file_send: dict | None = None
        self._modbus_rx_acc = bytearray()  # Modbus 事务在途时的响应累积
        self.message_entries: list[dict] = []  # {ts, direction, text}：导出/自动日志/控制通道共用
        self.rx_bytes_total = 0
        self.tx_bytes_total = 0
        self._known_port_names: list[str] | None = None  # 热插拔基线（None=还没首扫）
        self._connected_device = ''
        self._auto_log = AutoLogWriter(get_log_dir())
        self.recorder = SessionRecorder(get_log_dir())
        self._replay_state: dict | None = None

        # ---- 顶部连接行 ----
        self.combo_port = QtWidgets.QComboBox()
        self.combo_baud = QtWidgets.QComboBox()
        self.combo_baud.setEditable(True)
        self.combo_baud.addItems(['1500000', '921600', '460800', '115200'])
        self.btn_connect = QtWidgets.QPushButton('连接')
        self.btn_connect.clicked.connect(self.toggle_connect)
        self.btn_refresh = QtWidgets.QPushButton('刷新串口')
        self.btn_refresh.clicked.connect(self.refresh_ports)
        self.chk_auto_link = QtWidgets.QCheckBox('新设备自动连接')
        self.chk_auto_link.setToolTip('勾选后扫描到新插入的串口设备即自动连接（多个新设备优先 CH340/CH341）')
        self.chk_auto_link.toggled.connect(
            lambda on: write_setting('connect/auto_link', '1' if on else '0'))
        self.lbl_status = QtWidgets.QLabel('未连接')
        self.lbl_status.setObjectName('status_err')
        self.lbl_stats = QtWidgets.QLabel('RX 0.0 KiB | TX 0.0 KiB')
        self.lbl_stats.setObjectName('backlog')

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel('串口'))
        top.addWidget(self.combo_port, 2)
        top.addWidget(self.btn_refresh)
        top.addWidget(QtWidgets.QLabel('波特率'))
        top.addWidget(self.combo_baud, 1)
        top.addWidget(self.chk_auto_link)
        top.addWidget(self.btn_connect)
        top.addWidget(self.lbl_status, 2)
        top.addWidget(self.lbl_stats, 1)

        # ---- 日志区 + 查找条 + 记录操作行 ----
        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
        self.log.setToolTip('RX 日志：悬停任意行 0.4 秒自动 HEX⇄文本/十进制/float32 互转；'
                            'Ctrl+F 查找与时间戳定位')
        self._log_hover = LogHoverTip(self.log)
        self.find_bar = FindBar(self.log)
        self.find_bar.setVisible(False)

        self.btn_clear_log = QtWidgets.QPushButton('清空记录')
        self.btn_clear_log.setObjectName('tool')
        self.btn_clear_log.clicked.connect(self.clear_log)
        self.btn_export_log = QtWidgets.QPushButton('导出日志')
        self.btn_export_log.setObjectName('tool')
        self.btn_export_log.setToolTip('把当前 RX 日志（全部条目）导出为文本文件')
        self.btn_export_log.clicked.connect(self._export_log)
        self.chk_auto_save = QtWidgets.QCheckBox('自动保存日志')
        self.chk_auto_save.setChecked(True)
        self.chk_auto_save.setToolTip('收发的每条日志实时追加到 logs/serial_auto_log_*.txt（SYS 除外）')
        self.lbl_log_path = QtWidgets.QLabel('自动日志将在首次收到数据后生成到 logs 目录')
        self.lbl_log_path.setObjectName('backlog')

        logbar = QtWidgets.QHBoxLayout()
        logbar.addWidget(self.chk_auto_save)
        logbar.addWidget(self.lbl_log_path, 1)
        logbar.addWidget(self.btn_clear_log)
        logbar.addWidget(self.btn_export_log)

        # ---- 底部发送行 ----
        self.combo_rx_format = QtWidgets.QComboBox()
        self.combo_rx_format.addItems(['ASCII', 'HEX'])
        self.combo_rx_format.setToolTip('接收显示格式；切到 HEX 时未成行的残行立即落显')
        self.combo_rx_format.currentTextChanged.connect(self._on_rx_format_changed)

        self.combo_tx_format = QtWidgets.QComboBox()
        self.combo_tx_format.addItems(['ASCII', 'HEX'])

        self.combo_newline = QtWidgets.QComboBox()
        self.combo_newline.addItems(['无', 'LF', 'CR', 'CRLF'])
        self.combo_newline.setCurrentText('LF')

        self.edit_send = HistoryLineEdit()
        self.edit_send.setPlaceholderText('输入要发送的内容，Enter 发送，⬆/⬇ 回翻历史')
        self.edit_send.returnPressed.connect(self.send)
        self.btn_send = QtWidgets.QPushButton('发送')
        self.btn_send.clicked.connect(self.send)

        self.btn_find = QtWidgets.QPushButton('查找')
        self.btn_find.setObjectName('tool')
        self.btn_find.clicked.connect(self.find_bar.open_and_focus)

        self.btn_file = QtWidgets.QPushButton('文件发送')
        self.btn_file.setObjectName('tool')
        self.btn_file.clicked.connect(self.toggle_file_send)

        self.lbl_backlog = QtWidgets.QLabel('积压 0 B')
        self.lbl_backlog.setObjectName('backlog')

        bottom = QtWidgets.QHBoxLayout()
        bottom.addWidget(QtWidgets.QLabel('收'))
        bottom.addWidget(self.combo_rx_format)
        bottom.addWidget(QtWidgets.QLabel('发'))
        bottom.addWidget(self.combo_tx_format)
        bottom.addWidget(QtWidgets.QLabel('换行'))
        bottom.addWidget(self.combo_newline)
        bottom.addWidget(self.edit_send, 3)
        bottom.addWidget(self.btn_send)
        bottom.addWidget(self.btn_find)
        bottom.addWidget(self.btn_file)
        bottom.addWidget(self.lbl_backlog, 1)

        terminal = QtWidgets.QWidget()
        terminal_layout = QtWidgets.QVBoxLayout(terminal)
        terminal_layout.setContentsMargins(0, 0, 0, 0)
        terminal_layout.addLayout(top)
        terminal_layout.addLayout(logbar)
        terminal_layout.addWidget(self.find_bar)
        terminal_layout.addWidget(self.log, 1)
        terminal_layout.addLayout(bottom)

        # ---- 页签：终端 / 波形 / 频谱 / Modbus ----
        self.wave = WaveWindow()
        # 场景预设要读写主窗体波特率（只改选择不重连，对齐主线）
        self.wave.baud_getter = self.combo_baud.currentText
        self.wave.baud_setter = self.combo_baud.setCurrentText
        self.spectrum = SpectrumPanel(self.wave.state)
        self.modbus = ModbusPanel()
        self.modbus.transport = self.modbus_transport
        self.modbus.log_fn = self._append_log
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(terminal, '终端')
        self.tabs.addTab(self.wave, '波形')
        self.tabs.addTab(self.spectrum, '频谱')
        self.tabs.addTab(self.modbus, 'Modbus')
        self.tabs.setObjectName('main_tabs')
        self.setCentralWidget(self.tabs)

        find_shortcut = QtGui.QShortcut(QtGui.QKeySequence('Ctrl+F'), terminal)
        find_shortcut.activated.connect(self.find_bar.open_and_focus)

        self.flush_timer = QtCore.QTimer(self)
        self.flush_timer.timeout.connect(self.on_flush_tick)
        self.flush_timer.start(self.FLUSH_INTERVAL_MS)

        self.file_timer = QtCore.QTimer(self)
        self.file_timer.timeout.connect(self._file_send_tick)
        self.file_timer.setInterval(FILE_INTERVAL_MS)

        self.refresh_ports()
        self._wire_settings_memory()
        self._restore_connect_settings()
        self.start_port_watch()

        # ---- 帮助菜单：检查更新 / 回放录制 / 关于 ----
        menu_help = self.menuBar().addMenu('帮助')
        act_check = QtGui.QAction('检查更新', self)
        act_check.triggered.connect(lambda: self._run_update_check(manual=True))
        menu_help.addAction(act_check)
        act_replay = QtGui.QAction('回放会话录制…', self)
        act_replay.setToolTip('选择录制生成的 JSONL 文件，把当时的字节流重放进接收管线')
        act_replay.triggered.connect(self._replay_from_dialog)
        menu_help.addAction(act_replay)
        act_about = QtGui.QAction('关于', self)
        act_about.triggered.connect(self._show_about)
        menu_help.addAction(act_about)
        # AI 控制通道（TCP JSON 行协议，默认 127.0.0.1:9101）
        self._control_api = self._build_control_api()
        self._control_channel = ControlChannel(self._control_api, parent=self)
        self.act_control = QtGui.QAction('AI 控制通道 (127.0.0.1:9101)', self)
        self.act_control.setCheckable(True)
        self.act_control.setToolTip('开启后 AI/脚本可通过 TCP JSON 行协议控制本工具：'
                                    'ping/status/send/read_lines/wave_snapshot/…')
        self.act_control.toggled.connect(self._on_control_toggled)
        menu_help.addAction(self.act_control)
        self._update_check_fn = check_app_update  # 测试可注入替换

        # 启动更新检查：24h 节流（对齐主线），延迟 3s 等界面稳定
        QtCore.QTimer.singleShot(3000, self._maybe_auto_update_check)

    # ---------- 设置记忆（gui_settings_qt.ini：口/波特率/收发格式/换行/自动连接） ----------

    def _wire_settings_memory(self):
        self.combo_baud.currentTextChanged.connect(
            lambda text: write_setting('connect/baud', text))
        self.combo_rx_format.currentTextChanged.connect(
            lambda text: write_setting('connect/rx_format', text))
        self.combo_tx_format.currentTextChanged.connect(
            lambda text: write_setting('connect/tx_format', text))
        self.combo_newline.currentTextChanged.connect(
            lambda text: write_setting('connect/newline', text))

    def _restore_connect_settings(self):
        for key, combo in (('connect/rx_format', self.combo_rx_format),
                           ('connect/tx_format', self.combo_tx_format),
                           ('connect/newline', self.combo_newline)):
            saved = read_setting(key, '')
            if saved and combo.findText(saved) >= 0:
                combo.setCurrentText(saved)
        baud = read_setting('connect/baud', '')
        if baud.isdigit():
            self.combo_baud.setCurrentText(baud)
        if read_setting('connect/auto_link', '0') in ('1', 'true', 'yes'):
            self.chk_auto_link.setChecked(True)
        # 端口在设备列表刷新后才可能存在，最后恢复
        saved_port = read_setting('connect/port', '')
        if saved_port:
            idx = self.combo_port.findText(saved_port, QtCore.Qt.MatchStartsWith)
            if idx >= 0:
                self.combo_port.setCurrentIndex(idx)

    def _save_connect_settings(self):
        write_setting('connect/baud', self.combo_baud.currentText())
        write_setting('connect/rx_format', self.combo_rx_format.currentText())
        write_setting('connect/tx_format', self.combo_tx_format.currentText())
        write_setting('connect/newline', self.combo_newline.currentText())
        write_setting('connect/auto_link', '1' if self.chk_auto_link.isChecked() else '0')
        port = port_name_from_display(self.combo_port.currentText())
        if port:
            write_setting('connect/port', port)

    # ---------- 检测更新 ----------

    UPDATE_CHECK_INTERVAL_S = 24 * 3600

    def _maybe_auto_update_check(self):
        try:
            last = float(read_setting('update/last_check', '0') or 0)
        except ValueError:
            last = 0.0
        if time.time() - last < self.UPDATE_CHECK_INTERVAL_S:
            return
        write_setting('update/last_check', str(time.time()))
        self._run_update_check(manual=False)

    def _run_update_check(self, manual: bool):
        version = APP_VERSION
        runnable = _UpdateCheckRunnable(self._update_check_fn, version)
        runnable.signals.done.connect(
            lambda result: self._on_update_result(result, manual))
        QtCore.QThreadPool.globalInstance().start(runnable)

    def _on_update_result(self, result: dict, manual: bool):
        if result.get('has_update'):
            self._append_log('SYS', f"发现新版本 {result['latest']}"
                                    f"（当前 v{APP_VERSION}），下载: {result['page_url']}")
            self.set_status(f"有新版本 {result['latest']}（当前 v{APP_VERSION}）", 'status_ok')
            return
        if manual and result.get('ok'):
            self._append_log('SYS', f"已是最新版本（v{APP_VERSION}）")
        elif manual:
            self._append_log('SYS', f"检查更新失败: {result.get('note', '')}")
        # 自动检查失败静默（对齐主线）

    def _replay_from_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择会话录制文件', get_log_dir(), '会话录制 (*.jsonl);;所有文件 (*.*)')
        if path:
            self._start_replay(path)

    # ---------- AI 控制通道 ----------

    def _build_control_api(self) -> ControlAPI:
        """把主窗体能力包成控制通道可调用集（对齐主线 handle_control_request 的宿主面）。"""
        import re as _re

        from core.ports import port_display_text, scan_port_infos
        from core.wave_state import PROTOCOLS

        def status() -> dict:
            return {
                'connected': bool(self.worker),
                'port': self._connected_device,
                'baud': (self.worker.baud if self.worker else 0),
                'protocol': self.wave.state.protocol,
                'frame_total': self.wave.state.frame_total,
                'frame_count': self.wave.state.frame_count,
                'channels': len(self.wave.state.ch_values),
                'rx_entries': len(self.message_entries),
                'recording': self.recorder.active,
                'sim_on': self.wave.btn_sim.isChecked(),
            }

        def list_ports() -> list:
            return [port_display_text(d, desc) for d, desc in scan_port_infos()]

        def send(text: str, hex: bool, newline: str) -> dict:  # noqa: A002 (协议字段名)
            if not self.worker:
                raise RuntimeError('串口未连接。')
            if hex:
                payload = try_parse_hex_text(text) + NEWLINE_BYTES.get(newline, b'')
                if not payload:
                    raise ValueError('HEX 输入无效')
                display = bytes_to_hex_text(payload)
            else:
                payload = (text + NEWLINE_TEXT.get(newline, '')).encode('utf-8')
                if not payload:
                    raise ValueError('空内容')
                display = text + NEWLINE_TEXT.get(newline, '')
            if not self.worker.write_bytes(payload):
                raise RuntimeError('串口写入失败。')
            self._count_tx_bytes(len(payload))
            self._append_log('TX', display.replace('\r', '<CR>').replace('\n', '<LF>'))
            return {'ok': True}

        def read_lines(count: int, filter: str) -> dict:  # noqa: A002 (协议字段名)
            regex = _re.compile(filter) if filter else None
            start = max(0, len(self.message_entries) - count)
            lines = []
            for entry in self.message_entries[start:]:
                if regex is not None and not regex.search(entry['text']):
                    continue
                lines.append({
                    't': entry['ts'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
                    'dir': entry['direction'],
                    'text': entry['text'],
                })
            return {'ok': True, 'lines': lines}

        def wave_snapshot(count: int) -> dict:
            st = self.wave.state
            take = min(max(1, count), st.frame_count)
            channels = [{'name': st.channel_display_name(c),
                         'values': list(st.snapshot(c, take))}
                        for c in range(len(st.ch_values))]
            return {'ok': True, 'protocol': st.protocol, 'frame_total': st.frame_total,
                    'count': take, 'channels': channels}

        def set_protocol(protocol: str) -> dict:
            if protocol not in PROTOCOLS:
                return {'ok': False, 'error': 'protocol 必须是 FireWater/JustFloat/RawData'}
            self.wave.combo_protocol.setCurrentText(protocol)
            return {'ok': True, 'protocol': self.wave.state.protocol}

        def wave_clear() -> dict:
            self.wave.clear_buffers()
            return {'ok': True}

        def wave_sim(on: bool) -> dict:
            self.wave.btn_sim.setChecked(on)
            return {'ok': True, 'sim_on': self.wave.btn_sim.isChecked()}

        def record(on: bool) -> dict:
            self.toggle_session_record(on)
            return {'ok': True, 'recording': self.recorder.active,
                    'file': self.recorder.path}

        return ControlAPI(status=status, list_ports=list_ports, send=send,
                          read_lines=read_lines, wave_snapshot=wave_snapshot,
                          set_protocol=set_protocol, wave_clear=wave_clear,
                          wave_sim=wave_sim, record=record)

    def _on_control_toggled(self, on: bool):
        if on:
            if self._control_channel.start():
                self._append_log('SYS',
                                 f'AI 控制通道已开启: tcp://127.0.0.1:{self._control_channel.port}'
                                 '（JSON 行协议: ping/status/send/read_lines/wave_snapshot/'
                                 'set_protocol/wave_clear/wave_sim/record/list_ports）')
            else:
                self._append_log('SYS', 'AI 控制通道启动失败（端口可能被占用）')
                self.act_control.setChecked(False)
        else:
            self._control_channel.stop()
            self._append_log('SYS', 'AI 控制通道已关闭。')

    def _show_about(self):
        QtWidgets.QMessageBox.about(
            self, f'关于 {APP_NAME}',
            f'<b>{APP_NAME}</b> v{APP_VERSION}<br>'
            f'PowerShell 主线（v1.1.0）的 Qt 前端支线实验版。<br>'
            f'仓库: <a href="{REPO_URL}">{REPO_URL}</a><br>'
            f'发布页: <a href="{RELEASES_PAGE}">{RELEASES_PAGE}</a><br>'
            f'许可证: GPL-3.0')

    # ---------- 串口 / 热插拔 / 设置记忆 ----------

    PORT_WATCH_INTERVAL_MS = 2000  # 主线同款 2s 轮询

    def refresh_ports(self):
        infos = scan_port_infos()
        keep = port_name_from_display(self.combo_port.currentText())
        self.combo_port.clear()
        for device, desc in infos:
            self.combo_port.addItem(port_display_text(device, desc))
        if keep:
            idx = self.combo_port.findText(keep, QtCore.Qt.MatchStartsWith)
            if idx >= 0:
                self.combo_port.setCurrentIndex(idx)

    def start_port_watch(self):
        """首扫建基线（已插着的设备不触发自动连接）→ 启动 2s 热插拔轮询。"""
        self._known_port_names = [d for d, _ in scan_port_infos()]
        self._port_watch_timer = QtCore.QTimer(self)
        self._port_watch_timer.setInterval(self.PORT_WATCH_INTERVAL_MS)
        self._port_watch_timer.timeout.connect(self._port_watch_tick)
        self._port_watch_timer.start()

    def _port_watch_tick(self):
        infos = scan_port_infos()
        names = [d for d, _ in infos]
        if names == self._known_port_names:
            return
        new_devices = ([(d, desc) for d, desc in infos if d not in self._known_port_names]
                       if self._known_port_names is not None else [])
        self._known_port_names = names
        self.refresh_ports()
        self._maybe_auto_connect(new_devices)

    def _maybe_auto_connect(self, new_devices: list[tuple[str, str]]):
        if not new_devices or not self.chk_auto_link.isChecked():
            return
        target = pick_auto_connect_port(new_devices)
        if not target:
            return
        desc = next((d for d, desc in new_devices if d == target), '')
        self._append_log('SYS', f'检测到新串口设备 {port_display_text(target, desc)}，尝试自动连接…')
        idx = self.combo_port.findText(target, QtCore.Qt.MatchStartsWith)
        if idx >= 0:
            self.combo_port.setCurrentIndex(idx)
        if self.worker and port_name_from_display(self.combo_port.currentText()) == \
                self._connected_device:
            return  # 已经连着同一个口
        try:
            self.open_connection()
        except Exception as exc:
            self._append_log('SYS', f'自动连接失败: {exc}')

    def toggle_connect(self):
        if self.worker:
            self._teardown_worker()
            self.set_status('未连接', 'status_err')
            return
        if not self.combo_port.currentText():
            self.set_status('没有可用串口', 'status_err')
            return
        try:
            self.open_connection()
        except (ValueError, OSError) as exc:
            self.set_status(f'打开失败: {exc}', 'status_err')

    def open_connection(self):
        """按当前选口/波特率打开串口（手动连接与热插拔自动连接共用）。"""
        device = port_name_from_display(self.combo_port.currentText())
        if not device:
            self.set_status('没有可用串口', 'status_err')
            return
        try:
            baud = int(self.combo_baud.currentText())
        except ValueError:
            self.set_status('波特率必须是数字', 'status_err')
            return
        if self.worker:  # 换口重连：先拆旧连接
            self._teardown_worker()
        self.worker = SerialWorker(device, baud, self)
        self._connected_device = device
        self.worker.got_bytes.connect(self.ingest_bytes)
        self.worker.open_failed.connect(lambda msg: (self._teardown_worker(),
                                                     self.set_status(f'打开失败: {msg}', 'status_err')))
        self.worker.lost.connect(lambda msg: (self._teardown_worker(),
                                              self.set_status(f'连接丢失: {msg}', 'status_err'),
                                              self._stop_file_send('串口已断开')))
        self.worker.opened.connect(lambda: self.set_status(f'已连接 {device} @ {baud}', 'status_ok'))
        self.worker.opened.connect(self._on_connected)
        self.worker.start()
        write_setting('connect/port', device)
        write_setting('connect/baud', str(baud))

    def _on_connected(self):
        # 连接周期归零收发统计（对齐主线 reset_receive_stats 时机）
        self.rx_bytes_total = 0
        self.tx_bytes_total = 0
        self._update_stats_label()
        self._set_connect_params_locked(True)

    def _set_connect_params_locked(self, locked: bool):
        """连接期间锁定串口协议参数（改口/改波特率须先断开），置灰提示。"""
        self.combo_port.setEnabled(not locked)
        self.combo_baud.setEnabled(not locked)
        tip = '已连接：断开后才能修改' if locked else ''
        self.combo_port.setToolTip(tip)
        self.combo_baud.setToolTip(tip)

    def _teardown_worker(self):
        self._set_connect_params_locked(False)
        if not self.worker:
            return
        self.worker.stop()
        self.worker.deleteLater()
        self.worker = None
        self._auto_log.close()  # 断开即落盘收尾（文件名保留，重连后追加同一文件）

    def set_status(self, text: str, obj_name: str):
        self.lbl_status.setText(text)
        self.lbl_status.setObjectName(obj_name)
        self.lbl_status.style().unpolish(self.lbl_status)
        self.lbl_status.style().polish(self.lbl_status)

    def closeEvent(self, event):  # noqa: N802 (Qt 命名)
        self._save_connect_settings()
        if getattr(self, '_port_watch_timer', None):
            self._port_watch_timer.stop()
        self._auto_log.close()
        self.recorder.stop()
        self._control_channel.stop()
        self.wave.stop_timers()
        self.spectrum.stop_timers()
        self._stop_file_send('窗口关闭')
        self._teardown_worker()
        super().closeEvent(event)

    # ---------- RX 管线 ----------

    def ingest_bytes(self, data: bytes):
        now = time.monotonic()
        self.rx_bytes_total += len(data)
        self._update_stats_label()
        if self.recorder.active and self._replay_state is None:
            # 回放期间不再录自己（避免回放套娃）
            self.recorder.record_chunk(data)
        if self.combo_rx_format.currentText() == 'HEX':
            # 对齐主线：HEX 显示模式按到达块直接落显；先清掉未成行的文本残行
            tail = self.buffer.drain()
            if tail:
                self._append_log('RX', f'{tail} (格式切换落显)')
            self._append_log('RX', bytes_to_hex_text(data))
        else:
            for line in self.buffer.feed(data, now):
                self._append_log('RX', line)
        self._modbus_rx_acc.extend(data)
        self.wave.consume_bytes(data)
        self._update_backlog()

    def _update_stats_label(self):
        self.lbl_stats.setText(
            f'RX {self.rx_bytes_total / 1024.0:.1f} KiB | '
            f'TX {self.tx_bytes_total / 1024.0:.1f} KiB')

    def _count_tx_bytes(self, n: int):
        self.tx_bytes_total += n
        self._update_stats_label()

    def modbus_transport(self, frame: bytes, timeout_ms: int = 600) -> bytes:
        """Modbus 线上事务：清残留 → 写请求 → 有界读响应（对齐主线 send_modbus_wire_transaction）。

        响应长度可预期（功能码/异常位推出）或静默 150ms 即收；整体不超过 timeout_ms。
        """
        if not self.worker:
            return b''
        self._modbus_rx_acc = bytearray()
        if not self.worker.write_bytes(frame):
            return b''
        self._count_tx_bytes(len(frame))
        deadline = time.monotonic() + timeout_ms / 1000.0
        idle_at = time.monotonic() + 0.15
        expected = -1

        loop = QtCore.QEventLoop()
        poll = QtCore.QTimer(loop)
        poll.setInterval(10)

        def check():
            nonlocal expected, idle_at
            acc = self._modbus_rx_acc
            now = time.monotonic()
            if acc:
                idle_at = now + 0.15
                if expected < 0 and len(acc) >= 3:
                    expected = 5 if (acc[1] & 0x80) else 3 + acc[2] + 2
                if 0 <= expected <= len(acc):
                    loop.quit()
                    return
                if now > idle_at:
                    loop.quit()
                    return
            if now > deadline:
                loop.quit()

        poll.timeout.connect(check)
        poll.start()
        loop.exec()
        resp = bytes(self._modbus_rx_acc)
        self._modbus_rx_acc = bytearray()
        return resp

    def on_flush_tick(self):
        now = time.monotonic()
        tail = self.buffer.poll_idle_flush(now)
        if tail is not None:
            self._append_log('RX', f'{tail} (空闲落显)')
        self._flush_auto_log()
        self._update_backlog()

    def _flush_auto_log(self):
        """自动日志节拍：勾选开 → 懒建文件落盘；勾选关 → 积压直接丢（对齐主线）。"""
        if not self.chk_auto_save.isChecked():
            self._auto_log.discard()
            return
        if self._auto_log.flush() and self._auto_log.path:
            self.lbl_log_path.setText(f'日志: {self._auto_log.path}')

    def _export_log(self):
        default_name = f'serial_export_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt'
        log_dir = get_log_dir()
        os.makedirs(log_dir, exist_ok=True)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, '导出日志', os.path.join(log_dir, default_name),
            '文本日志 (*.txt);;所有文件 (*.*)')
        if not path:
            return
        try:
            self._export_log_to(path)
        except OSError as exc:
            self._append_log('SYS', f'导出日志失败: {exc}')
            QtWidgets.QMessageBox.warning(self, '导出失败', str(exc))

    def _export_log_to(self, path: str):
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(build_log_export_text(self.message_entries))
        self._append_log('SYS', f'日志已导出到:\n{path}')

    def _on_rx_format_changed(self, _text: str):
        # 切格式时把残行落到日志，避免"看起来丢了"
        tail = self.buffer.drain()
        if tail:
            self._append_log('RX', f'{tail} (格式切换落显)')
        self._update_backlog()

    def _update_backlog(self):
        total = self.buffer.backlog_bytes + self.wave.state.backlog_bytes
        self.lbl_backlog.setText(f'积压 {total} B')

    # ---------- 会话录制 / 回放 ----------

    REPLAY_INTERVAL_MS = 10

    def toggle_session_record(self, on: bool) -> str | None:
        """波形窗「录制」按钮的实际执行方。返回录制文件路径（停止时 None）。"""
        if on:
            path = self.recorder.start()
            self._append_log('SYS', f'会话录制开始: {path}')
            return path
        if self.recorder.active:
            self.recorder.stop()
            self._append_log('SYS', '会话录制结束。')
        return None

    def _start_replay(self, path: str):
        """回放录制文件：按录制顺序逐块定时喂回接收管线（不重新录制）。"""
        if self._replay_state is not None:
            self.set_status('已在回放中', 'status_err')
            return
        chunks = load_replay_chunks(path)
        if not chunks:
            self._append_log('SYS', f'回放文件没有可重放的数据块: {path}')
            return
        self._replay_state = {'chunks': chunks, 'index': 0, 'path': path}
        self._replay_timer = QtCore.QTimer(self)
        self._replay_timer.setInterval(self.REPLAY_INTERVAL_MS)
        self._replay_timer.timeout.connect(self._replay_step)
        self._replay_timer.start()
        self._append_log('SYS', f'会话回放开始: {path}（{len(chunks)} 块）')

    def _replay_step(self) -> bool:
        """喂下一块。返回是否还有剩余（测试可手工循环调用）。"""
        state = self._replay_state
        if state is None:
            return False
        if state['index'] >= len(state['chunks']):
            self._stop_replay()
            return False
        _ms, data = state['chunks'][state['index']]
        state['index'] += 1
        self.ingest_bytes(data)
        if state['index'] >= len(state['chunks']):
            self._stop_replay()
            return False
        return True

    def _stop_replay(self):
        state = self._replay_state
        self._replay_state = None
        if getattr(self, '_replay_timer', None):
            self._replay_timer.stop()
        if state:
            self._append_log('SYS', f"会话回放完成: {state['path']}（{state['index']} 块）")

    # ---------- 日志 / 发送 ----------

    def _append_log(self, direction: str, text: str):
        now = datetime.datetime.now()
        self.message_entries.append({'ts': now, 'direction': direction, 'text': text})
        if len(self.message_entries) > MAX_LOG_ENTRIES:
            # 长跑防涨：与主线同款裁剪量（丢最老 4000 条）
            del self.message_entries[:MAX_LOG_TRIM]
        if direction != 'SYS' and self.chk_auto_save.isChecked():
            self._auto_log.enqueue(format_entry_block(now, direction, text))
        if direction == 'RX':
            decoded = test_fault_dump_line(text)
            if decoded:
                self._append_log('SYS', f'HardFault 解码: {decoded}')
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

    def clear_log(self):
        """清空记录：显示、条目、残行缓冲一并清（字节统计不清——那是连接周期的量）。"""
        self.buffer.clear()
        self.message_entries.clear()
        self.log.clear()
        self._append_log('SYS', '消息记录已清空。')
        self._update_backlog()

    def send(self):
        raw = self.edit_send.text()
        if not raw:
            return
        newline_mode = self.combo_newline.currentText()
        payload_bytes, display_text = self._build_tx_payload(raw, newline_mode)
        if not payload_bytes:
            return
        sent = bool(self.worker and self.worker.write_bytes(payload_bytes))
        if sent:
            self._count_tx_bytes(len(payload_bytes))
        suffix = '' if sent else '  (未连接，仅入日志)'
        self._append_log('TX', display_text + suffix)
        self.edit_send.push_history(raw)
        self.edit_send.clear()

    def _build_tx_payload(self, raw: str, newline_mode: str):
        """按发送格式组包。返回 (bytes, 展示文本)；无效 HEX 返回 (b'', 错误文本)。"""
        if self.combo_tx_format.currentText() == 'HEX':
            try:
                payload = try_parse_hex_text(raw)
            except ValueError as exc:
                self.set_status(str(exc), 'status_err')
                return b'', ''
            payload += NEWLINE_BYTES.get(newline_mode, b'')
            if not payload:
                return b'', ''
            return payload, bytes_to_hex_text(payload)
        payload_text = raw + NEWLINE_TEXT.get(newline_mode, '')
        if not payload_text:
            return b'', ''
        # 主线 TX 展示：换行可视化（<CR>/<LF>）
        return payload_text.encode('utf-8'), \
            payload_text.replace('\r', '<CR>').replace('\n', '<LF>')

    # ---------- 文件发送（分块定时器，UI 不卡死；发送中再点 = 停止） ----------

    def toggle_file_send(self):
        if self._file_send:
            self._stop_file_send('手动停止')
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, '选择要发送的文件')
        if not path:
            return
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError as exc:
            self.set_status(f'文件读取失败: {exc}', 'status_err')
            return
        if not data:
            self.set_status('文件为空，没有可发送的内容。', 'status_err')
            return
        if not self.worker:
            self.set_status('串口未连接。', 'status_err')
            return
        name = path.replace('\\', '/').rsplit('/', 1)[-1]
        self._file_send = {'name': name, 'bytes': data, 'pos': 0}
        self.btn_file.setText('停止')
        self._append_log('TX', f'[文件发送] {name}（{len(data)} 字节，原样字节流）')
        self.file_timer.start()

    def _file_send_tick(self):
        state = self._file_send
        if not state:
            self.file_timer.stop()
            return
        if not self.worker:
            self._stop_file_send('串口已断开')
            return
        data = state['bytes']
        chunk = data[state['pos']:state['pos'] + FILE_CHUNK]
        try:
            ok = self.worker.write_bytes(chunk)
        except Exception as exc:  # pragma: no cover - 真实串口路径
            self._stop_file_send(f'写入失败: {exc}')
            self.set_status(f'文件发送失败: {exc}', 'status_err')
            return
        if not ok:
            self._stop_file_send('写入失败: 串口未打开')
            return
        self._count_tx_bytes(len(chunk))
        state['pos'] += len(chunk)
        if state['pos'] >= len(data):
            self._stop_file_send('完成')
            self.set_status(f"文件发送完成: {state['name']}（{len(data)} 字节）", 'status_ok')
        else:
            self.set_status(
                f"文件发送中 {state['name']}: {state['pos'] / 1024:.1f} / {len(data) / 1024:.1f} KiB",
                'status_ok')

    def _stop_file_send(self, reason: str):
        state = self._file_send
        if not state:
            return
        self._file_send = None
        self.file_timer.stop()
        self.btn_file.setText('文件发送')
        if reason != '完成':
            self._append_log(
                'SYS',
                f"[文件发送中断] {state['name']}: {reason}"
                f"（已发 {state['pos']} / {len(state['bytes'])} 字节）")
