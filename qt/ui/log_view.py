# -*- coding: utf-8 -*-
"""日志主区控件：等宽网格（族名由 app 级 QSS #log_view 给）+ Ctrl+滚轮字号缩放。

缩放走控件级动态样式表（只写 font-size）——app 级 #log_view 规则负责
font-family，控件级只覆盖 size，级联合并、切主题不丢。不用 setFont：QSS
字体优先级压过 setFont 的老坑。设置键 log/font_pt（0 = 跟随主题 9pt）。
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from core.settings import read_setting, write_setting

FONT_PT_KEY = 'log/font_pt'
ZOOM_MIN, ZOOM_MAX = 6, 24
BASE_PT = 9   # 与全局 QSS font-size 一致（跟随主题时的基准）


class LogView(QtWidgets.QPlainTextEdit):
    zoom_changed = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('log_view')   # app 级 QSS 靠它给等宽族名
        try:
            self._font_pt = int(read_setting(FONT_PT_KEY, '0') or '0')
        except ValueError:
            self._font_pt = 0
        self._apply_pt(self._font_pt, save=False)

    # ---------- 字号缩放 ----------

    def font_pt(self) -> int:
        return self._font_pt

    def zoom(self, direction: int):
        """direction: +1 放大 / -1 缩小 / 0 复位（跟随主题）。"""
        if direction == 0:
            self._apply_pt(0)
            return
        base = self._font_pt or BASE_PT
        self._apply_pt(max(ZOOM_MIN, min(ZOOM_MAX, base + direction)))

    def _apply_pt(self, pt: int, save: bool = True):
        self._font_pt = int(pt)
        self.setStyleSheet(f'font-size: {pt}pt;' if pt > 0 else '')
        self._sync_document_font()
        if save:
            write_setting(FONT_PT_KEY, str(pt))
        self.zoom_changed.emit(self._font_pt)

    def _sync_document_font(self):
        """把字号落到文档默认字体——QPlainTextEdit 正文按 document.defaultFont
        排版，控件级 QSS 改 size 后控件字体变了但文档不一定跟（实测渲染仍 9pt），
        这里显式同步；0 = 跟随控件字体（主题基准）。"""
        f = self.document().defaultFont()
        target = self._font_pt if self._font_pt > 0 else self.font().pointSize()
        if target and f.pointSize() != target:
            f.setPointSize(max(1, target))
            self.document().setDefaultFont(f)

    def changeEvent(self, event):  # noqa: N802 (Qt 命名)
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.FontChange:
            self._sync_document_font()   # 主题切换等外部字体变更后保持缩放

    # ---------- 输入事件 ----------

    def wheelEvent(self, event):  # noqa: N802 (Qt 命名)
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            self.zoom(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 (Qt 命名)
        mod = event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier
        if mod and event.key() in (QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Equal):
            self.zoom(1)
            event.accept()
            return
        if mod and event.key() == QtCore.Qt.Key.Key_Minus:
            self.zoom(-1)
            event.accept()
            return
        if mod and event.key() == QtCore.Qt.Key.Key_0:
            self.zoom(0)
            event.accept()
            return
        super().keyPressEvent(event)
