# -*- coding: utf-8 -*-
"""backtrace 解析对话框：收集到的 ESP32 崩溃地址 → addr2line → 源码位置。

用法：日志区检测到 Guru Meditation + backtrace 后「解析 backtrace」按钮点亮，
对话框里给 ELF 路径与 addr2line 工具（默认自动探测 IDF 工具链里的
xtensa-esp32{,s2,s3}-elf-addr2line / riscv32-esp-elf-addr2line），解析结果就地展示。
所有路径/芯片选择写 gui_settings_qt.ini 记忆。
"""
from __future__ import annotations

import os
import shutil
import subprocess

from PySide6 import QtWidgets

from core.settings import read_setting, write_setting

ELF_KEY = 'esp32/elf_path'
TOOL_KEY = 'esp32/addr2line_path'
CHIP_KEY = 'esp32/chip'

CHIPS = ('esp32', 'esp32s2', 'esp32s3', 'esp32c3')
CHIP_TOOL_HINT = {'esp32': 'xtensa-esp32-elf-addr2line',
                  'esp32s2': 'xtensa-esp32s2-elf-addr2line',
                  'esp32s3': 'xtensa-esp32s3-elf-addr2line',
                  'esp32c3': 'riscv32-esp-elf-addr2line'}
PROBE_TOOLS = ('xtensa-esp32-elf-addr2line', 'xtensa-esp32s3-elf-addr2line',
               'xtensa-esp32s2-elf-addr2line', 'riscv32-esp-elf-addr2line')
CHUNK = 64      # 单批地址上限（防超长命令行）
TIMEOUT_S = 10


def probe_addr2line() -> str:
    """按 IDF 常见名在 PATH 里探测 addr2line；找不到返回空串。"""
    for name in PROBE_TOOLS:
        found = shutil.which(name)
        if found:
            return found
    return ''


class BacktraceDialog(QtWidgets.QDialog):
    """崩溃地址 → 源码位置解析（同步 subprocess，10s 超时，结果就地展示）。"""

    def __init__(self, addresses: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle('解析 backtrace（ESP32 崩溃地址 → 源码位置）')
        self.resize(680, 460)
        self._addresses = [a for a in addresses if a]

        head = ('已收集 '
                f'{len(self._addresses)} 个地址: ' + ' '.join(self._addresses[:8])
                + (' …' if len(self._addresses) > 8 else '')
                ) if self._addresses else '尚未收集到 backtrace 地址'

        self.lbl_info = QtWidgets.QLabel(head)
        self.lbl_info.setWordWrap(True)

        self.edit_elf = QtWidgets.QLineEdit(read_setting(ELF_KEY, ''))
        self.edit_elf.setPlaceholderText('工程 ELF 文件（如 build/xxx.elf；idf.py build 产物）')
        btn_elf = QtWidgets.QPushButton('浏览…')
        btn_elf.setObjectName('tool')
        btn_elf.clicked.connect(self._browse_elf)

        self.edit_tool = QtWidgets.QLineEdit(read_setting(TOOL_KEY, '') or probe_addr2line())
        self.edit_tool.setPlaceholderText(
            'addr2line 可执行文件（自动探测 PATH；也可手动选 IDF 工具链里的，如 '
            'xtensa-esp32-elf-addr2line.exe）')
        btn_tool = QtWidgets.QPushButton('浏览…')
        btn_tool.setObjectName('tool')
        btn_tool.clicked.connect(self._browse_tool)

        self.combo_chip = QtWidgets.QComboBox()
        self.combo_chip.addItems(CHIPS)
        saved_chip = read_setting(CHIP_KEY, 'esp32')
        if saved_chip in CHIPS:
            self.combo_chip.setCurrentText(saved_chip)
        self.combo_chip.setToolTip('芯片型号决定 addr2line 工具链前缀（C3 为 RISC-V 工具链）')

        self.btn_run = QtWidgets.QPushButton('解析')
        self.btn_run.clicked.connect(self._run)

        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText(
            '解析结果（函数 @ 文件:行）。找不到工具链时：安装 ESP-IDF 后把 '
            '<IDF>/tools/xtensa-esp-elf/…/bin 加进 PATH，或在此手动选择 addr2line。')

        form = QtWidgets.QGridLayout()
        form.addWidget(QtWidgets.QLabel('ELF'), 0, 0)
        form.addWidget(self.edit_elf, 0, 1)
        form.addWidget(btn_elf, 0, 2)
        form.addWidget(QtWidgets.QLabel('addr2line'), 1, 0)
        form.addWidget(self.edit_tool, 1, 1)
        form.addWidget(btn_tool, 1, 2)
        form.addWidget(QtWidgets.QLabel('芯片'), 2, 0)
        form.addWidget(self.combo_chip, 2, 1)

        btns = QtWidgets.QHBoxLayout()
        btns.addWidget(self.btn_run)
        btns.addStretch(1)
        btn_close = QtWidgets.QPushButton('关闭')
        btn_close.setObjectName('tool')
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.lbl_info)
        lay.addLayout(form)
        lay.addLayout(btns)
        lay.addWidget(self.output, 1)

    # ---------- 交互 ----------

    def _browse_elf(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, '选择 ELF 文件',
                                                        self.edit_elf.text(),
                                                        'ELF (*.elf);;所有文件 (*.*)')
        if path:
            self.edit_elf.setText(path)

    def _browse_tool(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, '选择 addr2line',
                                                        self.edit_tool.text(),
                                                        '可执行文件 (*.exe);;所有文件 (*.*)')
        if path:
            self.edit_tool.setText(path)

    def _remember(self):
        write_setting(ELF_KEY, self.edit_elf.text().strip())
        write_setting(TOOL_KEY, self.edit_tool.text().strip())
        write_setting(CHIP_KEY, self.combo_chip.currentText())

    # ---------- 解析 ----------

    def _run(self):
        elf = self.edit_elf.text().strip()
        tool = self.edit_tool.text().strip()
        if not self._addresses:
            self.output.setPlainText('没有可解析的地址（先在日志里出现 backtrace）。')
            return
        if not elf or not os.path.isfile(elf):
            self.output.setPlainText(f'ELF 文件无效: {elf or "(空)"}\n'
                                     '请选择 idf.py build 产出的 .elf 文件。')
            return
        if not tool:
            chip = self.combo_chip.currentText()
            self.output.setPlainText('未找到 addr2line。\n'
                                     f'{chip} 对应工具: {CHIP_TOOL_HINT[chip]}\n'
                                     '请安装 ESP-IDF 或手动选择工具链里的 addr2line。')
            return
        self._remember()
        QtWidgets.QApplication.setOverrideCursor(QtWidgets.Qt.CursorShape.WaitCursor)
        chunks_out: list[str] = []
        try:
            for i in range(0, len(self._addresses), CHUNK):
                part = self._addresses[i:i + CHUNK]
                proc = subprocess.run(
                    [tool, '-pfiaC', '-e', elf, *part],
                    capture_output=True, text=True, errors='replace',
                    timeout=TIMEOUT_S,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if proc.returncode != 0:
                    chunks_out.append(f'[addr2line 返回 {proc.returncode}] {proc.stderr.strip()}')
                chunks_out.append(proc.stdout.strip())
            body = '\n'.join(c for c in chunks_out if c)
            self.output.setPlainText(body or '（addr2line 无输出——ELF 与地址可能不匹配）')
        except FileNotFoundError:
            self.output.setPlainText(f'addr2line 启动失败: {tool}\n路径不存在或不可执行。')
        except subprocess.TimeoutExpired:
            self.output.setPlainText(f'addr2line 超时（>{TIMEOUT_S}s）——ELF 过大或工具挂起。')
        except OSError as exc:
            self.output.setPlainText(f'addr2line 执行失败: {exc}')
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
