# -*- coding: utf-8 -*-
"""Modbus RTU 调试台页签 —— 复刻主线 Modbus 插件：手动事务 + 实时监视（合并轮询）。

传输经 transport(frame, timeout_ms) -> bytes 注入（主窗体接串口实现），
纯逻辑在 core/modbus.py，本文件只管字段、表格与轮询节拍。
事务回写主日志：TX/RX 条目主界面 Ctrl+F 可检索（对齐主线）。
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from core.hexutil import bytes_to_hex_text
from core.modbus import (WatchItem, build_modbus_request, decode_modbus_registers,
                         merge_watch_groups, parse_modbus_response,
                         slice_watch_value, watch_flash_tone)
from ui import theme

FC_ITEMS = ('01 读线圈', '02 读离散输入', '03 读保持寄存器', '04 读输入寄存器',
            '05 写单线圈', '06 写单寄存器', '15 写多线圈', '16 写多寄存器')
FC_MAP = (1, 2, 3, 4, 5, 6, 15, 16)
DECODE_ITEMS = ('十进制', 'HEX', '有符号', 'float32(ABCD)', 'float32(CDAB)')

FLASH_COLORS = {'up': '#80DC80', 'down': '#EB766C', 'changed': '#E8C66E'}  # 深色基准保留


def flash_color(tone: str) -> str:
    """闪烁高亮色随主题（浅色底上亮色高亮对比不足，配色键在 theme.py）。"""
    return theme.color({'up': 'flash_up', 'down': 'flash_down',
                        'changed': 'flash_changed'}[tone])


def _stamp_ms() -> str:
    now = time.localtime()
    ms = int((time.time() % 1) * 1000)
    return f'{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}.{ms:03d}'


class ModbusPanel(QtWidgets.QWidget):
    WATCH_INTERVAL_MS = 1000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.transport = None          # callable(frame: bytes, timeout_ms: int) -> bytes
        self.log_fn = None             # callable(direction: str, text: str) 主日志回写
        self.watch_items: list[WatchItem] = []
        self._flash_timers: dict[tuple[int, int], QtCore.QTimer] = {}

        # ---- 事务行 ----
        self.spin_slave = QtWidgets.QSpinBox()
        self.spin_slave.setRange(1, 247)
        self.spin_slave.setValue(1)
        self.combo_fc = QtWidgets.QComboBox()
        self.combo_fc.addItems(FC_ITEMS)
        self.combo_fc.setCurrentIndex(2)
        self.spin_start = QtWidgets.QSpinBox()
        self.spin_start.setRange(0, 0xFFFF)
        self.spin_count = QtWidgets.QSpinBox()
        self.spin_count.setRange(1, 2000)
        self.spin_count.setValue(1)
        self.edit_value = QtWidgets.QLineEdit()
        self.edit_value.setPlaceholderText('写值（05: 1/0；06: 0~65535 或 0x..；15/16: 逗号列表）')
        self.combo_decode = QtWidgets.QComboBox()
        self.combo_decode.addItems(DECODE_ITEMS)
        self.btn_exec = QtWidgets.QPushButton('执行')
        self.btn_exec.clicked.connect(self.run_transaction)

        self.lbl_frame = QtWidgets.QLabel('')

        txrow = QtWidgets.QHBoxLayout()
        for label, widget in (('从站', self.spin_slave), ('功能码', self.combo_fc),
                              ('起始地址', self.spin_start), ('数量', self.spin_count),
                              ('解码', self.combo_decode)):
            box = QtWidgets.QHBoxLayout()
            box.addWidget(QtWidgets.QLabel(label))
            box.addWidget(widget)
            txrow.addLayout(box)
        txrow.addWidget(self.edit_value, 2)
        txrow.addWidget(self.btn_exec)

        # ---- 结果框 ----
        self.result_box = QtWidgets.QPlainTextEdit()
        self.result_box.setReadOnly(True)
        self.result_box.setMaximumBlockCount(2000)

        # ---- 监视表 ----
        self.chk_watch = QtWidgets.QCheckBox('周期轮询')
        self.spin_watch_ms = QtWidgets.QSpinBox()
        self.spin_watch_ms.setRange(200, 60000)
        self.spin_watch_ms.setValue(self.WATCH_INTERVAL_MS)
        self.spin_watch_ms.setSuffix(' ms')
        self.btn_watch_add = QtWidgets.QPushButton('添加监视')
        self.btn_watch_add.setObjectName('tool')
        self.btn_watch_add.clicked.connect(self.add_watch)
        self.btn_watch_del = QtWidgets.QPushButton('删除选中')
        self.btn_watch_del.setObjectName('tool')
        self.btn_watch_del.clicked.connect(self.remove_selected_watch)
        self.btn_watch_clear = QtWidgets.QPushButton('清空监视')
        self.btn_watch_clear.setObjectName('tool')
        self.btn_watch_clear.clicked.connect(self.clear_watch)

        watchrow = QtWidgets.QHBoxLayout()
        watchrow.addWidget(self.chk_watch)
        watchrow.addWidget(self.spin_watch_ms)
        watchrow.addWidget(self.btn_watch_add)
        watchrow.addWidget(self.btn_watch_del)
        watchrow.addWidget(self.btn_watch_clear)
        watchrow.addStretch(1)

        self.watch_table = QtWidgets.QTableWidget(0, 6)
        self.watch_table.setHorizontalHeaderLabels(['名称', '键', '数量', '格式', '值', '状态'])
        self.watch_table.horizontalHeader().setStretchLastSection(True)
        self.watch_table.verticalHeader().setVisible(False)
        self.watch_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.addLayout(txrow)
        layout.addWidget(self.lbl_frame)
        layout.addWidget(self.result_box, 2)
        layout.addLayout(watchrow)
        layout.addWidget(self.watch_table, 1)

        self.watch_timer = QtCore.QTimer(self)
        self.watch_timer.timeout.connect(self.run_watch_cycle)

    # ---------- 事务 ----------

    def _collect_fields(self) -> dict:
        slave = self.spin_slave.value()
        start = self.spin_start.value()
        count = self.spin_count.value()
        fc = FC_MAP[self.combo_fc.currentIndex()]
        value_text = self.edit_value.text().strip()
        value_list = []
        if fc in (15, 16):
            value_list = [t.strip() for t in value_text.split(',') if t.strip()]
            if value_list:
                count = len(value_list)
        return {'slave': slave, 'fc': fc, 'start': start, 'count': count,
                'value_text': value_text, 'value_list': value_list}

    def _append_result(self, text: str):
        self.result_box.appendPlainText(f'[{_stamp_ms()}] {text}')

    def run_transaction(self):
        fields = self._collect_fields()
        frame, error = build_modbus_request(
            fields['slave'], fields['fc'], fields['start'], fields['count'],
            fields['value_text'], fields['value_list'])
        if error:
            self._append_result(f'组帧失败: {error}')
            return

        req_hex = bytes_to_hex_text(frame)
        self.lbl_frame.setText(f'发送: {req_hex}')

        if self.transport is None:
            self._append_result('串口未连接（先在主窗口连接）')
            return
        resp = self.transport(frame, 600)
        resp_hex = bytes_to_hex_text(resp)

        # 事务回写主窗口日志（TX/RX 条目 → 主界面 Ctrl+F 可检索）
        if self.log_fn:
            self.log_fn('TX', f'[Modbus] {req_hex}')
            if resp:
                self.log_fn('RX', f'[Modbus] {resp_hex}')

        if not resp:
            self._append_result(
                f"无响应（从站 {fields['slave']}，fc {fields['fc']:02X}，超时 600ms）")
            return

        self._append_result(f'请求 {req_hex}')
        parsed = parse_modbus_response(resp, fields['fc'], fields['slave'], fields['count'])
        if not parsed['ok']:
            self._append_result(f'响应 {resp_hex}  →  {parsed["error"]}')
            return

        fc = fields['fc']
        if fc <= 2:
            coil_text = ','.join(str(c) for c in parsed['coils'])
            self._append_result(f'响应 {resp_hex}')
            self._append_result(f"线圈/离散输入[{fields['count']}]: {coil_text}")
        elif fc <= 4:
            mode = self.combo_decode.currentText()
            decoded = decode_modbus_registers(parsed['reg_bytes'], mode)
            self._append_result(f'响应 {resp_hex}')
            self._append_result(f"寄存器[{fields['count']}] {mode}: {decoded}")
        else:
            self._append_result(f'响应 {resp_hex}  →  写入成功')

    # ---------- 监视 ----------

    def add_watch(self):
        fields = self._collect_fields()
        fc = fields['fc']
        if fc in (5, 6, 15, 16):
            self._append_result('监视只支持读功能码 01~04，已自动改为 03 读保持寄存器')
            fc = 3
        item = WatchItem(fields['slave'], fc, fields['start'], fields['count'],
                         decode=self.combo_decode.currentText(),
                         name=self.edit_value.text().strip())
        # 同键覆盖（改数量/格式 = 原位更新），新键追加
        for i, existing in enumerate(self.watch_items):
            if existing.key == item.key:
                item.last_value = existing.last_value
                item.last_status = existing.last_status
                self.watch_items[i] = item
                self._rebuild_watch_table()
                return
        self.watch_items.append(item)
        self._rebuild_watch_table()

    def remove_selected_watch(self):
        rows = sorted({i.row() for i in self.watch_table.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.watch_items):
                self.watch_items.pop(row)
        self._rebuild_watch_table()

    def clear_watch(self):
        self.watch_items = []
        self._rebuild_watch_table()

    def _rebuild_watch_table(self):
        self.watch_table.setRowCount(len(self.watch_items))
        for row, item in enumerate(self.watch_items):
            values = (item.name or '—', item.key, str(item.count), item.decode,
                      item.last_value or '—', item.last_status)
            for col, text in enumerate(values):
                self.watch_table.setItem(row, col, QtWidgets.QTableWidgetItem(text))

    def _set_watch_row(self, item: WatchItem, text: str, status: str):
        tone = watch_flash_tone(item.last_value, text)
        item.last_value = text
        item.last_status = status
        row = self.watch_items.index(item)
        self._fill_watch_row(row)
        if tone != 'same':
            self._flash_cell(row, 4, flash_color(tone))

    def _fill_watch_row(self, row: int):
        item = self.watch_items[row]
        self.watch_table.item(row, 4).setText(item.last_value or '—')
        self.watch_table.item(row, 5).setText(item.last_status)

    def _flash_cell(self, row: int, col: int, color: str):
        key = (row, col)
        cell = self.watch_table.item(row, col)
        if cell is None:
            return
        cell.setForeground(QtGui.QColor(color))
        old_timer = self._flash_timers.pop(key, None)
        if old_timer:
            old_timer.stop()
            old_timer.deleteLater()
        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(700)

        def restore():
            # 回落主文本色随主题（旧硬编码深色 #E1E6F0 在浅色白底表格上不可见）
            cell.setForeground(QtGui.QColor(theme.color('text')))
            self._flash_timers.pop(key, None)

        timer.timeout.connect(restore)
        timer.start()
        self._flash_timers[key] = timer

    def run_watch_cycle(self):
        if not self.chk_watch.isChecked() or self.transport is None:
            return
        if not self.watch_items:
            return
        for group in merge_watch_groups(self.watch_items):
            frame, error = build_modbus_request(
                group['slave'], group['fc'], group['start'], group['count'])
            if error:
                continue
            resp = self.transport(frame, 400)
            stamp = time.strftime('%H:%M:%S')
            if not resp:
                for item in group['items']:
                    self._set_watch_row(item, item.last_value, f'无响应 {stamp}')
                continue
            parsed = parse_modbus_response(resp, group['fc'], group['slave'], group['count'])
            if not parsed['ok']:
                for item in group['items']:
                    self._set_watch_row(item, item.last_value, f"{parsed['error']} {stamp}")
                continue
            for item in group['items']:
                text, _raw = slice_watch_value(parsed['reg_bytes'], group['fc'],
                                               group['start'], item)
                self._set_watch_row(item, text, stamp)
