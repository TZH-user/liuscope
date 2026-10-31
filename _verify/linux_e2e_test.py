# -*- coding: utf-8 -*-
"""Linux 支持页交付的标准测试（T2 控制面 + T3 视频链路，真机 RK3562）。

前置：板端 server_rk 守护已运行（TCP 9201）。PC 侧只依赖 stdlib + av + numpy。
用法: python -I _verify/linux_e2e_test.py [板IP] [收流端口=9305]
输出: 逐用例 [PASS]/[FAIL]/[SKIP] + 汇总；退出码 0=全过（SKIP 不计失败）。
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

HEADER = struct.Struct('>IIB')
FLAG_KEYFRAME = 0x01
IDR_MASK = 0x1F


class Client:
    def __init__(self, host: str, port: int = 9201, timeout: float = 4.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)

    def ask(self, req: dict, timeout: float | None = None) -> dict:
        if timeout:
            self.sock.settimeout(timeout)
        self.sock.sendall((json.dumps(req, ensure_ascii=False) + '\n').encode('utf-8'))
        buf = bytearray()
        while b'\n' not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('对端关闭')
            buf.extend(chunk)
        return json.loads(bytes(buf).partition(b'\n')[0].decode('utf-8'))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Runner:
    def __init__(self):
        self.results: list[tuple[str, str, str]] = []   # (id, 状态, 说明)

    def case(self, cid: str, fn):
        try:
            note = fn() or ''
            self.results.append((cid, 'PASS', note))
            print(f'[{cid}] PASS {note}')
        except AssertionError as exc:
            self.results.append((cid, 'FAIL', str(exc)))
            print(f'[{cid}] FAIL {exc}')
        except Exception as exc:    # noqa: BLE001（任何异常都算该用例失败）
            self.results.append((cid, 'FAIL', f'{type(exc).__name__}: {exc}'))
            print(f'[{cid}] FAIL {type(exc).__name__}: {exc}')

    def summary(self) -> int:
        fails = [r for r in self.results if r[1] == 'FAIL']
        skips = [r for r in self.results if r[1] == 'SKIP']
        print(f'\n==== 汇总: {len(self.results) - len(fails) - len(skips)} PASS / '
              f'{len(fails)} FAIL / {len(skips)} SKIP ====')
        for cid, st, note in fails:
            print(f'  FAILED {cid}: {note}')
        return 1 if fails else 0


def split_nals(blob: bytes):
    marks = [m.start() for m in __import__('re').finditer(b'\x00\x00\x01', blob)]
    out = []
    for k, m in enumerate(marks):
        start = m
        while start > 0 and blob[start - 1] == 0:
            start -= 1
        end = marks[k + 1] if k + 1 < len(marks) else len(blob)
        payload = blob[m + 3:end]
        if payload:
            out.append(payload)
    return out


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else '192.168.2.100'
    udp_port = int(sys.argv[2]) if len(sys.argv) > 2 else 9305
    r = Runner()

    # ---------- T2 控制面 ----------
    cli = Client(host)

    def t_hello():
        resp = cli.ask({'cmd': 'hello', 'v': 1, 'nonce': 'n-fix-1'})
        assert resp.get('ok'), resp
        assert resp.get('nonce') == 'n-fix-1', 'nonce 应回显'
        assert resp.get('challenge'), '必须有 challenge'
        return f"challenge={resp['challenge'][:8]}…"
    r.case('T2.1 hello 挑战与回显', t_hello)

    def t_pair_wrong():
        resp = cli.ask({'cmd': 'pair', 'code': '000000',
                        'challenge': cli.ask({'cmd': 'hello', 'v': 1,
                                              'nonce': 'n2'})['challenge']})
        assert not resp.get('ok') and ('配对' in resp.get('error', '') or
                                       '码' in resp.get('error', '')), resp
        return '错误码被拒'
    r.case('T2.2 错误配对码拒绝', t_pair_wrong)

    def t_unpaired_gate():
        c2 = Client(host)
        try:
            resp = c2.ask({'cmd': 'status'})
            assert not resp.get('ok') and '未配对' in resp.get('error', ''), resp
            return '业务命令未配对被拒'
        finally:
            c2.close()
    r.case('T2.3 未配对门禁', t_unpaired_gate)

    def t_pair_ok():
        hello = cli.ask({'cmd': 'hello', 'v': 1, 'nonce': secrets.token_hex(8)})
        resp = cli.ask({'cmd': 'pair', 'code': '246810',
                        'challenge': hello['challenge']})
        assert resp.get('ok') and resp.get('session'), resp
        return f"session={str(resp['session'])[:8]}…"
    r.case('T2.4 正确配对', t_pair_ok)

    video_running = {'v': False}

    def t_status():
        resp = cli.ask({'cmd': 'status'})
        assert resp.get('ok') and resp.get('paired'), resp
        v = resp.get('video', {})
        assert v.get('available'), f'ffmpeg 应可用: {v}'
        video_running['v'] = bool(v.get('running'))
        return f"running={v.get('running')} codec={v.get('codec')} capture={v.get('capture')}"
    r.case('T2.5 status 能力上报', t_status)

    if video_running['v']:     # 上轮残留：先兜底停流再测
        cli.ask({'cmd': 'video_stop'}, timeout=6.0)
        video_running['v'] = False

    start_resp = {}

    def t_video_start():
        resp = cli.ask({'cmd': 'video_start', 'listen_port': udp_port}, timeout=20.0)
        assert resp.get('ok'), resp
        assert str(resp.get('target', '')).endswith(str(udp_port)), resp
        assert 'pid=' in resp.get('note', ''), resp
        assert isinstance(resp.get('now_ms'), int), '应答必须带板端 epoch 毫秒（PC 算单向时延用）'
        assert 'overlay=' in resp.get('note', ''), 'note 应明示跑表 overlay 状态'
        start_resp.update(resp)
        return f"{resp.get('target')} {resp.get('note')} now_ms={resp.get('now_ms')}"
    r.case('T2.6 video_start', t_video_start)

    def t_dup_start():
        resp = cli.ask({'cmd': 'video_start', 'listen_port': udp_port}, timeout=6.0)
        assert not resp.get('ok') and '已在运行' in resp.get('error', ''), resp
        return '重复启动被拒'
    r.case('T2.7 重复 video_start 拒绝', t_dup_start)

    def t_stop_and_reap():
        resp = cli.ask({'cmd': 'video_stop'}, timeout=6.0)
        assert resp.get('ok'), resp
        st = cli.ask({'cmd': 'status'})
        assert not st['video'].get('running'), f'stop 后仍在跑: {st["video"]}'
        return 'ffmpeg 已回收（status.running=false）'
    r.case('T2.8 video_stop 与进程回收', t_stop_and_reap)

    def t_close():
        resp = cli.ask({'cmd': 'close'})
        assert resp.get('ok') and resp.get('bye'), resp
        rest = cli.sock.recv(256)
        assert rest == b'', f'close 后应断开: {rest!r}'
        return '连接正常关闭'
    r.case('T2.9 close 断开', t_close)
    cli.close()

    # ---------- T3 视频链路质量（含解码） ----------
    def t_video_stream():
        c3 = Client(host)
        c3.ask({'cmd': 'hello', 'v': 1, 'nonce': secrets.token_hex(8)})
        c3.ask({'cmd': 'pair', 'code': '246810',
                'challenge': c3.ask({'cmd': 'hello', 'v': 1, 'nonce': 'x'})['challenge']})
        try:
            c3.ask({'cmd': 'video_stop'}, timeout=6.0)
        except Exception:      # noqa: BLE001
            pass
        resp = c3.ask({'cmd': 'video_start', 'listen_port': udp_port}, timeout=20.0)
        assert resp.get('ok'), resp

        stats = {'n': 0, 'bytes': 0, 'idr': 0, 'lost': 0, 'last': None}
        buf = bytearray()
        done = threading.Event()

        def rx():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(('0.0.0.0', udp_port))
            s.settimeout(0.5)
            while not done.is_set():
                try:
                    data, _ = s.recvfrom(65535)
                except socket.timeout:
                    continue
                if len(data) <= HEADER.size:
                    continue
                seq, _ts, flags = HEADER.unpack_from(data)
                stats['n'] += 1
                stats['bytes'] += len(data) - HEADER.size
                stats['idr'] += 1 if flags & FLAG_KEYFRAME else 0
                if stats['last'] is not None:
                    d = (seq - stats['last']) & 0xFFFFFFFF
                    if d > 1:
                        stats['lost'] += d - 1
                stats['last'] = seq
                buf.extend(data[HEADER.size:])
            s.close()

        th = threading.Thread(target=rx, daemon=True)
        th.start()
        codec = av.CodecContext.create('h264', 'r')
        frames = []
        sizes = set()
        deadline = time.time() + 6.0
        while time.time() < deadline and len(frames) < 48:
            nals = split_nals(bytes(buf))
            if len(nals) >= 2:
                blob = b'\x00\x00\x00\x01'.join([b''] + nals[:-1])
                del buf[:buf.rfind(b'\x00\x00\x01')]
                for nal in nals[:-1]:
                    try:
                        for fr in codec.decode(av.packet.Packet(
                                b'\x00\x00\x00\x01' + nal)):
                            frames.append(fr)
                            sizes.add((fr.width, fr.height))
                    except av.error.InvalidDataError:
                        pass
            time.sleep(0.03)
        done.set()
        th.join(2)
        c3.ask({'cmd': 'video_stop'}, timeout=6.0)
        c3.close()

        assert stats['n'] >= 60, f"5s 应收 ≥60 包，实 {stats['n']}"
        assert stats['lost'] == 0, f"局域网不应丢包，实 {stats['lost']}"
        assert stats['idr'] >= 5, f"GOP24@12fps 5s 应 ≥5 个 IDR 包，实 {stats['idr']}"
        assert len(frames) >= 30, f"应解出 ≥30 帧（12fps×5s），实 {len(frames)}"
        assert sizes == {(1024, 600)}, f'帧尺寸应 1024x600，实 {sizes}'
        # 端口释放验证：重绑应成功
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(('0.0.0.0', udp_port))
        s.close()
        return (f"{stats['n']} 包/{stats['bytes']//1024}KB/IDR {stats['idr']}/"
                f"丢 {stats['lost']} → 解码 {len(frames)} 帧 {sorted(sizes)}，端口已释放")
    r.case('T3.1 视频链路质量+解码+端口回收', t_video_stream)

    return r.summary()


if __name__ == '__main__':
    sys.exit(main())
