# -*- coding: utf-8 -*-
"""波形场景预设：波特率+协议+滤波+Y 量程+窗口宽度 一键切换（wave_presets.txt，INI 风格）。

对齐主线 ui/wave_form.ps1 的 load/save 口径：
  [预设名] / baud= / protocol= / filter= / filter_param= / auto_y= / ymin= / ymax= / window=
文件为 UTF-8 带 BOM（WriteAllLines UTF8Encoding(true) 同款），手编友好。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

_PRESET_HEADER_RE = re.compile(r'^\[(.+)\]$')
_KEY_RE = re.compile(r'^([A-Za-z_]+)\s*=\s*(.*)$')


@dataclass
class WavePreset:
    name: str
    baud: str = ''
    protocol: str = ''
    filter: str = ''
    filter_param: str = ''
    auto_y: bool = True
    y_min: str = ''
    y_max: str = ''
    window: str = ''


def default_presets_path() -> str:
    """仓库根 wave_presets.txt（主线 app_root 同位置；用户文件，git 忽略）。"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'wave_presets.txt')


def load_wave_presets(path: str) -> list[WavePreset]:
    presets: list[WavePreset] = []
    try:
        with open(path, encoding='utf-8-sig') as f:
            current: WavePreset | None = None
            for raw in f:
                t = raw.strip()
                if not t:
                    continue
                m = _PRESET_HEADER_RE.match(t)
                if m:
                    current = WavePreset(name=m.group(1).strip())
                    presets.append(current)
                    continue
                if current is None:
                    continue
                km = _KEY_RE.match(t)
                if not km:
                    continue
                key, value = km.group(1), km.group(2).strip()
                if key == 'baud':
                    current.baud = value
                elif key == 'protocol':
                    current.protocol = value
                elif key == 'filter':
                    current.filter = value
                elif key == 'filter_param':
                    current.filter_param = value
                elif key == 'auto_y':
                    current.auto_y = bool(re.match(r'^(1|true|yes)$', value, re.IGNORECASE))
                elif key == 'ymin':
                    current.y_min = value
                elif key == 'ymax':
                    current.y_max = value
                elif key == 'window':
                    current.window = value
    except OSError:
        pass
    return presets


def save_wave_presets(presets: list[WavePreset], path: str) -> bool:
    lines: list[str] = []
    for p in presets:
        lines.append(f'[{p.name}]')
        lines.append(f'baud={p.baud}')
        lines.append(f'protocol={p.protocol}')
        lines.append(f'filter={p.filter}')
        lines.append(f'filter_param={p.filter_param}')
        lines.append(f'auto_y={"1" if p.auto_y else "0"}')
        lines.append(f'ymin={p.y_min}')
        lines.append(f'ymax={p.y_max}')
        lines.append(f'window={p.window}')
        lines.append('')
    try:
        with open(path, 'w', encoding='utf-8-sig', newline='\n') as f:
            f.write('\n'.join(lines))
        return True
    except OSError:
        return False
