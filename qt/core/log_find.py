# -*- coding: utf-8 -*-
"""日志时间定位：查询串是时刻/日期时直接跳到该时刻的日志头行。

对齐主线 serial_io.ps1 的 find_log_timestamp_jump 口径：
- 时刻形态：HH:mm(.ss)?(.fff)?；日期形态：yyyy-MM-dd [HH:mm(.ss)?(.fff)?]
- 只给时刻没给日期：日期取日志首条头行的日期（日志跨天续跑时直接搜时刻更符合直觉）
- 向前跳 = 首条时刻 ≥ 查询时刻；向后跳 = 末条时刻 < 查询时刻
- 非法时间（25:99）解析失败 → 调用方回退为普通文本搜索
"""
from __future__ import annotations

import re
from datetime import datetime

_QUERY_TIME_RE = re.compile(r'^\d{1,2}:\d{2}(:\d{2})?(\.\d{1,3})?$')
_QUERY_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}( \d{1,2}:\d{2}(:\d{2})?(\.\d{1,3})?)?$')
_HEADER_RE = re.compile(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\] ')
_HEADER_FORMAT = '%Y-%m-%d %H:%M:%S.%f'
_PARSE_FORMATS = ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
                  '%Y-%m-%d', '%H:%M:%S.%f', '%H:%M:%S', '%H:%M')


def is_timestamp_query(query: str) -> bool:
    q = (query or '').strip()
    if not q:
        return False
    return bool(_QUERY_TIME_RE.match(q) or _QUERY_DATE_RE.match(q))


def parse_timestamp(query: str) -> datetime | None:
    q = (query or '').strip()
    for fmt in _PARSE_FORMATS:
        try:
            return datetime.strptime(q, fmt)
        except ValueError:
            continue
    return None


def collect_timestamp_headers(text: str) -> list[tuple[int, int, datetime]]:
    """日志全文里的所有头行 → [(起点, 长度, 时刻)]（含方括号整段，供选中/高亮）。"""
    hits: list[tuple[int, int, datetime]] = []
    for m in _HEADER_RE.finditer(text):
        try:
            ts = datetime.strptime(m.group(1), _HEADER_FORMAT)
        except ValueError:
            continue
        hits.append((m.start(), len(m.group(0)) - 1, ts))  # -1 去掉尾随空格
    return hits


def find_timestamp_jump(positions: list[tuple[int, int, datetime]], query: str,
                        backward: bool, sel_start: int = 0, sel_end: int = 0):
    """在头行列表里定位查询时刻。返回 dict 或 None（非时间查询）。

    返回 {'total', 'found', 'ordinal', 'pos', 'length', 'time'}；找不到时 found=False。
    """
    if not is_timestamp_query(query):
        return None
    target = parse_timestamp(query)
    if target is None or not positions:
        return {'total': len(positions), 'found': False}

    # 只给了时刻：日期取日志首条头行
    if not re.search(r'\d{4}-\d{2}-\d{2}', query) and positions:
        first_ts = positions[0][2]
        target = first_ts.replace(hour=target.hour, minute=target.minute,
                                  second=target.second,
                                  microsecond=target.microsecond)

    candidates = [p for p in positions
                  if (p[2] < target if backward else p[2] >= target)]
    if not candidates:
        return {'total': len(positions), 'found': False}

    chosen = None
    if not backward:
        for item in candidates:
            if item[0] >= sel_end:
                chosen = item
                break
        if chosen is None:
            chosen = candidates[0]  # 到尾回绕
    else:
        for item in reversed(candidates):
            if item[0] < sel_start:
                chosen = item
                break
        if chosen is None:
            chosen = candidates[-1]
    return {
        'total': len(positions),
        'found': True,
        'ordinal': positions.index(chosen) + 1,
        'pos': chosen[0],
        'length': chosen[1],
        'time': chosen[2],
    }
