# -*- coding: utf-8 -*-
"""命令序列发送器（MobaXterm macro 的对应物，纯逻辑离屏可测）。

脚本语法（每行一条，# 注释、空行忽略）：
    send <text>      发送文本（UTF-8，按主窗换行模式补尾）
    sendhex <HEX>    发送十六进制字节（"01 03 00 00 00 01"，分隔符可混用）
    delay <ms>       等待毫秒（0 允许，仅作步进边界）

重复语义由 Runner 承担：整段脚本重复 N 次（1=单次）或无限循环直到 stop()。
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6 import QtCore

from core.hexutil import try_parse_hex_text


class TxSequenceError(ValueError):
    """脚本语法错误（带 1 起始行号，对话框定位用）。"""


@dataclass(frozen=True)
class SeqStep:
    kind: str        # 'send' | 'sendhex' | 'delay'
    text: str        # send: 原文本 / sendhex: 原串 / delay: 原串
    payload: bytes   # send/sendhex: 待发字节；delay: b''
    delay_ms: int    # 仅 delay 有效
    line_no: int     # 1 起始行号（错误定位/状态显示）


def build_send_payload(text: str, newline_mode: str = 'LF') -> bytes:
    """send 行 → 字节（文本 + 换行尾，与主窗 ASCII 发送口径一致）。"""
    tail = {'无': '', 'LF': '\n', 'CR': '\r', 'CRLF': '\r\n'}.get(newline_mode, '\n')
    return (text + tail).encode('utf-8')


def parse_script(script: str, newline_mode: str = 'LF') -> list[SeqStep]:
    """解析脚本 → 步骤表；语法错误抛 TxSequenceError（带行号）。

    完全没有可执行步骤也视为错误（空脚本按错点开始没有意义）。
    """
    steps: list[SeqStep] = []
    for line_no, raw_line in enumerate((script or '').splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        head, _, rest = line.partition(' ')
        head = head.lower()
        rest = rest.strip()
        if head == 'send':
            if not rest:
                raise TxSequenceError(f'第 {line_no} 行: send 缺内容')
            steps.append(SeqStep('send', rest, build_send_payload(rest, newline_mode),
                                 0, line_no))
        elif head == 'sendhex':
            if not rest:
                raise TxSequenceError(f'第 {line_no} 行: sendhex 缺内容')
            try:
                payload = try_parse_hex_text(rest)
            except ValueError as exc:
                raise TxSequenceError(f'第 {line_no} 行: HEX 无效（{exc}）') from exc
            if not payload:
                raise TxSequenceError(f'第 {line_no} 行: HEX 为空')
            steps.append(SeqStep('sendhex', rest, payload, 0, line_no))
        elif head == 'delay':
            try:
                ms = int(rest)
            except ValueError:
                raise TxSequenceError(f'第 {line_no} 行: delay 需要毫秒整数（{rest!r}）')
            if ms < 0:
                raise TxSequenceError(f'第 {line_no} 行: delay 不能为负')
            steps.append(SeqStep('delay', rest, b'', ms, line_no))
        else:
            raise TxSequenceError(f'第 {line_no} 行: 未知指令 {head!r}（send/sendhex/delay）')
    if not steps:
        raise TxSequenceError('没有可执行步骤（send/sendhex/delay）')
    return steps


def describe_step(step: SeqStep) -> str:
    """状态栏/日志用的一句描述。"""
    if step.kind == 'delay':
        return f'delay {step.delay_ms}ms'
    if step.kind == 'sendhex':
        return f'sendhex {step.text}'
    return f'send {step.text}'


class TxSequenceRunner(QtCore.QObject):
    """定时器驱动的序列执行器：send/sendhex 立即发、delay 步等毫秒。

    send_fn 由宿主注入（主窗把 worker 写入 + TX 日志 + 统计打包成一个函数），
    返回 False（未连接/写失败）即中止。delay 走 QTimer 单发，离屏测试用 1ms
    档 + processEvents 驱动。
    """

    step_started = QtCore.Signal(int, str)   # 序列内序号, 描述
    finished = QtCore.Signal(bool)           # True=跑完，False=中止（stop/写失败）

    def __init__(self, steps, send_fn, parent=None):
        """send_fn(step) -> bool：宿主注入的发送函数（写串口+落日志打包）。

        返回 False（未连接/写失败）中止序列。delay 步不会进 send_fn。
        """
        super().__init__(parent)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._steps = list(steps)
        self._send_fn = send_fn
        self._repeat = 1
        self._round = 0
        self._idx = 0
        self._running = False

    # ---- 控制 ----

    def start(self, repeat: int = 1):
        if self._running or not self._steps:
            return
        self._repeat = max(1, int(repeat))
        self._round = 0
        self._idx = 0
        self._running = True
        self._tick()   # 立即执行第一步（send 无定时器延迟）

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._timer.stop()
        self.finished.emit(False)

    def is_running(self) -> bool:
        return self._running

    # ---- 执行 ----

    def _tick(self):
        if not self._running:
            return
        if self._idx >= len(self._steps):
            self._round += 1
            if self._round >= self._repeat:
                self._running = False
                self.finished.emit(True)
                return
            self._idx = 0
        step = self._steps[self._idx]
        self.step_started.emit(self._idx, describe_step(step))
        if step.kind == 'delay':
            self._idx += 1
            self._timer.start(max(1, step.delay_ms) if step.delay_ms else 0)
            return
        if not self._send_fn(step):
            self._running = False
            self.finished.emit(False)
            return
        self._idx += 1
        self._timer.start(0)   # 让出事件循环，下一步下个事件循环片执行

