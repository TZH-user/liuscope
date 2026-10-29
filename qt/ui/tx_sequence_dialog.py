# -*- coding: utf-8 -*-
"""序列发送对话框：脚本编辑 + 重复/循环 + 开始/停止 +「连接后自动执行」。

脚本语法见 core/tx_sequence.py（send/sendhex/delay，# 注释）。发送走主窗
_send_tx_bytes 打包（写 worker + TX 日志 + 字节统计），未连接/写失败即中止。
设置键：txseq/script（脚本记忆）、txseq/auto_run（连接后自动执行）、
txseq/repeat、txseq/loop。
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from core.settings import read_setting, write_setting
from core.tx_sequence import TxSequenceError, TxSequenceRunner, parse_script

SCRIPT_KEY = 'txseq/script'
AUTO_RUN_KEY = 'txseq/auto_run'
REPEAT_KEY = 'txseq/repeat'
LOOP_KEY = 'txseq/loop'

SYNTAX_TIP = (
    '每行一条：send <文本> / sendhex <十六进制> / delay <毫秒>，# 开头为注释。\n'
    '例：\n'
    '  sendhex 01 03 00 00 00 01\n'
    '  delay 200\n'
    '  send AT+VERSION?\n'
    '发送走主窗连接（未连接会中止）；重复次数/循环见下方选项')


class TxSequenceDialog(QtWidgets.QDialog):
    """非模态对话框；宿主为 MainWindow（需要 _send_tx_bytes/_append_log/worker）。"""

    def __init__(self, win, parent=None):
        super().__init__(parent or win)
        self.setWindowTitle('序列发送')
        self.setModal(False)
        self._win = win
        self._runner: TxSequenceRunner | None = None

        self.edit_script = QtWidgets.QPlainTextEdit(read_setting(SCRIPT_KEY, ''))
        self.edit_script.setPlaceholderText(SYNTAX_TIP)
        self.edit_script.setToolTip(SYNTAX_TIP)

        self.spin_repeat = QtWidgets.QSpinBox()
        self.spin_repeat.setRange(1, 9999)
        self.spin_repeat.setValue(max(1, int(read_setting(REPEAT_KEY, '1') or '1')))
        self.spin_repeat.setToolTip('整段脚本重复次数')
        self.chk_loop = QtWidgets.QCheckBox('循环直到停止')
        self.chk_loop.setChecked(read_setting(LOOP_KEY, '0') in ('1', 'true', 'yes'))
        self.chk_auto = QtWidgets.QCheckBox('连接后自动执行')
        self.chk_auto.setToolTip('每次串口连上（含自动重连）后自动开始本序列')
        self.chk_auto.setChecked(read_setting(AUTO_RUN_KEY, '0') in ('1', 'true', 'yes'))

        self.btn_start = QtWidgets.QPushButton('开始')
        self.btn_start.clicked.connect(self.start_clicked)
        self.btn_stop = QtWidgets.QPushButton('停止')
        self.btn_stop.setObjectName('tool')
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_clicked)

        self.lbl_state = QtWidgets.QLabel('就绪')
        self.lbl_state.setObjectName('backlog')

        opts = QtWidgets.QHBoxLayout()
        opts.addWidget(QtWidgets.QLabel('重复'))
        opts.addWidget(self.spin_repeat)
        opts.addWidget(self.chk_loop)
        opts.addWidget(self.chk_auto)
        opts.addStretch(1)
        opts.addWidget(self.btn_start)
        opts.addWidget(self.btn_stop)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.edit_script, 1)
        lay.addLayout(opts)
        lay.addWidget(self.lbl_state)

    # ---------- 控制 ----------

    def start_clicked(self):
        if self._runner and self._runner.is_running():
            return
        try:
            steps = parse_script(self.edit_script.toPlainText())
        except TxSequenceError as exc:
            self.lbl_state.setText(f'⚠ {exc}')
            return
        write_setting(SCRIPT_KEY, self.edit_script.toPlainText())
        write_setting(REPEAT_KEY, str(self.spin_repeat.value()))
        write_setting(LOOP_KEY, '1' if self.chk_loop.isChecked() else '0')
        write_setting(AUTO_RUN_KEY, '1' if self.chk_auto.isChecked() else '0')
        repeat = 1 << 30 if self.chk_loop.isChecked() else self.spin_repeat.value()
        self._runner = TxSequenceRunner(steps, self._txseq_send, self)
        self._runner.step_started.connect(self._on_step)
        self._runner.finished.connect(self._on_finished)
        self._runner.start(repeat)
        if self._runner.is_running():
            # 首步可能同步中止（未连接/写失败）——finished 已恢复按钮，别覆盖
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)

    def stop_clicked(self):
        if self._runner:
            self._runner.stop()

    def auto_start(self):
        """主窗连接后自动执行入口：开关开着且脚本合法才开始。"""
        if self._runner and self._runner.is_running():
            return   # 已在跑（如自动重连），不打断
        self.start_clicked()

    def _txseq_send(self, step) -> bool:
        if step.kind == 'send':
            display = step.text
        else:
            from core.hexutil import bytes_to_hex_text
            display = bytes_to_hex_text(step.payload)
        return self._win._send_tx_bytes(step.payload, display)

    def _on_step(self, idx: int, desc: str):
        self.lbl_state.setText(f'第 {idx + 1} 步: {desc}')

    def _on_finished(self, completed: bool):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lbl_state.setText('完成' if completed else '已中止（停止/未连接/写失败）')

    # ---------- 生命周期 ----------

    def closeEvent(self, event):  # noqa: N802 (Qt 命名)
        if self._runner and self._runner.is_running():
            self._runner.stop()
        write_setting(SCRIPT_KEY, self.edit_script.toPlainText())
        super().closeEvent(event)
