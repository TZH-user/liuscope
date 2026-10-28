# -*- coding: utf-8 -*-
"""通过调试串口把 server-rk 部署包灌进板子(网线不可用时的带外通道)。

v3 防御式状态机(对 RK FIQ debugger 随机劫持控制台的适配):
- 任何命令组前先「无差别复位」:console\\n(在 FIQ=交还,bash=无害报错)+
  Ctrl+C(清行缓冲)+ 回车——两种模式下都收敛到 bash 提示符;
- 每条命令后若看不到 `$ ` 提示符,自动复位重试;
- 传输块:发内容(无换行)→ 回显比对 → 提交;每 10 块一次健康检查;
- 全程 md5 校验,失败整段重传(最多 3 轮)。
用法:python _verify/rk_deploy_serial.py
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import sys
import time

sys.path.insert(0, '_verify')
from rk_console import Console, wait_for   # noqa: E402

CHUNK = 512
EV = os.path.join(os.path.dirname(__file__), 'evidence_v13')
FILES = [
    ('d1', os.path.join(EV, 'rk_deploy.tar.gz')),
    ('d2', os.path.join(EV, 'wheels', 'pyserial-3.5-py2.py3-none-any.whl')),
]


def recover(c: Console):
    """无差别复位到 bash 提示符(FIJ 劫持/续行/行缓冲一网打尽)。"""
    c.send('console\n')
    time.sleep(0.35)
    c.send('\x03')
    time.sleep(0.25)
    c.send('\n')
    time.sleep(0.35)
    try:
        c.ser.reset_input_buffer()
    except Exception:
        pass


def sh(c: Console, cmd: str, timeout_s: float = 60.0, retries: int = 2) -> str:
    """复位→执行→等提示符;看不到提示符就复位重试。"""
    for i in range(retries + 1):
        recover(c)
        c.send(cmd + '\n')
        out = wait_for(c, r'\$ |# ', timeout_s)
        text = out.replace('\r', '')
        if re.search(r'\$ |# ', text):
            if 'debug>' in text:
                continue   # 被 FIQ 吞了,复位重来
            return text
    return text


def md5_board(c: Console, tag: str) -> str | None:
    out = sh(c, f'md5sum /tmp/{tag}.bin 2>/dev/null | cut -c1-32', 30)
    m = re.findall(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])', out)
    return m[-1] if m else None


def _wc(c: Console, tag: str) -> int:
    out = sh(c, f'wc -c < /tmp/{tag}.b64 2>/dev/null', 20)
    nums = re.findall(r'\d+', out.replace('\r', '').splitlines()[-2] if out else '')
    # 取输出里最后一个数(回显命令里也有数字,wc 结果在最后)
    return int(nums[-1]) if nums else 0


def transfer(c: Console, tag: str, raw: bytes) -> bool:
    """每块提交后 wc -c 对账:文件大小是唯一真相;不符则截断重发当前块。"""
    b64 = base64.b64encode(raw).decode()
    sh(c, f'rm -f /tmp/{tag}.b64', 20)
    if _wc(c, tag) != 0:
        sh(c, f'rm -f /tmp/{tag}.b64 && sync', 20)
    lines = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
    t0 = time.time()
    fails = 0
    for idx, chunk in enumerate(lines):
        expected = (idx + 1) * CHUNK
        attempt = 0
        while True:
            recover(c)
            c.send(f"printf '%s' '{chunk}' >> /tmp/{tag}.b64")   # 无换行
            got = wait_for(c, re.escape(chunk), 3.0)
            c.send('\n')                                          # 提交(无论回显如何)
            wait_for(c, r'\$ |# ', 8)
            actual = _wc(c, tag)
            if actual == expected:
                break
            fails += 1
            attempt += 1
            print(f'   块 {idx}: 对账 {actual} != {expected}(第 {attempt} 次),截断重发')
            sh(c, f'truncate -s {idx * CHUNK} /tmp/{tag}.b64', 20)
            if attempt >= 5:
                print(f'   块 {idx} 连续 5 次失败,中止')
                return False
        if (idx + 1) % 20 == 0:
            print(f'   … {idx + 1}/{len(lines)} 块 {time.time() - t0:.0f}s 重发 {fails}', flush=True)
    print(f'   传输完成 {time.time() - t0:.0f}s,{len(lines)} 块,对账修正 {fails}')
    return True


def main() -> int:
    c = Console()
    try:
        print('== 复位到 bash ==')
        sh(c, 'true')
        for tag, path in FILES:
            raw = open(path, 'rb').read()
            md5 = hashlib.md5(raw).hexdigest()
            have = md5_board(c, tag)
            if have == md5:
                print(f'== {os.path.basename(path)}: 板端已验证,跳过 ==')
                continue
            print(f'== 传输 {os.path.basename(path)}: {len(raw)}B ==')
            done = False
            for round_i in range(3):
                if transfer(c, tag, raw):
                    sh(c, f'base64 -d /tmp/{tag}.b64 > /tmp/{tag}.bin 2>/dev/null', 60)
                    have = md5_board(c, tag)
                    print('   md5:', have, '期望:', md5)
                    if have == md5:
                        done = True
                        break
                print(f'   第 {round_i + 1} 轮失败,整段重传')
            if not done:
                print('!! 三轮失败,中止')
                return 1

        print('== 解包与安装 ==')
        sh(c, 'tar xzf /tmp/d1.bin -C ~', 60)
        out = sh(c, 'python3 -m zipfile -e /tmp/d2.bin /tmp/ps/ && mkdir -p '
                    '~/.local/lib/python3.11/site-packages && '
                    'cp -r /tmp/ps/serial ~/.local/lib/python3.11/site-packages/ && '
                    'python3 -c "import serial; print(\'PYSERIAL-OK\', serial.VERSION)"', 120)
        print('pyserial:', [l.strip() for l in out.replace('\r', '').splitlines()
                            if 'PYSERIAL' in l] or '(未确认)')

        print('== 板端自检 ==')
        out = sh(c, 'cd ~ && timeout 150 python3 test_server_rk.py 2>&1 | tail -3', 200)
        print(out.replace('\r', '')[-400:])
        ok = 'SERVER-RK TESTS OK' in out.replace('\r', '')
        print('板端部署:', '完成' if ok else '自检未过')
        return 0 if ok else 1
    finally:
        c.close()


if __name__ == '__main__':
    sys.exit(main())
