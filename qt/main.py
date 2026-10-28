# -*- coding: utf-8 -*-
"""LiuScope（柳镜）入口。

运行：  python qt/main.py
自测：  python qt/main.py --selftest   （离屏 QT_QPA_PLATFORM=offscreen 亦可跑）
"""
import os
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
    only = None
    for arg in sys.argv:
        if arg.startswith('--selftest-only='):
            only = arg.split('=', 1)[1]
    if '--selftest' in sys.argv or only or '--selftest-exit' in sys.argv:
        # --selftest-exit = 冻结打包冒烟（冻结安全子集，退出码门禁）
        # --selftest-only=a,b = 组子集（诊断/定点回归；配 --selftest-exit 可在冻结下跑）
        from selftest import run_selftest
        try:
            rc = run_selftest(frozen='--selftest-exit' in sys.argv, only=only)
        except BaseException:
            # windowed 冻结 exe 异常退出会在解释器收尾挂死——硬退非零码给 CI 门禁
            import traceback
            traceback.print_exc()
            os._exit(1)
        sys.exit(rc)
    sys.exit(main())
