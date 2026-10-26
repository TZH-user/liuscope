# -*- coding: utf-8 -*-
"""频谱仪页签 —— 复刻主线频谱插件：FFT dB 谱 + 峰值保持/指数平均 + RBW + 瀑布图 + Marker。

数据源走波形缓冲快照（snapshot 插件合同），分析管线逐字对齐主线
get_spectrum_analysis：手动 RBW 由 fs/RBW 反推 FFT 长度（512..65536）、
参数变化重置轨迹与瀑布、Δf/RBW(ENBW=1.5·fs/n 汉宁窗) 口径一致。
冻结 = 停止分析更新（底层缓冲照常）。

本页新增：
- 频率轴联动：谱图与瀑布共用 (0, fs/2) 频率坐标（手动双向同步），任一图
  缩放/平移另一图跟随，同一频点两图垂直对齐（瀑布 ImageItem 仍按
  数组第0轴=X 转置 + invertY 实现「最新在顶」，勿回退）
- 导出PNG：谱图 + 瀑布（勾选时）纵向拼图，QPainter 在顶部标注采样率/
  FFT 长度/窗函数/RBW(ENBW)/峰值保持/平均/数据时间范围等
- 导出CSV：对话框二选一「最新一帧瞬时频点」/「最近 N 秒」；N 超过累计
  采集时长自动钳制并在界面提示；采集时长按帧时间戳累计，属性
  acquisition_seconds / last_analysis 供 AI 通道程序化读取
- 清空：轨迹/峰值保持/平均/瀑布/导出历史全部复位，采集时长清零
  （不动波形页共享缓冲，那边有自己的「清空」）
"""
from __future__ import annotations

import csv
import time
from datetime import datetime

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

import pyqtgraph as pg

from core.dsp import (color_map, magnitude_spectrum, max_bin, to_db,
                      update_ema, update_max_hold, waterfall_row)
from ui import theme

# 深色基准轨迹配色（色板定义在 theme.py，浅色套件整体加深保证白底对比度）
TRACE_COLOR = '#78D2FF'
PEAK_COLOR = '#A0C85A'
AVG_COLOR = '#50BEC8'
PEAK_HOLD_COLOR = '#FFC85A'

POINTS_ITEMS = ('512', '1024', '2048', '4096', '8192', '16384', '32768')
RBW_ITEMS = ('自动', '500', '250', '100', '50', '25', '10', '5', '1')
DIV_ITEMS = ('2', '5', '10', '20')
WF_MAX_ROWS = 120
HIST_MAX_FRAMES = 6000        # 导出CSV 历史帧数上限（10Hz 刷新 ≈ 10 分钟）
HIST_MAX_CELLS = 20_000_000   # 导出CSV 历史总单元格上限（防 32k 点长时爆内存）
PNG_MARGIN = 12               # PNG 标注条 / 拼图边距
PNG_LINE_GAP = 6


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

        # ---- 采集时长 / 导出历史（帧时间戳累计，供 CSV「最近 N 秒」与 AI 通道） ----
        self._now_fn = time.monotonic
        self._t_first: float | None = None   # 首个有效帧时间戳
        self._t_last: float | None = None    # 最近一个有效帧时间戳
        self._hist: list[tuple[float, np.ndarray]] = []  # (时间戳, dB 谱 float32)
        self._hist_bins = 0
        self._hist_fs = 0.0
        self._hist_used = 0
        self._last_analysis: dict | None = None
        self._just_cleared = False           # 清空提示保持到下一帧有效数据
        self._x_span: float | None = None    # 已统一过的频率轴满量程（fs）
        self._x_syncing = False              # 频率轴双向联动防递归

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
        self.chk_wf_flip = QtWidgets.QCheckBox('最新在底')
        self.chk_wf_flip.setToolTip('不勾选=最新帧在顶部（主线口径）；勾选=最新帧在底部（新内容向下滚动）')
        self.chk_log = QtWidgets.QCheckBox('对数色标')
        self.btn_freeze = QtWidgets.QPushButton('冻结')
        self.btn_freeze.setObjectName('tool')
        self.btn_freeze.setCheckable(True)
        self.btn_freeze.toggled.connect(self._on_freeze_toggled)

        self.btn_png = QtWidgets.QPushButton('导出PNG')
        self.btn_png.setObjectName('tool')
        self.btn_png.setToolTip('导出当前频谱图+瀑布图（勾选「瀑布」时）拼图为 PNG，'
                                '顶部标注采样率/FFT长度/窗函数/RBW(ENBW)/峰值保持/平均/数据时间范围；'
                                '不含控件行')
        self.btn_png.clicked.connect(self._export_png)
        self.btn_csv = QtWidgets.QPushButton('导出CSV')
        self.btn_csv.setObjectName('tool')
        self.btn_csv.setToolTip('导出CSV：可选「最新一帧瞬时频点」或「最近 N 秒」；'
                                'N 超过累计采集时长自动钳制并提示')
        self.btn_csv.clicked.connect(self._export_csv)
        self.btn_clear = QtWidgets.QPushButton('清空')
        self.btn_clear.setObjectName('tool')
        self.btn_clear.setToolTip('清空频谱轨迹/峰值保持/瀑布/导出缓冲并复位采集时长'
                                  '（不清波形页缓冲）')
        self.btn_clear.clicked.connect(self.clear_spectrum)

        drow = QtWidgets.QHBoxLayout()
        drow.addWidget(QtWidgets.QLabel('参考电平'))
        drow.addWidget(self.edit_ref)
        drow.addWidget(QtWidgets.QLabel('dB/格'))
        drow.addWidget(self.combo_div)
        drow.addWidget(self.chk_peak)
        drow.addWidget(self.chk_avg)
        drow.addWidget(self.chk_wf)
        drow.addWidget(self.chk_wf_flip)
        drow.addWidget(self.chk_log)
        drow.addWidget(self.btn_freeze)
        drow.addWidget(self.btn_png)
        drow.addWidget(self.btn_csv)
        drow.addWidget(self.btn_clear)
        drow.addStretch(1)

        # ---- 谱图 ----
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        # x 允许鼠标缩放/平移（与瀑布双向联动）；y 仍由 参考电平/dB每格 标定
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.setLimits(xMin=0)
        self.plot.setLabel('bottom', '频率', units='Hz')
        self.plot.setLabel('left', '幅度', units='dB')
        self._trace = self.plot.plot([], [], pen=pg.mkPen(TRACE_COLOR, width=1.6),
                                     fillLevel=0, fillBrush=pg.mkBrush(120, 210, 255, 30))
        self._peak_trace = self.plot.plot([], [], pen=pg.mkPen(PEAK_COLOR, width=1.0))
        self._avg_trace = self.plot.plot([], [], pen=pg.mkPen(AVG_COLOR, width=1.0))
        self._marker_line = pg.InfiniteLine(angle=90, movable=False,
                                            pen=pg.mkPen((255, 150, 150), width=1,
                                                         style=QtCore.Qt.PenStyle.DashLine))
        self._marker_line.hide()
        self.plot.addItem(self._marker_line)
        self._marker_text = pg.TextItem(color=theme.palette()['text'], anchor=(0, 0))
        self._marker_text.hide()
        self.plot.addItem(self._marker_text)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

        # ---- 瀑布 ----
        self.wf_plot = pg.PlotWidget()
        self.wf_plot.setMouseEnabled(x=True, y=False)   # x 与谱图双向联动
        self.wf_plot.getAxis('bottom').setLabel('频率 (Hz)')
        self.wf_plot.setLimits(yMin=0, yMax=WF_MAX_ROWS, xMin=0)
        # 主线语义：最新帧在顶。数据 row0=最新，pyqtgraph 默认 y=0 在底，必须反转 y 轴
        self.wf_plot.invertY(True)
        self._wf_image = pg.ImageItem()
        self.wf_plot.addItem(self._wf_image)
        self._wf_image.setLookupTable(
            np.array([list(color_map(i / 256)) for i in range(257)], dtype=np.uint8))
        self._wf_image.setLevels([0, 255])
        self._wf_image.setAutoDownsample(True)

        # 频率轴双向联动：任一图缩放/平移，另一图跟随（_x_syncing 防递归）
        self.plot.getViewBox().sigXRangeChanged.connect(self._on_x_range_changed)
        self.wf_plot.getViewBox().sigXRangeChanged.connect(self._on_x_range_changed)

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

        # 主题联动：按当前主题同步一次（启动即浅色的场景），此后随 set_theme 通知刷新
        self.apply_theme()
        theme.register_listener(self.apply_theme)

    # ---------- 主题联动 ----------

    def apply_theme(self, name: str | None = None):
        """主题切换回调（theme.set_theme 通知）：绘图区背景/坐标轴与轨迹配色随主题。"""
        theme.style_plot_widget(self.plot, name)
        theme.style_plot_widget(self.wf_plot, name)
        p = theme.palette(name)
        self._trace.setPen(pg.mkPen(p['sp_trace'], width=1.6))
        self._trace.setFillBrush(pg.mkBrush(p['sp_fill']))
        self._peak_trace.setPen(pg.mkPen(p['sp_peak'], width=1.0))
        self._avg_trace.setPen(pg.mkPen(p['sp_avg'], width=1.0))
        self._marker_line.setPen(pg.mkPen(p['sp_marker'], width=1,
                                          style=QtCore.Qt.PenStyle.DashLine))
        self._marker_text.setColor(p['text'])

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

    def _parse_params(self):
        """读参数行：通道/点数/采样率/手动 RBW（由 采样率/RBW 反推 FFT 长度，2 的幂）。"""
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
        try:
            rbw_manual = float(self.combo_rbw.currentText())
        except ValueError:
            rbw_manual = 0.0
        if rbw_manual > 0:
            n = max(1.0, fs / rbw_manual)
            points = max(512, min(65536, 2 ** int(round(np.log2(n)))))
        return channel, points, fs

    def _compute(self):
        """纯计算：拉数据 → FFT → dB，无副作用（Marker 用，不累计时间戳/历史）。"""
        channel, points, fs = self._parse_params()
        key = f'{points}|{fs}|{channel}'
        samples = self.wave.snapshot(channel, points)
        if len(samples) < 32:
            return None
        mag, used = magnitude_spectrum(samples)
        if used <= 0:
            return None
        db = to_db(mag)
        return {'db': db, 'used': used, 'fs': fs,
                'df': fs / used,                    # bin 间隔
                'rbw': 1.5 * fs / used,             # ENBW（汉宁窗等效噪声带宽）
                'window': '汉宁窗', 'key': key, 'channel': channel}

    def analyze(self):
        """拉数据 → FFT → dB → 累计（帧时间戳/导出历史/峰值保持/平均）。无数据返回 None。

        参数变化重置轨迹/瀑布/导出历史（对齐主线）。"""
        analysis = self._compute()
        if analysis is None:
            return None
        db = analysis['db']
        if self._key != analysis['key']:
            # 参数变化 → 轨迹与瀑布重置（对齐主线），导出历史一并作废
            self._key = analysis['key']
            self._max_hold = None
            self._avg_db = None
            self._waterfall = []
            self._wf_bins = 0
            self._hist = []
        if self.chk_peak.isChecked():
            if self._max_hold is None or self._max_hold.size != db.size:
                self._max_hold = np.full(db.size, -140.0)
            update_max_hold(db, self._max_hold)
        if self.chk_avg.isChecked():
            if self._avg_db is None or self._avg_db.size != db.size:
                self._avg_db = db.copy()
            else:
                update_ema(db, self._avg_db, 0.25)

        # 帧时间戳累计：每次分析到有效帧记一笔，采集时长 = 首末帧时间戳之差
        now = self._now_fn()
        if self._t_first is None:
            self._t_first = now
        self._t_last = now
        # 导出历史（「最近 N 秒」CSV 用）：float32 存 dB 谱，超上限丢最旧
        if self._hist_bins != db.size:
            # bin 数变化（缓冲尚未吃满 / 点数切换）→ 历史帧尺寸不一致，作废重计
            self._hist = []
            self._hist_bins = db.size
        self._hist.append((now, db.astype(np.float32)))
        while len(self._hist) > self._hist_cap(db.size):
            self._hist.pop(0)
        self._hist_fs = analysis['fs']
        self._hist_used = analysis['used']
        self._last_analysis = analysis
        return analysis

    @staticmethod
    def _hist_cap(bins: int) -> int:
        """导出历史帧数上限：常数帧数上限与总单元格预算取小（防 32k 点长时间爆内存）。"""
        return max(60, min(HIST_MAX_FRAMES, HIST_MAX_CELLS // max(1, bins)))

    @property
    def acquisition_seconds(self) -> float:
        """累计采集时长（s）= 首末有效帧时间戳之差；清空复位。供 AI 通道程序化读取。"""
        if self._t_first is None or self._t_last is None:
            return 0.0
        return max(0.0, self._t_last - self._t_first)

    @property
    def last_analysis(self) -> dict | None:
        """最近一帧分析 dict（db/used/fs/df/rbw/window/key/channel），无数据为 None。"""
        return self._last_analysis

    # ---------- 频率轴联动 ----------

    def _reset_x_span(self, fs: float):
        """频率轴满量程 (0, fs/2)：fs 变化或清空后重新出现数据时重置一次，
        其余时候保留用户缩放/平移（瀑布经联动自动跟随）。"""
        if self._x_span == fs:
            return
        self._x_span = fs
        self.plot.getViewBox().setXRange(0.0, fs / 2.0, padding=0)

    def _ensure_x_sync(self):
        """兜底：瀑布刚勾选显示/范围漂移时，强制回到谱图当前 x 范围（垂直对齐）。"""
        if not self.chk_wf.isChecked() or self._x_syncing:
            return
        (x0, x1), _ = self.plot.getViewBox().viewRange()
        (wx0, wx1), _ = self.wf_plot.getViewBox().viewRange()
        if abs(wx0 - x0) > 1e-9 or abs(wx1 - x1) > 1e-9:
            self.wf_plot.getViewBox().setXRange(x0, x1, padding=0)

    def _on_x_range_changed(self, *_):
        """双向联动槽：谁改了 x 范围就抄给另一图（同一频点两图垂直对齐）。"""
        if self._x_syncing:
            return
        self._x_syncing = True
        try:
            plot_vb, wf_vb = self.plot.getViewBox(), self.wf_plot.getViewBox()
            src, dst = (wf_vb, plot_vb) if self.sender() is wf_vb else (plot_vb, wf_vb)
            (x0, x1), _ = src.viewRange()
            dst.setXRange(x0, x1, padding=0)
        finally:
            self._x_syncing = False

    # ---------- 绘制 ----------

    def repaint_spectrum(self):
        self._sync_channels()
        self.wf_plot.setVisible(self.chk_wf.isChecked())
        if self.frozen:
            return
        analysis = self.analyze()
        ref, div, bottom = self._read_scale()
        self.plot.setYRange(bottom, ref, padding=0)
        if analysis is not None:
            self._reset_x_span(analysis['fs'])
        self._ensure_x_sync()

        if analysis is None:
            self._trace.setData([], [])
            self._peak_trace.setData([], [])
            self._avg_trace.setData([], [])
            self.lbl_peak.setText('主频: --')
            # 清空提示保持显示，直到下一帧有效数据到来（避免 100ms 后被冲掉）
            if not self._just_cleared:
                self.lbl_info.setText('等待数据…（连接串口 / 波形页开模拟数据）')
            return
        self._just_cleared = False

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
            f" | 通道 {self.wave.channel_display_name(analysis['channel'])}"
            f" | 累计采集 {self.acquisition_seconds:.1f} s")

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
        # pyqtgraph ImageItem 数组第0轴=X(频率bin)、第1轴=Y(时间行)——必须转置！
        # _waterfall[0]=最新；invertY(True) 视图下时间行0渲染在屏幕顶部，
        # 因此默认转置后即"最新在顶"（主线口径）；勾"最新在底"则只翻时间轴。
        img = np.ascontiguousarray(np.stack(self._waterfall).transpose(1, 0, 2))
        if self.chk_wf_flip.isChecked():
            img = np.ascontiguousarray(img[:, ::-1, :])
        self._wf_image.setImage(img, autoLevels=False)
        self._wf_image.setRect(QtCore.QRectF(0, 0, analysis['fs'] / 2.0, len(self._waterfall)))
        # x 轴不再每帧强制满量程：由 _reset_x_span/_on_x_range_changed 统一管理（与谱图联动）
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
        analysis = self._compute()   # 纯计算：Marker 读数不累计时间戳/历史
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

    # ---------- 导出 PNG ----------

    def _export_png(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, '导出PNG', 'spectrum.png', 'PNG 图片 (*.png)')
        if not path:
            return
        try:
            self.export_png(path)
        except (OSError, RuntimeError) as exc:
            QtWidgets.QMessageBox.warning(self, '导出PNG', f'导出失败: {exc}')
            return
        self.lbl_info.setText(f'已导出 PNG: {path}')

    def export_png(self, path: str) -> list[str]:
        """导出 PNG：频谱图 + 瀑布图（勾选「瀑布」时）纵向拼图，顶部用 QPainter
        标注当前参数（采样率/FFT 长度/窗函数/RBW(ENBW)/峰值保持/平均/数据时间范围）。

        口径：不含控件行与主频/信息标签；返回标注文本行（供测试与 AI 通道核对）。
        """
        plots = [self.plot.grab()]
        if self.chk_wf.isChecked():
            plots.append(self.wf_plot.grab())
        plots = [p for p in plots if p.width() >= 10 and p.height() >= 10]
        if not plots:
            raise OSError('频谱图渲染为空，无法导出')
        lines = self._annotation_lines()
        font = self.font()
        fm = QtGui.QFontMetrics(font)
        line_h = fm.height() + PNG_LINE_GAP
        ann_h = line_h * len(lines) + PNG_MARGIN
        width = max(p.width() for p in plots) + PNG_MARGIN * 2
        height = ann_h + sum(p.height() + PNG_MARGIN for p in plots)
        img = QtGui.QImage(width, height, QtGui.QImage.Format_RGB32)
        img.fill(QtGui.QColor(theme.color('window')))   # 标注条底随主题
        painter = QtGui.QPainter(img)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(theme.color('text')))
        y = PNG_MARGIN
        for line in lines:
            painter.drawText(PNG_MARGIN, y + fm.ascent(), line)
            y += line_h
        y = ann_h
        for pix in plots:
            painter.drawPixmap((width - pix.width()) // 2, y, pix)
            y += pix.height() + PNG_MARGIN
        painter.end()
        if not img.save(path, 'PNG'):
            raise OSError(f'PNG 写入失败: {path}')
        return lines

    def _annotation_lines(self) -> list[str]:
        """PNG 顶部参数标注：来自最近一次 analysis dict + 面板当前状态。"""
        lines = [f'频谱导出  {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}']
        a = self._last_analysis
        if a is None:
            lines.append('无有效频谱数据（未采集到数据或刚清空）')
            return lines
        acq = self.acquisition_seconds
        lines.append(
            f"采样率 {a['fs']:.0f} Hz | FFT 长度 {a['used']} 点 | 窗函数 {a['window']}"
            f" | Δf {a['df']:.3f} Hz | RBW(ENBW) {a['rbw']:.3f} Hz"
            f" | 通道 {self.wave.channel_display_name(a['channel'])}")
        lines.append(
            f"峰值保持 {'开' if self.chk_peak.isChecked() else '关'}"
            f" | 平均 {'开' if self.chk_avg.isChecked() else '关'}"
            f" | 瀑布 {'开' if self.chk_wf.isChecked() else '关'}"
            f" | 参考电平 {self._read_scale()[0]:.0f} dB"
            f" | {self.combo_div.currentText()} dB/格")
        lines.append(f'数据时间范围 0.0 – {acq:.1f} s（累计采集 {acq:.1f} s）')
        idx = max_bin(a['db'], 1)
        lines.append(f"主频 {idx * a['fs'] / a['used']:.2f} Hz @ {a['db'][idx]:.1f} dB")
        return lines

    # ---------- 导出 CSV ----------

    def _export_csv(self):
        if self._last_analysis is None and not self._hist:
            QtWidgets.QMessageBox.information(self, '导出CSV', '还没有可导出的频谱数据。')
            return
        dlg = _ExportCsvDialog(self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        mode, seconds = dlg.result_choice()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, '导出CSV', 'spectrum.csv', 'CSV (*.csv)')
        if not path:
            return
        try:
            self.export_csv(path, mode=mode, seconds=seconds)
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self, '导出CSV', f'导出失败: {exc}')

    def export_csv(self, path: str, mode: str = 'latest', seconds: float = 10.0) -> dict:
        """导出 CSV（对话框与 AI 通道共用的程序化入口）。

        mode='latest'：最新一帧瞬时频点（频率 Hz + dB）。
        mode='window'：最近 N 秒（按帧时间戳回溯：时间 s + 频率 Hz + dB；
        N 超过累计采集时长自动钳制到采集时长）。
        返回 {'mode','rows','frames','seconds_eff','clamped','note'}，
        并把结果写进 lbl_info 作界面提示。
        """
        if mode == 'latest':
            rows, frames, eff, clamped = self._write_csv_latest(path)
            label = '最新一帧'
        elif mode == 'window':
            rows, frames, eff, clamped = self._write_csv_window(path, seconds)
            label = f'最近 {eff:.1f} 秒'
        else:
            raise ValueError(f'未知导出模式: {mode}')
        note = f'已导出 CSV: {path}（{label}，{frames} 帧 / {rows} 行）'
        if clamped:
            note += f'——N 超过累计采集时长，已钳制到 {eff:.1f} s'
        self.lbl_info.setText(note)
        return {'mode': mode, 'rows': rows, 'frames': frames,
                'seconds_eff': eff, 'clamped': clamped, 'note': note}

    def _write_csv_latest(self, path: str):
        a = self._last_analysis
        if a is None:
            raise OSError('没有最新一帧频谱可导出')
        freqs = np.arange(a['db'].size) * a['df']
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow([f"# 频谱导出·最新一帧  采样率 {a['fs']:.0f} Hz | FFT {a['used']} 点"
                        f" | {a['window']} | RBW(ENBW) {a['rbw']:.3f} Hz"])
            w.writerow(['频率Hz', 'dB'])
            for fq, val in zip(freqs.tolist(), a['db'].tolist()):
                w.writerow([f'{fq:.6f}', f'{val:.3f}'])
        return freqs.size, 1, self.acquisition_seconds, False

    def _write_csv_window(self, path: str, seconds: float):
        if not self._hist:
            raise OSError('没有频谱历史可导出（还没采集到数据或刚清空）')
        acq = self.acquisition_seconds
        eff = max(0.0, float(seconds))
        clamped = False
        if eff > acq:
            eff, clamped = acq, True     # N 超过实际采集时长 → 钳制到采集时长
        bins = self._hist[0][1].size
        frames = [(ts, db) for ts, db in self._hist
                  if db.size == bins and ts >= self._t_last - eff - 1e-9]
        freqs = np.arange(bins) * (self._hist_fs / max(1, self._hist_used))
        rows = 0
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow([f"# 频谱导出·最近 {eff:.1f} 秒  采样率 {self._hist_fs:.0f} Hz"
                        f" | FFT {self._hist_used} 点 | 汉宁窗 | 共 {len(frames)} 帧"])
            w.writerow(['时间s', '频率Hz', 'dB'])
            for ts, db in frames:
                t_rel = ts - self._t_first
                for i, val in enumerate(db.tolist()):
                    w.writerow([f'{t_rel:.3f}', f'{freqs[i]:.6f}', f'{val:.3f}'])
                rows += db.size
        return rows, len(frames), eff, clamped

    # ---------- 清空 ----------

    def clear_spectrum(self):
        """清空：频谱轨迹/峰值保持/平均/瀑布/导出历史复位，采集时长清零。

        只清频谱页自己的缓冲；波形页共享缓冲不动（那边有自己的「清空」）。
        """
        self._key = ''
        self._max_hold = None
        self._avg_db = None
        self._waterfall = []
        self._wf_bins = 0
        self._hist = []
        self._hist_bins = 0
        self._t_first = None
        self._t_last = None
        self._x_span = None
        self._last_analysis = None
        self._just_cleared = True
        self._trace.setData([], [])
        self._peak_trace.setData([], [])
        self._avg_trace.setData([], [])
        self._wf_image.clear()
        self._marker_line.hide()
        self._marker_text.hide()
        self.lbl_peak.setText('主频: --')
        self.lbl_info.setText(
            '已清空：频谱轨迹/峰值保持/瀑布/导出缓冲，采集时长已复位'
            '（波形页缓冲不受影响）')

    def stop_timers(self):
        self._timer.stop()


class _ExportCsvDialog(QtWidgets.QDialog):
    """导出CSV对话框：二选一「最新一帧瞬时频点」/「最近 N 秒」。

    N 超过累计采集时长时实时在界面提示导出时会自动钳制（真正的钳制在
    export_csv 里执行，完成后把口径写进面板信息行）。
    """

    def __init__(self, panel: SpectrumPanel, parent=None):
        super().__init__(parent or panel)
        self.setWindowTitle('导出CSV')
        self.setMinimumWidth(440)
        acq = panel.acquisition_seconds

        self.radio_latest = QtWidgets.QRadioButton(
            '最新一帧瞬时频点（当前帧：频率 Hz + dB）')
        self.radio_window = QtWidgets.QRadioButton(
            '最近 N 秒（按帧时间戳回溯：时间 s + 频率 Hz + dB）')
        self.spin_seconds = QtWidgets.QDoubleSpinBox()
        self.spin_seconds.setRange(0.1, 3600.0)
        self.spin_seconds.setDecimals(1)
        self.spin_seconds.setValue(10.0)
        self.spin_seconds.setSuffix(' s')

        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.radio_window)
        row.addWidget(self.spin_seconds)
        row.addStretch(1)

        self.lbl_info = QtWidgets.QLabel(
            f'累计采集时长: {acq:.1f} s | 导出缓冲: {len(panel._hist)} 帧')
        self.lbl_warn = QtWidgets.QLabel('')
        # 警告色随主题取 status_warn（深色亮琥珀 / 浅色深琥珀，白底上 #FFC85A 对比仅 1.4）
        self.lbl_warn.setStyleSheet(f'color: {theme.color("status_warn")};')

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText('导出')
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.radio_latest)
        lay.addLayout(row)
        lay.addWidget(self.lbl_info)
        lay.addWidget(self.lbl_warn)
        lay.addWidget(buttons)

        has_latest = panel.last_analysis is not None
        has_hist = bool(panel._hist)
        self.radio_latest.setEnabled(has_latest)
        self.radio_window.setEnabled(has_hist)
        self.spin_seconds.setEnabled(has_hist)
        if has_latest:
            self.radio_latest.setChecked(True)
        if has_hist and not has_latest:
            self.radio_window.setChecked(True)
        self.radio_window.toggled.connect(self._update_warn)
        self.spin_seconds.valueChanged.connect(self._update_warn)
        self._update_warn()

    def _update_warn(self):
        """N 超过采集时长 → 界面提示将自动钳制（正式钳制在导出时执行）。"""
        panel = self.parent()
        acq = panel.acquisition_seconds
        if self.radio_window.isEnabled() and self.radio_window.isChecked() \
                and self.spin_seconds.value() > acq:
            self.lbl_warn.setText(f'N 超过累计采集时长 {acq:.1f} s，导出时自动钳制')
        else:
            self.lbl_warn.setText('')

    def _on_accept(self):
        if not (self.radio_latest.isChecked() or self.radio_window.isChecked()):
            QtWidgets.QMessageBox.information(self, '导出CSV', '请先选择导出模式。')
            return
        self.accept()

    def result_choice(self) -> tuple[str, float]:
        """返回 (mode, seconds)：'latest' 或 ('window', N 秒)。"""
        if self.radio_latest.isChecked():
            return 'latest', 0.0
        return 'window', float(self.spin_seconds.value())
