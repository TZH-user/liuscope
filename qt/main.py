# -*- coding: utf-8 -*-
"""LiuScope（柳镜）入口。

运行：  python qt/main.py
自测：  python qt/main.py --selftest   （离屏 QT_QPA_PLATFORM=offscreen 亦可跑）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6 import QtGui, QtWidgets  # noqa: E402

from ui import theme  # noqa: E402


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    # 应用图标（任务栏/标题栏；打包后 __file__ 位于解包目录，同样指向 assets/liuscope.ico）
    _icon = Path(__file__).resolve().parent / 'assets' / 'liuscope.ico'
    if _icon.exists():
        app.setWindowIcon(QtGui.QIcon(str(_icon)))
    # 启动恢复主题（读设置键 ui/theme，缺省深色）：一处统一，MainWindow 内不再重复设置
    theme.apply_saved_theme(app)

    from ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        from selftest import run_selftest
        sys.exit(run_selftest())
    if '--selftest-exit' in sys.argv:
        # 冻结打包冒烟：只跑冻结安全子集，以退出码表达结果（windowed exe 无控制台输出）
        from selftest import run_selftest
        sys.exit(run_selftest(frozen=True))
    sys.exit(main())
