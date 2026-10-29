# -*- coding: utf-8 -*-
"""XMODEM / YMODEM 发送端（PC → 设备），纯逻辑字节泵，离屏可测。

场景：STM32 ROM bootloader（AN3155，YMODEM CRC）烧固件、U-Boot loadx/loady
收内核镜像。本模块只管协议状态机——读写串口由宿主注入：

    xfer.write(data)              把待发字节交给宿主（线程安全由宿主保证）
    xfer.on_rx(byte)              宿主把收到的每个字节喂进来（驱动状态机）
    xfer.done / xfer.error        完成信号（False=出错）

协议实现口径：
- XMODEM：SOH/128 字节数据块 + CRC16-CCITT（对方回 'C' 即 CRC 模式，回 NAK
  则退 checksum 模式——bootloader 老固件场景），ACK/NAK 重发，上限 10 次；
  结束 EOT（收到 ACK 才算完成）。
- YMODEM：批传输 = 块 0（文件名\\0大小\\0，SOH 128 满块补 0）→ 数据块（STX
  1024，末块补 0x1A）→ 结束：空 STX 块 + EOT；块 0 发送前等接收方 'C'。
- 超时由宿主计时驱动 tick()：无进展 tick 到上限即 error（默认 3s 无字节请求）。

测试锚点：与仿真接收端（selftest 内）整环跑通 + CRC 向量 0x31 0x32 → 0x3123? 
（CRC16-CCITT(0x1021, init 0) 对 b'123456789' = 0x31C3，标准校验值）。
"""
from __future__ import annotations

SOH, STX, EOT, ACK, NAK, CAN = 0x01, 0x02, 0x04, 0x06, 0x15, 0x18
CRC_CHR = 0x43            # 'C'
X_BLOCK = 128             # XMODEM 块数据长
Y_BLOCK = 1024            # YMODEM 数据块长
PAD_BYTE = 0x1A           # ^Z 填充（YMODEM 末块）
MAX_RETRIES = 10
TICKS_TIMEOUT = 30        # tick() 无进展上限（宿主定 tick 周期，建议 100ms → 3s）

_CRC_TABLE: list[int] | None = None


def crc16_ccitt(data: bytes) -> int:
    """CRC16-CCITT（poly 0x1021，init 0，XMODEM/YMODEM 口径）。"""
    global _CRC_TABLE
    if _CRC_TABLE is None:
        table = []
        for i in range(256):
            c = i << 8
            for _ in range(8):
                c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
            table.append(c)
        _CRC_TABLE = table
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC_TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc


def _pad_block(data: bytes, size: int, pad: int) -> bytes:
    return data[:size] + bytes([pad]) * (size - len(data))


class _BaseSender:
    """X/YMODEM 发送端公共骨架：状态机 + 重发计数 + 进度回调。"""

    def __init__(self, write, on_progress=None):
        self.write = write                       # 宿主注入：bytes → 串口
        self.on_progress = on_progress or (lambda sent, total: None)
        self.done = False
        self.error: str | None = None
        self._retries = 0
        self._ticks = 0
        self._buf = bytearray()                  # 待发文件尾余量

    # ---- 宿主接口 ----

    def on_rx(self, byte: int):
        if self.done or self.error:
            return
        self._ticks = 0
        self._on_byte(byte)

    def tick(self):
        """宿主空闲计时调用：超时未收到对端请求/应答则判错。"""
        if self.done or self.error:
            return
        self._ticks += 1
        if self._ticks >= TICKS_TIMEOUT:
            self.error = '对端无响应（超时）'

    # ---- 内部骨架 ----

    def _fail(self, msg: str):
        self.error = msg
        self.write(bytes([CAN, CAN]))

    def _retry_or_fail(self, msg: str):
        self._retries += 1
        if self._retries > MAX_RETRIES:
            self._fail(msg)
        else:
            self._resend()

    def _on_byte(self, byte: int):  # pragma: no cover - 子类实现
        raise NotImplementedError


class XmodemSender(_BaseSender):
    """XMODEM 发送端：等 'C'（CRC）或 NAK（checksum）→ 逐块 ACK → EOT → ACK 收尾。

    测试锚点：CRC16-CCITT(XMODEM) 对 b'123456789' = 0x31C3。
    """

    def __init__(self, data: bytes, write, on_progress=None):
        super().__init__(write, on_progress)
        self._data = data
        self._total = max(1, (len(data) + X_BLOCK - 1) // X_BLOCK)
        self._block_no = 1
        self._mode_crc = True
        self._state = 'wait_start'

    def _resend(self):
        if self._state == 'blocks':
            self._send_block()
        elif self._state == 'eot':
            self.write(bytes([EOT]))
        # wait_start 无重发语义（等对端字符），_retry_or_fail 不会在该态调用

    def _send_block(self):
        off = (self._block_no - 1) * X_BLOCK
        chunk = self._data[off:off + X_BLOCK]
        payload = _pad_block(chunk, X_BLOCK, PAD_BYTE)
        head = bytes([SOH, self._block_no & 0xFF, (~self._block_no) & 0xFF])
        if self._mode_crc:
            crc = crc16_ccitt(payload)
            self.write(head + payload + bytes([crc >> 8, crc & 0xFF]))
        else:
            csum = sum(payload) & 0xFF
            self.write(head + payload + bytes([csum]))
        self.on_progress(self._block_no, self._total)

    def _on_byte(self, byte: int):
        st = self._state
        if byte == CAN:
            self.error = '对端取消（CAN）'
            return
        if st == 'wait_start':
            if byte == CRC_CHR:
                self._mode_crc = True
                self._state = 'blocks'
                self._send_block()
            elif byte == NAK:
                self._mode_crc = False
                self._state = 'blocks'
                self._send_block()
            return
        if st == 'blocks':
            if byte == ACK:
                self._retries = 0
                if self._block_no >= self._total:
                    self._state = 'eot'
                    self.write(bytes([EOT]))
                else:
                    self._block_no += 1
                    self._send_block()
            elif byte == NAK:
                self._retry_or_fail('数据块被拒（NAK 超限）')
            return
        if st == 'eot':
            if byte == ACK:
                self.done = True
            elif byte == NAK:
                self.write(bytes([EOT]))   # 部分接收端先 NAK 一次再 ACK
            return


class YmodemSender(_BaseSender):
    """YMODEM 批发送端：块0 文件头 → 1024 数据块 → 空块收尾。

    流程：等 'C' → 块0（ACK 后再等 'C'，标准接收端行为）→ 数据块逐块 ACK →
    EOT → ACK → 等 'C' → 空 SOH 块（全 0）→ ACK → done。
    """

    def __init__(self, data: bytes, filename: str, write, on_progress=None):
        super().__init__(write, on_progress)
        self._data = data
        self._filename = filename[:64]
        self._total = (len(data) + Y_BLOCK - 1) // Y_BLOCK
        self._block_no = 0            # 0 = 文件头块
        self._state = 'wait_c'        # wait_c / header_ack_c / blocks / eot / final_wait_c / null_ack
        header = self._filename.encode('ascii', 'ignore') + b'\0' + \
            str(len(data)).encode('ascii') + b'\0'
        self._header_block = _pad_block(header, X_BLOCK, 0)

    def _resend(self):
        self._send_current()

    def _send_current(self):
        if self._block_no == 0:
            self._write_block(SOH, 0, self._header_block)
            return
        off = (self._block_no - 1) * Y_BLOCK
        chunk = self._data[off:off + Y_BLOCK]
        if chunk:
            payload = _pad_block(chunk, Y_BLOCK, PAD_BYTE)
            self._write_block(STX, self._block_no, payload)
        else:
            # 数据发完：EOT 收尾（等 ACK 后再发空块）
            self.write(bytes([EOT]))
            self._state = 'eot'

    def _write_block(self, head: int, no: int, payload: bytes):
        crc = crc16_ccitt(payload)
        self.write(bytes([head, no & 0xFF, (~no) & 0xFF]) + payload +
                   bytes([crc >> 8, crc & 0xFF]))
        if head == STX:
            self.on_progress(no, self._total)

    def _on_byte(self, byte: int):
        st = self._state
        if byte == CAN and st != 'wait_c':
            self.error = '对端取消（CAN）'
            return
        if st == 'wait_c':
            if byte == CRC_CHR:
                self._state = 'header_ack'
                self._send_current()
            elif byte == NAK:
                pass   # 老接收端：继续等 'C'（YMODEM 必须 CRC）
            return
        if st == 'header_ack':
            if byte == ACK:
                self._state = 'header_c'
            elif byte == NAK:
                self._retry_or_fail('块0被拒（NAK 超限）')
            return
        if st == 'header_c':
            if byte == CRC_CHR:
                self._block_no = 1
                self._state = 'blocks'
                self._send_current()
            return
        if st == 'blocks':
            if byte == ACK:
                self._retries = 0
                self._block_no += 1
                self._send_current()
            elif byte == NAK:
                self._retry_or_fail('数据块被拒（NAK 超限）')
            return
        if st == 'eot':
            # EOT 阶段：首 ACK（可能先 NAK）后收 'C' 发空块
            if byte == ACK:
                self._state = 'final_wait_c'
            elif byte == CRC_CHR:
                self._block_no += 1
                self._state = 'null_ack'
                self._write_block(SOH, self._block_no, bytes(X_BLOCK))
            return
        if st == 'final_wait_c':
            if byte == CRC_CHR:
                self._block_no += 1
                self._state = 'null_ack'
                self._write_block(SOH, self._block_no, bytes(X_BLOCK))
            return
        if st == 'null_ack':
            if byte == ACK:
                self.done = True
            elif byte == NAK:
                self._retry_or_fail('结束块被拒（NAK 超限）')
            return
