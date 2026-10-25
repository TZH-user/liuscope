# -*- coding: utf-8 -*-
"""波形历史滑块条（主线 wave_form.ps1 逐语义复刻）。

三点拖拽：红 = 窗口起点、绿 = 中心（恒在两点正中）、紫 = 窗口终点。
- 拖左/右点：改窗口宽度，另一端钉住按下时的位置；
- 拖中点：窗口中心直接落到鼠标位置（宽度不变，绝对同步——记录
  "点位置-鼠标位置"的帧偏移，移动时鼠标指哪点在哪）。
总帧数 <2 时只画轨道；命中容差 9px，范围内点空也算中点。
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class WaveSliderBar(QtWidgets.QWidget):
    MARGIN = 14
    HIT_PX = 9.0

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setMinimumHeight(34)
        self.setMouseTracking(True)
        self._hover = 'none'
        self._drag = None

    # ---------- 几何 ----------

    def _metrics(self):
        left = self.MARGIN
        right = self.width() - self.MARGIN
        return left, right, max(1, right - left)

    def _frame_x(self, frame, total, left, span):
        return left + frame / max(1, total - 1) * span

    def _points(self):
        """(x_start, x_mid, x_end, total)；总帧数 <2 时 None。"""
        total = int(self.state.frame_total)
        if total < 2:
            return None
        start, end, _follow = self.state.view_range()
        left, _r, span = self._metrics()
        xs = self._frame_x(start, total, left, span)
        xe = self._frame_x(max(start, end - 1), total, left, span)
        return xs, (xs + xe) / 2.0, xe, total

    def _mouse_frame(self, x):
        left, _r, span = self._metrics()
        total = int(self.state.frame_total)
        return (x - left) / span * (total - 1)

    # ---------- 绘制 ----------

    def paintEvent(self, ev):  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        track_y = self.height() / 2.0
        left, right, _span = self._metrics()
        p.setPen(QtGui.QPen(QtGui.QColor(70, 80, 98), 2))
        p.drawLine(QtCore.QPointF(left, track_y), QtCore.QPointF(right, track_y))
        pts = self._points()
        if pts is None:
            return
        xs, xm, xe, _total = pts
        p.setPen(QtGui.QPen(QtGui.QColor(64, 190, 170), 5))
        p.drawLine(QtCore.QPointF(xs, track_y), QtCore.QPointF(xe, track_y))
        active = self._drag['point'] if self._drag else self._hover
        for x, color, key in ((xs, QtGui.QColor(232, 74, 74), 'start'),
                              (xm, QtGui.QColor(72, 201, 132), 'mid'),
                              (xe, QtGui.QColor(150, 61, 177), 'end')):
            if key == active:  # 悬停/拖动中的点画高亮外圈
                p.setPen(QtGui.QPen(QtGui.QColor(235, 240, 250), 2))
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                p.drawEllipse(QtCore.QPointF(x, track_y), 9.5, 9.5)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QtCore.QPointF(x, track_y), 6.0, 6.0)

    # ---------- 命中 ----------

    def _hit(self, x):
        pts = self._points()
        if pts is None:
            return 'none'
        xs, xm, xe, _ = pts
        for px, key in ((xs, 'start'), (xm, 'mid'), (xe, 'end')):
            if abs(x - px) <= self.HIT_PX:
                return key
        if xs - 4 <= x <= xe + 4:
            return 'mid'
        return 'none'

    # ---------- 拖拽（绝对同步）----------

    def _begin_drag(self, hit, x):
        start, end, _follow = self.state.view_range()
        point_frame = {'start': float(start),
                       'end': float(end - 1),
                       'mid': (start + end - 1) / 2.0}[hit]
        self._drag = {
            'point': hit,
            'grab': point_frame - self._mouse_frame(x),
            'width': end - start,
            'anchor': float(end) if hit == 'start' else float(start),
        }

    def _drag_to(self, x):
        d = self._drag
        st = self.state
        if d['point'] == 'mid':
            target = self._mouse_frame(x) + d['grab']
            half = d['width'] / 2.0
            st.set_view_window(int(round(target - half)), int(round(target + half)),
                               anchor='mid')
        elif d['point'] == 'start':
            st.set_view_window(int(round(self._mouse_frame(x) + d['grab'])),
                               int(d['anchor']), anchor='start')
        else:
            st.set_view_window(int(d['anchor']),
                               int(round(self._mouse_frame(x) + d['grab'])) + 1,
                               anchor='end')
        self.update()

    # ---------- 鼠标 ----------

    def mousePressEvent(self, ev):  # noqa: N802
        if ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        if self._points() is None:
            return
        hit = self._hit(ev.position().x())
        if hit != 'none':
            self._begin_drag(hit, ev.position().x())

    def mouseMoveEvent(self, ev):  # noqa: N802
        x = ev.position().x()
        if self._drag:
            self._drag_to(x)
            return
        hit = self._hit(x)
        if hit != self._hover:
            self._hover = hit
            self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor
                           if hit != 'none' else QtCore.Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, ev):  # noqa: N802
        self._drag = None
        self.update()

    def leaveEvent(self, ev):  # noqa: N802
        self._hover = 'none'
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        self.update()
