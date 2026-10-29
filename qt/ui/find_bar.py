# -*- coding: utf-8 -*-
"""日志查找条：Ctrl+F 呼出，实时弱色高亮全部匹配 + 强色定位当前处，⏎/按钮跳转。

对齐主线语义：'第 i/N 处' 计数、无匹配提示、关闭时清高亮；
输入 20:57 / 10:03.5 / 2026-10-24 这类时刻则切换为时间定位模式，
直接跳到该时刻的日志头行（对齐主线 find_log_timestamp_jump）。
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets, QtGui

from core.log_find import (collect_timestamp_headers, find_timestamp_jump,
                           is_timestamp_query)

HIT_BG = QtGui.QColor(244, 135, 113, 46)     # 弱色：全部匹配
HIT_CURRENT_BG = QtGui.QColor(244, 135, 113, 150)  # 强色：当前处
MAX_TIME_HIGHLIGHTS = 500  # 时间模式弱高亮上限（2 万条日志全高亮会卡）


class FindBar(QtWidgets.QWidget):
    def __init__(self, text_edit: QtWidgets.QTextEdit, parent=None):
        super().__init__(parent)
        self._edit = text_edit
        self._matches: list[QtGui.QTextCursor] = []
        self._index = -1
        self._time_mode = False

        self.edit_find = QtWidgets.QLineEdit()
        self.edit_find.setPlaceholderText('查找日志内容（Enter 下一个；输入 20:57 这类时刻可定位时间）')
        self.edit_find.returnPressed.connect(lambda: self.navigate(+1))
        self.edit_find.textChanged.connect(self._refresh)

        self.chk_regex = QtWidgets.QCheckBox('.*')
        self.chk_regex.setToolTip(
            '正则表达式查找（大小写不敏感，与普通查找一致）：\n'
            '如 RX 0x[0-9A-F]{2} 匹配十六进制、E \\(\\d+\\) 匹配 IDF 错误行。'
            '勾选后时刻定位停用，弱高亮上限 500 处')
        self.chk_regex.toggled.connect(self._refresh)

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
        row.addWidget(self.chk_regex)
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
        mode = '时刻 ' if self._time_mode else ''
        self.lbl_count.setText(f'{mode}第 {self._index + 1}/{len(self._matches)} 处')
        self._apply_highlights()

    def _make_cursor(self, pos: int, length: int) -> QtGui.QTextCursor:
        cursor = QtGui.QTextCursor(self._edit.document())
        cursor.setPosition(pos)
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.NextCharacter,
                            QtGui.QTextCursor.MoveMode.KeepAnchor, length)
        return cursor

    def _refresh(self):
        query = self.edit_find.text().strip()
        self._matches = []
        self._index = -1
        self._time_mode = bool(query) and not self.chk_regex.isChecked() \
            and is_timestamp_query(query)
        if not query:
            self.lbl_count.setText('')
            self._apply_highlights()
            return
        if self.chk_regex.isChecked():
            self._refresh_regex(query)
            return
        if self._time_mode:
            self._refresh_time_mode(query)
            return
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
            self.lbl_count.setText('无匹配')
        self._apply_highlights()

    def _refresh_regex(self, pattern: str):
        """正则模式：全文 re.finditer，弱高亮上限 500（同时间模式口径）。"""
        import re
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            self.lbl_count.setText(f'正则无效: {exc}')
            self._apply_highlights()
            return
        text = self._edit.toPlainText()
        self._matches = [self._make_cursor(m.start(), m.end() - m.start())
                         for m in rx.finditer(text)][:MAX_TIME_HIGHLIGHTS]
        if self._matches:
            self._index = 0
            self.lbl_count.setText(f'正则 第 1/{len(self._matches)} 处')
        else:
            self.lbl_count.setText('无匹配')
        self._apply_highlights()

    def _refresh_time_mode(self, query: str):
        """时间定位模式：跳到查询时刻的头行（起点取首条 ≥ 查询时刻）。"""
        sel = self._edit.textCursor()
        result = find_timestamp_jump(
            collect_timestamp_headers(self._edit.toPlainText()), query,
            backward=False, sel_start=sel.selectionStart(), sel_end=sel.selectionEnd())
        if result is None:
            self.lbl_count.setText('无匹配')
            self._apply_highlights()
            return
        if not result['found']:
            self.lbl_count.setText(f'日志共 {result["total"]} 条，无 ≥ 该时刻的条目')
            self._apply_highlights()
            return
        self._matches = [self._make_cursor(p, ln) for p, ln, _ts
                         in collect_timestamp_headers(self._edit.toPlainText())]
        self._index = result['ordinal'] - 1
        cursor = self._matches[self._index]
        self._edit.setTextCursor(cursor)
        self._edit.ensureCursorVisible()
        self.lbl_count.setText(
            f'时刻 第 {result["ordinal"]}/{result["total"]} 处 '
            f'({result["time"].strftime("%H:%M:%S.%f")[:-3]})')
        self._apply_highlights()

    def _apply_highlights(self):
        selections = []
        limit = MAX_TIME_HIGHLIGHTS if self._time_mode else len(self._matches)
        for i, cursor in enumerate(self._matches[:limit]):
            sel = QtWidgets.QTextEdit.ExtraSelection()  # PySide6 只在 QTextEdit 暴露该结构体；QPlainTextEdit.setExtraSelections 通用
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
        self._time_mode = False
        self._edit.setExtraSelections([])

    def keyPressEvent(self, event):  # noqa: N802 (Qt 命名)
        if event.key() == QtCore.Qt.Key_Escape:
            self.hide_and_clear()
            return
        super().keyPressEvent(event)
