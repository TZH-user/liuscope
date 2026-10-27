# -*- coding: utf-8 -*-
"""v2 M2 对接自测：板端 packetize →（UDP 回环）→ PC 端 H264UdpSource → VideoDecoder 全链互通。

证明两端独立实现的 [seq u32][ts_ms u32][flags u8] 线上约定咬合（网络字节序/大端）：
PyAV(libx264) 编码合成流 → split_nals → 板端 packetize 逐 NAL 打包（seq 逐包递增、
IDR/SPS 带关键帧标志）→ UDP 回环 → PC 端拆包/起始码重组 → AU 组包 → 软解。

断言口径：重组器会把 3 字节起始码补成 4 字节（Annex-B 合法、解码等价），故按
split_nals 归一化后的 NAL 列表逐条相等；触发 NAL 冲出最后一个真实 NAL，AUD 自身
无后续起始码留在缓冲（协议语义，非缺陷）。丢包实验只验传输层计数——H.264 缺参考帧
的后续 AU 报解码错属设计使然，M3 用 resync{from_seq} 请求板端强制 IDR 才是正解。

运行（仓库根目录，offscreen 无需硬件）：python server-rk/test_wire_compat.py
"""
import os
import socket
import struct
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'server-rk'))
sys.path.insert(0, str(ROOT / 'qt'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtWidgets                    # noqa: E402

from server_rk.video import packetize            # noqa: E402
from ui.device_screen import H264UdpSource, split_nals  # noqa: E402


def _encode_stream(h264_path: str, w=320, h=240, n_frames=30, fps=30) -> bytes:
    """合成 Annex-B 裸流（移动色块，baseline 无 B 帧，zerolatency）。"""
    import av
    import numpy as np
    cont = av.open(h264_path, 'w', format='h264')
    st = cont.add_stream('libx264', rate=fps)
    st.width, st.height, st.pix_fmt = w, h, 'yuv420p'
    st.time_base = Fraction(1, fps)
    st.options = {'profile': 'baseline', 'tune': 'zerolatency', 'preset': 'ultrafast'}
    for i in range(n_frames):
        img = np.zeros((h, w, 3), np.uint8)
        x = (i * 8) % (w - 40)
        img[100:140, x:x + 40] = (0, 200, 80)
        img[:, :8] = i * 8 % 256
        for pkt in st.encode(av.VideoFrame.from_ndarray(img, format='bgr24')):
            cont.mux(pkt)
    for pkt in st.encode():
        cont.mux(pkt)
    cont.close()
    return Path(h264_path).read_bytes()


def _pump(app, cond, timeout_s=15.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(cond())


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    tmp = tempfile.mkdtemp(prefix='rk_wire_')
    stream = _encode_stream(os.path.join(tmp, 'synthetic.h264'))
    nals = split_nals(stream)
    assert any(n[4] & 0x1F == 7 for n in nals) and any(n[4] & 0x1F == 5 for n in nals), (
        '流应含 SPS 与 IDR')

    # ---- A1 字节级（裸源直驱，返回值即重组输出；无线程、确定性）----
    src = H264UdpSource(0)
    resyncs = []
    src.resync_needed.connect(resyncs.append)
    parts = []
    datagrams = []
    seq, ts = 0, 0
    for nal in nals:
        pkts, seq = packetize(nal, seq, ts, mtu=1200,
                              keyframe=(nal[4] & 0x1F) in (5, 7))
        for p in pkts:
            datagrams.append(p)
            parts.append(src._process_datagram(p))
        ts += 33
    # 触发 NAL（AUD）的起始码冲出缓冲中的最后一个真实 NAL；AUD 自身留在缓冲
    flush = struct.pack('>IIB', seq, ts, 0) + b'\x00\x00\x00\x01\x09\x80'
    src._process_datagram(flush)
    collected = b''.join(parts)
    cn = split_nals(collected)
    assert cn == nals[:-1], (
        f'NAL 归一化往返不一致：收集 {len(cn)} 条 / 期望 {len(nals) - 1} 条')
    assert src.lost_count == 0 and src.packet_count == len(datagrams) + 1
    # 丢包计口径：seq 跳 5 → 恰好记丢 4 包并触发 resync
    src._process_datagram(struct.pack('>IIB', seq + 5, ts, 0) + b'\x00\x00\x00\x01\x65\x00')
    assert src.lost_count == 4 and resyncs, (
        f'seq 跳 5 应记丢 4 包并触发 resync，实际 lost={src.lost_count} resync={resyncs}')

    # ---- A2 解码级（页面生产接线：source 线程 → 队列信号 → 解码线程）----
    from ui.device_screen import DeviceScreenPage
    page = DeviceScreenPage()
    page.show()
    port = 29802 + os.getpid() % 200
    page.spin_port.setValue(port)
    page.btn_start.click()
    assert _pump(app, lambda: page.source is not None and page.source.is_bound), (
        'UDP 源应绑定')
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for pkt in datagrams:
        sock.sendto(pkt, ('127.0.0.1', port))
    sock.close()
    assert _pump(app, lambda: page.decoder is not None
                 and page.decoder.frame_count >= 25), '解码帧数不足'
    page.decoder.drain()
    app.processEvents()
    dec = page.decoder
    assert dec.last_size == (320, 240), f'帧尺寸 {dec.last_size}'
    assert dec.decode_errors == 0, f'解码错误 {dec.decode_errors}'
    assert page.source.lost_count == 0 and page.source.packet_count == len(datagrams)
    page.btn_start.click()   # toggle_stream：运行中点击即停止

    print(f'WIRE COMPAT OK — NAL {len(nals)} 个 / 包 {len(datagrams)} 个 / '
          f'归一化往返一致 / 丢包计口径正确 / 解出 {dec.frame_count} 帧 / '
          f'解码均耗 {dec.avg_decode_ms:.1f}ms')
    return 0


if __name__ == '__main__':
    sys.exit(main())
