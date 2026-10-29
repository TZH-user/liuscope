# -*- coding: utf-8 -*-
"""ANSI 渲染视觉验证（一次性）：离屏灌入仿真 Linux 串口控制台输出，截深浅两图。

产物：_verify/evidence/ansi_render_dark.png / ansi_render_light.png
用法：QT_QPA_PLATFORM=offscreen python _verify/ansi_demo_shot.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'qt'))

from PySide6 import QtWidgets  # noqa: E402

import ui.main_window as mw  # noqa: E402
from ui import theme  # noqa: E402

# 仿真内容：鲁班猫类 Linux 串口控制台（框线 banner + 彩色 dmesg + 256 色菜单 + 反显）
E = '\x1b'
LINES = [
    f'{E}[1;32mroot@lubancat{E}[0m:{E}[1;34m~{E}[0m$ dmesg | tail -4',
    f'{E}[    0.000000]{E}[0m Booting Linux on physical CPU 0x0000000000 [0x414fd0b1]',
    f'{E}[    1.234567]{E}[0m {E}[0;32mgoodcpu{E}[0m: cpu0: MPCORE performance counter',
    f'{E}[    2.345678]{E}[0m {E}[0;33mrockchip-vop2{E}[0m: no registered panel for VP1',
    f'{E}[    3.456789]{E}[0m {E}[0;31mdwc3{E}[0m: failed to fetch ulpi ext-caps -110',
    '',
    f'{E}[38;5;46m╔══════════════════════════════════════╗{E}[0m',
    f'{E}[38;5;46m║{E}[0m  {E}[1;97mLubanCat RK3562  ·  串口控制台就绪{E}[0m  {E}[38;5;46m║{E}[0m',
    f'{E}[38;5;46m╚══════════════════════════════════════╝{E}[0m',
    '',
    f'{E}[38;5;51m─── 系统菜单 ───{E}[0m',
    f'  {E}[1;33m1{E}[0m) {E}[97m查看系统信息{E}[0m     {E}[2m(uptime/free/uname){E}[0m',
    f'  {E}[1;33m2{E}[0m) {E}[97m网络配置{E}[0m        {E}[2m(ifconfig 聚合视图){E}[0m',
    f'  {E}[1;33m3{E}[0m) {E}[97m串口自检{E}[0m        {E}[2m(loopback 115200){E}[0m',
    f'  {E}[1;31m0{E}[0m) {E}[1;91m关机{E}[0m',
    '',
    f'{E}[38;2;255;140;0m真彩橙]{E}[0m {E}[7m 反显高亮 {E}[0m {E}[4m下划线{E}[0m {E}[9m删除线{E}[0m {E}[3m斜体{E}[0m',
    f'{E}[1;32mroot@lubancat{E}[0m:{E}[1;34m~{E}[0m$ _',
]


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from core.settings import read_setting, write_setting
    saved = (read_setting('ui/theme', 'dark'), read_setting('log/ansi_render', '1'))
    out_dir = os.path.join(os.path.dirname(__file__), 'evidence')
    os.makedirs(out_dir, exist_ok=True)
    win = None
    try:
        for name, out in (('dark', 'ansi_render_dark.png'), ('light', 'ansi_render_light.png')):
            write_setting('ui/theme', name)
            theme.set_theme(name)
            if win is not None:
                win.close()
            win = mw.MainWindow()
            win._port_watch_timer.stop()
            win.chk_auto_save.setChecked(False)
            win.clear_log()
            for line in LINES:
                win._append_log('RX', line)
            win.tabs.setCurrentIndex(0)
            win.resize(1000, 640)
            app.processEvents()
            png = win.log.grab().save(os.path.join(out_dir, out))
            assert png, f'截图失败: {out}'
            print(f'saved {out_dir}/{out}')
        return 0
    finally:
        if win is not None:
            win.close()
        write_setting('ui/theme', saved[0])
        write_setting('log/ansi_render', saved[1])


if __name__ == '__main__':
    sys.exit(main())
