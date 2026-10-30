# -*- coding: utf-8 -*-
"""端到端证据帧：控制通道触发板端发流 → UDP 收 NAL → PyAV 解码 → 存 PNG。

用法: python -I _verify/linux_video_frame.py [板IP] [收流端口] [输出png]
与 linux_ctrl_smoke.py 同一条控制链路（hello→pair→video_start），区别在收流侧
不数包而是重组 NAL 喂解码器，取首帧之后第 10 帧存图（跳过编码器warm-up帧）。
"""
from __future__ import annotations

import json
import secrets
import socket
import struct
import sys
import threading
import time

import av
import numpy as np

HEADER = struct.Struct('>IIB')


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else '192.168.2.100'
    udp_port = int(sys.argv[2]) if len(sys.argv) > 2 else 9306
    out_png = sys.argv[3] if len(sys.argv) > 3 else '_verify/linux_video_frame.png'

    sock = socket.create_connection((host, 9201), timeout=4)
    sock.settimeout(20)

    def ask(req: dict, timeout: float = 20.0) -> dict:
        sock.settimeout(timeout)
        sock.sendall((json.dumps(req, ensure_ascii=False) + '\n').encode('utf-8'))
        buf = bytearray()
        while b'\n' not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError('对端关闭')
            buf.extend(chunk)
        resp = json.loads(bytes(buf).partition(b'\n')[0].decode('utf-8'))
        if not resp.get('ok'):
            raise RuntimeError(f"控制失败: {resp}")
        return resp

    try:    # 兜底清理上次残留
        ask({'cmd': 'video_stop'}, timeout=6.0)
    except (RuntimeError, OSError):
        pass
    hello = ask({'cmd': 'hello', 'v': 1, 'nonce': secrets.token_hex(8)})
    ask({'cmd': 'pair', 'code': '246810', 'challenge': hello['challenge']})
    resp = ask({'cmd': 'video_start', 'listen_port': udp_port})
    print('video_start →', resp.get('target'), resp.get('note'))

    nal_buf = bytearray()
    frames = []
    done = threading.Event()

    def recv():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(('0.0.0.0', udp_port))
        s.settimeout(0.5)
        while not done.is_set():
            try:
                data, _addr = s.recvfrom(65535)
            except socket.timeout:
                continue
            if len(data) > HEADER.size:
                nal_buf.extend(data[HEADER.size:])
        s.close()

    t = threading.Thread(target=recv, daemon=True)
    t.start()

    codec = av.CodecContext.create('h264', 'r')
    deadline = time.time() + 15
    while time.time() < deadline and len(frames) < 11:
        if b'\x00\x00\x00\x01' not in nal_buf:
            time.sleep(0.05)
            continue
        marks = [m.start() for m in __import__('re').finditer(b'\x00\x00\x01', bytes(nal_buf))]
        if len(marks) < 2:
            time.sleep(0.05)
            continue
        blob = bytes(nal_buf[:marks[-1]])
        del nal_buf[:marks[-1]]
        for nal in __import__('re').split(b'\x00\x00\x01', blob)[1:]:
            if not nal:
                continue
            try:
                for frame in codec.decode(av.packet.Packet(b'\x00\x00\x00\x01' + nal)):
                    frames.append(frame)
            except av.error.InvalidDataError:
                pass    # 切包残片，跳过
    done.set()
    t.join(2)
    try:
        ask({'cmd': 'video_stop'}, timeout=6.0)
    finally:
        sock.close()

    if not frames:
        print('FAIL: 15s 内未解出任何帧')
        return 1
    frame = frames[min(10, len(frames) - 1)]
    arr = frame.to_ndarray(format='bgr24')
    from PIL import Image
    Image.fromarray(arr[:, :, ::-1]).save(out_png)
    print(f'OK: 解出 {len(frames)} 帧，尺寸 {frame.width}x{frame.height}，已存 {out_png}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
