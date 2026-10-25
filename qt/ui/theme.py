# -*- coding: utf-8 -*-
"""深色主题：一套 QSS 全局生效——对比 WinForms 逐控件自绘，这里是声明式一步到位。

配色基调与主线一致：底 #1E2430、输入 #262D3A、强调 #0E639C（VS Code 蓝）。
"""
DARK_QSS = """
QMainWindow, QWidget { background: #1E2430; color: #E1E6F0;
                       font-family: 'Microsoft YaHei UI'; font-size: 9pt; }
QComboBox, QLineEdit { background: #262D3A; color: #E1E6F0;
                       border: 1px solid #3E546A; border-radius: 4px; padding: 3px 6px; }
QComboBox QAbstractItemView { background: #262D3A; color: #E1E6F0;
                              selection-background-color: #0E639C; }
QPlainTextEdit, QTextEdit { background: #161B24; color: #D4D4D4;
                            border: 1px solid #2A3140; border-radius: 4px; }
QPushButton { background: #0E639C; color: white; border: none;
              border-radius: 4px; padding: 6px 14px; }
QPushButton:hover { background: #1177BB; }
QPushButton:pressed { background: #0A4E7E; }
QPushButton:disabled { background: #3E546A; }
QPushButton#tool { background: #2A3244; padding: 3px 10px; }
QPushButton#tool:hover { background: #38435A; }
QPushButton#tool:checked { background: #0E639C; color: white; }
QTabWidget::pane { border: 1px solid #2A3140; }
QTabBar::tab { background: #1C212D; color: #9AA4B2; padding: 5px 18px;
               border: 1px solid #2A3140; border-bottom: none; }
QTabBar::tab:selected { background: #1E2430; color: #E1E6F0; }
QTabBar::tab:hover { color: #E1E6F0; }
QCheckBox { spacing: 4px; }
QSpinBox { background: #262D3A; color: #E1E6F0;
           border: 1px solid #3E546A; border-radius: 4px; padding: 2px 4px; }
QLabel { background: transparent; }
QLabel#backlog { color: #969EAC; }
QLabel#status_ok  { color: #4EC9B0; }
QLabel#status_err { color: #F14C4C; }
QMenuBar { background: #1C212D; }
QMenuBar::item:selected { background: #0E639C; }
QMenu { background: #1C212D; color: #E1E6F0; }
QMenu::item:selected { background: #0E639C; }
QScrollBar:vertical { background: #1C212D; width: 10px; }
QScrollBar::handle:vertical { background: #3E546A; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { background: #1C212D; height: 10px; }
QScrollBar::handle:horizontal { background: #3E546A; border-radius: 5px; min-width: 24px; }
"""

# 日志行配色（与主线日志区一致的语义：RX 蓝白、TX 橙、SYS 暗）
RX_COLOR = '#9CDCFE'   # VS Code 变量蓝
TX_COLOR = '#CE9178'   # VS Code 字符串橙
SYS_COLOR = '#6A9955'  # VS Code 注释绿
TIME_COLOR = '#6A7385'
