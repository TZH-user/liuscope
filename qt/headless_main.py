# -*- coding: utf-8 -*-
"""无头捕获 CLI：不开界面直接采集/回放（python qt/headless_main.py --help）。

示例：
  实时捕获 10s：   python headless_main.py --port COM8 --baud 1500000 --duration 10
  错误监视：       python headless_main.py --port COM8 --error-regex "panic|assert" --duration 30
  回放录制文件：   python headless_main.py --replay logs/session_record_xxx.jsonl --duration 0
  期望测试：       python headless_main.py --port COM8 --expect-file steps.json
退出码：0 正常 | 2 错误模式命中 | 3 期望测试失败 | 4 复位失败 | 5 烧录失败
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.headless import HeadlessOptions, run_headless  # noqa: E402


def parse_args(argv=None) -> HeadlessOptions:
    parser = argparse.ArgumentParser(
        description='RK3562 串口收发工具 V6（Qt 支线）无头捕获模式')
    parser.add_argument('--port', default='', help='串口名（缺省自动选第一个可用口）')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--protocol', default='FireWater',
                        choices=['FireWater', 'JustFloat', 'RawData'])
    parser.add_argument('--duration', type=float, default=10.0, help='捕获时长（秒）')
    parser.add_argument('--out', default='', help='输出 JSONL（缺省 logs/headless_时间戳.jsonl）')
    parser.add_argument('--error-regex', default='', help='RX 命中即退出码 2')
    parser.add_argument('--trigger-file', default='', help='触发器配置 JSON（before/after 上下文落盘）')
    parser.add_argument('--expect-file', default='', help='期望测试步骤 JSON')
    parser.add_argument('--record', action='store_true', help='同时把原始字节流录进 JSONL（DATA 行）')
    parser.add_argument('--replay', default='', help='回放录制 JSONL（与 --port 互斥）')
    parser.add_argument('--reset-mode', default='', choices=['', 'rts', 'dtr', 'command'])
    parser.add_argument('--reset-command', default='')
    parser.add_argument('--flash-command', default='')
    parser.add_argument('--flash-timeout-ms', type=int, default=300000)
    parser.add_argument('--dtr', action='store_true')
    parser.add_argument('--rts', action='store_true')
    parser.add_argument('--log-dir', default='logs')
    args = parser.parse_args(argv)
    return HeadlessOptions(
        port=args.port, baud=args.baud, protocol=args.protocol,
        duration_s=args.duration, out=args.out, error_regex=args.error_regex,
        trigger_file=args.trigger_file, expect_file=args.expect_file,
        record=args.record, replay_path=args.replay,
        reset_mode=args.reset_mode, reset_command=args.reset_command,
        flash_command=args.flash_command, flash_timeout_ms=args.flash_timeout_ms,
        enable_dtr=args.dtr, enable_rts=args.rts, log_dir=args.log_dir)


def main(argv=None) -> int:
    opts = parse_args(argv)
    try:
        exit_code, _summary = run_headless(opts)
    except ValueError as exc:
        print(f'参数错误: {exc}')
        return 2
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
