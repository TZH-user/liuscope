# -*- coding: utf-8 -*-
"""serialscope-qt 入口。

运行：  python qt/main.py
自测：  python qt/main.py --selftest   （离屏 QT_QPA_PLATFORM=offscreen 亦可跑）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6 import QtWidgets  # noqa: E402

from ui.theme import DARK_QSS  # noqa: E402


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)

    from ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        from selftest import run_selftest
        sys.exit(run_selftest())
    sys.exit(main())
