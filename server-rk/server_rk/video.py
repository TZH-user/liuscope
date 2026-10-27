# -*- coding: utf-8 -*-
"""video.py — 视频段：ffmpeg-rkmpp 子进程生命周期 + UDP 打包（设计稿 §1/§4）。

M2 骨架只做两件事，不做任何帧处理：
1. FFmpegVideoSource：命令行构造 + 子进程起停。真实采集/编码由 ffmpeg 子进程完成
   （kmsgrab → hwdownload,nv12 → h264_rkmpp 无 B 帧 → UDP 裸 H.264）。ffmpeg 不在
   PATH 时 is_available=False，video_start 返回明确错误。
2. packetize：NAL → UDP 包，与 PC 端 H264UdpSource 的拆包约定**对称**：

   包 = [seq u32 大端][ts_ms u32 大端][flags u8][payload ≤mtu]
   flags：0x01 关键帧(IDR)、0x02 分片首包、0x04 分片末包（单包 NAL 首末同置）
   seq 为 UDP 包序号，每包 +1（u32 回绕）；PC 端按「首包起 → 末包止」拼接 payload
   还原 NAL；seq 跳变 → 控制通道 resync{from_seq} → 板端强制 IDR（M3 实现）。
"""
from __future__ import annotations

import logging
import shutil
import struct
import subprocess
import threading

log = logging.getLogger(__name__)

# ---- 编码参数常量（设计稿 §5.3：kmsgrab → h264_rkmpp 无 B 帧 → UDP 裸流，集中在此便于真板微调）----
FFMPEG_BIN = "ffmpeg"
CAPTURE_FORMAT = "kmsgrab"              # KMS 屏幕捕获（需 cap_sys_admin，真板验证）
CAPTURE_VF = "hwdownload,format=nv12"   # GPU 帧下载回 CPU 内存 NV12
VIDEO_CODEC = "h264_rkmpp"              # Rockchip 硬编码器（nyanmisaka ffmpeg-rkmpp）
GOP_FACTOR = 2                          # GOP = fps * 2（设计稿 30fps→-g 120 的参数化写法）
OUTPUT_FORMAT = "h264"                  # Annex-B 裸流
DEFAULT_FPS = 30
DEFAULT_BITRATE = "4M"
DEFAULT_MTU = 1200                      # 单包 payload 上限，与 PC 端拆包方约定一致

FLAG_KEYFRAME = 0x01
FLAG_FRAG_START = 0x02
FLAG_FRAG_END = 0x04
_HEADER = struct.Struct(">IIB")         # [seq u32][ts_ms u32][flags u8]，共 9 字节


def find_ffmpeg():
    """探测 PATH 里的 ffmpeg；找不到返回 None（调用方据此判 is_available=False）。"""
    return shutil.which(FFMPEG_BIN)


class FFmpegVideoSource:
    """ffmpeg 子进程封装：只管命令行构造与起停，popen_factory/finder 可注入便于测试。"""

    def __init__(self, ffmpeg_path: str | None = None, finder=find_ffmpeg,
                 popen_factory=subprocess.Popen):
        self._ffmpeg_path = ffmpeg_path
        self._finder = finder
        self._popen = popen_factory
        self._proc = None
        self._lock = threading.Lock()
        self._last_args: list[str] | None = None

    @property
    def is_available(self) -> bool:
        return self.ffmpeg_path is not None

    @property
    def ffmpeg_path(self) -> str | None:
        if self._ffmpeg_path is None:
            self._ffmpeg_path = self._finder()
        return self._ffmpeg_path

    def build_args(self, resolution=None, fps: int = DEFAULT_FPS,
                   bitrate: str = DEFAULT_BITRATE, udp_target: str | None = None) -> list[str]:
        """生成 ffmpeg 命令行（即设计稿 §5.3 的录制/推流段）。

        resolution 可选 (w, h)：kmsgrab 恒抓全屏，缩小输出走 -s
        （rkmpp 对 scale 的支持度真板验证，骨架期先给出确定性参数）。
        """
        if not udp_target:
            raise ValueError("udp_target 不能为空")
        args = [self.ffmpeg_path, "-hide_banner", "-loglevel", "warning",
                "-f", CAPTURE_FORMAT, "-i", "-",
                "-vf", CAPTURE_VF]
        if resolution:
            args += ["-s", f"{int(resolution[0])}x{int(resolution[1])}"]
        args += ["-c:v", VIDEO_CODEC,
                 "-bf", "0",                                  # 无 B 帧（低延迟硬性要求）
                 "-g", str(int(fps) * GOP_FACTOR),            # GOP = fps*2
                 "-b:v", str(bitrate),
                 "-f", OUTPUT_FORMAT,
                 f"udp://{udp_target}"]
        return args

    def start(self, resolution=None, fps: int = DEFAULT_FPS,
              bitrate: str = DEFAULT_BITRATE, udp_target: str | None = None):
        """起 ffmpeg 子进程。返回 (ok, 说明)；不可用/已在运行时明确报错。"""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return False, "视频进程已在运行（先 video_stop）"
            if not self.is_available:
                return False, f"ffmpeg 未找到（PATH 中无 {FFMPEG_BIN!r}），视频功能不可用"
            args = self.build_args(resolution=resolution, fps=fps, bitrate=bitrate,
                                   udp_target=udp_target)
            log.info("启动 ffmpeg: %s", " ".join(args))
            # stdin 保持打开：kmsgrab 的 '-' 只是占位输入（数据走 DRM API），进程不读 stdin，
            # 但管道被关闭会触发 EOF 行为；stderr 丢弃（M2 骨架不做诊断采集）
            self._proc = self._popen(args, stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._last_args = args
            return True, f"ffmpeg pid={self._proc.pid}"

    def stop(self):
        """优雅终止：terminate(SIGTERM) → 3s 超时 kill。"""
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                log.warning("ffmpeg 未响应 SIGTERM，强制 kill")
                proc.kill()
                proc.wait(timeout=3)
        log.info("ffmpeg 已停止")

    def describe(self) -> dict:
        running = bool(self._proc is not None and self._proc.poll() is None)
        return {"running": running, "pid": self._proc.pid if running else None,
                "available": self.is_available, "args_tail": (self._last_args or [])[-6:]}


def packetize(nal_bytes: bytes, seq: int, ts_ms: int, mtu: int = DEFAULT_MTU,
              keyframe: bool = False):
    """NAL → UDP 包列表；返回 (packets, 下一 seq)。空 NAL 返回 ([], seq)。

    分片规则见模块 docstring；单包 NAL 首末标志同置，keyframe 标志置于该 NAL 全部分片。
    """
    if not nal_bytes:
        return [], seq
    chunks = [nal_bytes[i:i + mtu] for i in range(0, len(nal_bytes), mtu)]
    packets = []
    for i, chunk in enumerate(chunks):
        flags = 0
        if keyframe:
            flags |= FLAG_KEYFRAME
        if i == 0:
            flags |= FLAG_FRAG_START
        if i == len(chunks) - 1:
            flags |= FLAG_FRAG_END
        packets.append(_HEADER.pack(seq & 0xFFFFFFFF, ts_ms & 0xFFFFFFFF, flags) + chunk)
        seq = (seq + 1) & 0xFFFFFFFF
    return packets, seq
