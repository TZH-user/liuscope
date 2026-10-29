# -*- coding: utf-8 -*-
"""双主题（深色/浅色）全局对比度审计（离屏可跑，无需显示器/串口）。

两层校验：
1. 语义配对：按 ui/theme.py 配色字典 + QSS 规则整理「前景/背景」配对，
   做 WCAG 2.x 相对亮度对比度计算（正文 ≥4.5，禁用按钮 ≥3.0，
   曲线身份色（CH 芯片/读数行，与曲线一一对应的身份标记）按非文本 ≥3.0）；
2. 像素抽样复核：离屏实例化 MainWindow，对代表性控件（tool 按钮、
   主按钮、禁用按钮、页签栏、下拉框、日志区）QWidget.grab() 后
   「最常见色=底 + 距底最远像素=墨色」实测对比度，防止配色表算过但渲染不对。

用法：python _verify/theme_contrast_audit.py（退出码 0=全绿，1=有违规）
"""
from __future__ import annotations

import os
import re
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QT_DIR = os.path.join(ROOT, 'qt')
if QT_DIR not in sys.path:
    sys.path.insert(0, QT_DIR)

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PySide6.QtGui import QFontDatabase, QImage  # noqa: E402

from ui import theme  # noqa: E402

# 曲线身份色（CH 芯片/悬停读数行）：与曲线一一对应的身份标记，按非文本 ≥3.0
IDENTITY_NEED = 3.0
TEXT_NEED = 4.5
DISABLED_NEED = 3.0


# ---------- WCAG 相对亮度与对比度 ----------

def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_lum(color) -> float:
    """#RRGGBB 或 (r,g,b) → WCAG 相对亮度。"""
    if isinstance(color, str):
        h = color.lstrip('#')
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    else:
        r, g, b = (c / 255 for c in color[:3])
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg, bg) -> float:
    l1, l2 = rel_lum(fg), rel_lum(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


# ---------- 审计记录 ----------

violations: list[str] = []
checked = 0


def record(desc: str, fg, bg, need: float, ratio: float | None = None) -> None:
    """记录一条对比度结论（ratio 缺省按语义色现算）。"""
    global checked
    checked += 1
    ratio = contrast(fg, bg) if ratio is None else ratio
    ok = ratio >= need
    if isinstance(fg, str) and isinstance(bg, str):
        pair = f'{fg}/{bg}'
    else:
        pair = '实测像素'
    line = f"{'通过' if ok else '违规'}  {ratio:5.2f} (需≥{need})  {desc}  [{pair}]"
    print(line)
    if not ok:
        violations.append(line)


# ---------- 第一层：语义配对（theme.py 配色字典，深浅两套同构） ----------

def audit_semantic_pairs() -> None:
    for name in (theme.LIGHT_KEY, theme.DARK_KEY):
        p = theme.palette(name)
        tag = theme.theme_label(name)
        def c(desc, fg, bg, need=TEXT_NEED):
            record(f'[{tag}] {desc}', fg, bg, need)

        # 按钮（主按钮 / tool 按钮 / checked / 禁用；文字均为白色）
        c('主按钮 白字/accent 底', '#FFFFFF', p['accent'])
        c('主按钮 hover 白字/accent_hover 底', '#FFFFFF', p['accent_hover'])
        c('主按钮 pressed 白字/accent_pressed 底', '#FFFFFF', p['accent_pressed'])
        c('tool 按钮 白字/tool_btn 底', '#FFFFFF', p['tool_btn'])
        c('tool 按钮 hover 白字/tool_btn_hover 底', '#FFFFFF', p['tool_btn_hover'])
        c('tool 按钮 checked 白字/accent 底', '#FFFFFF', p['accent'])
        c('禁用按钮 白字/弱化底', '#FFFFFF', p['btn_disabled_bg'], DISABLED_NEED)
        # 页签
        c('页签未选中文字/页签栏底', p['text_dim'], p['panel'])
        c('页签选中文字/窗体底', p['text'], p['window'])
        c('页签悬停文字/页签栏底', p['text'], p['panel'])
        # 输入控件与复选框
        c('QComboBox/QLineEdit/QSpinBox 文字/控件底', p['text'], p['control'])
        c('QCheckBox 文字/窗体底', p['text'], p['window'])
        # 日志区
        c('log_text/log_bg', p['log_text'], p['log_bg'])
        for key in ('rx', 'tx', 'sys', 'time'):
            c(f'日志行 {key}/log_bg', p[key], p['log_bg'])
        # 状态与弱文本
        c('status_ok 标签', p['status_ok'], p['window'])
        c('status_err 标签', p['status_err'], p['window'])
        c('status_warn（导出警告文本）', p['status_warn'], p['window'])
        c('backlog 弱文本', p['text_dim'], p['window'])
        # 菜单与提示
        c('QMenu 文字/菜单底', p['text'], p['panel'])
        c('菜单/菜单项选中 白字/accent 底', '#FFFFFF', p['accent'])
        c('QToolTip 文字/提示底', p['text'], p['control'])
        # Modbus 闪烁高亮（监视表格底=窗体底）
        for tone in ('flash_up', 'flash_down', 'flash_changed'):
            c(f'Modbus 闪烁 {tone}', p[tone], p['window'])
        # 曲线身份色 16 色（窗体底，非文本阈值）
        for i, cc in enumerate(p['curve_colors']):
            c(f'曲线身份色 CH{i}/窗体底', cc, p['window'], IDENTITY_NEED)


# ---------- 第二层：逐页签可见控件按类推导配对 + 内联样式扫描 ----------

def classify_widget(w: QtWidgets.QWidget, p: dict):
    """控件 → (描述, 前景, 背景, 阈值)；不适用返回 None。"""
    if isinstance(w, QtWidgets.QPushButton):
        if not w.isEnabled():
            return ('禁用按钮', '#FFFFFF', p['btn_disabled_bg'], DISABLED_NEED)
        if w.objectName() == 'tool':
            if w.isCheckable() and w.isChecked():
                return ('tool 按钮(checked)', '#FFFFFF', p['accent'], TEXT_NEED)
            return ('tool 按钮', '#FFFFFF', p['tool_btn'], TEXT_NEED)
        return ('主按钮', '#FFFFFF', p['accent'], TEXT_NEED)
    if isinstance(w, QtWidgets.QLabel):
        obj = w.objectName()
        if obj == 'backlog':
            return ('backlog 弱文本', p['text_dim'], p['window'], TEXT_NEED)
        if obj == 'status_ok':
            return ('status_ok 标签', p['status_ok'], p['window'], TEXT_NEED)
        if obj == 'status_err':
            return ('status_err 标签', p['status_err'], p['window'], TEXT_NEED)
        return ('QLabel', p['text'], p['window'], TEXT_NEED)
    if isinstance(w, (QtWidgets.QComboBox, QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox)):
        return ('输入控件', p['text'], p['control'], TEXT_NEED)
    if isinstance(w, (QtWidgets.QCheckBox, QtWidgets.QRadioButton)):
        return ('复选框文本', p['text'], p['window'], TEXT_NEED)
    if isinstance(w, (QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit)):
        return ('日志/文本域', p['log_text'], p['log_bg'], TEXT_NEED)
    return None


_INLINE_COLOR_RE = re.compile(r'(?:^|[^-])color\s*:\s*(#[0-9A-Fa-f]{6})')


def audit_page_widgets(page: QtWidgets.QWidget, p: dict, tag: str) -> None:
    """枚举页签可见控件：按类推导配对 + 内联样式颜色扫描。"""
    for w in page.findChildren(QtWidgets.QWidget):
        if not w.isVisibleTo(page):
            continue
        desc = classify_widget(w, p)
        if desc:
            record(f'[{tag}] {page.windowTitle() or "主窗"}·{type(w).__name__}({desc[0]})',
                   desc[1], desc[2], desc[3])
        # 内联样式里的 hex 文本色（控件级样式优先级高于全局 QSS，单独扫）
        ss = w.styleSheet()
        for m in _INLINE_COLOR_RE.finditer(ss):
            fg = m.group(1)
            # 身份色复选框（CH 芯片，颜色=曲线色）按非文本阈值，其余按正文
            need = IDENTITY_NEED if isinstance(w, QtWidgets.QCheckBox) else TEXT_NEED
            bg = p['chip_bg'] if isinstance(w, QtWidgets.QCheckBox) else p['window']
            record(f'[{tag}] 内联样式 {type(w).__name__}({w.objectName() or "-"}) 文本色',
                   fg, bg, need)


# ---------- 第三层：代表性控件 grab() 像素抽样复核 ----------

def _image_colors(img: QImage):
    """统计图内颜色分布。"""
    counts: dict[tuple[int, int, int], int] = {}
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            key = (c.red(), c.green(), c.blue())
            counts[key] = counts.get(key, 0) + 1
    return counts


def _pixel_pair(img: QImage):
    """(最常见色=底, 距底最远像素=墨色) —— 墨色取与底曼哈顿距离最大者。"""
    counts = _image_colors(img)
    bg = max(counts, key=counts.get)
    best, best_d = None, -1
    for (r, g, b), n in counts.items():
        d = abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2])
        if d > best_d:
            best, best_d = (r, g, b), d
    return bg, best, best_d


def _near_count(img: QImage, color, tol: int = 60) -> int:
    c = QtGui.QColor(color) if isinstance(color, str) else QtGui.QColor(*color[:3])
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            p = img.pixelColor(x, y)
            if (abs(p.red() - c.red()) + abs(p.green() - c.green())
                    + abs(p.blue() - c.blue())) <= tol:
                n += 1
    return n


def audit_pixels(app, win) -> None:
    """代表性控件 grab 复核：底色必须等于配色表（防 QSS 未生效），实测墨色对比度。"""
    app.processEvents()

    def grab_check(desc: str, widget: QtWidgets.QWidget, expect_bg: str,
                   fg_need: float, fg_expect: str | None = None):
        img = widget.grab().toImage()
        bg, fg, dist = _pixel_pair(img)
        tag = theme.theme_label()
        exp = expect_bg.lower()
        got = '#%02X%02X%02X' % bg
        record(f'[{tag}] 像素 {desc} 底色=配色表', expect_bg, expect_bg, 1.0)
        if got.lower() != exp:
            violations.append(f'[{tag}] 像素 {desc} 底色 {got} != 配色表 {exp}（QSS 未生效?）')
            print(f'违规  底色不符  [{tag}] 像素 {desc}: {got} != {exp}')
        if fg_expect is not None and _near_count(img, fg_expect, 60) < 5:
            violations.append(f'[{tag}] 像素 {desc} 未找到期望前景色 {fg_expect} 的像素')
            print(f'违规  前景缺失  [{tag}] 像素 {desc}: 缺 {fg_expect}')
        if dist <= 60:
            violations.append(f'[{tag}] 像素 {desc} 图内无前景墨色（文本未渲染?）')
            print(f'违规  无墨色    [{tag}] 像素 {desc}')
            return
        record(f'[{tag}] 像素 {desc} 墨色/底色 实测对比', fg, bg, fg_need)

    p = theme.palette()
    tab_bar = win.tabs.tabBar()
    # 终端页代表控件
    win.tabs.setCurrentIndex(0)
    app.processEvents()
    grab_check('tool 按钮(清空记录)', win.btn_clear_log, p['tool_btn'], TEXT_NEED, '#FFFFFF')
    grab_check('主按钮(发送)', win.btn_send, p['accent'], TEXT_NEED, '#FFFFFF')
    grab_check('主按钮(连接)', win.btn_connect, p['accent'], TEXT_NEED, '#FFFFFF')
    grab_check('下拉框(波特率)', win.combo_baud, p['control'], TEXT_NEED)
    grab_check('日志区', win.log, p['log_bg'], TEXT_NEED)
    # 日志行色渲染在位（RX/TX/SYS/时间戳都有成片像素）
    img = win.log.grab().toImage()
    for key in ('rx', 'tx', 'sys'):
        record(f'[{theme.theme_label()}] 像素 日志行 {key} 色已渲染(对 log_bg)',
               p[key], p['log_bg'], TEXT_NEED)
        if _near_count(img, p[key], 60) < 5:
            violations.append(f'[{theme.theme_label()}] 日志行 {key} 色未在日志区渲染出来')
            print(f'违规  行色缺失  [{theme.theme_label()}] 日志 {key} 色')
    # 页签：选中 + 未选中 各裁一块
    for idx in (0, 1):
        rect = tab_bar.tabRect(idx)
        crop = tab_bar.grab().toImage().copy(rect)
        expect_bg = p['window'] if idx == win.tabs.currentIndex() else p['panel']
        desc = '选中页签' if idx == win.tabs.currentIndex() else '未选中页签'
        bg, fg, dist = _pixel_pair(crop)
        tag = theme.theme_label()
        got = '#%02X%02X%02X' % bg
        if got.lower() != expect_bg.lower():
            violations.append(f'[{tag}] 像素 {desc} 底色 {got} != 配色表 {expect_bg}')
            print(f'违规  底色不符  [{tag}] 像素 {desc}: {got} != {expect_bg}')
        else:
            record(f'[{tag}] 像素 {desc} 底色=配色表', expect_bg, expect_bg, 1.0)
        if dist > 60:
            record(f'[{tag}] 像素 {desc} 文字墨色/底色 实测对比', fg, bg, TEXT_NEED)
        else:
            violations.append(f'[{tag}] 像素 {desc} 无文字墨色')
            print(f'违规  无墨色    [{tag}] 像素 {desc}')
    # 设置页禁用按钮（CLI 路径为空时「恢复自动探测」置灰）
    win.tabs.setCurrentIndex(5)
    app.processEvents()
    btn = win.settings_page.btn_cli_reset
    if not btn.isEnabled():
        grab_check('禁用按钮(恢复自动探测)', btn, p['btn_disabled_bg'], DISABLED_NEED, '#FFFFFF')
    else:
        violations.append('[设置页] 「恢复自动探测」未处于禁用态，缺禁用像素样本')
        print('违规  样本缺失  设置页禁用按钮未禁用')


# ---------- 主流程 ----------

def main() -> int:
    from core.settings import read_setting, write_setting
    import ui.main_window as mw

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\msyh.ttc')
    saved_theme = read_setting(theme.THEME_SETTING_KEY, theme.DARK_KEY)
    saved_cli = read_setting('stm32_cli_path', '')
    saved_update = read_setting('update/last_check', '0')
    write_setting('update/last_check', '9999999999')  # 屏蔽 3s 自动更新检查
    print(f'语义配对（含每页签控件枚举） + 像素抽样，主题：深色/浅色')
    print('=' * 72)

    audit_semantic_pairs()

    for name in (theme.LIGHT_KEY, theme.DARK_KEY):
        # MainWindow.__init__ 会 apply_saved_theme 读回 ini，先写入目标主题再建窗
        write_setting(theme.THEME_SETTING_KEY, name)
        theme.set_theme(name)
        tag = theme.theme_label(name)
        win = mw.MainWindow()
        win.show()
        assert theme.current_theme() == name, '建窗后主题必须仍是目标主题'
        win._port_watch_timer.stop()          # 端口监视定时器不干扰审计
        win.chk_auto_save.setChecked(False)   # 不往 logs/ 落自动日志
        win.wave.state.enabled = False
        win.combo_rx_format.setCurrentText('ASCII')
        # 日志区铺上三类行（RX/TX/SYS）+ 时间戳，供像素抽样。
        # 注意先 clear_log：离屏环境下，往建窗时已有内容的文档再追加的行
        # grab 画不出来（既有自测组同样先清空，见 selftest.test_tx_alignment_and_hover）
        win.clear_log()
        win._append_log('RX', 'temp=23.50,hum=55,vbat=3.87')
        win._append_log('TX', 'AT+VERSION?')
        win._append_log('SYS', '审计样本行：主题对比度像素抽样')
        win.settings_page.set_cli_path('')    # 置出禁用按钮样本
        app.processEvents()

        texts = [win.tabs.tabText(i) for i in range(win.tabs.count())]
        assert texts == ['终端', '波形', '频谱', 'Modbus', 'STM32', 'Linux 支持',
                         '设置', '关于'], str(texts)
        for i, tab_name in enumerate(texts):
            win.tabs.setCurrentIndex(i)
            app.processEvents()
            audit_page_widgets(win.tabs.widget(i), theme.palette(), f'{tag}·{tab_name}')

        audit_pixels(app, win)

        win._port_watch_timer.stop()
        win.close()
        app.processEvents()

    write_setting('stm32_cli_path', saved_cli)
    write_setting('update/last_check', saved_update)
    theme.set_theme(saved_theme if saved_theme in theme.THEME_LABELS else theme.DARK_KEY)

    print('=' * 72)
    print(f'共 {checked} 项检查，违规 {len(violations)} 项')
    for v in violations:
        print('  ' + v)
    return 1 if violations else 0


if __name__ == '__main__':
    sys.exit(main())
