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
import os
import re
import socket
import shutil
import struct
import subprocess
import threading
import time

log = logging.getLogger(__name__)

# ---- 编码参数常量（设计稿 §5.3：kmsgrab → h264_rkmpp 无 B 帧 → UDP 裸流，集中在此便于真板微调）----
FFMPEG_BIN = "ffmpeg"
CAPTURE_FORMAT = "kmsgrab"              # KMS 屏幕捕获（需 cap_sys_admin，真板验证）
CAPTURE_VF = "hwdownload,format=nv12"   # GPU 帧下载回 CPU 内存 NV12
# 编码器候选链：按序探测 `ffmpeg -encoders`，取第一个可用项。
# h264_rkmpp 是设计稿目标（Rockchip 硬编，nyanmisaka ffmpeg-rkmpp 分支）；Debian 官方
# ffmpeg 不带 rkmpp → 落 h264_v4l2m2m（内核 V4L2 M2M 硬编，RK356x 有 VPU）；再不行
# （含 Windows 联通性自测环境）→ libx264 软编兜底。
VIDEO_CODEC_CANDIDATES = ("h264_rkmpp", "h264_v4l2m2m", "libx264")
VIDEO_CODEC = VIDEO_CODEC_CANDIDATES[0]  # 兼容旧引用（describe/测试快照）；实际以探测为准
# 各编码器的附加低延迟参数（无 B 帧由通用 -bf 0 保证；软编另加零延迟调优）
_CODEC_EXTRA_ARGS = {
    "libx264": ("-preset", "ultrafast", "-tune", "zerolatency"),
}
SOFT_CODEC = "libx264"
# 软编实时性钳制（真板 RK3562 实测 x264 ultrafast 720p30 ≈ 0.52x：不钳 fps 会无限
# 积压延迟，镜像越来越落后）。12fps ≈ 1.3x 余量；硬编路径不受影响。
SOFT_CODEC_MAX_FPS = 12
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


_PROBE_ARGS = ("-f", "lavfi", "-i", "nullsrc=s=256x256:d=0.2", "-frames:v", "1")

# ---- 采集管线候选（设计稿 §5.3 kmsgrab 为目标；真板驱动不可用则落 x11grab）----
# RK3562 真板验证（Debian 12 / rockchip 4.0 驱动）：kmsgrab 抓帧本身可用，但
# hwdownload 滤镜链首帧即崩（Failed to inject frame into filter network）→ 落
# x11grab（X11 会话 + lightdm 授权文件；桌面镜像场景下等效）。
# 探测 = 1 帧真采集+滤镜链全流程跑一遍，清单纯列出 ≠ 运行时可用。
KMSGRAB_PIPELINE = {"name": "kmsgrab",
                    "input_args": ["-f", "kmsgrab", "-i", "-"],
                    "vf": CAPTURE_VF, "extra_env": {}}
X11_AUTH_CANDIDATES = ("/var/run/lightdm/root/:0", "/root/.Xauthority")


def _x11_pipelines(env: dict, x11_socket_dir: str = "/tmp/.X11-unix") -> list[dict]:
    """按 /tmp/.X11-unix 插槽与授权文件候选展开 x11grab 管线列表。"""
    try:
        sockets = sorted(s for s in os.listdir(x11_socket_dir) if s.startswith("X"))
    except OSError:
        sockets = []
    auths = [env["XAUTHORITY"]] if env.get("XAUTHORITY") else []
    auths += [p for p in X11_AUTH_CANDIDATES if os.path.exists(p)]
    auths = list(dict.fromkeys(auths)) or [None]
    out = []
    for sock in sockets:
        display = ":" + sock[1:]
        for auth in auths:
            extra = {"DISPLAY": display}
            if auth:
                extra["XAUTHORITY"] = auth
            out.append({"name": f"x11grab{display}", "vf": "format=nv12",
                        "input_args": ["-f", "x11grab", "-framerate", str(DEFAULT_FPS),
                                       "-i", display],
                        "extra_env": extra})
    return out


def _probe_pipeline(ffmpeg_path: str, pipe: dict, run, env: dict) -> bool:
    """1 帧真采集验证（含滤镜链与进程环境）。"""
    args = [ffmpeg_path, "-hide_banner", "-loglevel", "error",
            *pipe["input_args"], "-frames:v", "1"]
    if pipe["vf"]:
        args += ["-vf", pipe["vf"]]
    args += ["-f", "null", "-"]
    try:
        proc = run(args, capture_output=True, text=True, timeout=15,
                   env={**env, **pipe["extra_env"]})
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def pick_pipeline(ffmpeg_path: str, run=subprocess.run, env: dict | None = None,
                  x11_socket_dir: str = "/tmp/.X11-unix") -> dict:
    """探测可用采集管线：kmsgrab 优先，失败按 X11 插槽/授权展开 x11grab。"""
    env = dict(os.environ if env is None else env)
    for pipe in [KMSGRAB_PIPELINE, *_x11_pipelines(env, x11_socket_dir)]:
        if _probe_pipeline(ffmpeg_path, pipe, run, env):
            return pipe
    raise RuntimeError("ffmpeg 无可用采集管线（kmsgrab 滤镜链与 x11grab 会话均探测失败；"
                       "确认桌面会话或 DRM 权限）")


def pick_codec(ffmpeg_path: str, run=subprocess.run) -> str:
    """探测 ffmpeg 实际可用的 H.264 编码器：-encoders 短名单 → 逐个 1 帧真编码验证。

    只看 -encoders 清单不够：h264_v4l2m2m 在 RK3562（Debian 内核无 m2m 编码
    节点）上会列出但起会话即死。候选序遍历短名单，第一个**真编码成功**的入选；
    全军覆没抛 RuntimeError（说明串到 video_start 的报错里）。run 可注入便于测试。
    """
    try:
        proc = run([ffmpeg_path, "-hide_banner", "-encoders"],
                   capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"探测 ffmpeg 编码器失败: {exc}") from exc
    listing = proc.stdout or ""
    failures = []
    for codec in VIDEO_CODEC_CANDIDATES:
        if not re.search(rf"^\s*V[A-Za-z0-9.]*\s+{re.escape(codec)}\s", listing, re.M):
            continue
        try:
            probe = run([ffmpeg_path, "-hide_banner", "-loglevel", "error",
                         *_PROBE_ARGS, "-c:v", codec, "-f", "null", "-"],
                        capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            failures.append(codec)
            continue
        if probe.returncode == 0:
            return codec
        failures.append(codec)
    raise RuntimeError("ffmpeg 无可用 H.264 编码器（候选 "
                       + ", ".join(VIDEO_CODEC_CANDIDATES)
                       + ("；探测失败: " + ", ".join(failures) if failures else "")
                       + "）")


class FFmpegVideoSource:
    """ffmpeg 子进程封装：只管命令行构造与起停，popen_factory/finder 可注入便于测试。"""

    def __init__(self, ffmpeg_path: str | None = None, finder=find_ffmpeg,
                 popen_factory=subprocess.Popen, codec_picker=pick_codec,
                 pipeline_picker=pick_pipeline):
        self._ffmpeg_path = ffmpeg_path
        self._finder = finder
        self._popen = popen_factory
        self._codec_picker = codec_picker    # 可注入便于测试（免真跑 ffmpeg）
        self._pipeline_picker = pipeline_picker
        self._proc = None
        self._udp_sock = None                # 打包泵的发送 socket（stop 时关闭）
        self._pump_thread: threading.Thread | None = None
        self._codec: str | None = None       # start() 时探测一次并缓存
        self._pipeline: dict | None = None   # 采集管线（kmsgrab/x11grab），同上
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
                   bitrate: str = DEFAULT_BITRATE, udp_target: str | None = None,
                   codec: str | None = None, pipeline: dict | None = None) -> list[str]:
        """生成 ffmpeg 命令行（即设计稿 §5.3 的录制/推流段）。

        resolution 可选 (w, h)：kmsgrab 恒抓全屏，缩小输出走 -s
        （rkmpp 对 scale 的支持度真板验证，骨架期先给出确定性参数）。
        codec 缺省用 start() 探测缓存项（self._codec），再退回候选链首项。
        """
        if not udp_target:
            raise ValueError("udp_target 不能为空")
        fps = int(fps or DEFAULT_FPS)   # fps 可省（控制链路不传）；None 不能漏进算术
        bitrate = str(bitrate or DEFAULT_BITRATE)   # 同上（None 曾落成 "-b:v None"）
        codec = codec or self._codec or VIDEO_CODEC
        pipe = pipeline or self._pipeline or KMSGRAB_PIPELINE   # 未探测时回设计稿默认
        args = [self.ffmpeg_path, "-hide_banner", "-loglevel", "warning",
                *pipe["input_args"]]
        if pipe["vf"]:
            args += ["-vf", pipe["vf"]]
        if resolution:
            args += ["-s", f"{int(resolution[0])}x{int(resolution[1])}"]
        args += ["-c:v", codec,
                 "-bf", "0",                                  # 无 B 帧（低延迟硬性要求）
                 "-g", str(int(fps) * GOP_FACTOR),            # GOP = fps*2
                 "-b:v", bitrate,
                 "-r", str(int(fps))]                         # 输出帧率真钳制（软编防积压）
        args += list(_CODEC_EXTRA_ARGS.get(codec, ()))        # 软编兜底的零延迟参数
        # 输出走 stdout（Annex-B），由 FFmpegVideoSource 的泵线程 packetize 后再发
        # UDP——ffmpeg 直发 udp:// 是裸流，没有 §4 包头，PC 端 H264UdpSource 没法拆
        args += ["-f", OUTPUT_FORMAT, "-"]
        return args

    def start(self, resolution=None, fps: int = DEFAULT_FPS,
              bitrate: str = DEFAULT_BITRATE, udp_target: str | None = None):
        """起 ffmpeg 子进程。返回 (ok, 说明)；不可用/已在运行时明确报错。"""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return False, "视频进程已在运行（先 video_stop）"
            if not self.is_available:
                return False, f"ffmpeg 未找到（PATH 中无 {FFMPEG_BIN!r}），视频功能不可用"
            if self._codec is None:
                try:
                    self._codec = self._codec_picker(self.ffmpeg_path)
                except RuntimeError as exc:
                    return False, str(exc)
            if self._pipeline is None:
                try:
                    self._pipeline = self._pipeline_picker(self.ffmpeg_path)
                except RuntimeError as exc:
                    return False, str(exc)
            if self._codec == SOFT_CODEC:
                # 软编兜底的实时性钳制：fps 超软编算力往回收——超出只会积压延迟；
                # 分辨率跟随采集源原生气（镜像桌面无需放大到 720p）
                fps = min(int(fps or DEFAULT_FPS), SOFT_CODEC_MAX_FPS)
            args = self.build_args(resolution=resolution, fps=fps, bitrate=bitrate,
                                   udp_target=udp_target)
            log.info("启动 ffmpeg: %s", " ".join(args))
            # stdin 保持打开：kmsgrab 的 '-' 只是占位输入（数据走 DRM API），进程不读 stdin，
            # 但管道被关闭会触发 EOF 行为；stderr 丢弃（M2 骨架不做诊断采集）
            env = {**os.environ, **self._pipeline["extra_env"]}
            self._proc = self._popen(args, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     env=env)
            self._last_args = args
            # 泵线程：Annex-B → §4 包头 → UDP（目标在 start 前已定，无需再变）
            host, _, port_s = udp_target.rpartition(":")
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock = sock
            self._pump_thread = threading.Thread(
                target=self._pump, args=(sock, (host, int(port_s))),
                name="server-rk-video-pump", daemon=True)
            self._pump_thread.start()
            time.sleep(0.8)     # 立死检测：编码器/采集源不可用会秒退
            if self._proc.poll() is not None:
                rc = self._proc.returncode
                self._codec = None      # 选错编码器/采集器可能致死：下次重新探测
                self._pipeline = None
                self._proc = None
                return False, f"ffmpeg 启动即退出（rc={rc}）：编码器/采集源不可用"
            return True, (f"ffmpeg pid={self._proc.pid} codec={self._codec} "
                          f"capture={self._pipeline['name']}")

    def _pump(self, sock, target: tuple[str, int]):
        """ffmpeg stdout（Annex-B）→ NAL 切分 → packetize（§4 包头）→ UDP。

        ffmpeg 直发 udp:// 是裸流没有包头（M2 骨架的空洞），打包必须在
        本进程做才与 PC 端 H264UdpSource 对称。stop() 关 socket 触发 OSError 收尾。
        """
        proc = self._proc
        buf = bytearray()
        seq = 0
        try:
            while proc is not None and proc.poll() is None:
                out = getattr(proc, "stdout", None)
                chunk = out.read(4096) if out is not None else b""
                if not chunk:
                    break
                buf.extend(chunk)
                if b'\x00\x00\x01' not in buf:
                    continue
                marks = [m.start() for m in re.finditer(b'\x00\x00\x01', bytes(buf))]
                if len(marks) < 2:
                    continue          # 末个起始码的 NAL 未完，留在缓冲
                blob = bytes(buf[:marks[-1]])
                del buf[:marks[-1]]
                ts = int(time.time() * 1000) & 0xFFFFFFFF
                for nal, is_idr in split_nal_frames(blob):
                    pkts, seq = packetize(_NAL_SC + nal, seq, ts, keyframe=is_idr)
                    for p in pkts:
                        sock.sendto(p, target)
        except OSError:
            pass                      # stop() 关闭 socket：正常收尾路径
        finally:
            # EOF/退出：冲刷残缓冲里的末个 NAL（防尾帧丢失）
            try:
                if b'\x00\x00\x01' in buf:
                    for nal, is_idr in split_nal_frames(bytes(buf)):
                        pkts, seq = packetize(_NAL_SC + nal, seq,
                                              int(time.time() * 1000), keyframe=is_idr)
                        for p in pkts:
                            sock.sendto(p, target)
            except OSError:
                pass
            out = getattr(proc, "stdout", None)
            if out is not None:
                try:
                    out.close()
                except OSError:
                    pass

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
        sock, self._udp_sock = self._udp_sock, None
        if sock is not None:
            sock.close()     # 泵线程 sendto OSError → 收尾
        log.info("ffmpeg 已停止")

    def describe(self) -> dict:
        running = bool(self._proc is not None and self._proc.poll() is None)
        return {"running": running, "pid": self._proc.pid if running else None,
                "available": self.is_available, "codec": self._codec,
                "capture": (self._pipeline or {}).get("name"),
                "args_tail": (self._last_args or [])[-6:]}


_NAL_SC = b"\x00\x00\x00\x01"   # 对外统一 4 字节起始码（与 PC 端 split_nals 对称）


def split_nal_frames(blob: bytes) -> list[tuple[bytes, bool]]:
    """Annex-B → [(NAL 载荷（不含起始码）, 是否 IDR)]；3/4 字节起始码兼容。

    与 PC 端 split_nals 同一套边界规则：起始码前导 0 归本起始码、尾部 0 归下一个。
    IDR 判定 = NAL 头字节低 5 位 == 5（nal_unit_type）。
    """
    marks = [m.start() for m in re.finditer(b'\x00\x00\x01', blob)]
    out = []
    for k, m in enumerate(marks):
        start = m
        while start > 0 and blob[start - 1] == 0:
            start -= 1
        end = marks[k + 1] if k + 1 < len(marks) else len(blob)
        while end > m + 3 and blob[end - 1] == 0:
            end -= 1
        payload = blob[m + 3:end]
        if payload:
            out.append((payload, (payload[0] & 0x1F) == 5))
    return out


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
    return packets, seq
