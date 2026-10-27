# -*- coding: utf-8 -*-
"""ESP32 日志层自测组(IDF 日志解析、复位横幅/崩溃检测、backtrace 提取、端口错误提示)。

约定:本文件提供 groups() 返回测试函数列表;由 qt/selftest.py 的 run_selftest 挂载。
所有测试必须离屏可跑、无真机、无网络、不起 sys.executable 子进程(冻结冒烟安全)。
"""
from __future__ import annotations


def test_esp32_log_placeholder():
    """占位组:代理实现前保持套件可全绿。"""
    assert True


def groups():
    return [test_esp32_log_placeholder]
