# -*- coding: utf-8 -*-
"""设置页签：主题（深色/浅色/自定义，即时生效 + 写 gui_settings_qt.ini 记忆）、
自定义配色（外观标准化六键：窗口背景/控件底/正文文字/强调色/日志底/日志文字，
WCAG 对比度即时复核并警示）、页面显示（STM32/Linux 支持页显隐记忆）、
「曲线填充」开关（迁自波形页工具行，读写波形页同款设置键）、
STM32CubeProgrammer CLI 路径预留（文件选择器 + 记忆，默认空=自动探测）。

与波形页衔接：填充开关直接读写 wave_window.FILL_SETTING_KEY（wave/fill_under，
主线口径仅显式 'false' 关），变更后调用 wave.apply_fill_from_setting() 立即生效。
"""
from __future__ import annotations

import json

from PySide6 import QtGui, QtWidgets

from core.settings import read_setting, write_setting
from ui import theme
from ui.wave_window import FILL_SETTING_KEY

THEME_SETTING_KEY = 'ui/theme'          # 主题记忆键（theme.apply_saved_theme 读回）
STM32_CLI_PATH_KEY = 'stm32_cli_path'   # CubeProgrammer CLI 路径键（默认空=自动探测）
SHOW_STM32_KEY = 'ui/show_stm32'        # 页面显隐记忆键
SHOW_SCREEN_KEY = 'ui/show_screen'      # （缺省显示；主窗监听 toggled 应用）


class SettingsPage(QtWidgets.QWidget):
    """设置页：外观（主题/自定义配色）/ 页面显示 / 波形 / 连接（ESP32）/ 烧录。"""

    def __init__(self, wave=None, parent=None):
        super().__init__(parent)
        self.wave = wave   # 波形页引用：填充开关变更后让其立即生效（测试可注入替身）
        self._syncing = False
        self._custom_overrides: dict = self._load_overrides()

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

        # ---- 外观：自定义配色（仅自定义主题下显示） ----
        self.custom_box = QtWidgets.QWidget()
        custom_lay = QtWidgets.QVBoxLayout(self.custom_box)
        custom_lay.setContentsMargins(0, 0, 0, 0)
        custom_lay.setSpacing(6)

        base_row = QtWidgets.QHBoxLayout()
        base_row.addWidget(QtWidgets.QLabel('基准主题（曲线色/绘图区继承基准）'))
        self.combo_custom_base = QtWidgets.QComboBox()
        self.combo_custom_base.addItem('深色', theme.DARK_KEY)
        self.combo_custom_base.addItem('浅色', theme.LIGHT_KEY)
        self.combo_custom_base.setCurrentIndex(self.combo_custom_base.findData(
            read_setting(theme.CUSTOM_BASE_KEY, theme.DARK_KEY)))
        self.combo_custom_base.currentIndexChanged.connect(self._on_custom_base_changed)
        base_row.addWidget(self.combo_custom_base)
        base_row.addStretch(1)
        custom_lay.addLayout(base_row)

        self._color_buttons: dict[str, QtWidgets.QPushButton] = {}
        color_grid = QtWidgets.QGridLayout()
        color_grid.setHorizontalSpacing(12)
        for i, (key, label) in enumerate(theme.CUSTOM_EDITABLE.items()):
            btn = QtWidgets.QPushButton(label)
            btn.setObjectName('tool')
            btn.setFixedWidth(110)
            btn.clicked.connect(lambda _=False, k=key: self._pick_color(k))
            self._color_buttons[key] = btn
            color_grid.addWidget(btn, i // 3, i % 3)
        custom_lay.addLayout(color_grid)

        self.lbl_contrast = QtWidgets.QLabel('')
        self.lbl_contrast.setObjectName('status_err')
        self.lbl_contrast.setWordWrap(True)
        custom_lay.addWidget(self.lbl_contrast)

        btn_row = QtWidgets.QHBoxLayout()
        btn_reset_custom = QtWidgets.QPushButton('恢复默认配色')
        btn_reset_custom.setObjectName('tool')
        btn_reset_custom.setToolTip('清空全部自定义覆盖，回到基准主题原配色')
        btn_reset_custom.clicked.connect(self._reset_custom)
        btn_row.addWidget(btn_reset_custom)
        btn_row.addStretch(1)
        custom_lay.addLayout(btn_row)
        self.custom_box.setVisible(theme.current_theme() == theme.CUSTOM_KEY)
        self._refresh_custom_ui()

        # ---- 页面显示（显隐记忆，主窗监听后 setTabVisible） ----
        self.chk_show_stm32 = QtWidgets.QCheckBox('显示 STM32 页（CubeProgrammer 烧录）')
        self.chk_show_stm32.setChecked(read_setting(SHOW_STM32_KEY, '1') != '0')
        self.chk_show_stm32.toggled.connect(lambda on: write_setting(
            SHOW_STM32_KEY, '1' if on else '0'))
        self.chk_show_screen = QtWidgets.QCheckBox('显示 Linux 支持页（设备屏投屏）')
        self.chk_show_screen.setChecked(read_setting(SHOW_SCREEN_KEY, '1') != '0')
        self.chk_show_screen.toggled.connect(lambda on: write_setting(
            SHOW_SCREEN_KEY, '1' if on else '0'))
        self.chk_on_top = QtWidgets.QCheckBox('窗口置顶')
        self.chk_on_top.setToolTip('总在最前（台架看日志常用）；主窗监听本开关即时应用')
        self.chk_on_top.setChecked(read_setting('ui/on_top', '0') in ('1', 'true', 'yes'))
        self.chk_on_top.toggled.connect(lambda on: write_setting(
            'ui/on_top', '1' if on else '0'))

        # ---- 日志：关键词高亮 / 选中即复制 / 时间戳模式 ----
        self.edit_kw_alert = QtWidgets.QLineEdit(read_setting('log/kw_alert', ''))
        self.edit_kw_alert.setPlaceholderText('如: error, assert, fail, over temp')
        self.edit_kw_alert.setToolTip(
            '错误关键词，逗号或空格分隔：RX 行命中任一词整行红色加粗。\n'
            '大小写不敏感；匹配剥掉 ANSI 转义后的文本；关键词色优先于设备色（用户意图优先）。'
            '立即生效，无需重启')
        self.edit_kw_alert.editingFinished.connect(lambda: write_setting(
            'log/kw_alert', self.edit_kw_alert.text().strip()))
        self.edit_kw_watch = QtWidgets.QLineEdit(read_setting('log/kw_watch', ''))
        self.edit_kw_watch.setPlaceholderText('如: retry, timeout, warning')
        self.edit_kw_watch.setToolTip(
            '关注关键词，逗号或空格分隔：RX 行命中任一词整行琥珀色（弱于错误级）。\n'
            '与错误关键词同时命中按错误级处理')
        self.edit_kw_watch.editingFinished.connect(lambda: write_setting(
            'log/kw_watch', self.edit_kw_watch.text().strip()))
        self.chk_copy_on_select = QtWidgets.QCheckBox('选中日志即复制（PuTTY 风格）')
        self.chk_copy_on_select.setToolTip(
            '鼠标选中的日志文本立即进剪贴板（拖选过程持续同步）；默认关')
        self.chk_copy_on_select.setChecked(
            read_setting('log/copy_on_select', '0') in ('1', 'true', 'yes'))
        self.chk_copy_on_select.toggled.connect(lambda on: write_setting(
            'log/copy_on_select', '1' if on else '0'))
        self.chk_elapsed_stamp = QtWidgets.QCheckBox('时间戳显示为连接起的相对秒数')
        self.chk_elapsed_stamp.setToolTip(
            '开 = 每行时间戳是 [xx.xxxs]（自连接/重连起算，对齐示波器口径）；\n'
            '关 = 全时刻 [YYYY-MM-DD HH:MM:SS.mmm]（默认；导出与 Ctrl+F 时间定位用它）')
        self.chk_elapsed_stamp.setChecked(read_setting('log/stamp_mode', 'clock') == 'elapsed')
        self.chk_elapsed_stamp.toggled.connect(lambda on: write_setting(
            'log/stamp_mode', 'elapsed' if on else 'clock'))

        # ---- 波形：曲线填充（读写波形页原设置键，主线口径缺省开） ----
        self.chk_fill = QtWidgets.QCheckBox('曲线填充（曲线下方同色浅填充）')
        self.chk_fill.setToolTip(
            '波形页每条曲线下方按通道色 alpha=26 浅填充（主线同款）；'
            f'设置键 {FILL_SETTING_KEY}，切换后波形页立即生效')
        self.chk_fill.setChecked(read_setting(FILL_SETTING_KEY, 'true').lower() != 'false')
        self.chk_fill.toggled.connect(self._on_fill_toggled)

        # ---- 连接：ESP32（v1.3.0，主窗连接时读取） ----
        self.chk_dtr_neutral = QtWidgets.QCheckBox('打开串口时不拉 DTR/RTS（ESP32 防复位）')
        self.chk_dtr_neutral.setToolTip(
            'ESP32 自动下载电路把 DTR/RTS 映射到 IO0/EN，打开端口时默认拉线会复位板子；\n'
            '勾选后打开串口立即松开两根线。对 loop:// 等虚拟端口自动忽略')
        self.chk_dtr_neutral.setChecked(
            read_setting('connect/dtr_rts_neutral', '0') in ('1', 'true', 'yes'))
        self.chk_dtr_neutral.toggled.connect(lambda on: write_setting(
            'connect/dtr_rts_neutral', '1' if on else '0'))
        self.chk_auto_reconnect = QtWidgets.QCheckBox('断线自动重连（每 2 秒重试，设备重插后自动接回）')
        self.chk_auto_reconnect.setToolTip(
            '运行中串口异常时不立刻断开，后台每 2s 按原参数重试；\n'
            '适配烧录后 COM 重枚举、板子自动复位等场景')
        self.chk_auto_reconnect.setChecked(
            read_setting('connect/auto_reconnect', '0') in ('1', 'true', 'yes'))
        self.chk_auto_reconnect.toggled.connect(lambda on: write_setting(
            'connect/auto_reconnect', '1' if on else '0'))

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
        layout.addWidget(self.custom_box)
        layout.addWidget(QtWidgets.QLabel('页面显示'))
        layout.addWidget(self.chk_show_stm32)
        layout.addWidget(self.chk_show_screen)
        layout.addWidget(self.chk_on_top)
        layout.addWidget(QtWidgets.QLabel('日志'))
        kw_row1 = QtWidgets.QHBoxLayout()
        kw_row1.addWidget(QtWidgets.QLabel('错误关键词'))
        kw_row1.addWidget(self.edit_kw_alert, 1)
        kw_row2 = QtWidgets.QHBoxLayout()
        kw_row2.addWidget(QtWidgets.QLabel('关注关键词'))
        kw_row2.addWidget(self.edit_kw_watch, 1)
        layout.addLayout(kw_row1)
        layout.addLayout(kw_row2)
        layout.addWidget(self.chk_copy_on_select)
        layout.addWidget(self.chk_elapsed_stamp)
        layout.addWidget(QtWidgets.QLabel('波形'))
        layout.addWidget(self.chk_fill)
        layout.addWidget(QtWidgets.QLabel('连接（ESP32）'))
        layout.addWidget(self.chk_dtr_neutral)
        layout.addWidget(self.chk_auto_reconnect)
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
        self.custom_box.setVisible(key == theme.CUSTOM_KEY)
        if key == theme.CUSTOM_KEY:
            self._refresh_custom_ui()

    # ---------- 自定义配色 ----------

    def _load_overrides(self) -> dict:
        try:
            data = json.loads(read_setting(theme.CUSTOM_COLORS_KEY, '') or '{}')
        except (ValueError, TypeError):
            return {}
        return {k: v for k, v in data.items()
                if k in theme.CUSTOM_EDITABLE and isinstance(v, str)}

    def _pick_color(self, key: str):
        current = theme.palette('custom').get(key, '#000000')
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(current), self, f'选择{theme.CUSTOM_EDITABLE[key]}')
        if not color.isValid():
            return
        self.apply_custom_color(key, color.name())

    def apply_custom_color(self, key: str, hex_color: str):
        """程序化改色入口（测试/脚本共用）：更新覆盖 → 主题模块重刷 → 界面联动。"""
        self._custom_overrides[key] = hex_color
        issues = theme.set_custom_colors(
            self._custom_overrides, app=QtWidgets.QApplication.instance())
        self._refresh_custom_ui()
        self._show_contrast_issues(issues)

    def _on_custom_base_changed(self, index: int):
        base = self.combo_custom_base.itemData(index)
        if not base:
            return
        issues = theme.set_custom_colors(
            self._custom_overrides, base=str(base),
            app=QtWidgets.QApplication.instance())
        self._refresh_custom_ui()
        self._show_contrast_issues(issues)

    def _reset_custom(self):
        self._custom_overrides = {}
        issues = theme.set_custom_colors(
            {}, base=str(self.combo_custom_base.currentData()),
            app=QtWidgets.QApplication.instance())
        self._refresh_custom_ui()
        self._show_contrast_issues(issues)

    def _refresh_custom_ui(self):
        """取色按钮底色/文字色跟随当前自定义配色，警示文案重算。"""
        p = theme.palette('custom')
        for key, btn in self._color_buttons.items():
            fg, bg = p['text'], p[key]
            btn.setStyleSheet(
                f'QPushButton#tool {{ background: {bg}; color: {fg}; }}')
        if not self.custom_box.isHidden():
            self._show_contrast_issues(theme.custom_palette_issues(p))

    def _show_contrast_issues(self, issues: list):
        if issues:
            self.lbl_contrast.setText('⚠ 对比度不足: ' + '；'.join(w for w, _ in issues))
        else:
            self.lbl_contrast.setText('')

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
