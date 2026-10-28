# -*- coding: utf-8 -*-
"""RK3562(LubanCat)调试串口控制台驱动——上架/部署/联调复用。

用法(交互式探测):
    python _verify/rk_console.py probe     # 开口、回车、识别提示符并打印
    python _verify/rk_console.py cmd "ip addr"   # 已登录 shell 时直接执行命令
约定:COM 口/波特率可用环境变量 RK_COM / RK_BAUD 覆盖(缺省 COM19/1500000);
打开后立刻松开 DTR/RTS(避免打断板子);全部原始输出落 _verify/evidence_v13/rk_console.log。
"""
from __future__ import annotations

import os
import sys
import time

import serial

COM = os.environ.get('RK_COM', 'COM19')
BAUD = int(os.environ.get('RK_BAUD', '1500000'))
LOG = os.path.join(os.path.dirname(__file__), 'evidence_v13', 'rk_console.log')


class Console:
    def __init__(self, com=COM, baud=BAUD, log=LOG):
        os.makedirs(os.path.dirname(log), exist_ok=True)
        self.ser = serial.serial_for_url(com, baud, timeout=0.2)
        try:
            self.ser.dtr = False
            self.ser.rts = False
        except Exception:
            pass
        self._logf = open(log, 'a', encoding='utf-8', errors='replace')

    def _log(self, kind: str, data: bytes):
        self._logf.write(f'[{time.strftime("%H:%M:%S")}] {kind}: {data!r}\n')
        self._logf.flush()

    def read_until_idle(self, idle_s: float = 1.2, max_s: float = 12.0) -> bytes:
        """读到静默 idle_s 为止(板子开机日志可能持续吐)。"""
        buf = bytearray()
        deadline = time.monotonic() + max_s
        last = time.monotonic()
        while time.monotonic() < deadline:
            chunk = self.ser.read(4096)
            if chunk:
                buf.extend(chunk)
                self._log('RX', chunk)
                last = time.monotonic()
            elif time.monotonic() - last >= idle_s:
                break
        return bytes(buf)

    def send(self, text: str):
        data = text.encode('utf-8')
        self.ser.write(data)
        self._log('TX', data)

    def command(self, cmd: str, idle_s: float = 1.5, max_s: float = 30.0) -> str:
        """发命令 + 回车,读回全部输出(含回显),返回文本。"""
        self.ser.reset_input_buffer()
        self.send(cmd + '\n')
        out = self.read_until_idle(idle_s=idle_s, max_s=max_s)
        return out.decode('utf-8', errors='replace')

    def close(self):
        try:
            self.ser.close()
        finally:
            self._logf.close()


def wait_for(c, pattern: str, timeout_s: float) -> str:
    """阻塞等输出流出现 pattern(正则),返回累计文本(登录态机的正确姿势)。"""
    import re as _re
    deadline = time.monotonic() + timeout_s
    acc = bytearray()
    rx = _re.compile(pattern)
    while time.monotonic() < deadline:
        chunk = c.ser.read(2048)
        if chunk:
            acc.extend(chunk)
            c._log('RX', chunk)
            if rx.search(acc.decode('utf-8', errors='replace')):
                break
        else:
            time.sleep(0.05)
    return acc.decode('utf-8', errors='replace')


def login(c, user: str, pwd: str, timeout_s: float = 14.0) -> str:
    """expect 式登录:回车到用户名提示 → 发用户名 → 等密码提示 → 发密码。

    返回登录全过程文本;调用方自行判 '错误'/'#'/'$'。
    """
    c.send('\r\n')
    wait_for(c, r'用户名|login:', 8)
    c.send(user + '\r\n')
    wait_for(c, r'密码|assword', 10)
    time.sleep(0.6)
    c.send(pwd + '\r\n')
    return wait_for(c, r'[$#] |错误', timeout_s)


def probe():
    c = Console()
    try:
        c.send('\r\n')
        out = c.read_until_idle(idle_s=2.0, max_s=8.0)
        text = out.decode('utf-8', errors='replace')
        print('--- 静默 2s 后收到的内容(尾部 1200 字) ---')
        print(text[-1200:] if text else '(空——端口无输出)')
    finally:
        c.close()


def cmd_main():
    cmds = sys.argv[2:]
    c = Console()
    try:
        c.send('\r\n')
        c.read_until_idle(idle_s=1.0, max_s=4.0)   # 清可能挂着的提示符
        for cmd in cmds:
            print(f'$ {cmd}')
            print(c.command(cmd))
            print('=' * 60)
    finally:
        c.close()


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == 'probe':
        probe()
    elif len(sys.argv) >= 3 and sys.argv[1] == 'cmd':
        cmd_main()
    else:
        print(__doc__)
