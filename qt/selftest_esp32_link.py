# -*- coding: utf-8 -*-
"""ESP32 连接层自测组(DTR/RTS 策略、复位/下载模式时序、自动重连、VID/PID 标注)。

约定:本文件提供 groups() 返回测试函数列表;由 qt/selftest.py 的 run_selftest 挂载。
所有测试必须离屏可跑、无真机、无网络、不起 sys.executable 子进程(冻结冒烟安全)。
"""
from __future__ import annotations


def test_esp32_link_placeholder():
    """占位组:代理实现前保持套件可全绿。"""
    assert True


def groups():
    return [test_esp32_link_placeholder]
