# -*- coding: utf-8 -*-
"""波形窗（M2）：pyqtgraph 实时曲线，能力对齐主线波形窗，交互按 Qt 习惯调整。

布局：
  工具行  协议 | 窗口宽度 | 滤波+参数 | 暂停 | 清空 | 模拟数据 | 导出CSV
  Y轴行   自动Y | Ymin/Ymax | 通道芯片（点按开关曲线、双击改名）
  绘图区  pyqtgraph：左键拖动平移/滚轮缩放浏览历史，拖回最右端恢复实时跟随
  状态行  帧 | 解析失败 | FPS | 视图模式

暂停语义与主线一致：冻结曲线数据，RX 显示与 JustFloat 帧排空不受影响。
"""
from __future__ import annotations

import csv
import math
import random
import struct
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

import pyqtgraph as pg

from core.ai_doc import get_ai_format_doc
from core.settings import read_setting, write_setting
from core.wave_presets import WavePreset, default_presets_path, load_wave_presets, save_wave_presets
from core.wave_state import FILTERS, PROTOCOLS, WaveState
from ui.xy_view import XYSection

CURVE_COLORS = ['#4EC9B0', '#569CD6', '#CE9178', '#C586C0',
                '#DCDCAA', '#F48771', '#9CDCFE', '#D4D4D4']
WINDOW_PRESETS = ('200', '500', '1000', '2000', '5000', '10000', '50000')

pg.setConfigOptions(antialias=False, background='#161B26', foreground='#8A93A6')


class WaveWindow(QtWidgets.QWidget):
    REPAINT_INTERVAL_MS = 50
    STATS_INTERVAL_MS = 500
    SIM_INTERVAL_MS = 50

    def __init__(self, state: WaveState | None = None, parent=None):
        super().__init__(parent)
        self.state = state or WaveState()
        self.state.enabled = True  # 波形页常驻，数据随收随解析
        self.state.on_note = self._on_note
        self._curves: list[pg.PlotDataItem] = []
        self._chips: list[QtWidgets.QCheckBox] = []
        self._syncing = False
        self._fps_last_count = 0
        self._fps_last_at = time.monotonic()
        self._sim_timer = QtCore.QTimer(self)
        self._sim_timer.timeout.connect(self._on_sim_tick)
        self._sim_t = 0.0
        self._preset_syncing = False

        # ---- 工具行 ----
        self.combo_protocol = QtWidgets.QComboBox()
        self.combo_protocol.addItems(PROTOCOLS)
        self.combo_protocol.currentTextChanged.connect(self._on_protocol_changed)

        self.combo_window = QtWidgets.QComboBox()
        self.combo_window.setEditable(True)
        self.combo_window.addItems(WINDOW_PRESETS)
        self.combo_window.setCurrentText('1000')
        self.combo_window.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.combo_window.currentTextChanged.connect(self._on_window_changed)

        self.combo_filter = QtWidgets.QComboBox()
        self.combo_filter.addItems(FILTERS)
        self.combo_filter.currentTextChanged.connect(self._on_filter_mode_changed)

        self.spin_filter_param = QtWidgets.QSpinBox()
        self.spin_filter_param.setRange(2, 1024)
        self.spin_filter_param.setValue(int(self.state.filter_param))
        self.spin_filter_param.setToolTip('滑动平均=窗口宽度；零相位低通=强度N（alpha=1/N）')
        self.spin_filter_param.valueChanged.connect(self._on_filter_param_changed)

        self.btn_pause = QtWidgets.QPushButton('暂停')
        self.btn_pause.setObjectName('tool')
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(self._on_pause_toggled)

        self.btn_clear = QtWidgets.QPushButton('清空')
        self.btn_clear.setObjectName('tool')
        self.btn_clear.clicked.connect(self.clear_buffers)

        self.btn_sim = QtWidgets.QPushButton('模拟数据')
        self.btn_sim.setObjectName('tool')
        self.btn_sim.setCheckable(True)
        self.btn_sim.toggled.connect(self._on_sim_toggled)

        self.btn_csv = QtWidgets.QPushButton('导出CSV')
        self.btn_csv.setObjectName('tool')
        self.btn_csv.clicked.connect(self._export_csv)

        self.btn_format = QtWidgets.QPushButton('格式说明')
        self.btn_format.setObjectName('tool')
        self.btn_format.setToolTip('三协议数据格式速查；一键复制给 AI 的接入说明')
        self.btn_format.clicked.connect(self._show_format_dialog)

        self.btn_xy = QtWidgets.QPushButton('XY相位图')
        self.btn_xy.setObjectName('tool')
        self.btn_xy.setCheckable(True)
        self.btn_xy.setToolTip('IQ 李萨如/星座图：1~4 个正方格窗口，余晖累积，'
                               '格线正方用于目检正交性')
        self.btn_xy.toggled.connect(self._on_xy_toggled)

        # ---- 场景预设（波特率+协议+滤波+Y 量程+窗口 一键切换；文件在仓库根） ----
        self.presets_path = default_presets_path()
        self.baud_getter = lambda: ''   # 主窗体注入：读当前波特率
        self.baud_setter = lambda text: None  # 主窗体注入：写主窗体波特率
        self.combo_preset = QtWidgets.QComboBox()
        self.combo_preset.setToolTip('场景预设：一键切波特率/协议/滤波/量程/窗口')
        self.combo_preset.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.combo_preset.currentTextChanged.connect(self._on_preset_selected)
        self.btn_preset_save = QtWidgets.QPushButton('存预设')
        self.btn_preset_save.setObjectName('tool')
        self.btn_preset_save.setToolTip('把当前波特率+协议+滤波+Y 量程+窗口宽度存为场景预设')
        self.btn_preset_save.clicked.connect(self._save_preset_clicked)
        self.btn_preset_del = QtWidgets.QPushButton('删预设')
        self.btn_preset_del.setObjectName('tool')
        self.btn_preset_del.clicked.connect(self._delete_preset_clicked)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.addWidget(QtWidgets.QLabel('协议'))
        toolbar.addWidget(self.combo_protocol)
        toolbar.addWidget(QtWidgets.QLabel('窗口'))
        toolbar.addWidget(self.combo_window)
        toolbar.addWidget(QtWidgets.QLabel('滤波'))
        toolbar.addWidget(self.combo_filter)
        toolbar.addWidget(self.spin_filter_param)
        toolbar.addWidget(self.btn_pause)
        toolbar.addWidget(self.btn_clear)
        toolbar.addWidget(self.btn_sim)
        toolbar.addWidget(self.btn_csv)
        toolbar.addWidget(self.btn_format)
        toolbar.addWidget(self.btn_xy)
        toolbar.addWidget(QtWidgets.QLabel('预设'))
        toolbar.addWidget(self.combo_preset)
        toolbar.addWidget(self.btn_preset_save)
        toolbar.addWidget(self.btn_preset_del)
        toolbar.addStretch(1)

        # ---- Y 轴行 ----
        self.chk_auto_y = QtWidgets.QCheckBox('自动Y')
        self.chk_auto_y.setChecked(self.state.auto_y)
        self.chk_auto_y.toggled.connect(self._on_auto_y_toggled)

        self.edit_ymin = QtWidgets.QLineEdit()
        self.edit_ymin.setPlaceholderText('Y min')
        self.edit_ymax = QtWidgets.QLineEdit()
        self.edit_ymax.setPlaceholderText('Y max')
        for edit in (self.edit_ymin, self.edit_ymax):
            edit.setMaximumWidth(80)
            edit.editingFinished.connect(self._on_y_limits_edited)
        self._set_y_edit_enabled(self.state.auto_y)

        self.chip_area = QtWidgets.QScrollArea()
        self.chip_area.setWidgetResizable(True)
        self.chip_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.chip_area.setMaximumHeight(34)
        self.chip_host = QtWidgets.QWidget()
        self.chip_layout = QtWidgets.QHBoxLayout(self.chip_host)
        self.chip_layout.setContentsMargins(0, 0, 0, 0)
        self.chip_layout.addStretch(1)
        self.chip_area.setWidget(self.chip_host)

        # ---- XY 相位图区（默认隐藏，工具行按钮开关） ----
        self.xy_section = XYSection(self.state)
        self.xy_section.setVisible(False)

        yrow = QtWidgets.QHBoxLayout()
        yrow.addWidget(self.chk_auto_y)
        yrow.addWidget(self.edit_ymin)
        yrow.addWidget(self.edit_ymax)
        yrow.addWidget(self.chip_area, 1)

        # ---- 绘图区 ----
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        vb = self.plot.getViewBox()
        vb.sigRangeChangedManually.connect(self._on_manual_range)
        vb.setMouseEnabled(x=True, y=False)  # Y 由自动/手动量程管，鼠标只管 X

        # ---- 状态行 ----
        self.lbl_stats = QtWidgets.QLabel('帧 0 | 解析失败 0 | FPS 0 | 视图: 跟随')

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addLayout(toolbar)
        layout.addLayout(yrow)
        layout.addWidget(self.xy_section)
        layout.addWidget(self.plot, 1)
        layout.addWidget(self.lbl_stats)

        self._repaint_timer = QtCore.QTimer(self)
        self._repaint_timer.timeout.connect(self.repaint_wave)
        self._repaint_timer.start(self.REPAINT_INTERVAL_MS)

        self.refresh_presets()  # 场景预设列表（仓库根 wave_presets.txt）

        self._stats_timer = QtCore.QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start(self.STATS_INTERVAL_MS)

    # ---------- 数据入口 ----------

    def consume_bytes(self, data: bytes):
        self.state.consume_bytes(data)

    def _on_note(self, text: str):
        # 积压/失步提示交给主窗体日志（主窗体负责接到 add_message_entry 对应物）
        parent = self.parent()
        while parent is not None and not isinstance(parent, QtWidgets.QMainWindow):
            parent = parent.parent()
        if parent is not None and hasattr(parent, '_append_log'):
            parent._append_log('SYS', text)

    # ---------- 工具行 ----------

    def _on_protocol_changed(self, protocol: str):
        self.state.switch_protocol(protocol)
        self._rebuild_all()

    def _on_window_changed(self, text: str):
        try:
            self.state.set_view_width(int(text))
        except ValueError:
            pass

    def _on_filter_mode_changed(self, mode: str):
        self.state.filter_mode = mode
        self.spin_filter_param.setEnabled(mode != '关')

    def _on_filter_param_changed(self, value: int):
        self.state.filter_param = value

    def _on_pause_toggled(self, paused: bool):
        self.state.paused = paused
        self.btn_pause.setText('继续' if paused else '暂停')

    def _on_sim_toggled(self, on: bool):
        if on:
            self._sim_t = 0.0
            self._sim_timer.start(self.SIM_INTERVAL_MS)
        else:
            self._sim_timer.stop()

    def _on_xy_toggled(self, on: bool):
        self.xy_section.setVisible(on)
        if on:
            self.xy_section.refresh_channels()
            self.xy_section.start()
        else:
            self.xy_section.stop()

    def clear_buffers(self):
        self._sim_t = 0.0
        self.state.reset_buffers()
        self._rebuild_all()

    # ---------- Y 轴 ----------

    def _on_auto_y_toggled(self, checked: bool):
        self.state.auto_y = checked
        self._set_y_edit_enabled(checked)

    def _set_y_edit_enabled(self, auto: bool):
        self.edit_ymin.setEnabled(not auto)
        self.edit_ymax.setEnabled(not auto)

    def _on_y_limits_edited(self):
        try:
            y_min = float(self.edit_ymin.text())
            y_max = float(self.edit_ymax.text())
        except ValueError:
            return
        self.state.y_min = y_min
        self.state.y_max = y_max

    # ---------- 通道芯片 ----------

    def _ensure_chips(self):
        st = self.state
        while len(self._chips) < len(st.ch_values):
            c = len(self._chips)
            chip = QtWidgets.QCheckBox(st.channel_display_name(c))
            chip.setChecked(True)
            color = CURVE_COLORS[c % len(CURVE_COLORS)]
            chip.setStyleSheet(f'QCheckBox {{ color: {color}; padding: 2px 6px; }}')
            chip.toggled.connect(lambda checked, idx=c: self._on_chip_toggled(idx, checked))
            chip.setToolTip('点按开关该通道曲线；双击改名')
            chip.installEventFilter(self)
            row = self.chip_layout.count() - 1
            self.chip_layout.insertWidget(row, chip)
            self._chips.append(chip)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt 命名)
        if event.type() == QtCore.QEvent.MouseButtonDblClick and obj in self._chips:
            idx = self._chips.index(obj)
            name, ok = QtWidgets.QInputDialog.getText(
                self, '通道改名', f'通道 {idx} 显示名:',
                text=self.state.channel_display_name(idx))
            if ok:
                clean = self.state.set_channel_name(idx, name)
                if clean:
                    self._chips[idx].setText(clean)
                    joined = self.state.names_joined()
                    if joined:
                        write_setting('wave/channel_names', joined)
            return True
        return super().eventFilter(obj, event)

    def _on_chip_toggled(self, idx: int, checked: bool):
        if 0 <= idx < len(self.state.enabled_channels):
            self.state.enabled_channels[idx] = checked

    def _restore_channel_names(self):
        joined = read_setting('wave/channel_names', '')
        if not joined:
            return
        for idx, name in enumerate(joined.split(',')):
            if not name:
                continue
            while len(self.state.channel_names) <= idx:
                self.state.channel_names.append('')
            self.state.channel_names[idx] = name
        self._refresh_chip_names()

    def _refresh_chip_names(self):
        for idx, chip in enumerate(self._chips):
            chip.setText(self.state.channel_display_name(idx))

    # ---------- 绘制 ----------

    def _ensure_curves(self, n: int):
        while len(self._curves) < n:
            c = len(self._curves)
            pen = pg.mkPen(CURVE_COLORS[c % len(CURVE_COLORS)], width=1.2)
            curve = self.plot.plot([], [], pen=pen)
            self._curves.append(curve)

    def _rebuild_all(self):
        """协议切换/清空后：曲线与芯片按空缓冲重建。"""
        self.plot.clear()
        self._curves = []
        for chip in self._chips:
            self.chip_layout.removeWidget(chip)
            chip.deleteLater()
        self._chips = []
        self._ensure_curves(len(self.state.ch_values))
        self._ensure_chips()

    def repaint_wave(self):
        st = self.state
        start, end, _follow = st.view_range()
        self._ensure_curves(len(st.ch_values))
        self._ensure_chips()
        for c, curve in enumerate(self._curves):
            on = st.enabled_channels[c] if c < len(st.enabled_channels) else True
            data, offset, length = st.view_data(c, start, end)
            if on and length > 0:
                xs = np.arange(offset, offset + length, dtype=np.float64)
                curve.setData(xs, data[offset:offset + length])
            else:
                curve.setData([], [])
        vb = self.plot.getViewBox()
        self._syncing = True
        vb.setXRange(start, max(start + 1, end), padding=0)
        # y_range() 内部按 auto_y 自动计算或使用手动上下限
        y_min, y_max = st.y_range()
        vb.setYRange(y_min, y_max, padding=0)
        self._syncing = False

    def _on_manual_range(self):
        if self._syncing:
            return
        vb = self.plot.getViewBox()
        x0, x1 = vb.viewRange()[0]
        self.state.set_view_window(int(round(x0)), int(round(x1)))

    # ---------- 状态行 ----------

    def _update_stats(self):
        st = self.state
        now = time.monotonic()
        dt = max(now - self._fps_last_at, 1e-6)
        fps = int((st.fps_count - self._fps_last_count) / dt)
        self._fps_last_count = st.fps_count
        self._fps_last_at = now
        start, end, follow = st.view_range()
        mode = '跟随' if follow else f'历史 [{start}, {end}]'
        self.lbl_stats.setText(
            f'帧 {st.frame_total} | 解析失败 {st.fail_total} | FPS {fps} | 视图: {mode}')

    # ---------- 模拟数据（三协议通吃，走真实解码管线） ----------

    def _on_sim_tick(self):
        st = self.state
        self._sim_t += self.SIM_INTERVAL_MS / 1000.0
        t = self._sim_t
        if st.protocol == 'JustFloat':
            st.consume_bytes(struct.pack('<3f', math.sin(t), math.cos(t * 0.7) * 0.8,
                                         random.uniform(-0.3, 0.3)) + b'\x00\x00\x80\x7f')
        elif st.protocol == 'RawData':
            center = 128 + math.sin(t) * 100
            st.consume_bytes(bytes(
                max(0, min(255, int(center + random.uniform(-8, 8))))
                for _ in range(4)))
        else:
            vals = (math.sin(t), math.cos(t * 0.7) * 0.8, random.uniform(-0.3, 0.3))
            line = ','.join(f'{v:.3f}' for v in vals)
            st.consume_bytes((line + '\n').encode('ascii'))

    # ---------- 场景预设 ----------

    def refresh_presets(self, select: str = ''):
        self._preset_syncing = True
        try:
            self.combo_preset.clear()
            for p in load_wave_presets(self.presets_path):
                self.combo_preset.addItem(p.name)
            if select:
                self.combo_preset.setCurrentText(select)
        finally:
            self._preset_syncing = False

    def _on_preset_selected(self, name: str):
        if not name or getattr(self, '_preset_syncing', False):
            return
        self.apply_wave_preset(name)

    def apply_wave_preset(self, name: str) -> bool:
        """逐项写入 → 自动走现有信号处理器（协议切换清缓冲、滤波键失效、Y 手动生效）。
        波特率只改主窗体选择不强制重连（对齐主线 apply_wave_preset）。"""
        target = next((p for p in load_wave_presets(self.presets_path) if p.name == name), None)
        if target is None:
            return False
        if target.baud.isdigit():
            self.baud_setter(target.baud)
        if self.combo_protocol.findText(target.protocol) >= 0:
            self.combo_protocol.setCurrentText(target.protocol)
        if self.combo_filter.findText(target.filter) >= 0:
            self.combo_filter.setCurrentText(target.filter)
        if target.filter_param.isdigit():
            self.spin_filter_param.setValue(int(target.filter_param))
        self.chk_auto_y.setChecked(target.auto_y)
        if not target.auto_y:
            if target.y_min:
                self.edit_ymin.setText(target.y_min)
            if target.y_max:
                self.edit_ymax.setText(target.y_max)
            self._on_y_limits_edited()
        if target.window.isdigit():
            self.combo_window.setCurrentText(target.window)
        return True

    def capture_wave_preset(self, name: str) -> WavePreset:
        st = self.state
        return WavePreset(
            name=name,
            baud=self.baud_getter().strip(),
            protocol=self.combo_protocol.currentText(),
            filter=self.combo_filter.currentText(),
            filter_param=str(self.spin_filter_param.value()),
            auto_y=self.chk_auto_y.isChecked(),
            y_min=self.edit_ymin.text().strip(),
            y_max=self.edit_ymax.text().strip(),
            window=self.combo_window.currentText().strip())

    def _save_preset_clicked(self):
        default_name = self.combo_preset.currentText() or '新预设'
        name, ok = QtWidgets.QInputDialog.getText(self, '存预设', '预设名:', text=default_name)
        name = (name or '').strip()
        if not ok or not name:
            return
        presets = [p for p in load_wave_presets(self.presets_path) if p.name != name]
        presets.append(self.capture_wave_preset(name))
        if save_wave_presets(presets, self.presets_path):
            self.refresh_presets(select=name)

    def _delete_preset_clicked(self):
        name = self.combo_preset.currentText()
        if not name:
            return
        if QtWidgets.QMessageBox.question(
                self, '删预设', f'删除预设「{name}」?') != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        presets = [p for p in load_wave_presets(self.presets_path) if p.name != name]
        save_wave_presets(presets, self.presets_path)
        self.refresh_presets()

    # ---------- 数据格式说明 / AI 接入说明 ----------

    FORMAT_HELP_TEXT = """FireWater（文本协议，默认）
  每行输出各通道数值，逗号分隔，换行(\\n)结束一帧：
    printf("%.4f,%.4f\\n", ch0, ch1);
  示例行: 1.23,4.56
  支持负数/小数/科学计数法(1.5e-3)，单行最多 32 通道，
  行首 $ 视为命令行自动忽略，含 ANSI 颜色序列的行会先清洗再解析。

JustFloat（二进制协议）
  N 个 float32（小端）+ 固定帧尾 0x00 0x00 0x80 0x7F：
    float ch[3];
    ch[0]=1.2f; ch[1]=3.4f; ch[2]=5.6f;
    HAL_UART_Transmit(&huart,(uint8_t*)ch,sizeof(ch),10);
    uint8_t tail[4]={0x00,0x00,0x80,0x7F};
    HAL_UART_Transmit(&huart,tail,4,10);
  通道数 = 帧长/4，帧间通道数可以变化；帧尾丢失自动重同步。

RawData（原始字节流）
  每个字节(0~255)直接作为一个采样点，单通道连续波形：
    HAL_UART_Transmit(&huart,(uint8_t*)adc_buf,len,10);
  适合 ADC 等高速连续采样流，无帧头帧尾、无通道交织。

-------------------- 提示 --------------------
 1. 波形不动时先核对「协议」下拉框是否与设备输出一致；
    串口有数据但帧数为 0 时状态栏会出现"疑似协议不匹配"提示。
 2. 三种协议都在字节级解析，与接收格式(ASCII/HEX)显示互不影响；
    二进制协议在正文区显示为乱码属正常现象。
 3. 「模拟数据」按钮会按当前协议自动生成测试信号。"""

    def _show_format_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle('数据格式说明')
        dlg.resize(640, 520)
        layout = QtWidgets.QVBoxLayout(dlg)
        text = QtWidgets.QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(self.FORMAT_HELP_TEXT)
        text.setFont(QtGui.QFont('Consolas', 10))
        layout.addWidget(text, 1)

        buttons = QtWidgets.QHBoxLayout()
        btn_copy = QtWidgets.QPushButton('复制AI接入说明')
        btn_copy.setToolTip('按当前协议生成一段可直接粘给 AI 的接入要求文案')
        btn_copy.clicked.connect(lambda: self._copy_ai_doc(dlg))
        btn_close = QtWidgets.QPushButton('关闭')
        btn_close.clicked.connect(dlg.accept)
        buttons.addWidget(btn_copy)
        buttons.addStretch(1)
        buttons.addWidget(btn_close)
        layout.addLayout(buttons)
        dlg.exec()

    def _copy_ai_doc(self, dialog: QtWidgets.QDialog | None = None):
        doc = get_ai_format_doc(self.combo_protocol.currentText())
        QtWidgets.QApplication.clipboard().setText(doc)
        if dialog is not None:
            dialog.setWindowTitle('数据格式说明 — 已复制到剪贴板')
            QtCore.QTimer.singleShot(1500, lambda: dialog.setWindowTitle('数据格式说明'))

    # ---------- 导出 CSV ----------

    def _export_csv(self):
        st = self.state
        if not st.ch_values:
            QtWidgets.QMessageBox.information(self, '导出CSV', '还没有数据可导出。')
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, '导出CSV', 'wave.csv', 'CSV (*.csv)')
        if not path:
            return
        try:
            self.write_csv(path)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, '导出CSV', f'导出失败: {exc}')

    def write_csv(self, path: str):
        """全量缓冲导出：表头为通道显示名（utf-8-sig，Excel 直开不乱码）。"""
        st = self.state
        n_channels = len(st.ch_values)
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['frame'] + [st.channel_display_name(c) for c in range(n_channels)])
            for row in range(st.frame_count):
                writer.writerow([row] + [st.ch_values[c][row] for c in range(n_channels)])

    # ---------- 收尾 ----------

    def stop_timers(self):
        self._repaint_timer.stop()
        self._stats_timer.stop()
        self._sim_timer.stop()
        self.xy_section.stop()

    def showEvent(self, event):  # noqa: N802 (Qt 命名)
        self._restore_channel_names()
        super().showEvent(event)
