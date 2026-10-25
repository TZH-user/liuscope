# -*- coding: utf-8 -*-
"""自动日志落盘与日志导出文本（对齐主线 serial_io.ps1 的口径）。

- 文件：logs/serial_auto_log_YYYYMMDD_HHMMSS.txt，懒创建——首次真正要写时才建文件
- 块格式：'[时间] 方向\\r\\n正文\\r\\n\\r\\n'，与主线自动日志逐字节同构（SYS 不落盘）
- 导出文本：全部条目 '[时间] 方向\\n正文\\n空行'，UTF-8
"""
from __future__ import annotations

import os
import time
from datetime import datetime


def get_log_dir(app_root: str | None = None) -> str:
    """日志目录：仓库根 logs/（git 忽略）。测试可传 app_root 覆盖。"""
    if app_root is None:
        # qt/core/auto_log.py → 上三级 = 仓库根
        app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(app_root, 'logs')


def format_entry_block(ts: datetime, direction: str, text: str) -> str:
    body = text if text.endswith('\r\n') else text + '\r\n'
    return f'[{ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]}] {direction}\r\n{body}\r\n'


def build_log_export_text(entries: list[dict]) -> str:
    parts: list[str] = []
    for entry in entries:
        ts: datetime = entry['ts']
        parts.append(f'[{ts.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]}] {entry["direction"]}\n')
        parts.append(f'{entry["text"]}\n\n')
    return ''.join(parts)


class AutoLogWriter:
    """懒创建的追加写日志器；pending 由调用方按勾选状态决定是否入队。"""

    def __init__(self, log_dir: str, now_fn=time.time):
        self._log_dir = log_dir
        self._now_fn = now_fn
        self._path: str | None = None
        self._writer = None
        self._pending: list[str] = []

    @property
    def path(self) -> str | None:
        return self._path

    @property
    def active(self) -> bool:
        return self._writer is not None

    def pending_count(self) -> int:
        return len(self._pending)

    def enqueue(self, block: str):
        self._pending.append(block)

    def flush(self) -> bool:
        """把积压块写盘。返回本次是否实际写了。勾选关闭时由调用方先 discard。"""
        if not self._pending:
            return False
        self._ensure_open()
        try:
            for block in self._pending:
                self._writer.write(block)
            self._writer.flush()
        except OSError:
            return False
        self._pending = []
        return True

    def discard(self):
        """勾选关闭：未写的块直接丢（对齐主线 flush_pending_log_blocks 的关闭分支）。"""
        self._pending = []

    def _ensure_open(self):
        if self._writer is not None:
            return
        if self._path is None:
            os.makedirs(self._log_dir, exist_ok=True)
            name = time.strftime('serial_auto_log_%Y%m%d_%H%M%S.txt',
                                 time.localtime(self._now_fn()))
            self._path = os.path.join(self._log_dir, name)
        # 断开重连后追加同一文件（对齐主线：log_file_path 整个运行期不变）
        self._writer = open(self._path, 'a', encoding='utf-8', newline='')

    def close(self):
        try:
            self.flush()
        except OSError:
            pass
        if self._writer is not None:
            try:
                self._writer.close()
            except OSError:
                pass
            self._writer = None
