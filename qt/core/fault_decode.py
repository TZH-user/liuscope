# -*- coding: utf-8 -*-
"""STM32 HardFault 寄存器 dump 解码（移植主线 core/fault_decode.ps1 逐位口径）。

RX 日志里出现 CFSR/HFSR 寄存器 dump 时，把各错误位翻成可读原因；
配合 MMFAR/BFAR 给出错地址。返回 'CFSR=0x…；原因1；原因2' 或 None。
"""
from __future__ import annotations

import re

_RE_CFSR = re.compile(r'CFSR[:=]\s*0x([0-9A-Fa-f]{1,8})')
_RE_HFSR = re.compile(r'HFSR[:=]\s*0x([0-9A-Fa-f]{1,8})')
_RE_MMFAR = re.compile(r'MMFAR[:=]\s*0x([0-9A-Fa-f]{1,8})')
_RE_BFAR = re.compile(r'BFAR[:=]\s*0x([0-9A-Fa-f]{1,8})')


def test_fault_dump_line(text: str) -> str | None:
    if not text or not ('CFSR' in text or 'HFSR' in text):
        return None

    def grab(pattern):
        m = pattern.search(text)
        return int(m.group(1), 16) if m else None

    cfsr = grab(_RE_CFSR)
    hfsr = grab(_RE_HFSR)
    mmfar = grab(_RE_MMFAR)
    bfar = grab(_RE_BFAR)
    if cfsr is None and hfsr is None:
        return None

    reasons: list[str] = []
    if cfsr is not None:
        mmfsr = cfsr & 0xFF
        bfsr = (cfsr >> 8) & 0xFF
        ufsr = (cfsr >> 16) & 0xFFFF
        if mmfsr & 0x01:
            reasons.append('MMFSR.IACCVIOL 取指访问违例(MPU/XN区域)')
        if mmfsr & 0x02:
            reasons.append('MMFSR.DACCVIOL 数据访问违例(MPU)')
        if mmfsr & 0x08:
            reasons.append('MMFSR.MUNSTKERR 出栈时MemManage错误')
        if mmfsr & 0x10:
            reasons.append('MMFSR.MSTKERR 入栈时MemManage错误')
        if mmfsr & 0x20:
            reasons.append('MMFSR.MLSPERR 惰性状态保存时错误')
        if (mmfsr & 0x80) and mmfar is not None:
            reasons.append(f'MMFSR.MMARVALID 出错地址 MMFAR=0x{mmfar:08X}')
        if bfsr & 0x01:
            reasons.append('BFSR.IBUSERR 取指总线错误')
        if bfsr & 0x02:
            reasons.append('BFSR.PRECISERR 精确数据总线错误')
        if bfsr & 0x04:
            reasons.append('BFSR.IMPRECISERR 非精确数据总线错误(常见于写缓冲)')
        if bfsr & 0x08:
            reasons.append('BFSR.UNSTKERR 出栈时总线错误')
        if bfsr & 0x10:
            reasons.append('BFSR.STKERR 入栈时总线错误')
        if bfsr & 0x20:
            reasons.append('BFSR.LSPERR 惰性状态保存总线错误')
        if (bfsr & 0x80) and bfar is not None:
            reasons.append(f'BFSR.BFARVALID 出错地址 BFAR=0x{bfar:08X}')
        if ufsr & 0x0001:
            reasons.append('UFSR.UNDEFINSTR 未定义指令')
        if ufsr & 0x0002:
            reasons.append('UFSR.INVSTATE 状态无效(常见:函数指针错误/Thumb位)')
        if ufsr & 0x0004:
            reasons.append('UFSR.INVPC 无效PC加载(返回地址错)')
        if ufsr & 0x0008:
            reasons.append('UFSR.NOCP 调用无协处理器(常见:未使能FPU却用浮点)')
        if ufsr & 0x0100:
            reasons.append('UFSR.UNALIGNED 非对齐访问')
        if ufsr & 0x0200:
            reasons.append('UFSR.DIVBYZERO 除零(SDIV/UDIV)')
    if hfsr is not None:
        if hfsr & 0x00000002:
            reasons.append('HFSR.VECTTBL 取异常向量表时总线错误')
        if hfsr & 0x40000000:
            reasons.append('HFSR.FORCED 强制升级为HardFault(具体原因查CFSR)')
    if not reasons:
        reasons.append('CFSR/HFSR 各错误位均未置位')

    parts = []
    if cfsr is not None:
        parts.append(f'CFSR=0x{cfsr:08X}')
    if hfsr is not None:
        parts.append(f'HFSR=0x{hfsr:08X}')
    parts.extend(reasons)
    return '；'.join(parts)
