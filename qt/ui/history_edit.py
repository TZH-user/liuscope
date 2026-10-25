# -*- coding: utf-8 -*-
"""发送历史输入框：cmd 风格 ⬆/⬇ 回翻 —— 移植自主线 push_send_history / recall_send_history。

语义：pos=-1 表示正在编辑新行；⬆ 第一次先记草稿，停在最旧一条不回绕；
⬇ 走到底恢复草稿；连续重复不重复入栈；栈满（200）丢最旧。
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class HistoryLineEdit(QtWidgets.QLineEdit):
    HISTORY_MAX = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[str] = []
        self._pos = -1
        self._draft = ''

    def push_history(self, text: str):
        if not text or not text.strip():
            return
        if self._history and self._history[-1] == text:
            self._pos = -1
            return
        self._history.append(text)
        if len(self._history) > self.HISTORY_MAX:
            self._history.pop(0)
        self._pos = -1

    def recall(self, direction: int) -> bool:
        """direction: -1 = ⬆ 上一条（更旧），+1 = ⬇ 下一条（更新）。"""
        count = len(self._history)
        if count == 0:
            return False
        if direction < 0:
            if self._pos < 0:
                self._draft = self.text()
                self._pos = count - 1
            elif self._pos > 0:
                self._pos -= 1
            self.setText(self._history[self._pos])
        else:
            if self._pos < 0:
                return False
            self._pos += 1
            if self._pos >= count:
                self._pos = -1
                self.setText(self._draft)
            else:
                self.setText(self._history[self._pos])
        self.setCursorPosition(len(self.text()))
        return True

    def keyPressEvent(self, event):  # noqa: N802 (Qt 命名)
        key = event.key()
        if key == QtCore.Qt.Key_Up:
            if not self.recall(-1):
                event.accept()
                return
            return
        if key == QtCore.Qt.Key_Down:
            if not self.recall(+1):
                event.accept()
                return
            return
        super().keyPressEvent(event)
