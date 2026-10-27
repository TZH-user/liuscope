# -*- coding: utf-8 -*-
"""ESP32 复位 / 下载模式控制时序（esptool ClassicReset 语义）。

ESP32 经典自动下载电路：RTS→EN（复位）、DTR→IO0（启动模式选择）。
- pulse_reset：EN 拉低再放开，芯片重新运行用户程序（DTR 不动）
- enter_download_mode：复位期间拉低 IO0，出复位后先保持 IO0=L 让 ROM
  bootloader 接管，再放开 IO0 → 进入下载模式（esptool ClassicResetSequence）

sleep 抽成模块级变量，单测可替换成记录器以断言精确时序。
注意：S2/S3/C3 的内置 USB-Serial/JTAG 口没有这套经典时序（专用 USB 复位协议），
对它调用这些函数通常无效或抛错——调用方须容错。
"""
from __future__ import annotations

import time

sleep = time.sleep   # 模块级：测试替换后可记录时序且零等待


def pulse_reset(port) -> None:
    """运行模式复位：RTS（EN）脉冲，DTR 不动。"""
    port.rts = True
    sleep(0.1)
    port.rts = False


def enter_download_mode(port) -> None:
    """进 ROM 下载模式（esptool ClassicResetSequence 语义）。"""
    port.dtr = False    # IO0=H
    port.rts = True     # EN=L，复位中
    sleep(0.1)
    port.dtr = True     # IO0=L
    port.rts = False    # EN=H 出复位 → 以 IO0=L 启动进 bootloader
    sleep(0.05)
    port.dtr = False    # IO0=H，bootloader 运行
