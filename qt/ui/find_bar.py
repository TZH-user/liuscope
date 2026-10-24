# -*- coding: utf-8 -*-
"""日志查找条：Ctrl+F 呼出，实时弱色高亮全部匹配 + 强色定位当前处，⏎/按钮跳转。

对齐主线语义：'第 i/N 处' 计数、无匹配提示、关闭时清高亮。
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets, QtGui

HIT_BG = QtGui.QColor(244, 135, 113, 46)     # 弱色：全部匹配
HIT_CURRENT_BG = QtGui.QColor(244, 135, 113, 150)  # 强色：当前处


class FindBar(QtWidgets.QWidget):
    def __init__(self, text_edit: QtWidgets.QTextEdit, parent=None):
        super().__init__(parent)
        self._edit = text_edit
        self._matches: list[QtGui.QTextCursor] = []
        self._index = -1

        self.edit_find = QtWidgets.QLineEdit()
        self.edit_find.setPlaceholderText('查找日志内容（Enter 下一个）')
        self.edit_find.returnPressed.connect(lambda: self.navigate(+1))
        self.edit_find.textChanged.connect(self._refresh)

        self.btn_prev = QtWidgets.QPushButton('上一个')
        self.btn_prev.setObjectName('tool')
        self.btn_prev.clicked.connect(lambda: self.navigate(-1))
        self.btn_next = QtWidgets.QPushButton('下一个')
        self.btn_next.setObjectName('tool')
        self.btn_next.clicked.connect(lambda: self.navigate(+1))

        self.lbl_count = QtWidgets.QLabel('')
        self.btn_close = QtWidgets.QPushButton('×')
        self.btn_close.setObjectName('tool')
        self.btn_close.setFixedWidth(28)
        self.btn_close.clicked.connect(self.hide_and_clear)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edit_find, 1)
        row.addWidget(self.btn_prev)
        row.addWidget(self.btn_next)
        row.addWidget(self.lbl_count)
        row.addWidget(self.btn_close)

    # ---------- 跳转 ----------

    def navigate(self, direction: int):
        if not self._matches:
            return
        self._index = (self._index + direction) % len(self._matches)
        cursor = self._matches[self._index]
        self._edit.setTextCursor(cursor)
        self._edit.ensureCursorVisible()
        self.lbl_count.setText(f'第 {self._index + 1}/{len(self._matches)} 处')
        self._apply_highlights()

    def _refresh(self):
        query = self.edit_find.text()
        self._matches = []
        self._index = -1
        if query:
            doc = self._edit.document()
            cursor = QtGui.QTextCursor(doc)
            while True:
                cursor = doc.find(query, cursor)
                if cursor.isNull():
                    break
                self._matches.append(QtGui.QTextCursor(cursor))
        if self._matches:
            self._index = 0
            self.lbl_count.setText(f'第 1/{len(self._matches)} 处')
        else:
            self.lbl_count.setText('无匹配' if query else '')
        self._apply_highlights()

    def _apply_highlights(self):
        selections = []
        for i, cursor in enumerate(self._matches):
            sel = QtWidgets.QTextEdit.ExtraSelection()
            sel.format = QtGui.QTextCharFormat()
            sel.format.setBackground(HIT_CURRENT_BG if i == self._index else HIT_BG)
            sel.cursor = cursor
            selections.append(sel)
        self._edit.setExtraSelections(selections)

    # ---------- 显隐 ----------

    def open_and_focus(self):
        self.show()
        self.edit_find.setFocus()
        self.edit_find.selectAll()

    def hide_and_clear(self):
        self.hide()
        self.edit_find.clear()
        self._matches = []
        self._index = -1
        self._edit.setExtraSelections([])

    def keyPressEvent(self, event):  # noqa: N802 (Qt 命名)
        if event.key() == QtCore.Qt.Key_Escape:
            self.hide_and_clear()
            return
        super().keyPressEvent(event)
