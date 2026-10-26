# -*- coding: utf-8 -*-
"""XY 相位图（IQ 李萨如/星座图，移植主线 wave_form.ps1 XY 段的口径）。

- 1~4 个正方格窗口（格子必须正方形——IQ 正交性靠圆看起来是圆来判断）
- 每窗口 X/Y 源通道独立可选（默认配对 CH0/1、CH2/3、CH4/5、CH6/7，越界夹回）
- 余晖：像素累积层逐帧衰减（0=关 0.5s/1s/2s/5s/常驻=-1 不衰减）
- 量程：固定 ±N（默认 ±8）或自适应（按最近数据自动拟合，2 的幂量化防抖）
- 余晖迁移：量程切换把已积累像素按中心拉伸映射到新量程（主线 migrate_wave_xy_accum
  同款观感，轨迹不清空；新量程自适应时等下一帧算出半幅再迁移）；换源量程未变=
  恒等映射，轨迹同样保留
- 回铺：进 XY/改窗口数/通道数变化时把「最近窗口」一帧数据一次铺入，立即有画面
  （主线 rebuild_wave_xy_accum 同款）
- 分格：每轴独立分格数（默认 8，档位 4/5/8/10/16，钳制 2..16）
- 画法：线 = 相邻有效点连轨迹（出界/NaN 断线，孤立点 2px 方点，主线同款）；点 = 逐点打点
- 格子配色随主题（底=绘图区底、网格/十字/文字=主题弱文本、点色按主题加深）
- 右键中文菜单：恢复自动缩放/导出PNG/导出CSV/显示网格/清除余晖；悬停显示坐标读出
"""
from __future__ import annotations

import math
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ui import theme

CELL_SIZE = 220
POINT_COLOR = np.array([105, 224, 148], dtype=np.uint8)   # 深色基准点色（保留兼容引用）
BG_COLOR = np.array([16, 19, 26], dtype=np.uint8)         # 深色基准底色（保留兼容引用）
GRID_COLOR = QtGui.QColor(52, 62, 80)                     # 深色基准（运行时走主题色）
CROSS_COLOR = QtGui.QColor(96, 108, 130)
TEXT_COLOR = QtGui.QColor(150, 160, 178)
MAX_FEED_PER_TICK = 1024      # 单 tick 最多入图点数（UI 保护）
ADAPTIVE_WINDOW = 500         # 自适应量程参考的最近帧数
BACKFILL_POINTS = 1024        # 回铺点数（进 XY/改窗口数时一次铺入的「最近窗口」）
PERSIST_OPTIONS = ('0', '0.5s', '1s', '2s', '5s', '常驻')
SPAN_OPTIONS = ('自适应', '±2', '±4', '±8', '±16', '±32')
DIVS_OPTIONS = ('4', '5', '8', '10', '16')   # 主线 get_wave_xy_divisions 同款档位
DRAW_MODES = ('线', '点')                     # 线=轨迹连线（主线），点=逐点打点
DEFAULT_PAIRS = ((0, 1), (2, 3), (4, 5), (6, 7))
LINE_MARGIN = 2.0             # 线画法出界容差（像素）——出界断线，防横跨整屏假连线
# 点色按主题（白底需加深保证对比；其余格子色全部取自 theme.palette()）
_POINT_BY_THEME = {'dark': '#69E094', 'light': '#1E8E3E'}


def _hex_rgb(hexstr: str) -> np.ndarray:
    """'#RRGGBB' → uint8 RGB 数组（渲染层用）。"""
    c = QtGui.QColor(hexstr)
    return np.array([c.red(), c.green(), c.blue()], dtype=np.uint8)


def persist_seconds(option: str) -> float:
    """余晖档位 → 秒；-1 = 常驻（不衰减），0 = 关（每帧清屏重画）。"""
    return {'0': 0.0, '0.5s': 0.5, '1s': 1.0, '2s': 2.0, '5s': 5.0}.get(option, -1.0)


def span_half(option: str) -> int:
    """量程档位 → 半幅 N；0 = 自适应。"""
    return {'±2': 2, '±4': 4, '±8': 8, '±16': 16, '±32': 32}.get(option, 0)


def clamp_divs(divs: int) -> int:
    """分格数钳制（主线 get_wave_xy_divisions 同口径：<2 回落 8，>16 取 16）。"""
    if divs < 2:
        return 8
    return min(int(divs), 16)


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
        self.setMouseTracking(True)
        self._accum = np.zeros((CELL_SIZE, CELL_SIZE), dtype=np.float32)
        self._render = np.tile(BG_COLOR, (CELL_SIZE, CELL_SIZE, 1))
        self._pending: tuple[np.ndarray, np.ndarray] | None = None
        self._persist_option = '1s'
        self.x_ch = 0
        self.y_ch = min(1, DEFAULT_PAIRS[slot][1])
        self.span_option = '±8'
        self.divs = 8
        self.draw_mode = '线'
        self.show_grid = True
        self.menu_provider = None    # callable(cell, global_pos)：XYSection 注入右键菜单
        self._hover_px: QtCore.QPoint | None = None
        self._last_span = 8.0        # 最近一次入图用的半幅（悬停坐标读出/迁移基准用）
        self._migrate_from: float | None = None  # 待迁移旧半幅（新量程自适应时挂起）
        self.apply_theme()           # 格子配色随当前主题（theme.set_theme 通知刷新）

    # ---- 主题 ----

    def apply_theme(self, name: str | None = None):
        """格子配色随主题：底=绘图区底、网格/十字/文字=主题弱文本、点色按主题加深。"""
        p = theme.palette(name)
        key = name or theme.current_theme()
        self._bg = _hex_rgb(p['plot_bg'])
        self._point = _hex_rgb(_POINT_BY_THEME.get(key, _POINT_BY_THEME['dark']))
        self._grid_color = QtGui.QColor(p['border'])
        self._cross_color = QtGui.QColor(p['text_dim'])
        self._text_color = QtGui.QColor(p['text_dim'])
        self._rerender()

    def _rerender(self):
        """按当前累积与配色重建渲染帧（主题切换/迁移后调用）。"""
        self._render = (self._bg.astype(np.float32) +
                        np.clip(self._accum, 0.0, 1.0)[:, :, None] *
                        (self._point.astype(np.float32) - self._bg)).astype(np.uint8)
        self.update()

    # ---- 配置 ----

    def set_pair(self, x_ch: int, y_ch: int):
        self.x_ch, self.y_ch = x_ch, y_ch

    def set_span_option(self, option: str):
        self.span_option = option

    def set_persist_option(self, option: str):
        self._persist_option = option

    def set_draw_mode(self, mode: str):
        self.draw_mode = mode if mode in DRAW_MODES else '线'

    def set_divs(self, divs: int):
        self.divs = clamp_divs(divs)

    def set_show_grid(self, on: bool):
        self.show_grid = bool(on)
        self.update()

    def clear_pixels(self):
        self._accum[:] = 0.0
        self._migrate_from = None  # 累积已清空，挂起的迁移一并作废
        self._render[:] = self._bg
        self.update()

    # ---- 余晖迁移（主线 migrate_wave_xy_accum 同款观感） ----

    def migrate_span(self, old_half: float, new_half: float):
        """量程切换的余晖拉伸迁移：把已积累像素绕中心按 old/new 比例缩放到新量程，
        轨迹不清空。old/new 都是半幅值（>0）；相等时为恒等映射直接返回。"""
        if old_half <= 0 or new_half <= 0 or abs(old_half - new_half) < 1e-9:
            return
        factor = old_half / new_half      # 新像素 = 中心 + (旧像素-中心)×factor
        s = CELL_SIZE
        mid = s / 2.0
        rows, cols = np.mgrid[0:s, 0:s].astype(np.float32)
        src_x = np.clip((cols - mid) / factor + mid, 0, s - 1).astype(np.int64)
        src_y = np.clip((rows - mid) / factor + mid, 0, s - 1).astype(np.int64)
        self._accum = self._accum[src_y, src_x].copy()
        self._last_span = float(new_half)
        self._rerender()

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
        px = xs * scale + s / 2.0
        py = s / 2.0 - ys * scale
        if self.draw_mode == '线':
            self._draw_trace(px, py, s)
        else:
            self._accum[np.clip(py.astype(np.int64), 0, s - 1),
                        np.clip(px.astype(np.int64), 0, s - 1)] = 1.0

    def _draw_trace(self, px: np.ndarray, py: np.ndarray, s: int):
        """线画法（主线 draw_wave_xy_range 同款语义）：连续有效点连成轨迹，
        NaN/出界断线，孤立点画 2px 方点。全向量化插值，无逐段 Python 循环。"""
        valid = (np.isfinite(px) & np.isfinite(py) &
                 (px >= -LINE_MARGIN) & (px <= s + LINE_MARGIN) &
                 (py >= -LINE_MARGIN) & (py <= s + LINE_MARGIN))
        if not valid.any():
            return
        hit = np.zeros((s, s), dtype=bool)
        pairs = valid[:-1] & valid[1:] if px.size >= 2 else np.zeros(0, dtype=bool)
        if pairs.any():
            x0, y0 = px[:-1][pairs], py[:-1][pairs]
            x1, y1 = px[1:][pairs], py[1:][pairs]
            steps = np.maximum(np.abs(x1 - x0), np.abs(y1 - y0)).astype(np.int64) + 1
            total = int(steps.sum())
            if total:
                seg = np.repeat(np.arange(steps.size), steps)
                off = np.arange(total) - np.repeat(np.cumsum(steps) - steps, steps)
                t = off / steps[seg]
                lx = x0[seg] + t * (x1[seg] - x0[seg])
                ly = y0[seg] + t * (y1[seg] - y0[seg])
                hit[np.clip(ly.astype(np.int64), 0, s - 1),
                    np.clip(lx.astype(np.int64), 0, s - 1)] = True
        # 孤立有效点（前后都不相连）画 2px 方点
        iso = valid.copy()
        if px.size >= 2:
            iso[1:] &= ~pairs
            iso[:-1] &= ~pairs
        if iso.any():
            ix = np.clip(px[iso].astype(np.int64), 0, s - 1)
            iy = np.clip(py[iso].astype(np.int64), 0, s - 1)
            for dx in (0, 1):
                for dy in (0, 1):
                    hit[np.clip(iy + dy, 0, s - 1), np.clip(ix + dx, 0, s - 1)] = True
        self._accum[hit] = 1.0

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
            span = self._span(*self._pending)
            if self._migrate_from is not None:
                # 换成自适应量程时迁移被挂起：新半幅此时才可知，先迁移再画新点
                self.migrate_span(self._migrate_from, span)
                self._migrate_from = None
            self._last_span = span
            self._draw_points(*self._pending, span=span)
            self._pending = None
            changed = True
        if changed:
            self._rerender()
        return changed

    # ---- 悬停坐标读出 ----

    def mouseMoveEvent(self, ev):  # noqa: N802 (Qt 命名)
        self._hover_px = ev.position().toPoint()
        self.update()

    def leaveEvent(self, ev):  # noqa: N802 (Qt 命名)
        self._hover_px = None
        self.update()

    def mousePressEvent(self, ev):  # noqa: N802 (Qt 命名)
        if ev.button() == QtCore.Qt.MouseButton.RightButton and self.menu_provider is not None:
            self.menu_provider(self, ev.globalPosition().toPoint())
            return
        super().mousePressEvent(ev)

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
            if self.show_grid:  # 分格线（右键「显示网格」可关；中心十字恒画）
                painter.setPen(self._grid_color)
                for k in range(1, divs):
                    pos = int(k * CELL_SIZE / divs)
                    painter.drawLine(pos, 0, pos, CELL_SIZE)
                    painter.drawLine(0, pos, CELL_SIZE, pos)
            painter.setPen(self._cross_color)
            mid = CELL_SIZE // 2
            painter.drawLine(mid, 0, mid, CELL_SIZE)
            painter.drawLine(0, mid, CELL_SIZE, mid)

            painter.setPen(self._text_color)
            if half > 0:
                label = f'±{half}'
            else:
                label = '自适应'
            painter.drawText(4, 14, f'{label}  X CH{self.x_ch} / Y CH{self.y_ch}')
            painter.drawText(4, CELL_SIZE - 4,
                             f'每格 {2 * half / divs:g}' if half > 0 else f'{divs} 格')

            # 悬停坐标读出：十字虚线 + 像素反算回 (X,Y) 数值
            if self._hover_px is not None:
                hx, hy = self._hover_px.x(), self._hover_px.y()
                painter.setPen(QtGui.QPen(self._cross_color, 1,
                                          QtCore.Qt.PenStyle.DashLine))
                painter.drawLine(0, hy, CELL_SIZE, hy)
                painter.drawLine(hx, 0, hx, CELL_SIZE)
                scale = (CELL_SIZE / 2.0 - 1) / max(self._last_span, 1e-9)
                xv = (hx - CELL_SIZE / 2.0) / scale
                yv = (CELL_SIZE / 2.0 - hy) / scale
                painter.setPen(self._text_color)
                painter.drawText(4, CELL_SIZE - 18, f'X {xv:.4g}  Y {yv:.4g}')
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
        self.csv_export = None       # callable：波形页注入「导出CSV」（右键菜单用）

        self.combo_count = QtWidgets.QComboBox()
        self.combo_count.addItems(['1', '2', '4'])
        self.combo_count.setCurrentText('2')
        self.combo_count.setToolTip('XY 窗口个数（1/2/4）：2 = 左右双屏，4 = 2×2；'
                                    '格子保持正方形，IQ 正交性一眼可判')
        self.combo_count.currentTextChanged.connect(self._on_count_changed)

        self.combo_persist = QtWidgets.QComboBox()
        self.combo_persist.addItems(PERSIST_OPTIONS)
        self.combo_persist.setCurrentText('1s')
        self.combo_persist.setToolTip('余晖时间（全体窗口共用）：旧轨迹按此时间常数淡出；'
                                      '常驻 = 只累积不清；关 = 只画最新数据')
        self.combo_persist.currentTextChanged.connect(self._on_persist_changed)

        self.combo_draw = QtWidgets.QComboBox()
        self.combo_draw.addItems(DRAW_MODES)
        self.combo_draw.setCurrentText('线')
        self.combo_draw.setToolTip('轨迹画法：线 = 相邻点连线（示波器轨迹，出界断线）；'
                                   '点 = 逐点打点（星座图）')
        self.combo_draw.currentTextChanged.connect(self._on_draw_changed)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel('窗口数'))
        controls.addWidget(self.combo_count)
        controls.addWidget(QtWidgets.QLabel('余晖'))
        controls.addWidget(self.combo_persist)
        controls.addWidget(QtWidgets.QLabel('显示'))
        controls.addWidget(self.combo_draw)
        controls.addStretch(1)
        self._slot_rows: list[QtWidgets.QHBoxLayout] = []
        self._slot_x: list[QtWidgets.QComboBox] = []
        self._slot_y: list[QtWidgets.QComboBox] = []
        self._slot_span: list[QtWidgets.QComboBox] = []
        self._slot_divs: list[QtWidgets.QComboBox] = []

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

        self.apply_theme()          # 按当前主题给全体格子上色
        theme.register_listener(self.apply_theme)

    # ---- 主题 ----

    def apply_theme(self, name: str | None = None):
        """主题切换回调（theme.set_theme 通知）：全体格子配色随主题换。"""
        for cell in self._cells:
            cell.apply_theme(name)

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
        for combo in (*self._slot_x, *self._slot_y, *self._slot_span, *self._slot_divs):
            combo.deleteLater()
        self._slot_x, self._slot_y = [], []
        self._slot_span, self._slot_divs = [], []
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
            combo_x.setToolTip('该窗口的 X 轴（横轴）数据源通道')
            combo_y.setToolTip('该窗口的 Y 轴（纵轴）数据源通道')
            combo_x.currentIndexChanged.connect(
                lambda idx, s=slot: self._on_pair_changed(s))
            combo_y.currentIndexChanged.connect(
                lambda idx, s=slot: self._on_pair_changed(s))
            combo_span = QtWidgets.QComboBox()
            combo_span.addItems(SPAN_OPTIONS)
            combo_span.setCurrentText('±8')
            combo_span.setToolTip('该窗口的 XY 量程：默认固定 ±8（两轴 -8~+8，中心 0，'
                                  '适合 IQ 星座图）；自适应 = 按数据范围自动缩放')
            combo_span.currentTextChanged.connect(
                lambda text, s=slot: self._on_span_changed(s, text))
            combo_divs = QtWidgets.QComboBox()
            combo_divs.addItems(DIVS_OPTIONS)
            combo_divs.setCurrentText('8')
            combo_divs.setToolTip('该窗口每轴的分格数（刻度随每格显示在窗内）')
            combo_divs.currentTextChanged.connect(
                lambda text, s=slot: self._on_divs_changed(s, text))

            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(f'窗{slot + 1}'))
            row.addWidget(combo_x)
            row.addWidget(combo_y)
            row.addWidget(QtWidgets.QLabel('量程'))
            row.addWidget(combo_span)
            row.addWidget(QtWidgets.QLabel('分格'))
            row.addWidget(combo_divs)
            self._slot_rows.append(row)
            self._slot_x.append(combo_x)
            self._slot_y.append(combo_y)
            self._slot_span.append(combo_span)
            self._slot_divs.append(combo_divs)

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
            cell.set_draw_mode(self.combo_draw.currentText())
            cell.set_divs(int(self._slot_divs[slot].currentText()))
            cell.menu_provider = self._show_cell_menu
            self._cells.append(cell)
            self.cell_layout.addWidget(cell, slot // cols, slot % cols,
                                       QtCore.Qt.AlignmentFlag.AlignCenter)
        self._sync_pairs()
        self._backfill_cells()  # 改窗口数/换通道数：把最近窗口一次铺入，立即有画面

    def _backfill_cells(self):
        """回铺（主线 rebuild_wave_xy_accum 同款）：进 XY/改窗口数时把「最近窗口」
        一帧数据一次铺入各格（tick(0) 不衰减只画点），进入瞬间即有画面。"""
        st = self._state
        if st.frame_count <= 0:
            return
        n = min(st.frame_count, BACKFILL_POINTS)
        for slot, cell in enumerate(self._cells):
            if slot >= len(self._slot_x):
                break
            cell.feed(st.snapshot(self._slot_x[slot].currentIndex(), n),
                      st.snapshot(self._slot_y[slot].currentIndex(), n))
            cell.tick(0.0)
        self._last_total = st.frame_count  # 回铺过的帧不再由例行 tick 重复喂入

    def _sync_pairs(self):
        for slot, cell in enumerate(self._cells):
            if slot < len(self._slot_x):
                cell.set_pair(self._slot_x[slot].currentIndex(),
                              self._slot_y[slot].currentIndex())
                cell.set_span_option(self._slot_span[slot].currentText())
                cell.set_divs(int(self._slot_divs[slot].currentText()))

    # ---- 槽位回调 ----

    def _on_count_changed(self, _text: str):
        self._rebuild_slots()

    def _on_persist_changed(self, text: str):
        for cell in self._cells:
            cell.set_persist_option(text)

    def _on_draw_changed(self, text: str):
        for cell in self._cells:
            cell.set_draw_mode(text)
            cell.clear_pixels()  # 画法切换后旧余晖混着两种画法无意义，直接清

    def _on_pair_changed(self, slot: int):
        self._sync_pairs()
        # 主线换源走余晖迁移（量程未变=恒等映射，migrate_wave_xy_accum），轨迹不清空；
        # 旧通道的轨迹保留、新通道数据继续叠加（对齐主线观感）

    def _on_span_changed(self, slot: int, text: str):
        old_option = (self._cells[slot].span_option if slot < len(self._cells) else '')
        self._sync_pairs()
        if slot >= len(self._cells):
            return
        cell = self._cells[slot]
        old_half = span_half(old_option) or cell._last_span  # 旧量程自适应 → 用最近入图半幅
        new_half = span_half(text)
        if new_half > 0 and old_half > 0:
            # 固定量程间：立即把余晖拉伸迁移到新量程（主线 migrate_wave_xy_accum，不清空）
            cell.migrate_span(old_half, new_half)
        else:
            # 新量程自适应：半幅要等下一帧数据算出，挂起到 tick 里再迁移
            cell._migrate_from = old_half

    def _on_divs_changed(self, slot: int, text: str):
        self._sync_pairs()

    def refresh_channels(self):
        """通道数变化后重建通道下拉（保窗格，重配对）。"""
        self._rebuild_slots()

    # ---- 单格右键中文菜单 ----

    def _new_cell_menu(self, cell: XYCell):
        """构建单格中文菜单（动作即时接线，供右键与验证脚本直接触发）。"""
        slot = cell.slot
        menu = QtWidgets.QMenu(self)
        act_auto = menu.addAction('恢复自动缩放')
        act_png = menu.addAction('导出PNG')
        act_csv = menu.addAction('导出CSV')
        if self.csv_export is None:
            act_csv.setEnabled(False)
        menu.addSeparator()
        act_grid = menu.addAction('显示网格')
        act_grid.setCheckable(True)
        act_grid.setChecked(cell.show_grid)
        act_clear = menu.addAction('清除余晖')
        slot_span = self._slot_span[slot] if slot < len(self._slot_span) else None
        act_auto.triggered.connect(
            lambda: slot_span.setCurrentText('自适应') if slot_span else None)
        act_png.triggered.connect(lambda: self._export_cell_png(cell))
        # 触发时再取 self.csv_export（注入晚于建菜单也不失效，便于替换/测试）
        act_csv.triggered.connect(
            lambda: self.csv_export() if self.csv_export is not None else None)
        act_grid.toggled.connect(cell.set_show_grid)
        act_clear.triggered.connect(cell.clear_pixels)
        return menu

    def _show_cell_menu(self, cell: XYCell, global_pos):
        self._new_cell_menu(cell).exec(global_pos)

    def _export_cell_png(self, cell: XYCell):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, '导出PNG', f'xy_win{cell.slot + 1}.png', 'PNG (*.png)')
        if not path:
            return
        if not cell.grab().save(path):
            QtWidgets.QMessageBox.warning(self, '导出PNG', '导出失败: PNG 渲染保存失败')

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
        self._backfill_cells()      # 进 XY：把最近窗口铺入，立即有画面（主线同款）
        self._last_total = self._state.frame_count
        self._last_tick_at = time.monotonic()
        self._timer.start()

    def stop(self):
        self._timer.stop()
