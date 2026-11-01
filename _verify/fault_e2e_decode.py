# -*- coding: utf-8 -*-
"""!FAULT 端到端无头验证：真机捕获行 → 宽容解析 → ELF32 符号化 → 位域人话。

数据源：真机 FAULTTEST 触发（bench_b01_fault.py 落盘 fault_line.txt），
符号库：实验区 serial_port V4.3.0 构建产物 serial_port.elf（与板上固件同源）。
用法：python -X utf8 _verify/fault_e2e_decode.py   （退出码 0 = 全断言过）
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qt"))

from core.fault_report import (  # noqa: E402
    ElfSymbols, decode_reasons, describe_address, parse_fault_line, symbolize,
)

LINE_FILE = pathlib.Path(
    r"F:\软件仓库_新\开发软件\串口调试助手实验区\h743xih6\logs\fault_line.txt")
ELF = pathlib.Path(
    r"F:\软件仓库_新\开发软件\串口调试助手实验区\h743xih6\serial_port\build\Debug\serial_port.elf")

line = LINE_FILE.read_text(encoding="utf-8").strip()
print(f"输入行: {line}")

info = parse_fault_line(line)
assert info is not None, "!FAULT 行解析失败"
print(f"类型   : {info.fault_type}")
print(f"PC     : {info.pc:#010x}  LR: {info.lr:#010x}  XPSR: {info.xpsr:#010x}")
print(f"CFSR   : {info.cfsr:#010x}  HFSR: {info.hfsr:#010x}")

syms = ElfSymbols.from_file(str(ELF))
print(f"ELF    : 函数符号 {len(syms.addrs)} 个（{ELF.name}）")
sym_pc = symbolize(syms, info.pc)
sym_lr = symbolize(syms, info.lr)
print(f"PC 符号: {sym_pc}")
print(f"LR 符号: {sym_lr}")
assert sym_pc and sym_pc != f"{info.pc:#010x}", "PC 未符号化到函数"

reasons = decode_reasons(info)
print(f"位域   : {reasons}")
assert any("非对齐" in r or "UNALIGNED" in r for r in reasons), \
    f"UNALIGNED 位域未解出: {reasons}"

desc = describe_address(info)
print(f"地址人话: {desc}")

print("FAULT-DECODE-PASS")
