# -*- coding: utf-8 -*-
"""日志区行悬停 HEX⇄文本互转提示（对齐主线：悬停 0.4s 弹出，行变化才重算）。

主窗体日志一条目占一个 block：'[时间] 方向 正文'。悬停时取该 block 正文
做格式互转；头行式两条目（方向后无正文）则取下一个 block。
"""
from __future__ import annotations

import re

from PySide6 import QtCore, QtGui, QtWidgets

from core.text_format import alt_format_tooltip

_HEADER_ONLY_RE = re.compile(r'^\[[^\]]+\] (RX|TX|SYS)\s*$')
_ENTRY_RE = re.compile(r'^\[[^\]]+\] (RX|TX|SYS)\s?(.*)$', re.DOTALL)


def tooltip_for_block(block: QtGui.QTextBlock) -> str:
    """取该 block（或其后继正文 block）的格式互转提示。"""
    if not block.isValid():
        return ''
    text = block.text()
    if _HEADER_ONLY_RE.match(text):
        return alt_format_tooltip(block.next().text())
    m = _ENTRY_RE.match(text)
    return alt_format_tooltip(m.group(2)) if m else ''


class LogHoverTip(QtCore.QObject):
    """挂在日志 QTextEdit 视口上的事件过滤器；行变化即重算，0.4s 后弹出。"""

    HOVER_DELAY_MS = 400

    def __init__(self, log: QtWidgets.QTextEdit, parent=None):
        super().__init__(parent or log)
        self._log = log
        self._last_block = -1
        self._tooltip = ''
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.HOVER_DELAY_MS)
        self._timer.timeout.connect(self._show_tooltip)
        log.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt 命名)
        et = event.type()
        if et == QtCore.QEvent.Type.MouseMove:
            self._on_move(event.position().toPoint())
        elif et == QtCore.QEvent.Type.Leave:
            self._reset()
        return False

    def _on_move(self, pos: QtCore.QPoint):
        cursor = self._log.cursorForPosition(pos)
        block_no = cursor.blockNumber()
        if block_no == self._last_block:
            return
        self._last_block = block_no
        self._tooltip = tooltip_for_position(self._log, block_no)
        if self._tooltip:
            self._timer.start()
        else:
            self._timer.stop()
            QtWidgets.QToolTip.hideText()

    def _reset(self):
        self._last_block = -1
        self._tooltip = ''
        self._timer.stop()
        QtWidgets.QToolTip.hideText()

    def _show_tooltip(self):
        if self._tooltip:
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), self._tooltip, self._log)


def tooltip_for_position(log: QtWidgets.QTextEdit, block_no: int) -> str:
    block = log.document().findBlockByNumber(block_no)
    return tooltip_for_block(block)
