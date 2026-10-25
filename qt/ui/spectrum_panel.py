# -*- coding: utf-8 -*-
"""频谱仪页签 —— 复刻主线频谱插件：FFT dB 谱 + 峰值保持/指数平均 + RBW + 瀑布图 + Marker。

数据源走波形缓冲快照（snapshot 插件合同），分析管线逐字对齐主线
get_spectrum_analysis：手动 RBW 由 fs/RBW 反推 FFT 长度（512..65536）、
参数变化重置轨迹与瀑布、Δf/RBW(ENBW=1.5·fs/n 汉宁窗) 口径一致。
冻结 = 停止分析更新（底层缓冲照常）。
"""
from __future__ import annotations

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

import pyqtgraph as pg

from core.dsp import (color_map, magnitude_spectrum, max_bin, to_db,
                      update_ema, update_max_hold, waterfall_row)

TRACE_COLOR = '#78D2FF'
PEAK_COLOR = '#A0C85A'
AVG_COLOR = '#50BEC8'
PEAK_HOLD_COLOR = '#FFC85A'

POINTS_ITEMS = ('512', '1024', '2048', '4096', '8192', '16384', '32768')
RBW_ITEMS = ('自动', '500', '250', '100', '50', '25', '10', '5', '1')
DIV_ITEMS = ('2', '5', '10', '20')
WF_MAX_ROWS = 120


class SpectrumPanel(QtWidgets.QWidget):
    REPAINT_INTERVAL_MS = 100

    def __init__(self, wave_state, parent=None):
        super().__init__(parent)
        self.wave = wave_state
        self.frozen = False
        self._key = ''                 # points|fs|channel —— 变化即重置轨迹/瀑布
        self._max_hold: np.ndarray | None = None
        self._avg_db: np.ndarray | None = None
        self._waterfall: list[np.ndarray] = []
        self._wf_bins = 0

        # ---- 参数行 ----
        self.combo_ch = QtWidgets.QComboBox()
        self.combo_pts = QtWidgets.QComboBox()
        self.combo_pts.addItems(POINTS_ITEMS)
        self.combo_pts.setCurrentText('2048')
        self.edit_fs = QtWidgets.QLineEdit('1000')
        self.edit_fs.setMaximumWidth(80)
        self.edit_fs.setToolTip('采样率 Hz = 帧率 × 每帧点数（决定频率轴）')
        self.combo_rbw = QtWidgets.QComboBox()
        self.combo_rbw.addItems(RBW_ITEMS)
        self.combo_rbw.setToolTip('手动 RBW：由 采样率/RBW 反推 FFT 长度（2 的幂）')

        prow = QtWidgets.QHBoxLayout()
        prow.addWidget(QtWidgets.QLabel('通道'))
        prow.addWidget(self.combo_ch)
        prow.addWidget(QtWidgets.QLabel('点数'))
        prow.addWidget(self.combo_pts)
        prow.addWidget(QtWidgets.QLabel('采样率'))
        prow.addWidget(self.edit_fs)
        prow.addWidget(QtWidgets.QLabel('RBW'))
        prow.addWidget(self.combo_rbw)
        prow.addStretch(1)

        # ---- 显示行 ----
        self.edit_ref = QtWidgets.QLineEdit('0')
        self.edit_ref.setMaximumWidth(70)
        self.edit_ref.setToolTip('参考电平 dB：y 轴顶格')
        self.combo_div = QtWidgets.QComboBox()
        self.combo_div.addItems(DIV_ITEMS)
        self.combo_div.setCurrentText('10')
        self.chk_peak = QtWidgets.QCheckBox('峰值保持')
        self.chk_avg = QtWidgets.QCheckBox('平均')
        self.chk_wf = QtWidgets.QCheckBox('瀑布')
        self.chk_wf.setChecked(True)
        self.chk_log = QtWidgets.QCheckBox('对数色标')
        self.btn_freeze = QtWidgets.QPushButton('冻结')
        self.btn_freeze.setObjectName('tool')
        self.btn_freeze.setCheckable(True)
        self.btn_freeze.toggled.connect(self._on_freeze_toggled)

        drow = QtWidgets.QHBoxLayout()
        drow.addWidget(QtWidgets.QLabel('参考电平'))
        drow.addWidget(self.edit_ref)
        drow.addWidget(QtWidgets.QLabel('dB/格'))
        drow.addWidget(self.combo_div)
        drow.addWidget(self.chk_peak)
        drow.addWidget(self.chk_avg)
        drow.addWidget(self.chk_wf)
        drow.addWidget(self.chk_log)
        drow.addWidget(self.btn_freeze)
        drow.addStretch(1)

        # ---- 谱图 ----
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.setLabel('bottom', '频率', units='Hz')
        self.plot.setLabel('left', '幅度', units='dB')
        self._trace = self.plot.plot([], [], pen=pg.mkPen(TRACE_COLOR, width=1.6),
                                     fillLevel=0, fillBrush=pg.mkBrush(120, 210, 255, 30))
        self._peak_trace = self.plot.plot([], [], pen=pg.mkPen(PEAK_COLOR, width=1.0))
        self._avg_trace = self.plot.plot([], [], pen=pg.mkPen(AVG_COLOR, width=1.0))
        self._marker_line = pg.InfiniteLine(angle=90, movable=False,
                                            pen=pg.mkPen((255, 150, 150), width=1,
                                                         style=QtCore.Qt.DashLine))
        self._marker_line.hide()
        self.plot.addItem(self._marker_line)
        self._marker_text = pg.TextItem(color=(215, 222, 235), anchor=(0, 0))
        self._marker_text.hide()
        self.plot.addItem(self._marker_text)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

        # ---- 瀑布 ----
        self.wf_plot = pg.PlotWidget()
        self.wf_plot.setMouseEnabled(x=False, y=False)
        self.wf_plot.getAxis('bottom').setLabel('频率 (Hz)')
        self.wf_plot.setLimits(yMin=0, yMax=WF_MAX_ROWS)
        # 主线语义：最新帧在顶。数据 row0=最新，pyqtgraph 默认 y=0 在底，必须反转 y 轴
        self.wf_plot.invertY(True)
        self._wf_image = pg.ImageItem()
        self.wf_plot.addItem(self._wf_image)
        self._wf_image.setLookupTable(
            np.array([list(color_map(i / 256)) for i in range(257)], dtype=np.uint8))
        self._wf_image.setLevels([0, 255])
        self._wf_image.setAutoDownsample(True)

        self.lbl_peak = QtWidgets.QLabel('主频: --')
        self.lbl_info = QtWidgets.QLabel('')

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addLayout(prow)
        layout.addLayout(drow)
        layout.addWidget(self.plot, 3)
        layout.addWidget(self.wf_plot, 1)
        layout.addWidget(self.lbl_peak)
        layout.addWidget(self.lbl_info)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.repaint_spectrum)
        self._timer.start(self.REPAINT_INTERVAL_MS)

    # ---------- 分析（对齐主线 get_spectrum_analysis） ----------

    def _read_scale(self):
        """参考电平 / dB每格。只影响显示标度，不参与 FFT。"""
        try:
            ref = float(self.edit_ref.text())
        except ValueError:
            ref = 0.0
        try:
            div = float(self.combo_div.currentText())
        except ValueError:
            div = 10.0
        if div <= 0 or div > 50:
            div = 10.0
        return ref, div, ref - 10.0 * div

    def analyze(self):
        """拉数据 → FFT → dB。无数据返回 None；参数变化重置轨迹/瀑布。"""
        channel = max(0, self.combo_ch.currentIndex())
        try:
            points = int(self.combo_pts.currentText())
        except ValueError:
            points = 2048
        if points < 64:
            points = 2048
        try:
            fs = float(self.edit_fs.text())
        except ValueError:
            fs = 1000.0
        if fs <= 0:
            fs = 1000.0

        # 手动 RBW：由 采样率/RBW 反推 FFT 长度（2 的幂，512..65536）
        try:
            rbw_manual = float(self.combo_rbw.currentText())
        except ValueError:
            rbw_manual = 0.0
        if rbw_manual > 0:
            n = max(1.0, fs / rbw_manual)
            points = max(512, min(65536, 2 ** int(round(np.log2(n)))))

        key = f'{points}|{fs}|{channel}'
        samples = self.wave.snapshot(channel, points)
        if len(samples) < 32:
            return None
        mag, used = magnitude_spectrum(samples)
        if used <= 0:
            return None
        db = to_db(mag)

        if self._key != key:
            # 参数变化 → 轨迹与瀑布重置（对齐主线）
            self._key = key
            self._max_hold = None
            self._avg_db = None
            self._waterfall = []
            self._wf_bins = 0

        if self.chk_peak.isChecked():
            if self._max_hold is None or self._max_hold.size != db.size:
                self._max_hold = np.full(db.size, -140.0)
            update_max_hold(db, self._max_hold)
        if self.chk_avg.isChecked():
            if self._avg_db is None or self._avg_db.size != db.size:
                self._avg_db = db.copy()
            else:
                update_ema(db, self._avg_db, 0.25)

        return {'db': db, 'used': used, 'fs': fs,
                'df': fs / used,                    # bin 间隔
                'rbw': 1.5 * fs / used,             # ENBW（汉宁窗等效噪声带宽）
                'key': key, 'channel': channel}

    # ---------- 绘制 ----------

    def repaint_spectrum(self):
        self._sync_channels()
        self.wf_plot.setVisible(self.chk_wf.isChecked())
        if self.frozen:
            return
        analysis = self.analyze()
        ref, div, bottom = self._read_scale()
        self.plot.setYRange(bottom, ref, padding=0)

        if analysis is None:
            self._trace.setData([], [])
            self._peak_trace.setData([], [])
            self._avg_trace.setData([], [])
            self.lbl_peak.setText('主频: --')
            self.lbl_info.setText('等待数据…（连接串口 / 波形页开模拟数据）')
            return

        db = analysis['db']
        used = analysis['used']
        fs = analysis['fs']
        freqs = np.arange(db.size) * (fs / used)
        self._trace.setData(freqs, db)
        if self.chk_peak.isChecked() and self._max_hold is not None:
            self._peak_trace.setData(freqs, self._max_hold)
        else:
            self._peak_trace.setData([], [])
        if self.chk_avg.isChecked() and self._avg_db is not None:
            self._avg_trace.setData(freqs, self._avg_db)
        else:
            self._avg_trace.setData([], [])

        # 主频标注（跳过 DC）
        idx = max_bin(db, 1)
        peak_freq = idx * fs / used
        self.lbl_peak.setText(f'主频: {peak_freq:.2f} Hz @ {db[idx]:.1f} dB')
        self.lbl_info.setText(
            f"Δf {analysis['df']:.3f} Hz | RBW(ENBW) {analysis['rbw']:.3f} Hz"
            f" | 采样率 {fs:.0f} Hz | {used} 点 FFT（汉宁窗）"
            f" | 通道 {self.wave.channel_display_name(analysis['channel'])}")

        self._repaint_waterfall(analysis, bottom, ref)

    def _repaint_waterfall(self, analysis, bottom, ref):
        if not self.chk_wf.isChecked():
            return
        gamma = 0.5 if self.chk_log.isChecked() else 1.0
        row = waterfall_row(analysis['db'], bottom, ref, gamma)
        if self._wf_bins != row.shape[0]:
            self._waterfall = []
            self._wf_bins = row.shape[0]
        self._waterfall.insert(0, row)
        while len(self._waterfall) > WF_MAX_ROWS:
            self._waterfall.pop()
        if len(self._waterfall) < 2:
            return
        # 列表 index 0 = 最新一行 → 图像顶部；旧行被顶向下方（与主线一致）
        img = np.stack(self._waterfall)
        self._wf_image.setImage(img, autoLevels=False)
        self._wf_image.setRect(QtCore.QRectF(0, 0, analysis['fs'] / 2.0, len(self._waterfall)))
        self.wf_plot.setXRange(0, analysis['fs'] / 2.0, padding=0)
        self.wf_plot.setYRange(0, len(self._waterfall), padding=0)

    def _sync_channels(self):
        count = len(self.wave.ch_values)
        if self.combo_ch.count() != count:
            current = self.combo_ch.currentIndex()
            self.combo_ch.clear()
            for i in range(count):
                self.combo_ch.addItem(self.wave.channel_display_name(i))
            self.combo_ch.setCurrentIndex(current if 0 <= current < count else 0)

    # ---------- Marker / 冻结 ----------

    def _on_mouse_moved(self, pos):
        if not self.plot.sceneBoundingRect().contains(pos):
            self._marker_line.hide()
            self._marker_text.hide()
            return
        vb = self.plot.getViewBox()
        x, _ = vb.mapSceneToView(pos).x(), 0
        analysis = self.analyze()
        if analysis is None:
            return
        fs = analysis['fs']
        used = analysis['used']
        freq = max(0.0, min(x, fs / 2.0))
        idx = int(round(freq / analysis['df']))
        idx = max(0, min(idx, analysis['db'].size - 1))
        self._marker_line.setPos(freq)
        self._marker_line.show()
        text = f'{idx * fs / used:.2f} Hz  {analysis["db"][idx]:.1f} dB'
        self._marker_text.setText(text)
        self._marker_text.setPos(freq, self._read_scale()[0])
        self._marker_text.show()

    def _on_freeze_toggled(self, frozen: bool):
        self.frozen = frozen
        self.btn_freeze.setText('继续' if frozen else '冻结')

    def stop_timers(self):
        self._timer.stop()
