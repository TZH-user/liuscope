# -*- coding: utf-8 -*-
"""故障诊断页端到端证据：离屏 MainWindow 摄入真机 !FAULT 行 → 树形位域+符号化渲染 → 截图。

数据为 bench_b01_fault.py 真机捕获（FAULTTEST，UsageFault/非对齐），
ELF 用板上 V4.3.0 同源构建产物。产出 _verify/evidence/fault_diag_panel.png。
用法：python -X utf8 _verify/fault_diag_panel_shot.py   （退出码 0 = 断言全过）
"""
import os
import pathlib
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')  # offscreen 无字体配置，不指认则 CJK 全豆腐

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qt"))

from PySide6 import QtCore, QtWidgets  # noqa: E402

import ui.main_window as mw  # noqa: E402

LINE_FILE = pathlib.Path(
    r"F:\软件仓库_新\开发软件\串口调试助手实验区\h743xih6\logs\fault_line.txt")
ELF = pathlib.Path(
    r"F:\软件仓库_新\开发软件\串口调试助手实验区\h743xih6\serial_port\build\Debug\serial_port.elf")
OUT = ROOT / "_verify" / "evidence" / "fault_diag_panel.png"


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = mw.MainWindow()
    win.show()
    win._port_watch_timer.stop()

    win.fault.set_elf_path(str(ELF))
    line = LINE_FILE.read_text(encoding='utf-8').strip()
    win.ingest_bytes(line.encode('ascii') + b'\r\n')
    for _ in range(20):  # 洪泛合并入口异步消费，泵事件循环等它落账
        app.processEvents()
        QtCore.QThread.msleep(20)

    state = win.fault.elf_state()
    assert state.get('loaded'), f"ELF 加载失败: {state}"
    assert len(win.fault.history()) >= 1, "!FAULT 行未被记录进历史"

    # 徽标在终端页顶行（主页可见、点击跳故障页的设计）——须在切页前断言
    badge_visible = win.btn_fault_badge.isVisible()
    assert badge_visible, "状态栏异常徽标未点亮"
    assert win.btn_fault_badge.text().startswith('⚠ UsageFault'), \
        f"徽标文案异常: {win.btn_fault_badge.text()}"

    tw = win.findChild(QtWidgets.QTabWidget)
    tw.setCurrentWidget(win.fault)
    app.processEvents()

    assert win.fault.tree.topLevelItemCount() > 0, "位域树为空"

    assert OUT.parent.mkdir(parents=True, exist_ok=True) is None
    ok = win.fault.grab().save(str(OUT))
    assert ok, "截图保存失败"

    print(f"PANEL-OK 历史={len(win.fault.history())} "
          f"树节点={win.fault.tree.topLevelItemCount()} "
          f"徽标={badge_visible} 截图={OUT}")
    win.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
