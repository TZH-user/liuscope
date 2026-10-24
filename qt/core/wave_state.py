# -*- coding: utf-8 -*-
"""波形协议解析与缓冲 —— 移植自主线 core/protocol.ps1（纯逻辑，不触碰控件）。

协议语义与主线逐字对齐：
- FireWater：文本行 "ch0,ch1,..."（1..32 个数，\\n 结尾；$ 开头是命令行不解析；
  含 ANSI 转义的行先清洗再试一次）
- JustFloat：N×float32(小端) + 帧尾 00 00 80 7F，跨包分片自动同步失步丢弃
- RawData：每字节 0..255 直接作为单通道采样点

与主线的一个有意差异：主线把"是否解析"挂在波形窗可见性（wave_enabled）上，
Qt 版波形是主窗体常驻页签，这里由 UI 通过 enabled 属性控制（默认开）。
"""
from __future__ import annotations

import codecs
import re
import struct
import time

import numpy as np

from core.dsp import moving_average, zero_phase_lowpass

WAVE_NUMBER_RE = re.compile(r'^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$')

# ANSI 转义清洗（对齐主线 sanitize_terminal_text：NUL + OSC + CSI + ESC Fe）
_RE_OSC = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)')
_RE_CSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
_RE_ESC = re.compile(r'\x1b[@-_]')

PROTOCOLS = ('FireWater', 'JustFloat', 'RawData')
FILTERS = ('关', '滑动平均', '零相位低通')

_JF_TAIL = b'\x00\x00\x80\x7f'


def sanitize_terminal_text(text: str) -> str:
    if not text:
        return ''
    clean = text.replace('\x00', '')
    clean = _RE_OSC.sub('', clean)
    clean = _RE_CSI.sub('', clean)
    clean = _RE_ESC.sub('', clean)
    return clean


def try_parse_wave_frame(line: str | None):
    """解析一行 FireWater 数据为数值列表；失败返回 None（对齐主线同名函数）。"""
    if line is None or not line.strip():
        return None
    trimmed = line.strip()
    if not trimmed or len(trimmed) > 4096:
        return None
    if trimmed.startswith('$'):
        return None
    fields = trimmed.split(',')
    if len(fields) > 32:
        return None
    values = []
    for field in fields:
        field = field.strip()
        if not field or not WAVE_NUMBER_RE.match(field):
            return None
        try:
            values.append(float(field))
        except ValueError:
            return None
    return values


class WaveState:
    """波形缓冲状态机。纯逻辑无控件，可单测；UI 每帧调 consume_bytes / view_data / y_range。

    暂停语义与主线一致：paused 冻结曲线数据（append 直接返回），
    但 FireWater 行解析与 JustFloat 帧排空照常进行（RX 显示不受影响）。
    """

    MAX_FRAMES = 200_000
    TRIM_CHUNK = 10_000        # append_frame 路径的裁剪步长（与主线一致）
    JF_DROP_BYTES = 65_536     # JustFloat 找不到帧尾的失步丢弃阈值
    LINE_DROP_CHARS = 8_192    # FireWater 行缓冲超长丢弃阈值
    MIN_WINDOW = 50
    MAX_WINDOW = 200_000
    OVERFLOW_NOTE_INTERVAL = 5.0  # 积压提示节流（秒）

    def __init__(self, now_fn=time.monotonic):
        self.now_fn = now_fn
        self.protocol = 'FireWater'
        self.enabled = False
        self.paused = False

        self.ch_values: list[list[float]] = []   # 每通道一列历史采样
        self.channel_last: list[float] = []
        self.enabled_channels: list[bool] = []
        self.channel_names: list[str] = []

        self.jf_buffer = bytearray()
        self.line_buffer = ''
        self._decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')

        self.frame_count = 0       # 当前缓冲帧数（含 RawData 采样点）
        self.frame_total = 0
        self.fail_total = 0
        self.fps_count = 0         # 自上次统计以来的新增帧数，UI 每秒差分出 FPS

        self.window_frames = 1000
        self.view_start = -1       # -1 = 实时跟随；>=0 = 历史浏览起点
        self.auto_y = True
        self.y_min = -1.0
        self.y_max = 1.0
        self.filter_mode = '关'
        self.filter_param = 16
        self._filter_cache: dict[int, tuple[str, np.ndarray]] = {}

        self.on_note = None           # callable(str)：积压/失步提示（UI 接到日志）
        self.on_names_changed = None  # callable()：通道名改动（UI 接到设置持久化）
        self._note_last = 0.0

    # ---------- 字节入口 ----------

    def consume_bytes(self, data: bytes):
        if not self.enabled or not data:
            return
        if self.protocol == 'JustFloat':
            self.consume_justfloat_bytes(data)
        elif self.protocol == 'RawData':
            self.consume_rawdata_bytes(data)
        else:
            self.consume_firewater_bytes(data)

    def consume_firewater_bytes(self, data: bytes):
        text = self._decoder.decode(data)
        if not text:
            return
        self.line_buffer += text
        normalized = self.line_buffer.replace('\r\n', '\n').replace('\r', '\n')
        parts = normalized.split('\n')
        if len(parts) <= 1:
            if len(self.line_buffer) >= self.LINE_DROP_CHARS:
                self.line_buffer = ''
                self.fail_total += 1
                self._note_overflow('FireWater 行缓冲超长已丢弃')
            return
        for line in parts[:-1]:
            self.ingest_firewater_line(line)
        self.line_buffer = parts[-1]
        if len(self.line_buffer) >= self.LINE_DROP_CHARS:
            self.line_buffer = ''
            self._note_overflow('FireWater 行缓冲超长已丢弃')

    def ingest_firewater_line(self, line: str):
        frame = try_parse_wave_frame(line)
        if frame is None and '\x1b' in line:
            frame = try_parse_wave_frame(sanitize_terminal_text(line))
        if frame is None:
            if ',' in line:
                self.fail_total += 1
            return
        self.append_frame(frame)

    def consume_justfloat_bytes(self, data: bytes):
        buf = self.jf_buffer
        buf.extend(data)
        while len(buf) >= 4:
            # bytearray.find 是 C 速度：等价于主线逐字节找第一个 00 00 80 7F
            tail_index = buf.find(_JF_TAIL)
            if tail_index < 0:
                if len(buf) > self.JF_DROP_BYTES:
                    buf.clear()
                    self.fail_total += 1
                    self._note_overflow('JustFloat 找不到帧尾，缓冲失步已丢弃重来')
                return
            if tail_index > 0 and tail_index % 4 == 0:
                n = tail_index // 4
                values = struct.unpack('<%df' % n, bytes(buf[:tail_index]))
                self.append_frame(values)
            else:
                # 帧尾位置不在 4 的倍数上：帧不同步或脏数据
                self.fail_total += 1
            del buf[:tail_index + 4]

    def consume_rawdata_bytes(self, data: bytes):
        if not data or self.paused:
            return
        if not self.ch_values:
            self.ch_values.append([])
            self.channel_last.append(0.0)
            self.enabled_channels.append(True)
        self.ch_values[0].extend(float(b) for b in data)
        self.channel_last[0] = float(data[-1])
        self.frame_count += len(data)
        self.frame_total += len(data)
        self.fps_count += len(data)
        if self.frame_count > self.MAX_FRAMES:
            remove = self.frame_count - self.MAX_FRAMES
            for lst in self.ch_values:
                del lst[:remove]
            self.frame_count -= remove
            if self.view_start >= 0:
                self.view_start -= remove
                if self.view_start < 0:
                    self.view_start = -1

    # ---------- 帧缓冲 ----------

    def append_frame(self, values):
        if self.paused or not values:
            return
        count = len(values)
        while len(self.ch_values) < count:
            # 新通道用 0 补齐历史帧，保持各通道缓冲对齐
            self.ch_values.append([0.0] * self.frame_count)
            self.channel_last.append(0.0)
            self.enabled_channels.append(True)
        if count < len(self.ch_values):
            # 短帧用各通道最近值补齐，保证每条曲线连续
            values = list(values) + self.channel_last[count:]
        for i, v in enumerate(values):
            self.ch_values[i].append(v)
            self.channel_last[i] = v
        self.frame_count += 1
        self.frame_total += 1
        self.fps_count += 1
        if self.frame_count > self.MAX_FRAMES:
            for lst in self.ch_values:
                del lst[:self.TRIM_CHUNK]
            self.frame_count -= self.TRIM_CHUNK
            if self.view_start >= 0:
                self.view_start -= self.TRIM_CHUNK
                if self.view_start < 0:
                    self.view_start = -1

    def reset_decoder(self):
        self._decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.line_buffer = ''

    def reset_buffers(self):
        # 对齐主线 reset_wave_buffers：只清数据缓冲，行缓冲/解码器不动
        self.ch_values = []
        self.channel_last = []
        self.enabled_channels = []
        self.jf_buffer.clear()
        self.frame_count = 0
        self.frame_total = 0
        self.fail_total = 0
        self.fps_count = 0
        self.view_start = -1
        self._filter_cache = {}

    def switch_protocol(self, protocol: str):
        """切协议 = 清数据缓冲 + 重置解码器（对齐主线 combo 切换行为）。"""
        if protocol and protocol != self.protocol:
            self.protocol = protocol
            self.reset_buffers()
            self.reset_decoder()

    # ---------- 提示 ----------

    def _note_overflow(self, detail: str):
        now = self.now_fn()
        if now - self._note_last < self.OVERFLOW_NOTE_INTERVAL:
            return
        self._note_last = now
        if self.on_note:
            self.on_note(
                f'[积压] {detail}。收到的内容可能不是当前协议格式，或波特率不匹配。')

    # ---------- 视图（跟随/历史/平移/宽度） ----------

    def view_range(self):
        """返回 (start, end, follow)。历史模式拖回最右端自动恢复实时跟随。"""
        width = max(self.MIN_WINDOW, int(self.window_frames))
        total = self.frame_count
        if total <= 0:
            return 0, 0, True
        if width >= total:
            self.view_start = -1
            return 0, total, True
        if self.view_start < 0:
            return total - width, total, True
        start = self.view_start
        if start > total - width:
            self.view_start = -1
            return total - width, total, True
        return start, start + width, False

    def pan_view(self, delta_frames: int):
        """平移可视窗口。delta > 0 = 看更新数据，< 0 = 看更旧数据。"""
        total = self.frame_count
        if delta_frames == 0 or total <= 1:
            return
        width = max(self.MIN_WINDOW, int(self.window_frames))
        if width >= total:
            self.view_start = -1
            return
        start, _end, _follow = self.view_range()
        new_start = int(round(start)) + delta_frames
        if new_start < 0:
            new_start = 0
        if new_start >= total - width:
            self.view_start = -1
            return
        self.view_start = new_start

    def set_view_window(self, new_start: int, new_end: int, anchor: str = 'mid'):
        """绝对定位窗口（鼠标指哪窗口到哪）。宽度下限 50；右端贴住末尾恢复跟随。"""
        total = self.frame_count
        if total <= 1:
            return
        if new_end > total:
            new_end = total
        if new_start < 0:
            new_start = 0
        if (new_end - new_start) < self.MIN_WINDOW:
            if anchor == 'start':
                new_start = new_end - self.MIN_WINDOW
            elif anchor == 'end':
                new_end = new_start + self.MIN_WINDOW
            else:
                center = (new_start + new_end) / 2.0
                new_start = int(round(center - self.MIN_WINDOW / 2.0))
                new_end = new_start + self.MIN_WINDOW
            if new_start < 0:
                new_start = 0
                new_end = self.MIN_WINDOW
            if new_end > total:
                new_end = total
                new_start = total - self.MIN_WINDOW
                if new_start < 0:
                    new_start = 0
        self.window_frames = new_end - new_start
        if new_end >= total:
            self.view_start = -1
            return
        self.view_start = new_start

    def set_view_width(self, new_width: int):
        """改窗口宽度；历史浏览时保持窗口中心不变。"""
        total = self.frame_count
        new_width = max(self.MIN_WINDOW, min(int(new_width), self.MAX_WINDOW))
        if new_width == int(self.window_frames):
            return
        old_width = int(self.window_frames)
        self.window_frames = new_width
        if self.view_start >= 0 and total > 1 and old_width > 0:
            center = self.view_start + old_width / 2.0
            new_start = int(round(center - new_width / 2.0))
            if new_start < 0:
                new_start = 0
            if new_width >= total:
                self.view_start = -1
            elif new_start > total - new_width:
                self.view_start = total - new_width
            else:
                self.view_start = new_start

    # ---------- 数据视图 / 滤波 ----------

    def _filter_key(self) -> str:
        return f'{self.frame_count}|{self.filter_mode}|{self.filter_param}'

    def view_data(self, channel: int, start: int, end: int):
        """取通道数据。滤波为非破坏视图：原始缓冲不动，结果按版本缓存。

        返回 (data, offset, length)——data 是整段缓冲的滤波结果数组，UI 取
        data[offset:offset+length] 绘制。
        """
        if channel < 0 or channel >= len(self.ch_values):
            return np.zeros(0), 0, 0
        key = self._filter_key()
        cache = self._filter_cache.get(channel)
        if cache is None or cache[0] != key:
            raw = np.asarray(self.ch_values[channel], dtype=np.float64)
            if self.filter_mode == '滑动平均':
                filtered = moving_average(raw, max(2, int(self.filter_param)))
            elif self.filter_mode == '零相位低通':
                filtered = zero_phase_lowpass(raw, 1.0 / max(2, int(self.filter_param)))
            else:
                filtered = raw
            cache = (key, filtered)
            self._filter_cache[channel] = cache
        data = cache[1]
        safe_start = max(0, min(int(start), data.size))
        safe_end = max(safe_start, min(int(end), data.size))
        return data, safe_start, safe_end - safe_start

    def snapshot(self, channel: int = 0, count: int = 4096):
        """插件数据合同：永不抛错、永不返回 None，只回数值列表（空 = 无数据）。"""
        try:
            if channel < 0 or channel >= len(self.ch_values):
                return []
            lst = self.ch_values[channel]
            take = min(max(1, int(count)), len(lst))
            if take <= 0:
                return []
            return list(lst[len(lst) - take:])
        except Exception:
            return []

    def y_range(self, enabled=None):
        """Y 轴量程：自动模式扫可视窗口内启用通道的 min/max（跳过 NaN/Inf）+ 6% 余量。"""
        if enabled is None:
            enabled = self.enabled_channels
        any_enabled = any(enabled)
        if self.auto_y:
            vmin, vmax = float('inf'), float('-inf')
            total = self.frame_count
            if total > 0 and any_enabled:
                probe = min(total, int(self.window_frames))
                probe_start = total - probe
                for c, on in enumerate(enabled):
                    if not on or c >= len(self.ch_values):
                        continue
                    seg = np.asarray(self.ch_values[c][probe_start:], dtype=np.float64)
                    seg = seg[np.isfinite(seg)]
                    if seg.size:
                        vmin = min(vmin, float(seg.min()))
                        vmax = max(vmax, float(seg.max()))
            if vmin > vmax:
                vmin, vmax = -1.0, 1.0
            elif vmin == vmax:
                if vmin == 0.0:
                    vmin, vmax = -1.0, 1.0
                else:
                    delta = abs(vmin) * 0.1 + 0.000001
                    vmin -= delta
                    vmax += delta
            else:
                pad = (vmax - vmin) * 0.06
                vmin -= pad
                vmax += pad
            return vmin, vmax
        y_min, y_max = self.y_min, self.y_max
        if y_max <= y_min:
            y_max = y_min + 1.0
        return y_min, y_max

    # ---------- 通道名 ----------

    def channel_display_name(self, index: int) -> str:
        if 0 <= index < len(self.channel_names) and self.channel_names[index].strip():
            return self.channel_names[index]
        return f'CH{index}'

    def set_channel_name(self, index: int, name: str) -> str | None:
        """改通道名（去掉逗号/换行）；返回清洗后的名字，无效改动返回 None。"""
        if index < 0 or index >= len(self.ch_values):
            return None
        clean = re.sub(r'[,，\r\n]', '', (name or '').strip())
        if not clean:
            return None
        while len(self.channel_names) <= index:
            self.channel_names.append('')
        self.channel_names[index] = clean
        if self.on_names_changed:
            self.on_names_changed()
        return clean

    def names_joined(self) -> str:
        return ','.join(self.channel_names)
