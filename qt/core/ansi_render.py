# -*- coding: utf-8 -*-
"""ANSI 终端转义渲染：一行串口文本 → 结构化显示段（纯函数，离屏可单测）。

串口回传常带终端控制序列——鲁班猫等 Linux 串口控制台的颜色、框线排版艺术字，
ESP-IDF 日志的等级色码。本模块做三件事：

- SGR（``\\x1b[…m``）解析为显示段属性：前/背景色（16 色 / 256 色 / 真彩）、
  粗体、暗淡、斜体、下划线、删除线、反显；
- 其余转义序列（光标移动、清屏、OSC 标题、字符集切换）对滚动日志无意义，
  一律剥掉——滚动日志不是终端，不做光标仿真；
- 输出的是段（Segment）而非 HTML：主窗用 QTextCharFormat 直插文档，文本
  内容零损耗（查找/悬停互转/导出都按原文工作），空格天然保排版（框线图/
  艺术字不塌陷），绕开 appendHtml 的空白折叠。

颜色随主题走：16 色按日志底色明暗取深/浅两套端末调色板；256 色/真彩按设备
指定值原样呈现，但对日志底色对比度过低的颜色朝日志文字色混合，保证可读
（对齐本项目 WCAG 对比度文化，见 theme.py 的对比度警示）。
"""
from __future__ import annotations

import re
from typing import NamedTuple

# CSI 序列：参数字节 + 中间字节 + 终止字节（m = SGR，其余为光标/擦除等）
_CSI_RE = re.compile(r'\x1b\[([0-9;:<=>?]*)([ -/]*)([@-~])')
# OSC 序列：窗口标题等，BEL 或 ST(ESC \) 结尾，容忍缺失
_OSC_RE = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?')
# 其余 Esc 序列：单终止字节（ESC 7 / ESC = / ESC c …）或 中间字节(0x20-0x2F)* + 终止
# 字节（字符集切换 \x1b(B 等三字节形态）
_ESC_RE = re.compile(r'\x1b(?:[0-~]|[ -/]*[0-~])')

# 终端 16 色调色板（对齐 VS Code 终端深/浅两套，久经可读性打磨）
_DARK_PALETTE = (
    '#000000', '#cd3131', '#0dbc79', '#e5e510', '#2472c8', '#bc3fbc',
    '#11a8cd', '#e5e5e5', '#767676', '#f14c4c', '#23d18b', '#f5f543',
    '#3b8eea', '#d670d6', '#29b8db', '#e5e5e5',
)
_LIGHT_PALETTE = (
    '#000000', '#cd3131', '#00bc00', '#949800', '#0451a5', '#bc05bc',
    '#0598bc', '#555555', '#666666', '#cd3131', '#14ce14', '#b5ba00',
    '#0451a5', '#bc05bc', '#0598bc', '#a5a5a5',
)

# 设备指定色对日志底色的最低对比度（WCAG），不足则朝日志文字色混合保底
_CONTRAST_FLOOR = 2.0


class Segment(NamedTuple):
    """一行内的连续显示段：属性相同的文本归为一段。"""
    text: str
    color: str            # 前景 '#rrggbb'（已过对比度保底）
    bg: str | None        # 背景；None = 日志底色（反显/显式 SGR 背景才有值）
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False


def has_ansi(text: str) -> bool:
    """行内是否出现 ESC 控制序列（含 SGR 与其他 CSI/OSC）。"""
    return '\x1b' in text


def strip_ansi(text: str) -> str:
    """剥掉全部转义序列，只留可见文本（解析/导出场景用）。"""
    text = _OSC_RE.sub('', text)
    text = _CSI_RE.sub('', text)
    return _ESC_RE.sub('', text)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip('#')
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return '#%02x%02x%02x' % rgb


def _luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 相对亮度。"""
    channel = []
    for c in rgb:
        c /= 255.0
        channel.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channel[0] + 0.7152 * channel[1] + 0.0722 * channel[2]


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _luminance(a), _luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float
         ) -> tuple[int, int, int]:
    """线性混合：t=0 取 a，t=1 取 b。"""
    return (round(a[0] + (b[0] - a[0]) * t),
            round(a[1] + (b[1] - a[1]) * t),
            round(a[2] + (b[2] - a[2]) * t))


def _ensure_visible(rgb: tuple[int, int, int], bg: tuple[int, int, int],
                    text: tuple[int, int, int]) -> tuple[int, int, int]:
    """对比度不足 _CONTRAST_FLOOR 时朝文字色混合，直至达标（保底可读）。"""
    if contrast_ratio(rgb, bg) >= _CONTRAST_FLOOR:
        return rgb
    for t in (0.25, 0.5, 0.75, 1.0):
        blended = _mix(rgb, text, t)
        if contrast_ratio(blended, bg) >= _CONTRAST_FLOOR:
            return blended
    return text


def _base_palette(bg_hex: str) -> tuple[str, ...]:
    """按日志底色明暗选 16 色调色板（自定义主题同样适用）。"""
    dark_bg = _luminance(_hex_to_rgb(bg_hex)) < 0.5
    return _DARK_PALETTE if dark_bg else _LIGHT_PALETTE


def _color_from_256(n: int) -> tuple[int, int, int]:
    """xterm 256 色编号 → RGB（16-231 色阶 / 232-255 灰阶）。"""
    if n < 232:   # 6×6×6 色阶
        n -= 16
        steps = (0, 95, 135, 175, 215, 255)
        return steps[n // 36], steps[(n // 6) % 6], steps[n % 6]
    v = 8 + (n - 232) * 10   # 24 级灰阶
    return v, v, v


def _resolve_device_color(val: int, palette: tuple[str, ...], fg_default: str,
                          bg_default: str, text_color: str) -> str:
    """设备指定色 → 可读 '#rrggbb'。

    val < 16 走主题调色板（各端 0-15 实现不一）；16-255 是 256 色编号；
    >255 是打包真彩 (r<<16)|(g<<8)|b。后两者对底色对比不足时朝文字色混合。
    """
    bg = _hex_to_rgb(bg_default)
    text = _hex_to_rgb(text_color)
    if val < 16:
        rgb = _hex_to_rgb(palette[val])
    elif val <= 255:
        rgb = _color_from_256(val)
    else:
        rgb = (val >> 16) & 0xFF, (val >> 8) & 0xFF, val & 0xFF
    return _rgb_to_hex(_ensure_visible(rgb, bg, text))


class _Attrs:
    """当前 SGR 属性状态（跨段延续，直到复位）。"""

    __slots__ = ('fg', 'bg', 'bold', 'dim', 'italic', 'underline',
                 'strike', 'reverse')

    def __init__(self):
        self.reset()

    def reset(self):
        self.fg = None       # None = 默认前景；否则 '#rrggbb'
        self.bg = None
        self.bold = self.dim = self.italic = False
        self.underline = self.strike = self.reverse = False


def _parse_sgr(params: list[str], attrs: _Attrs, palette: tuple[str, ...],
               fg_default: str, bg_default: str, text_color: str):
    """SGR 参数流 → 更新 attrs。兼容 38;5;n / 38;2;r;g;b 与 38:5:n 冒号形态。"""
    nums: list[int] = []
    for p in params:
        if ':' in p:            # 38:5:196 / 38:2:r:g:b —— 冒号子参数
            head, *sub = p.split(':')
            if head in ('38', '48'):
                _apply_extended_colon(head, sub, attrs, palette, fg_default,
                                      bg_default, text_color)
            elif head.isdigit():
                nums.append(int(head))
            continue
        nums.append(int(p) if p.isdigit() else 0)

    i = 0
    while i < len(nums):
        n = nums[i]
        if n == 0:
            attrs.reset()
        elif n == 1:
            attrs.bold = True
        elif n == 2:
            attrs.dim = True
        elif n == 3:
            attrs.italic = True
        elif n == 4:
            attrs.underline = True
        elif n == 7:
            attrs.reverse = True
        elif n == 9:
            attrs.strike = True
        elif n == 22:
            attrs.bold = attrs.dim = False
        elif n == 23:
            attrs.italic = False
        elif n == 24:
            attrs.underline = False
        elif n == 27:
            attrs.reverse = False
        elif n == 29:
            attrs.strike = False
        elif 30 <= n <= 37 or 90 <= n <= 97:
            idx = (n - 30) if n < 40 else (n - 90 + 8)
            attrs.fg = _resolve_device_color(idx, palette, fg_default,
                                             bg_default, text_color)
        elif n == 38 or n == 48:
            take, val = _extended_value(nums[i + 1:])
            i += take
            if val is not None:
                color = _resolve_device_color(val, palette, fg_default,
                                              bg_default, text_color)
                if n == 38:
                    attrs.fg = color
                else:
                    attrs.bg = color
        elif n == 39:
            attrs.fg = None
        elif 40 <= n <= 47 or 100 <= n <= 107:
            idx = (n - 40) if n < 50 else (n - 100 + 8)
            attrs.bg = _resolve_device_color(idx, palette, fg_default,
                                             bg_default, text_color)
        elif n == 49:
            attrs.bg = None
        i += 1


def _extended_value(rest: list[int]) -> tuple[int, int | None]:
    """38/48 后的扩展色：5;n（消耗 2 参）或 2;r;g;b（消耗 4 参，打包真彩）。

    返回 (消耗参数数, 编号)。参数残缺时消耗 mode 本身，避免死循环。
    """
    if not rest:
        return 0, None
    mode = rest[0]
    if mode == 5 and len(rest) >= 2:
        return 2, rest[1]
    if mode == 2 and len(rest) >= 4:
        return 4, ((rest[1] & 0xFF) << 16) | ((rest[2] & 0xFF) << 8) | (rest[3] & 0xFF)
    return 1, None


def _apply_extended_colon(head: str, sub: list[str], attrs: _Attrs,
                          palette: tuple[str, ...], fg_default: str,
                          bg_default: str, text_color: str):
    """冒号形态 38:5:n / 38:2:r:g:b（部分固件/模拟器发出的带冒号变体）。"""
    nums = [int(s) for s in sub if s.isdigit()]
    if not nums:
        return
    if nums[0] == 5 and len(nums) >= 2:
        val = nums[1]
    elif nums[0] == 2 and len(nums) >= 4:
        val = ((nums[1] & 0xFF) << 16) | ((nums[2] & 0xFF) << 8) | (nums[3] & 0xFF)
    else:
        return
    color = _resolve_device_color(val, palette, fg_default, bg_default,
                                  text_color)
    if head == '38':
        attrs.fg = color
    else:
        attrs.bg = color


def parse_segments(text: str, fg_default: str, bg_default: str,
                   text_color: str) -> tuple[list[Segment], bool]:
    """一行含转义的串口文本 → (显示段列表, 是否出现过 SGR)。

    fg_default/bg_default/text_color 取主题：日志前景 / 日志底 / 日志文字。
    段内文本是剥离转义后的原文切片——空格、制表符原样保留；未出现 SGR 时
    had_sgr=False，调用方可用整行单色渲染（仍应使用返回的段文本，因为
    非 SGR 转义已剥除）。
    """
    palette = _base_palette(bg_default)
    attrs = _Attrs()
    segs: list[Segment] = []
    buf: list[str] = []
    cur_style: tuple | None = None
    dirty = True          # 首段 / SGR 变更后下一段需重算样式
    had_sgr = False

    def style_now() -> tuple:
        fg = attrs.fg or fg_default
        bg = attrs.bg
        if attrs.reverse:
            # 反显：前景取原背景（未设则日志底色），背景取原前景
            fg = attrs.bg or bg_default
            bg = attrs.fg or fg_default
        if attrs.dim and not attrs.reverse:
            fg = _rgb_to_hex(_mix(_hex_to_rgb(fg), _hex_to_rgb(bg_default), 0.45))
        return (fg, bg, attrs.bold, attrs.italic, attrs.underline, attrs.strike)

    def flush():
        if not buf:
            return
        fg, bg, bold, italic, underline, strike = cur_style
        segs.append(Segment(''.join(buf), fg, bg, bold, italic, underline, strike))
        buf.clear()

    # OSC 先剥（其内容可含任意字符，包括伪 SGR）
    text = _OSC_RE.sub('', text)
    pos = 0
    for m in _CSI_RE.finditer(text):
        chunk = text[pos:m.start()]
        pos = m.end()
        if chunk:
            if dirty:
                flush()
                cur_style = style_now()
                dirty = False
            buf.append(chunk)
        if m.group(2) or m.group(3) != 'm':
            continue          # 光标移动/擦除等：剥掉不留痕
        had_sgr = True
        dirty = True
        _parse_sgr(m.group(1).split(';'), attrs, palette, fg_default,
                   bg_default, text_color)
    tail = text[pos:]
    if tail:
        if dirty:
            flush()
            cur_style = style_now()
            dirty = False
        buf.append(tail)
    flush()
    return segs, had_sgr
