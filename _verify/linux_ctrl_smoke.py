# -*- coding: utf-8 -*-
"""Linux 支持页端到端冒烟：控制通道触发板端发流 + UDP 收流计数。

用法: python _verify/linux_ctrl_smoke.py [板IP] [收流端口(默认9305,避开页面占用的9202)]
流程: TCP 9201 hello→pair(默认码)→video_start{listen_port} → 绑 UDP 收 3s
      → video_stop → 输出包数/字节/seq 连续性/IDR 标志，退出码 0=有流且 seq 无大跳。
"""
from __future__ import annotations

import json
import secrets
import socket
import struct
import sys
import threading
import time

HEADER = struct.Struct('>IIB')
FLAG_KEYFRAME = 0x01


class Client:
    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=4)
        self.sock.settimeout(4)

    def ask(self, req: dict, timeout: float = 4.0) -> dict:
        self.sock.settimeout(timeout)   # video_start 冷启动含管线/编码探测，~10s 量级
        self.sock.sendall((json.dumps(req, ensure_ascii=False) + '\n').encode('utf-8'))
        buf = bytearray()
        while b'\n' not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('对端关闭')
            buf.extend(chunk)
        resp = json.loads(bytes(buf).partition(b'\n')[0].decode('utf-8'))
        if not resp.get('ok'):
            raise RuntimeError(f"控制失败: {resp}")
        return resp


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else '192.168.2.100'
    udp_port = int(sys.argv[2]) if len(sys.argv) > 2 else 9305

    cli = Client(host, 9201)
    hello = cli.ask({'cmd': 'hello', 'v': 1, 'nonce': secrets.token_hex(8)})
    cli.ask({'cmd': 'pair', 'code': '246810', 'challenge': hello['challenge']})
    status = cli.ask({'cmd': 'status'})
    print('status:', json.dumps(status, ensure_ascii=False))
    try:    # 兜底清理上次异常残留的 ffmpeg（孤儿流）
        cli.ask({'cmd': 'video_stop'}, timeout=6.0)
    except (RuntimeError, OSError):
        pass
    resp = cli.ask({'cmd': 'video_start', 'listen_port': udp_port}, timeout=20.0)
    print('video_start →', resp.get('target'), resp.get('note'))

    stats = {'n': 0, 'bytes': 0, 'drops': 0, 'idr': 0, 'last': None}
    done = threading.Event()

    def sniff():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(('0.0.0.0', udp_port))
        s.settimeout(0.5)
        while not done.is_set():
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                continue
            if len(data) <= HEADER.size:
                continue
            seq, _ts, flags = HEADER.unpack_from(data)
            st = stats
            st['n'] += 1
            st['bytes'] += len(data) - HEADER.size
            st['idr'] += 1 if flags & FLAG_KEYFRAME else 0
            if st['last'] is not None:
                delta = (seq - st['last']) & 0xFFFFFFFF
                if delta > 1:
                    st['drops'] += delta - 1
            st['last'] = seq
        s.close()

    t = threading.Thread(target=sniff, daemon=True)
    t.start()
    time.sleep(3.0)
    done.set()
    t.join(2)

    try:
        cli.ask({'cmd': 'video_stop'}, timeout=6.0)
    finally:
        cli.sock.close()
    print(f"收流: {stats['n']} 包 / {stats['bytes']} B / IDR {stats['idr']} / "
          f"seq 跳丢 {stats['drops']}")
    ok = stats['n'] > 20
    print('SMOKE', 'OK' if ok else 'FAIL（板端没发出流）')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
