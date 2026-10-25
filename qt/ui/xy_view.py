# -*- coding: utf-8 -*-
"""XY 相位图（IQ 李萨如/星座图，移植主线 wave_form.ps1 XY 段的口径）。

- 1~4 个正方格窗口（格子必须正方形——IQ 正交性靠圆看起来是圆来判断）
- 每窗口 X/Y 源通道独立可选（默认配对 CH0/1、CH2/3、CH4/5、CH6/7，越界夹回）
- 余晖：像素累积层逐帧衰减（0=关 0.5s/1s/2s/5s/常驻=-1 不衰减）
- 量程：固定 ±N（默认 ±8）或自适应（按最近数据自动拟合，2 的幂量化防抖）
"""
from __future__ import annotations

import math
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

CELL_SIZE = 220
POINT_COLOR = np.array([105, 224, 148], dtype=np.uint8)
BG_COLOR = np.array([16, 19, 26], dtype=np.uint8)
GRID_COLOR = QtGui.QColor(52, 62, 80)
CROSS_COLOR = QtGui.QColor(96, 108, 130)
TEXT_COLOR = QtGui.QColor(150, 160, 178)
MAX_FEED_PER_TICK = 1024      # 单 tick 最多入图点数（UI 保护）
ADAPTIVE_WINDOW = 500         # 自适应量程参考的最近帧数
PERSIST_OPTIONS = ('0', '0.5s', '1s', '2s', '5s', '常驻')
SPAN_OPTIONS = ('自适应', '±2', '±4', '±8', '±16', '±32')
DEFAULT_PAIRS = ((0, 1), (2, 3), (4, 5), (6, 7))


def persist_seconds(option: str) -> float:
    """余晖档位 → 秒；-1 = 常驻（不衰减），0 = 关（每帧清屏重画）。"""
    return {'0': 0.0, '0.5s': 0.5, '1s': 1.0, '2s': 2.0, '5s': 5.0}.get(option, -1.0)


def span_half(option: str) -> int:
    """量程档位 → 半幅 N；0 = 自适应。"""
    return {'±2': 2, '±4': 4, '±8': 8, '±16': 16, '±32': 32}.get(option, 0)


def adaptive_span(values: np.ndarray) -> float:
    """自适应半幅：按最大绝对值向上取 2 的幂（量化防抖，量程不随数据抖动）。"""
    if values.size == 0:
        return 8.0
    peak = float(np.max(np.abs(values[np.isfinite(values)]))) if np.any(np.isfinite(values)) else 0.0
    if peak <= 0:
        return 8.0
    return 2.0 ** math.ceil(math.log2(peak))


class XYCell(QtWidgets.QWidget):
    """单格 IQ 视图：单通道亮度累积层（余晖）+ 网格覆盖层。

    累积只存亮度（float 0..1），渲染时才映射 BG→点色——背景不会跟着衰减漂黑。
    """

    def __init__(self, slot: int, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.setFixedSize(CELL_SIZE, CELL_SIZE)
        self._accum = np.zeros((CELL_SIZE, CELL_SIZE), dtype=np.float32)
        self._render = np.tile(BG_COLOR, (CELL_SIZE, CELL_SIZE, 1))
        self._pending: tuple[np.ndarray, np.ndarray] | None = None
        self._persist_option = '1s'
        self.x_ch = 0
        self.y_ch = min(1, DEFAULT_PAIRS[slot][1])
        self.span_option = '±8'
        self.divs = 8

    # ---- 配置 ----

    def set_pair(self, x_ch: int, y_ch: int):
        self.x_ch, self.y_ch = x_ch, y_ch

    def set_span_option(self, option: str):
        self.span_option = option

    def set_persist_option(self, option: str):
        self._persist_option = option

    def clear_pixels(self):
        self._accum[:] = 0.0
        self._render[:] = BG_COLOR
        self.update()

    # ---- 数据 ----

    def feed(self, xs: np.ndarray, ys: np.ndarray):
        n = min(len(xs), len(ys))
        if n > 0:
            self._pending = (np.asarray(xs[-n:], dtype=np.float64),
                             np.asarray(ys[-n:], dtype=np.float64))

    def _persist(self) -> float:
        return persist_seconds(self._persist_option)

    def _span(self, xs: np.ndarray, ys: np.ndarray) -> float:
        half = span_half(self.span_option)
        if half > 0:
            return float(half)
        if xs.size == 0 and ys.size == 0:
            return 8.0
        recent = np.concatenate([xs[-ADAPTIVE_WINDOW:], ys[-ADAPTIVE_WINDOW:]])
        return adaptive_span(recent)

    def _draw_points(self, xs: np.ndarray, ys: np.ndarray, span: float):
        s = CELL_SIZE
        scale = (s / 2.0 - 1) / max(span, 1e-9)
        px = np.clip((xs * scale + s / 2.0).astype(np.int64), 0, s - 1)
        py = np.clip((s / 2.0 - ys * scale).astype(np.int64), 0, s - 1)
        self._accum[py, px] = 1.0

    def tick(self, dt: float) -> bool:
        """衰减 + 画新点。返回画面是否变化。"""
        persist = self._persist()
        changed = False
        if persist == 0.0:
            if self._accum.any():
                self._accum[:] = 0.0
                changed = True
        elif persist > 0:
            self._accum *= math.exp(-dt / persist)
            changed = True
        if self._pending is not None:
            self._draw_points(*self._pending, span=self._span(*self._pending))
            self._pending = None
            changed = True
        if changed:
            self._render = (BG_COLOR.astype(np.float32) +
                            np.clip(self._accum, 0.0, 1.0)[:, :, None] *
                            (POINT_COLOR.astype(np.float32) - BG_COLOR)).astype(np.uint8)
            self.update()
        return changed

    # ---- 绘制 ----

    def paintEvent(self, event):  # noqa: N802 (Qt 命名)
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, False)
            img = np.ascontiguousarray(self._render)
            qimg = QtGui.QImage(img.data, CELL_SIZE, CELL_SIZE,
                                3 * CELL_SIZE, QtGui.QImage.Format.Format_RGB888)
            painter.drawImage(self.rect(), qimg)

            half = span_half(self.span_option)
            divs = max(2, self.divs)
            painter.setPen(GRID_COLOR)
            for k in range(1, divs):
                pos = int(k * CELL_SIZE / divs)
                painter.drawLine(pos, 0, pos, CELL_SIZE)
                painter.drawLine(0, pos, CELL_SIZE, pos)
            painter.setPen(CROSS_COLOR)
            mid = CELL_SIZE // 2
            painter.drawLine(mid, 0, mid, CELL_SIZE)
            painter.drawLine(0, mid, CELL_SIZE, mid)

            painter.setPen(TEXT_COLOR)
            if half > 0:
                label = f'±{half}'
            else:
                label = '自适应'
            painter.drawText(4, 14, f'{label}  X CH{self.x_ch} / Y CH{self.y_ch}')
            painter.drawText(4, CELL_SIZE - 4,
                             f'每格 {2 * half / divs:g}' if half > 0 else f'{divs} 格')
        finally:
            painter.end()


class XYSection(QtWidgets.QWidget):
    """XY 相位图区：控制行 + 正方格窗口栅格（1/2/4 窗）。"""

    TICK_INTERVAL_MS = 50

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self._state = state
        self._last_total = 0
        self._last_tick_at = time.monotonic()
        self._cells: list[XYCell] = []

        self.combo_count = QtWidgets.QComboBox()
        self.combo_count.addItems(['1', '2', '4'])
        self.combo_count.setCurrentText('2')
        self.combo_count.currentTextChanged.connect(self._on_count_changed)

        self.combo_persist = QtWidgets.QComboBox()
        self.combo_persist.addItems(PERSIST_OPTIONS)
        self.combo_persist.setCurrentText('1s')
        self.combo_persist.currentTextChanged.connect(self._on_persist_changed)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel('窗口数'))
        controls.addWidget(self.combo_count)
        controls.addWidget(QtWidgets.QLabel('余晖'))
        controls.addWidget(self.combo_persist)
        controls.addStretch(1)
        self._slot_rows: list[QtWidgets.QHBoxLayout] = []
        self._slot_x: list[QtWidgets.QComboBox] = []
        self._slot_y: list[QtWidgets.QComboBox] = []
        self._slot_span: list[QtWidgets.QComboBox] = []

        self.cell_layout = QtWidgets.QGridLayout()
        self.cell_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter |
                                      QtCore.Qt.AlignmentFlag.AlignTop)
        self.cell_host = QtWidgets.QWidget()
        self.cell_host.setLayout(self.cell_layout)

        self._rebuild_slots()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        layout.addLayout(self._slots_row())
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setMaximumHeight(CELL_SIZE * 2 + 40)
        scroll.setWidget(self.cell_host)
        layout.addWidget(scroll)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self.TICK_INTERVAL_MS)
        self._timer.timeout.connect(self.tick)

    # ---- 布局 ----

    def _slots_row(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        for r in self._slot_rows:
            row.addLayout(r)
        row.addStretch(1)
        return row

    def _channel_count(self) -> int:
        return max(1, len(self._state.ch_values))

    def _rebuild_slots(self):
        for combo in (*self._slot_x, *self._slot_y, *self._slot_span):
            combo.deleteLater()
        self._slot_x, self._slot_y, self._slot_span = [], [], []
        self._slot_rows = []
        count = int(self.combo_count.currentText())
        n_ch = self._channel_count()
        for slot in range(count):
            combo_x = QtWidgets.QComboBox()
            combo_y = QtWidgets.QComboBox()
            for c in range(n_ch):
                combo_x.addItem(f'CH{c}')
                combo_y.addItem(f'CH{c}')
            pair = DEFAULT_PAIRS[slot]
            combo_x.setCurrentIndex(min(pair[0], n_ch - 1))
            combo_y.setCurrentIndex(min(pair[1], n_ch - 1))
            combo_x.currentIndexChanged.connect(
                lambda idx, s=slot: self._on_pair_changed(s))
            combo_y.currentIndexChanged.connect(
                lambda idx, s=slot: self._on_pair_changed(s))
            combo_span = QtWidgets.QComboBox()
            combo_span.addItems(SPAN_OPTIONS)
            combo_span.setCurrentText('±8')
            combo_span.currentTextChanged.connect(
                lambda text, s=slot: self._on_span_changed(s, text))

            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(f'窗{slot + 1}'))
            row.addWidget(combo_x)
            row.addWidget(combo_y)
            row.addWidget(combo_span)
            self._slot_rows.append(row)
            self._slot_x.append(combo_x)
            self._slot_y.append(combo_y)
            self._slot_span.append(combo_span)

        # 格子栅格：1=独占 2=左右双屏 4=2×2（格子正方，IQ 正交性靠圆看）
        while self.cell_layout.count():
            item = self.cell_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cells = []
        cols = {1: 1, 2: 2, 4: 2}[count]
        for slot in range(count):
            cell = XYCell(slot)
            cell.set_persist_option(self.combo_persist.currentText())
            self._cells.append(cell)
            self.cell_layout.addWidget(cell, slot // cols, slot % cols,
                                       QtCore.Qt.AlignmentFlag.AlignCenter)
        self._sync_pairs()

    def _sync_pairs(self):
        for slot, cell in enumerate(self._cells):
            if slot < len(self._slot_x):
                cell.set_pair(self._slot_x[slot].currentIndex(),
                              self._slot_y[slot].currentIndex())
                cell.set_span_option(self._slot_span[slot].currentText())

    # ---- 槽位回调 ----

    def _on_count_changed(self, _text: str):
        self._rebuild_slots()

    def _on_persist_changed(self, text: str):
        for cell in self._cells:
            cell.set_persist_option(text)

    def _on_pair_changed(self, slot: int):
        self._sync_pairs()

    def _on_span_changed(self, slot: int, text: str):
        self._sync_pairs()

    def refresh_channels(self):
        """通道数变化后重建通道下拉（保窗格，重配对）。"""
        self._rebuild_slots()

    # ---- 数据节拍 ----

    def tick(self):
        now = time.monotonic()
        dt = min(now - self._last_tick_at, 1.0)
        self._last_tick_at = now
        st = self._state
        total = st.frame_count
        if total < self._last_total:  # 清缓冲/换协议 → 全部重来
            self._last_total = 0
            for cell in self._cells:
                cell.clear_pixels()
        new_n = min(total - self._last_total, MAX_FEED_PER_TICK)
        self._last_total = total
        if new_n > 0:
            for slot, cell in enumerate(self._cells):
                if slot >= len(self._slot_x):
                    break
                xs = st.snapshot(self._slot_x[slot].currentIndex(), new_n)
                ys = st.snapshot(self._slot_y[slot].currentIndex(), new_n)
                cell.feed(xs, ys)
        for cell in self._cells:
            cell.tick(dt)

    # ---- 启停 ----

    def start(self):
        self._last_total = self._state.frame_count
        self._last_tick_at = time.monotonic()
        self._timer.start()

    def stop(self):
        self._timer.stop()
