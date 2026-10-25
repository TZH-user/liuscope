# -*- coding: utf-8 -*-
"""会话录制/回放（对齐主线 headless.ps1 的 JSONL 口径）。

录制：每个到达串口的字节块写一行
  {"t": "...", "ms": 相对开始毫秒, "dir": "DATA", "b64": base64, "n": 字节数}
回放：读回 DATA 行按原字节块喂入接收管线（GUI 回放逐块定时喂，headless 回放全速喂）。
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime


def _stamp_text() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


class SessionRecorder:
    """JSONL 会话录制器；AutoFlush（对齐主线 start_session_record）。"""

    def __init__(self, log_dir: str, stamp_fn=None):
        self._log_dir = log_dir
        self._stamp_fn = stamp_fn or (
            lambda: time.strftime('%Y%m%d_%H%M%S', time.localtime()))
        self._path: str | None = None
        self._fh = None
        self._start_monotonic = 0.0

    @property
    def active(self) -> bool:
        return self._fh is not None

    @property
    def path(self) -> str | None:
        return self._path

    def start(self) -> str:
        if self._fh is not None:
            return self._path
        os.makedirs(self._log_dir, exist_ok=True)
        self._path = os.path.join(self._log_dir, f'session_record_{self._stamp_fn()}.jsonl')
        self._fh = open(self._path, 'w', encoding='utf-8', newline='')
        self._start_monotonic = time.monotonic()
        return self._path

    def record_chunk(self, data: bytes):
        if self._fh is None or not data:
            return
        record = {
            't': _stamp_text(),
            'ms': int((time.monotonic() - self._start_monotonic) * 1000),
            'dir': 'DATA',
            'b64': base64.b64encode(data).decode('ascii'),
            'n': len(data),
        }
        try:
            self._fh.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')
            self._fh.flush()
        except OSError:
            pass

    def stop(self):
        if self._fh is None:
            return
        try:
            self._fh.flush()
            self._fh.close()
        except OSError:
            pass
        self._fh = None


def load_replay_chunks(path: str) -> list[tuple[int, bytes]]:
    """读回录制文件里的 DATA 行 → [(ms, bytes)]（坏行/缺文件跳过）。"""
    chunks: list[tuple[int, bytes]] = []
    try:
        f = open(path, encoding='utf-8')
    except OSError:
        return chunks
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get('dir') != 'DATA':
                continue
            b64 = record.get('b64')
            if not b64:
                continue
            try:
                chunks.append((int(record.get('ms', 0)), base64.b64decode(b64)))
            except (ValueError, TypeError):
                continue
    return chunks
