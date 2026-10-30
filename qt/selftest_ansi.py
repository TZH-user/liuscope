# -*- coding: utf-8 -*-
"""ANSI 渲染自测组(SGR 解析→显示段、主题调色板对比度保底、主窗显示管线集成)。

约定:本文件提供 groups() 返回测试函数列表;由 qt/selftest.py 的 run_selftest 挂载。
所有测试离屏可跑、无真机、无网络、不起 sys.executable 子进程(冻结冒烟安全)。
"""
from __future__ import annotations

import sys


def test_ansi_render_core():
    """SGR→显示段纯逻辑:着色/属性/256色/真彩/剥除/保排版/异常输入。"""
    from core import ansi_render

    FG, BG, TEXT = '#eeeeee', '#1e1e1e', '#eeeeee'

    # strip/has：SGR、光标移动、OSC、两字节 Esc 全剥
    assert not ansi_render.has_ansi('普通文本')
    assert ansi_render.has_ansi('\x1b[31m红')
    dirty = '\x1b]0;title\x07\x1b[2J\x1b[H\x1b(B\x1b[1;32mhi\x1b[0m'
    assert ansi_render.strip_ansi(dirty) == 'hi', ansi_render.strip_ansi(dirty)

    # 基本着色：红字 + 复位后回落默认
    segs, had_sgr = ansi_render.parse_segments('\x1b[31m红\x1b[0m普通', FG, BG, TEXT)
    assert had_sgr and len(segs) == 2, segs
    assert segs[0].text == '红' and segs[0].color == '#cd3131', segs   # 深底 1 号红
    assert segs[1].text == '普通' and segs[1].color == FG

    # 属性：粗体/斜体/下划线/删除线/反显(换底色)/暗淡(向底色混合)
    segs, _ = ansi_render.parse_segments('\x1b[1mB\x1b[3mI\x1b[4mU\x1b[9mS',
                                         FG, BG, TEXT)
    assert [s.text for s in segs] == ['B', 'I', 'U', 'S']
    assert segs[0].bold and segs[1].italic and segs[2].underline and segs[3].strike
    segs, _ = ansi_render.parse_segments('\x1b[7m反\x1b[27m常\x1b[2m暗', FG, BG, TEXT)
    assert segs[0].bg == FG and segs[0].color == BG, '反显应前景背景互换'
    assert segs[1].bg is None and segs[1].color == FG, '27 关反显'
    assert segs[2].color != FG and segs[2].color.startswith('#'), '暗淡应向底色混合'

    # 256 色 / 真彩（深底直接呈现）
    segs, _ = ansi_render.parse_segments('\x1b[38;5;196mX\x1b[0m', FG, BG, TEXT)
    assert segs[0].color == '#ff0000', segs    # 196 号 = 纯红
    segs, _ = ansi_render.parse_segments('\x1b[48;5;46mB', FG, BG, TEXT)
    assert segs[0].bg == '#00ff00', segs       # 46 号背景绿，对比达标原样呈现
    segs, _ = ansi_render.parse_segments('\x1b[48;5;21mB', FG, BG, TEXT)
    assert segs[0].bg != '#0000ff', segs       # 21 号纯蓝对深底对比不足 → 保底混合
    segs, _ = ansi_render.parse_segments('\x1b[38;2;255;140;0mT', FG, BG, TEXT)
    assert segs[0].color == '#ff8c00', segs    # 真彩橙

    # 冒号形态与分号形态等价
    a, _ = ansi_render.parse_segments('\x1b[38:5:46mX', FG, BG, TEXT)
    b, _ = ansi_render.parse_segments('\x1b[38;5;46mX', FG, BG, TEXT)
    assert a == b, (a, b)
    c, _ = ansi_render.parse_segments('\x1b[38:2:1:2:3mX', FG, BG, TEXT)
    d, _ = ansi_render.parse_segments('\x1b[38;2;1;2;3mX', FG, BG, TEXT)
    assert c == d, (c, d)

    # 保排版：空格原样保留在段文本里（QTextCharFormat 直插，不折叠）
    segs, _ = ansi_render.parse_segments('│ A │  BB │\tX', FG, BG, TEXT)
    assert segs[0].text == '│ A │  BB │\tX', segs

    # 非 SGR 序列剥掉不留痕；纯文本行 had_sgr=False（段文本仍可用）
    segs, had_sgr = ansi_render.parse_segments('\x1b[2K\x1b[31m进度', FG, BG, TEXT)
    assert had_sgr and segs[0].text == '进度'
    assert all('[' not in s.text and '\x1b' not in s.text for s in segs)
    segs, had_sgr = ansi_render.parse_segments('plain  line', FG, BG, TEXT)
    assert not had_sgr and len(segs) == 1 and segs[0].text == 'plain  line'
    assert segs[0].color == FG
    # 只剩非 SGR 转义的行：had_sgr=False，但返回文本已剥净
    segs, had_sgr = ansi_render.parse_segments('\x1b[2J\x1b[H', FG, BG, TEXT)
    assert not had_sgr and all(s.text == '' for s in segs)

    # 异常输入不崩：空参数/残缺扩展/未知终止字节/裸 ESC
    for raw in ('\x1b[;31;m', '\x1b[38;5', '\x1b[38;2;1m', '\x1b[9999z',
                '\x1b[m', '\x1b'):
        ansi_render.parse_segments(raw + 'x', FG, BG, TEXT)
    segs, _ = ansi_render.parse_segments('\x1b[m复位', FG, BG, TEXT)
    assert segs[-1].text == '复位'
    print('ANSI RENDER CORE OK')


def test_ansi_palette_contrast():
    """深/浅两套 16 色板对各自底色的对比度全部达标（WCAG 保底文化）。"""
    import re

    from core import ansi_render

    for bg, text in (('#1e1e1e', '#eeeeee'), ('#ffffff', '#111111')):
        for code in list(range(30, 38)) + list(range(90, 98)):
            segs, _ = ansi_render.parse_segments(f'\x1b[{code}m█', text, bg, text)
            rgb = ansi_render._hex_to_rgb(segs[0].color)
            ratio = ansi_render.contrast_ratio(rgb, ansi_render._hex_to_rgb(bg))
            assert ratio >= ansi_render._CONTRAST_FLOOR - 1e-9, \
                f'{code} 号色 {segs[0].color} 对底 {bg} 对比度 {ratio:.2f} 不达标'
        # 256 色路径同样保底：深灰 234 号对白底必须被混合到可读
        segs, _ = ansi_render.parse_segments('\x1b[38;5;234mX', text, bg, text)
        rgb = ansi_render._hex_to_rgb(segs[0].color)
        assert ansi_render.contrast_ratio(rgb, ansi_render._hex_to_rgb(bg)) \
            >= ansi_render._CONTRAST_FLOOR - 1e-9, '256 色对比度保底失效'
    # 深浅两套调色板确实不同（按底色明暗切换）
    assert ansi_render._base_palette('#1e1e1e') != ansi_render._base_palette('#ffffff')
    print('ANSI PALETTE CONTRAST OK')


def _find_block(log, needle: str):
    b = log.document().firstBlock()
    while b.isValid():
        if needle in b.text():
            return b
        b = b.next()
    return None


def test_ansi_render_ui():
    """主窗集成:开关默认开/保排版落显/SGR 着色/悬停互转不回归/关闭回落旧行为。"""
    from PySide6 import QtWidgets

    import ui.main_window as mw
    from core import ansi_render
    from selftest import _CONNECT_KEYS, _restore_settings, _snapshot_settings

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    saved = _snapshot_settings(_CONNECT_KEYS + ('log/ansi_render',))
    win = None
    try:
        win = mw.MainWindow()
        win._port_watch_timer.stop()
        assert win.chk_ansi.isChecked(), 'ANSI 渲染默认开'

        # 无 SGR 行：空格保排版（落进文档的是普通空格，查找/悬停不受影响）
        win._append_log('RX', '│ A │  BB │')
        assert '│ A │  BB │' in win.log.toPlainText(), '多空格必须原样保留'

        # 悬停互转回归：ANSI 开着时 HEX 行照常可互转（此前 &nbsp; 方案破坏过）
        win._append_log('RX', '41 42 43 44')
        from ui.log_hover import tooltip_for_position
        b = _find_block(win.log, '41 42 43 44')
        assert b is not None, '正文必须与头行同块'
        assert tooltip_for_position(win.log, b.blockNumber()) \
            .startswith('文本(UTF-8): ABCD')

        # SGR 行：设备色落显（查块内字符格式，QPlainTextEdit 无 toHtml）、
        # 转义码不可见、原文条目仍存原始转义
        from ui import theme
        segs, _ = ansi_render.parse_segments('\x1b[32mOK\x1b[0m', theme.RX_COLOR,
                                             theme.color('log_bg'),
                                             theme.color('log_text'))
        expect = segs[0].color
        win._append_log('RX', '\x1b[32mOK\x1b[0m')
        b = _find_block(win.log, 'OK')
        assert b is not None and ' RX' in b.text(), '头行与正文必须同块'
        cur = win.log.textCursor()
        cur.setPosition(b.position() + b.text().index('OK') + 1)  # charFormat 取光标前一字符
        seen = cur.charFormat().foreground().color().name()
        assert seen.lower() == expect.lower(), f'设备绿应落进字符格式: {seen} != {expect}'
        assert '\x1b' not in win.log.toPlainText(), '渲染开启时转义码不可见'
        assert win.message_entries[-1]['text'] == '\x1b[32mOK\x1b[0m', \
            '条目原文必须保留原始转义（导出/自动日志不动）'

        # SGR 粗体落进字符格式
        win._append_log('RX', '\x1b[1m粗体\x1b[0m')
        b = _find_block(win.log, '粗体')
        cur = win.log.textCursor()
        cur.setPosition(b.position() + b.text().index('粗体') + 1)
        assert cur.charFormat().fontWeight() >= 700, 'SGR 1 应映射为粗体字符格式'

        # 开关关闭 → 回落旧行为：转义码原样显示、空白折叠
        win.chk_ansi.setChecked(False)
        win._append_log('RX', 'a  b\x1b[31m红')
        text = win.log.toPlainText()
        assert '\x1b[31m' in text, '关闭渲染后转义码应原样显示（旧行为）'
        assert 'a  b' not in text, '关闭渲染后空白应折叠（旧行为）'
        from core.settings import read_setting
        assert read_setting('log/ansi_render', '1') == '0', '开关状态应持久化'

        # TX/SYS 行不走 ANSI 路径（聊天式右对齐/系统行不变）
        win.chk_ansi.setChecked(True)
        n0 = len(win.message_entries)
        win._append_log('TX', 'hello  world')
        assert win.message_entries[-1]['direction'] == 'TX'
        assert len(win.message_entries) == n0 + 1
        # 只剩清屏序列的行：无可见正文，条目应退化为仅头行（无正文可悬停）
        win._append_log('RX', '\x1b[2J\x1b[H')
        import re
        from ui.log_hover import _HEADER_ONLY_RE
        b = win.log.document().lastBlock()
        if not b.text().strip():
            b = b.previous()
        assert _HEADER_ONLY_RE.match(b.text()), \
            f'纯转义行应退化为仅头行条目: {b.text()!r}'
    finally:
        if win is not None:
            win._port_watch_timer.stop()
            win.close()
        _restore_settings(saved)
    print('ANSI RENDER UI OK')


def test_log_wrap_mono_art():
    """日志显示网格:等宽字体(字符画不歪)+视口宽自动折行+窗口缩放自适应重排
    + 真机 motd 猫画逐字节保真(前导/行内多空格不塌)。

    背景:鲁班猫 motd 猫画(/etc/update-motd.d/00-header)按终端 1 字符=1 格
    网格排,旧版日志区继承全局比例字体(YaHei UI)且 NoWrap——画被压扁歪斜、
    长行只出横向滚动条。修复后网格对齐靠等宽字体,文本本身本来就零损耗。
    """
    from PySide6 import QtCore, QtGui, QtWidgets

    import ui.main_window as mw
    from ui import theme
    from selftest import _CONNECT_KEYS, _restore_settings, _snapshot_settings

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    saved = _snapshot_settings(_CONNECT_KEYS)
    win = None
    try:
        win = mw.MainWindow()
        win._port_watch_timer.stop()

        # 等宽字体经 QSS(#log_view) 生效:控件字体与文档默认字体都要命中。
        # 注意 QSS 解析出的 QFont 只带 family 不带 fixedPitch 标志，等宽与否
        # 要问 QFontInfo（真实字体引擎）而不是 QFont.fixedPitch()。
        # offscreen 平台插件（Windows）不带系统字体库：families() 为空、QFontInfo
        # 无对象可解析——此时只验 QSS 注入与 family 一致，QFontInfo 断言留给真实平台。
        mono_fam = theme.mono_font().family()
        win.log.ensurePolished()
        assert win.log.font().family() == mono_fam, win.log.font().family()
        if set(QtGui.QFontDatabase.families()):
            assert QtGui.QFontInfo(win.log.font()).fixedPitch(), \
                f'QSS 解析出的 {win.log.font().family()} 应为真实等宽字体'
        assert win.log.document().defaultFont().family() == mono_fam, \
            win.log.document().defaultFont().family()

        # 折行配置:视口宽折行 + 无空格长串按字符断(HEX 不再横向滚丢)
        assert win.log.lineWrapMode() == QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth
        assert win.log.wordWrapMode() == QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere

        # 真机 motd 猫画行原样字节保真:前导 11 空格 + 行内 24 空格一个不能少
        art = '\x1b[1;34m' + ' ' * 11 + 'OOOO' + ' ' * 24 + 'OOOO\x1b[0m'
        win._append_log('RX', art)
        b = _find_block(win.log, 'OOOO')
        assert b is not None, '猫画行必须落显'
        assert (' ' * 11 + 'OOOO' + ' ' * 24 + 'OOOO') in b.text(), repr(b.text())

        # 折行 + 自适应重排：超视口宽的长行折多行，拉宽窗口后重排回单行。
        # 坑1：日志视口宽由布局管理——缩放必须缩顶层窗口，直接 resize 日志控件无效。
        # 坑2：主窗有最小宽度钳制（顶行控件撑的），行长按当前视口宽动态取，不写死像素。
        win.show()
        fm = QtGui.QFontMetrics(win.log.font())
        adv = fm.horizontalAdvance('A')
        w0 = win.log.viewport().width()
        assert w0 > 0 and adv > 0
        win._append_log('RX', 'A' * (int(w0 / adv) + 20))
        blk = _find_block(win.log, 'AAAA')
        layout = win.log.document().documentLayout()
        app.processEvents()
        layout.blockBoundingRect(blk)   # 强制排版该块(QPlainTextDocumentLayout)
        assert blk.lineCount() > 1, '超视口宽的长行应折行'
        win.resize(QtCore.QSize(win.width() * 2, win.height()))
        app.processEvents()
        layout.blockBoundingRect(blk)
        assert blk.lineCount() == 1, '拉宽后应自适应重排回单行'
    finally:
        if win is not None:
            win._port_watch_timer.stop()
            win.close()
        _restore_settings(saved)
    print('LOG WRAP MONO ART OK')


def groups():
    return [test_ansi_render_core, test_ansi_palette_contrast, test_ansi_render_ui,
            test_log_wrap_mono_art]
