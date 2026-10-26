# -*- coding: utf-8 -*-
"""设置页签：主题（深色/浅色，即时生效 + 写 gui_settings_qt.ini 记忆）、
「曲线填充」开关（迁自波形页工具行，读写波形页同款设置键）、
STM32CubeProgrammer CLI 路径预留（文件选择器 + 记忆，默认空=自动探测，
下一阶段 STM32 页会读该设置键）。

与波形页衔接：填充开关直接读写 wave_window.FILL_SETTING_KEY（wave/fill_under，
主线口径仅显式 'false' 关），变更后调用 wave.apply_fill_from_setting() 立即生效。
"""
from __future__ import annotations

from PySide6 import QtWidgets

from core.settings import read_setting, write_setting
from ui import theme
from ui.wave_window import FILL_SETTING_KEY

THEME_SETTING_KEY = 'ui/theme'          # 主题记忆键（theme.apply_saved_theme 读回）
STM32_CLI_PATH_KEY = 'stm32_cli_path'   # CubeProgrammer CLI 路径键（默认空=自动探测）


class SettingsPage(QtWidgets.QWidget):
    """设置页：外观（主题）/ 波形（曲线填充）/ 烧录（STM32 CLI 路径预留）。"""

    def __init__(self, wave=None, parent=None):
        super().__init__(parent)
        self.wave = wave   # 波形页引用：填充开关变更后让其立即生效（测试可注入替身）
        self._syncing = False

        # ---- 外观：主题 ----
        self.combo_theme = QtWidgets.QComboBox()
        for key, label in theme.THEME_LABELS.items():
            self.combo_theme.addItem(label, key)
        self.combo_theme.setCurrentIndex(
            self.combo_theme.findData(theme.current_theme()))
        self.combo_theme.setToolTip('界面主题：切换即时生效并写入设置记忆，重启后保持')
        self.combo_theme.currentIndexChanged.connect(self._on_theme_changed)

        appearance_row = QtWidgets.QHBoxLayout()
        appearance_row.addWidget(QtWidgets.QLabel('主题'))
        appearance_row.addWidget(self.combo_theme)
        appearance_row.addStretch(1)

        # ---- 波形：曲线填充（读写波形页原设置键，主线口径缺省开） ----
        self.chk_fill = QtWidgets.QCheckBox('曲线填充（曲线下方同色浅填充）')
        self.chk_fill.setToolTip(
            '波形页每条曲线下方按通道色 alpha=26 浅填充（主线同款）；'
            f'设置键 {FILL_SETTING_KEY}，切换后波形页立即生效')
        self.chk_fill.setChecked(read_setting(FILL_SETTING_KEY, 'true').lower() != 'false')
        self.chk_fill.toggled.connect(self._on_fill_toggled)

        # ---- 烧录：STM32CubeProgrammer CLI 路径（预留，默认空=自动探测） ----
        self.edit_cli = QtWidgets.QLineEdit(read_setting(STM32_CLI_PATH_KEY, ''))
        self.edit_cli.setReadOnly(True)
        self.edit_cli.setPlaceholderText('留空 = 自动探测 STM32_Programmer_CLI.exe')
        self.edit_cli.setToolTip(
            'STM32CubeProgrammer 命令行（STM32_Programmer_CLI.exe）完整路径；'
            '留空时下一阶段 STM32 页按常见安装位置自动探测')
        self.btn_cli_browse = QtWidgets.QPushButton('浏览…')
        self.btn_cli_browse.setObjectName('tool')
        self.btn_cli_browse.clicked.connect(self._browse_cli)
        self.btn_cli_reset = QtWidgets.QPushButton('恢复自动探测')
        self.btn_cli_reset.setObjectName('tool')
        self.btn_cli_reset.setToolTip('清空已记忆的 CLI 路径，回到自动探测')
        self.btn_cli_reset.setEnabled(bool(self.edit_cli.text()))
        self.btn_cli_reset.clicked.connect(lambda: self.set_cli_path(''))

        cli_row = QtWidgets.QHBoxLayout()
        cli_row.addWidget(QtWidgets.QLabel('STM32CubeProgrammer CLI'))
        cli_row.addWidget(self.edit_cli, 1)
        cli_row.addWidget(self.btn_cli_browse)
        cli_row.addWidget(self.btn_cli_reset)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(QtWidgets.QLabel('外观'))
        layout.addLayout(appearance_row)
        layout.addWidget(QtWidgets.QLabel('波形'))
        layout.addWidget(self.chk_fill)
        layout.addWidget(QtWidgets.QLabel('烧录'))
        layout.addLayout(cli_row)
        layout.addStretch(1)

    # ---------- 主题 ----------

    def _on_theme_changed(self, index: int):
        if self._syncing:
            return
        key = self.combo_theme.itemData(index)
        if not key:
            return
        write_setting(THEME_SETTING_KEY, str(key))
        theme.set_theme(str(key))

    # ---------- 曲线填充（与波形页同键） ----------

    def _on_fill_toggled(self, on: bool):
        write_setting(FILL_SETTING_KEY, 'true' if on else 'false')
        if self.wave is not None:
            self.wave.apply_fill_from_setting()

    # ---------- STM32 CLI 路径 ----------

    def set_cli_path(self, path: str):
        """记忆 CLI 路径（空串=恢复自动探测）。对话框与测试共用此入口。"""
        clean = (path or '').strip()
        write_setting(STM32_CLI_PATH_KEY, clean)
        self.edit_cli.setText(clean)
        self.btn_cli_reset.setEnabled(bool(clean))

    def _browse_cli(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择 STM32CubeProgrammer CLI', self.edit_cli.text(),
            'STM32_Programmer_CLI (STM32_Programmer_CLI.exe);;所有文件 (*.*)')
        if path:
            self.set_cli_path(path)
