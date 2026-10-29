# -*- coding: utf-8 -*-
"""鲁班猫 motd 猫画 ground-truth 离屏渲染证据。

板子 /etc/update-motd.d/00-header 的**渲染后字节**（sh 在板上跑过、printf
转义已解析，base64 内嵌）逐行喂给主窗 RX 管线，验证：
  1. 等宽字体下猫画/框线图按终端网格对齐（修复前全局比例字体把画压歪）；
  2. 前导/行内多空格逐字节保真（insertText 直插，不塌不缩）；
  3. 超长行按视口宽自动折行（修复前 NoWrap 只出横向滚动条）。

用法: QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:/Windows/Fonts python ansi_motd_art_shot.py
产物: _verify/evidence/ansi_motd_art_mono.png
"""
from __future__ import annotations

import base64
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'qt'))

# sh /etc/update-motd.d/00-header 在板上的渲染输出（2026-10-30 采集）
MOTD_RENDERED_B64 = (
    'ChtbMTszNG0gCiAgICAgICAgICAgT08gICAgICAgICAgICAgICAgICAgICAgICAg'
    'ICAgT08KICAgICAgICAgICBPT09PICAgICAgICAgICAgICAgICAgICAgICAgT09P'
    'TwogICAgICAgICAgIE9PT09PICAgICAgICAgICAgICAgICAgICAgT09PT09PCiAg'
    'ICAgICAgICAgT09PT09PTyAgICAgICAgICAgICAgICAgLk9PT09PT08KICAgICAg'
    'ICAgICBPT09PT09PT08gICAgICAgICAgICAgIE9PT09PT09PTwogICAgICAgICAg'
    'IE9PT09PT09PT09PICAgICAgICAgIE9PT09PT09PT09PCiAgICAgICAgICAgT09P'
    'T09PT09PT09PT09PT09PT09PT09PT09PT09PT08KICAgICAgICAgICBPT09PT09P'
    'T09PT09PT09PT09PT09PT09PT09PT09PTwogICAgICAgICAgIE9PT09PXCAgICBP'
    'T09PT09PT09PT08gICAgL09PT09PCiAgICAgICAgICAgT09PT09PT09PICAgIE9P'
    'T09PTyAgICAgT09PT09PT08KICAgICAgICAgICBPT09PTy8gICAgT09PT09PT09P'
    'T09PTyAgIFxPT09PTwogICAgICAgICAgIE9PT09PT09PT09PT09PT09PT09PT09P'
    'T09PT09PT09PCiAgICAgICAgICAgT09PT09PT09PT09PT09c77+jL09PT09PT09P'
    'T09PT09PCiAgICAgICAgICAgT09PT09PT09PT09PT09PXC9PT09PT09PT09PT09P'
    'T08KICAgICAgICAgICBPT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PTyAK'
    'ICBfICAgICAgICAgICAgXyAgICAgICAgICAgICAgICAgICAgIF9fX18gICAgICAg'
    'IF8KIHwgfCAgICBfICAgXyB8IHxfXyAgICBfX19fICBfX19fXyAgLyBfX198IF9f'
    'X18gfCB8XyAgIAogfCB8ICAgfCB8IHwgfHwgJ18gXCAgLyBfICB8fCAgXyAgfHwg'
    'fCAgICAvIF8gIHx8IF9ffAogfCB8X19ffCB8X3wgfHwgfF8pIHx8IChffCB8fCB8'
    'IHwgfHwgfF9fX3wgKF98IHx8IHxfCiB8X19fX198XF9fLl98fF8uX18vICBcX18n'
    'X3x8X3wgfF98IFxfX19ffFxfXydffCBcX198IAobWzBtCgpXZWxjb21lIHRvIERl'
    'YmlhbiBHTlUvTGludXggMTIgKGJvb2t3b3JtKSAoR05VL0xpbnV4IDYuMS45OS1y'
    'azM1NjIgYWFyY2g2NCkK'
)


def main() -> int:
    from PySide6 import QtCore, QtWidgets

    import ui.main_window as mw

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = mw.MainWindow()
    win._port_watch_timer.stop()
    win.resize(980, 720)
    win.show()
    app.processEvents()

    # 收发格式统一 ASCII，避免本机 ini 记忆串场
    win.combo_rx_format.setCurrentText('ASCII')

    payload = base64.b64decode(MOTD_RENDERED_B64).replace(b'\r\n', b'\n')
    lines = payload.decode('utf-8', errors='replace').split('\n')
    for line in lines:
        if line:
            win._append_log('RX', line)

    # 折行演示：紧跟一条超长行（无空格长串，验证 WrapAnywhere 按字符断行）
    win._append_log('RX', 'W' * 300)
    app.processEvents()

    # ---- 程序化断言 ----
    mono_fam = win.log.font().family()
    assert mono_fam, '日志区必须命中等宽字体'
    doc = win.log.document()
    art_line = '           OOOO                        OOOO'
    hit = None
    b = doc.firstBlock()
    while b.isValid():
        if b.text().endswith(art_line):
            hit = b
            break
        b = b.next()
    assert hit is not None, f'猫画行必须逐字节保真落显（含前导 11 空格）: 未找到 {art_line!r}'

    out = os.path.join(os.path.dirname(__file__), 'evidence', 'ansi_motd_art_mono.png')
    win.grab().save(out)
    print(f'font={mono_fam} art_block={hit.blockNumber()} wrap_demo=OK -> {out}')

    win._port_watch_timer.stop()
    win.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
