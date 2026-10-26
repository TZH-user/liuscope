# -*- coding: utf-8 -*-
"""双主题体系（深色/浅色）：一套配色字典 + 模板化 QSS，全局声明式一步生效。

配色基调与主线一致：深色底 #1E2430、输入 #262D3A、强调 #0E639C（VS Code 蓝）；
浅色为同构亮色版（白底绘图区 + 整体加深的曲线/轨迹色保证对比度；
按钮统一蓝底白字，禁用态弱化蓝灰底，全部文本/底色配对过 WCAG 对比度审计）。

统一取色接口：palette()/color()/curve_colors()，各页建页与换肤共用；
切换入口 set_theme()（换全局 QSS + pyqtgraph 背景/前景 + 通知已注册页面刷新），
启动恢复 apply_saved_theme()（读设置键 ui/theme，缺省深色，重启保持）。
"""
from __future__ import annotations

from PySide6 import QtWidgets

import pyqtgraph as pg

from core.settings import read_setting

THEME_SETTING_KEY = 'ui/theme'
DARK_KEY = 'dark'
LIGHT_KEY = 'light'
THEME_LABELS = {'dark': '深色', 'light': '浅色'}

# 默认曲线 16 色（主线 get_wave_channel_color 同色号，深色基准；浅色套件见 LIGHT）
_CURVE_COLORS_DARK = ['#00C8FF', '#FF6E6E', '#78FF8C', '#FFDC00',
                      '#FF82FF', '#AA8CFF', '#FFA03C', '#00FFC8',
                      '#5AA0FF', '#FF69B4', '#B4FF3C', '#FFFA96',
                      '#0096FF', '#C878FF', '#78DC78', '#FFC878']
# 浅色底（白）上整体加深、拉开明度的同构 16 色（黄/浅绿等高明度色全部压暗）
_CURVE_COLORS_LIGHT = ['#0090D8', '#D93025', '#1E8E3E', '#B07700',
                       '#C2185B', '#7B5CD6', '#D96400', '#00897B',
                       '#2962CC', '#D81B60', '#4C8A16', '#9A8B00',
                       '#01579B', '#8E24AA', '#388E3C', '#B26A00']

# 完整配色键：窗体/控件/文本/绘图区背景/网格前景/曲线色/状态色/日志行色……
DARK = dict(
    window='#1E2430',          # 窗体底
    panel='#1C212D',           # 页签栏/菜单/滚动条底
    control='#262D3A',         # 输入控件底
    border='#3E546A',          # 控件边框/滑块
    text='#E1E6F0',            # 主文本
    text_dim='#969EAC',        # 弱文本（backlog 等）
    accent='#0E639C',          # 强调（按钮/选中）
    accent_hover='#1177BB',
    accent_pressed='#0A4E7E',
    tool_btn='#2A3244',        # 工具行小按钮
    tool_btn_hover='#38435A',
    btn_disabled_bg='#3E546A', # 禁用按钮底（=border 色，白字对比 7.8 可辨）
    log_bg='#161B24',          # 日志/文本域底
    log_border='#2A3140',
    log_text='#D4D4D4',
    plot_bg='#161B26',         # pyqtgraph 绘图区底
    plot_fg='#8A93A6',         # 坐标轴/刻度/网格（pyqtgraph 0.14 同一支笔）
    chip_border='#8FA1BC',     # CH/自动Y 复选框 indicator 边框
    chip_border_hi='#C8D2E0',
    chip_bg='#20262F',
    flash_up='#80DC80',        # Modbus 监视值闪烁高亮（升/降/变化）
    flash_down='#EB766C',
    flash_changed='#E8C66E',
    status_ok='#4EC9B0',
    status_err='#FF6B6B',      # 窗体底上 ≥4.5（#F14C4C 仅 4.35）
    status_warn='#FFC85A',     # 导出对话框警告文本
    rx='#9CDCFE',              # 日志 RX（VS Code 变量蓝）
    tx='#CE9178',              # 日志 TX（VS Code 字符串橙）
    sys='#6A9955',             # 日志 SYS（VS Code 注释绿）
    time='#7E889B',            # 时间戳（对 log_bg ≥4.5）
    crosshair='#5AA0FF',       # 悬停十字线
    readout_bg=(18, 22, 32, 225),   # 悬停读数框底（RGBA）
    readout_border='#3E546A',
    curve_colors=list(_CURVE_COLORS_DARK),
    sp_trace='#78D2FF',        # 频谱轨迹
    sp_peak='#A0C85A',         # 峰值保持
    sp_avg='#50BEC8',          # 指数平均
    sp_marker=(255, 150, 150), # Marker 线
    sp_fill=(120, 210, 255, 30),    # 轨迹线下填充
)

LIGHT = dict(
    window='#F2F4F8',
    panel='#E8ECF2',
    control='#FFFFFF',
    border='#B9C2CF',
    text='#24292F',
    text_dim='#5A6472',
    accent='#0E639C',          # 强调色两套主题保持同族蓝
    accent_hover='#1177BB',
    accent_pressed='#0A4E7E',
    tool_btn='#0E639C',        # 工具行小按钮：与 accent 同族蓝（白字对比 6.4，
    tool_btn_hover='#1177BB',  # 旧浅灰 #DDE3EC 配白字几乎不可读，统一蓝底白字）
    btn_disabled_bg='#7E93A8', # 禁用按钮：弱化蓝灰底+白字（对比 3.2，既可辨
                               # 「禁用」又不与 enabled 深蓝混淆；旧 border 灰仅 1.8）
    log_bg='#FFFFFF',
    log_border='#C9D1DC',
    log_text='#2F3540',
    plot_bg='#FFFFFF',
    plot_fg='#4A5568',
    chip_border='#7E8CA0',
    chip_border_hi='#39546F',
    chip_bg='#FFFFFF',
    flash_up='#0E7A3A',        # 浅色底上的闪烁高亮（亮色版仅 ~1.7 不可读）
    flash_down='#C0392B',
    flash_changed='#8A5C00',
    status_ok='#0A7A60',       # 窗体底上 ≥4.5（#0E8A6E 仅 3.9）
    status_err='#C42B2B',
    status_warn='#8F6100',     # 导出对话框警告文本（#FFC85A 在白底仅 1.4 不可读）
    rx='#0B69A3',
    tx='#A0522D',
    sys='#3F7B3F',
    time='#67717D',            # 时间戳（对白底 log_bg ≥4.5；#8A93A0 仅 3.1）
    crosshair='#2962CC',
    readout_bg=(255, 255, 255, 230),
    readout_border='#9AA6B8',
    curve_colors=list(_CURVE_COLORS_LIGHT),
    sp_trace='#0F7AC4',
    sp_peak='#5E8F1F',
    sp_avg='#0E8A96',
    sp_marker=(200, 60, 60),
    sp_fill=(15, 122, 196, 30),
)


def _build_qss(p: dict) -> str:
    """由配色字典模板化生成全局 QSS（深浅两套同构，选择器与旧深色版一致）。"""
    return f"""
QMainWindow, QWidget {{ background: {p['window']}; color: {p['text']};
                       font-family: 'Microsoft YaHei UI'; font-size: 9pt; }}
QComboBox, QLineEdit {{ background: {p['control']}; color: {p['text']};
                       border: 1px solid {p['border']}; border-radius: 4px; padding: 3px 6px; }}
QComboBox QAbstractItemView {{ background: {p['control']}; color: {p['text']};
                              selection-background-color: {p['accent']}; }}
QPlainTextEdit, QTextEdit {{ background: {p['log_bg']}; color: {p['log_text']};
                            border: 1px solid {p['log_border']}; border-radius: 4px; }}
QPushButton {{ background: {p['accent']}; color: white; border: none;
              border-radius: 4px; padding: 6px 14px; }}
QPushButton:hover {{ background: {p['accent_hover']}; }}
QPushButton:pressed {{ background: {p['accent_pressed']}; }}
QPushButton:disabled {{ background: {p['btn_disabled_bg']}; }}
QPushButton#tool {{ background: {p['tool_btn']}; padding: 3px 10px; }}
QPushButton#tool:hover {{ background: {p['tool_btn_hover']}; }}
QPushButton#tool:checked {{ background: {p['accent']}; color: white; }}
/* #tool 是 ID 选择器，优先级压过上面的 :disabled，必须显式补禁用态底色 */
QPushButton#tool:disabled {{ background: {p['btn_disabled_bg']}; }}
QTabWidget::pane {{ border: 1px solid {p['log_border']}; }}
QTabBar::tab {{ background: {p['panel']}; color: {p['text_dim']}; padding: 5px 18px;
               border: 1px solid {p['log_border']}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {p['window']}; color: {p['text']}; }}
QTabBar::tab:hover {{ color: {p['text']}; }}
QCheckBox {{ spacing: 4px; }}
/* 全局 QSS 生效后单选钮 indicator 不再走原生绘制，暗色下未选中态会不可见——显式画 */
QRadioButton {{ spacing: 4px; }}
QRadioButton::indicator {{ width: 13px; height: 13px; border-radius: 7px;
                          border: 1px solid {p['border']}; background: {p['control']}; }}
QRadioButton::indicator:hover {{ border: 1px solid {p['accent']}; }}
QRadioButton::indicator:checked {{ background: {p['accent']};
                                   border: 1px solid {p['accent']}; }}
QSpinBox {{ background: {p['control']}; color: {p['text']};
           border: 1px solid {p['border']}; border-radius: 4px; padding: 2px 4px; }}
QLabel {{ background: transparent; }}
QLabel#backlog {{ color: {p['text_dim']}; }}
QLabel#status_ok  {{ color: {p['status_ok']}; }}
QLabel#status_err {{ color: {p['status_err']}; }}
QMenuBar {{ background: {p['panel']}; }}
QMenuBar::item:selected {{ background: {p['accent']}; color: white; }}
QMenu {{ background: {p['panel']}; color: {p['text']}; }}
QMenu::item:selected {{ background: {p['accent']}; color: white; }}
QScrollBar:vertical {{ background: {p['panel']}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {p['border']}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: {p['panel']}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {p['border']}; border-radius: 5px; min-width: 24px; }}
QToolTip {{ background: {p['control']}; color: {p['text']}; border: 1px solid {p['border']}; }}
"""


DARK_QSS = _build_qss(DARK)   # 兼容旧引用（main.py / 各验证脚本）
LIGHT_QSS = _build_qss(LIGHT)

# 日志行配色（模块级：set_theme 切主题时同步更新；main_window 每次追加日志动态读取，
# 旧日志行保留当时的颜色，新行用新色）
RX_COLOR = DARK['rx']
TX_COLOR = DARK['tx']
SYS_COLOR = DARK['sys']
TIME_COLOR = DARK['time']

_current = DARK_KEY
_listeners: list = []   # 主题切换监听（各页 __init__ 注册 apply_theme 回调）


def current_theme() -> str:
    """当前主题键（'dark' / 'light'）。"""
    return _current


def theme_label(name: str | None = None) -> str:
    """主题键 → 中文显示名（深色/浅色）。"""
    return THEME_LABELS[name or _current]


def palette(name: str | None = None) -> dict:
    """取整套配色字典（未指定主题时取当前主题），返回副本防外改。"""
    return dict(DARK if (name or _current) == DARK_KEY else LIGHT)


def color(key: str, name: str | None = None) -> str:
    """单键取色：color('plot_bg') / color('chip_border', 'light')。"""
    return palette(name)[key]


def curve_colors(name: str | None = None) -> list[str]:
    """默认曲线 16 色（按主题给出对比度达标的一套）。"""
    return list(palette(name)['curve_colors'])


def register_listener(fn) -> None:
    """注册主题切换回调：fn(theme_key)，set_theme 时逐个通知。"""
    _listeners.append(fn)


def style_plot_widget(plot: pg.PlotWidget, name: str | None = None) -> None:
    """按主题刷新一个 pyqtgraph PlotWidget：背景 + 坐标轴前景。

    pyqtgraph 0.14 里轴的 setPen 同时决定轴线/刻度/刻度文字与网格线色。
    """
    p = palette(name)
    plot.setBackground(p['plot_bg'])
    for ax in ('bottom', 'left'):
        axis = plot.getAxis(ax)
        axis.setPen(p['plot_fg'])
        axis.setTextPen(p['plot_fg'])


def set_theme(name: str, app: QtWidgets.QApplication | None = None) -> None:
    """切换主题：全局 QSS + pyqtgraph 默认背景/前景即时生效，并通知各页刷新。

    未知主题名回落深色。app 缺省取当前 QApplication（离屏测试同样适用）。
    """
    global _current, RX_COLOR, TX_COLOR, SYS_COLOR, TIME_COLOR
    if name not in THEME_LABELS:
        name = DARK_KEY
    _current = name
    p = DARK if name == DARK_KEY else LIGHT
    RX_COLOR, TX_COLOR, SYS_COLOR, TIME_COLOR = p['rx'], p['tx'], p['sys'], p['time']
    pg.setConfigOptions(background=p['plot_bg'], foreground=p['plot_fg'])
    app = app or QtWidgets.QApplication.instance()
    if app is not None:
        app.setStyleSheet(_build_qss(p))
    for fn in list(_listeners):
        fn(name)


def apply_saved_theme(app: QtWidgets.QApplication | None = None) -> str:
    """启动恢复：读设置键 ui/theme（缺省深色），返回生效主题键。"""
    saved = read_setting(THEME_SETTING_KEY, DARK_KEY).strip().lower()
    set_theme(saved if saved in THEME_LABELS else DARK_KEY, app)
    return _current
