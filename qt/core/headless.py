# -*- coding: utf-8 -*-
"""无头捕获模式（不开界面，供 AI/脚本调用）——移植主线 core/headless.ps1。

JSONL 事件流（与主线逐字段同构）：
  META  会话元信息（首个事件）
  DATA  原始字节块（仅 --record / 录制回放文件里就有）
  RX    FireWater 文本行（解析出帧时带 ch）/ JustFloat 帧行（ch）/ RawData 采样行
  FAULT CFSR/HFSR dump 自动解码
  EVT   触发器命中（before/after 上下文落盘 logs/events/）
  TX    期望测试发送
  TEST / TEST_SUMMARY  期望测试步骤与汇总
  RESET / FLASH / FLASH_RESULT  复位与烧录编排
  SUMMARY  收尾汇总（最后事件）

退出码：0 正常 | 2 命中错误模式 | 3 期望测试失败 | 4 复位命令失败 | 5 烧录失败。
串口经 serial_factory 注入（默认 pyserial），回放模式全程不需要串口。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime

from core.fault_decode import test_fault_dump_line
from core.wave_state import PROTOCOLS, WaveState, try_parse_wave_frame

EXIT_OK = 0
EXIT_ERROR_HITS = 2
EXIT_TEST_FAIL = 3
EXIT_RESET_FAIL = 4
EXIT_FLASH_FAIL = 5


def get_json_prop(obj: dict, name: str, default=None):
    if not isinstance(obj, dict):
        return default
    value = obj.get(name, default)
    return default if value is None else value


@dataclass
class TriggerEngine:
    """触发器引擎：命中正则后带 before/after 行上下文落盘（对齐主线 new_trigger_engine）。"""
    events_dir: str
    triggers: list = field(default_factory=list)
    hits: int = 0

    @classmethod
    def from_config(cls, config_file: str, events_dir: str) -> 'TriggerEngine':
        with open(config_file, encoding='utf-8') as f:
            config = json.load(f)
        engine = cls(events_dir=events_dir)
        os.makedirs(events_dir, exist_ok=True)
        for item in config:
            pattern = str(get_json_prop(item, 'pattern', ''))
            if not pattern:
                continue
            engine.triggers.append({
                'name': str(get_json_prop(item, 'name', f'trigger_{len(engine.triggers) + 1}')),
                'regex': re.compile(pattern, re.IGNORECASE),
                'before': max(1, int(get_json_prop(item, 'before_lines', 20))),
                'after': max(1, int(get_json_prop(item, 'after_lines', 50))),
                'once': bool(get_json_prop(item, 'once', True)),
                'context': [],
                'segment': None,
                'after_remaining': 0,
                'hit_line': '',
                'done': False,
            })
        return engine

    def feed(self, line: str) -> list[dict]:
        events = []
        for trigger in self.triggers:
            if trigger['done']:
                continue
            if trigger['segment'] is None:
                trigger['context'].append(line)
                del trigger['context'][:-trigger['before']]
                if trigger['regex'].search(line):
                    trigger['hit_line'] = line
                    trigger['segment'] = list(trigger['context'])
                    trigger['after_remaining'] = trigger['after']
            else:
                trigger['segment'].append(line)
                trigger['after_remaining'] -= 1
            if trigger['segment'] is not None and trigger['after_remaining'] <= 0:
                stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]
                file_path = os.path.join(self.events_dir, f"{trigger['name']}_{stamp}.txt")
                header = (f"# 触发: {trigger['name']}  时间: "
                          f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
                          f"  命中行: {trigger['hit_line']}")
                with open(file_path, 'w', encoding='utf-8', newline='') as f:
                    f.write(header + '\n')
                    f.write('\n'.join(trigger['segment']) + '\n')
                events.append({'name': trigger['name'],
                               'text': trigger['hit_line'],
                               'file': file_path})
                self.hits += 1
                trigger['segment'] = None
                if trigger['once']:
                    trigger['done'] = True
        return events


def invoke_expect_steps(steps: list, send_func, read_func,
                        default_timeout_ms: int = 3000) -> dict:
    """期望测试（对齐主线 invoke_expect_steps）：send → 等待窗口内正则匹配。"""
    results = []
    for i, step in enumerate(steps):
        name = str(get_json_prop(step, 'name', f'step_{i + 1}'))
        expect = str(get_json_prop(step, 'expect', ''))
        timeout_ms = int(get_json_prop(step, 'timeout_ms', default_timeout_ms))
        send_text = None
        if get_json_prop(step, 'send') is not None:
            as_hex = bool(get_json_prop(step, 'hex', False))
            newline_mode = str(get_json_prop(step, 'newline', 'CRLF'))
            send_func(str(get_json_prop(step, 'send')), as_hex, newline_mode)
            send_text = str(get_json_prop(step, 'send'))
        started = time.monotonic()
        received = ''
        matched = None
        if expect:
            received = read_func(timeout_ms)
            m = re.search(expect, received, re.IGNORECASE)
            passed = m is not None
            if passed:
                matched = m.group(0)
        else:
            if get_json_prop(step, 'send') is None:
                read_func(timeout_ms)
            passed = True
        received_tail = received[-400:]
        results.append({
            'name': name, 'send': send_text, 'expect': expect, 'pass': passed,
            'elapsed_ms': int((time.monotonic() - started) * 1000),
            'matched': matched, 'received_tail': received_tail,
        })
    fail_count = sum(1 for r in results if not r['pass'])
    return {'steps': results, 'total': len(results), 'fail': fail_count,
            'all_pass': fail_count == 0}


def invoke_stream_command(command: str, timeout_ms: int = 300000) -> dict:
    """执行外部命令并收集输出（烧录/复位命令模式；超时杀进程）。"""
    timed_out = False
    try:
        proc = subprocess.run(command, shell=True, capture_output=True, text=True,
                              errors='replace', timeout=timeout_ms / 1000.0)
        exit_code = proc.returncode
        output = (proc.stdout or '') + (proc.stderr or '')
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = -1
        output = ''
    lines = [line for line in output.splitlines() if line.strip()]
    return {'exit_code': exit_code, 'timed_out': timed_out, 'lines': lines}


@dataclass
class HeadlessOptions:
    port: str = ''
    baud: int = 115200
    protocol: str = 'FireWater'
    duration_s: float = 10.0
    out: str = ''
    error_regex: str = ''
    trigger_file: str = ''
    expect_file: str = ''
    record: bool = False
    replay_path: str = ''
    reset_mode: str = ''          # '' | rts | dtr | command
    reset_command: str = ''
    flash_command: str = ''
    flash_timeout_ms: int = 300000
    enable_dtr: bool = False
    enable_rts: bool = False
    log_dir: str = 'logs'


def run_headless(opts: HeadlessOptions, serial_factory=None, print_fn=print) -> tuple[int, dict]:
    """执行无头会话。返回 (退出码, summary)。串口由 serial_factory(port, baud, dtr, rts) 注入。"""
    if opts.duration_s <= 0:
        opts.duration_s = 10.0
    if opts.protocol not in PROTOCOLS:
        raise ValueError(f'协议无效: {opts.protocol}')
    if opts.replay_path and opts.port:
        raise ValueError('-ReplayFile 与 -Port 不能同时使用。')
    if opts.replay_path and (opts.flash_command or opts.reset_mode):
        raise ValueError('-ReplayFile 不能与 -FlashCommand/-ResetMode 同时使用。')
    if not opts.replay_path and not opts.port:
        from core.ports import scan_port_infos
        available = sorted(d for d, _ in scan_port_infos())
        if not available:
            raise ValueError('未找到可用串口，请用 -Port 指定。')
        opts.port = available[0]
        print_fn(f'未指定 -Port，自动选择: {opts.port}')

    os.makedirs(opts.log_dir, exist_ok=True)
    if not opts.out:
        prefix = 'headless_replay_' if opts.replay_path else 'headless_'
        opts.out = os.path.join(
            opts.log_dir,
            time.strftime(prefix + '%Y%m%d_%H%M%S.jsonl', time.localtime()))

    trigger_engine = None
    if opts.trigger_file:
        trigger_engine = TriggerEngine.from_config(
            opts.trigger_file, os.path.join(opts.log_dir, 'events'))

    state = {'rx_bytes': 0, 'lines': 0}
    error_hits: list[dict] = []
    error_regex = re.compile(opts.error_regex, re.IGNORECASE) if opts.error_regex else None
    text_buffer = ['']
    wave = WaveState()
    wave.enabled = True
    wave.protocol = opts.protocol
    out_fh = open(opts.out, 'w', encoding='utf-8', newline='')

    def emit(obj: dict):
        out_fh.write(json.dumps(obj, ensure_ascii=False, separators=(',', ':')) + '\n')
        out_fh.flush()

    def stamp() -> str:
        return datetime.now().isoformat()

    def process_chunk(chunk: bytes, ms: int, write_data: bool):
        if not chunk:
            return
        if write_data:
            emit({'t': stamp(), 'ms': ms, 'dir': 'DATA',
                  'b64': base64.b64encode(chunk).decode('ascii'), 'n': len(chunk)})
        state['rx_bytes'] += len(chunk)

        before = wave.frame_count
        wave.consume_bytes(chunk)
        after = wave.frame_count

        if opts.protocol == 'FireWater':
            text_buffer[0] += chunk.decode('utf-8', errors='replace')
            normalized = text_buffer[0].replace('\r\n', '\n').replace('\r', '\n')
            parts = normalized.split('\n')
            text_buffer[0] = parts[-1]
            for line in parts[:-1]:
                state['lines'] += 1
                row = {'t': stamp(), 'ms': ms, 'dir': 'RX', 'text': line}
                frame = try_parse_wave_frame(line)
                if frame is not None:
                    row['ch'] = frame
                if error_regex is not None and error_regex.search(line):
                    row['hit'] = True
                    error_hits.append({'ms': ms, 'text': line})
                emit(row)
                decoded = test_fault_dump_line(line)
                if decoded:
                    emit({'t': stamp(), 'ms': ms, 'dir': 'FAULT',
                          'text': line, 'decoded': decoded})
                if trigger_engine:
                    for event in trigger_engine.feed(line):
                        emit({'t': stamp(), 'ms': ms, 'dir': 'EVT', **event})
            if len(text_buffer[0]) >= 8192:
                text_buffer[0] = ''
        elif after > before:
            if opts.protocol == 'RawData':
                emit({'t': stamp(), 'ms': ms, 'dir': 'RX',
                      'samples': list(wave.snapshot(0, after - before)),
                      'count': after - before})
            else:
                for idx in range(before, after):
                    emit({'t': stamp(), 'ms': ms, 'dir': 'RX',
                          'ch': [wave.ch_values[c][idx]
                                 for c in range(len(wave.ch_values))]})

    exit_code = EXIT_OK
    serial = None

    if not opts.replay_path:
        # 编排阶段：复位 / 烧录（串口未开），成功后再打开串口捕获
        if opts.reset_mode:
            if opts.reset_mode not in ('rts', 'dtr', 'command'):
                raise ValueError(f'ResetMode 无效: {opts.reset_mode}')
            if opts.reset_mode == 'command':
                if not opts.reset_command:
                    raise ValueError('ResetMode=command 需要 -ResetCommand')
                print_fn(f'复位(命令): {opts.reset_command}')
                reset_result = invoke_stream_command(opts.reset_command, 120000)
                emit({'t': stamp(), 'ms': 0, 'dir': 'RESET', 'mode': 'command',
                      'exit_code': reset_result['exit_code'],
                      'timed_out': reset_result['timed_out']})
                if reset_result['timed_out'] or reset_result['exit_code'] != 0:
                    out_fh.close()
                    print_fn('结果: 复位命令失败 → 退出码 4')
                    return EXIT_RESET_FAIL, {}
            else:
                _reset_pulse(opts, serial_factory, print_fn)
                emit({'t': stamp(), 'ms': 0, 'dir': 'RESET', 'mode': opts.reset_mode})
            time.sleep(0.06)

        if opts.flash_command:
            print_fn(f'烧录: {opts.flash_command}')
            flash_result = invoke_stream_command(opts.flash_command, opts.flash_timeout_ms)
            for line in flash_result['lines']:
                emit({'t': stamp(), 'ms': 0, 'dir': 'FLASH', 'text': line})
            emit({'t': stamp(), 'ms': 0, 'dir': 'FLASH_RESULT',
                  'exit_code': flash_result['exit_code'],
                  'timed_out': flash_result['timed_out']})
            if flash_result['timed_out'] or flash_result['exit_code'] != 0:
                out_fh.close()
                reason = '烧录超时' if flash_result['timed_out'] else \
                    f"烧录失败 exit={flash_result['exit_code']}"
                print_fn(f'结果: {reason} → 退出码 5')
                return EXIT_FLASH_FAIL, {}
            print_fn('烧录成功，开始捕获...')

        if serial_factory is None:
            def serial_factory(port, baud, dtr, rts):  # noqa: E306 - 默认 pyserial
                import serial as pyserial
                s = pyserial.serial_for_url(port, baud, timeout=0.1, write_timeout=1.0)
                s.dtr = dtr  # pyserial 构造器不收 dtr/rts，开完后设属性
                s.rts = rts
                return s
        started_at = time.monotonic()
        serial = serial_factory(opts.port, opts.baud, opts.enable_dtr, opts.enable_rts)
        print_fn(f"已连接 {opts.port} @ {opts.baud} | 协议 {opts.protocol} | "
                 f"捕获 {opts.duration_s:g}s → {opts.out}")
        emit({'t': stamp(), 'ms': 0, 'dir': 'META', 'port': opts.port, 'baud': opts.baud,
              'protocol': opts.protocol, 'replay': False})
        record_chunks = opts.record
        deadline = started_at + opts.duration_s
        while time.monotonic() < deadline:
            try:
                waiting = serial.in_waiting
            except Exception as exc:
                print_fn(f'串口读取异常: {exc}')
                break
            if waiting:
                chunk = serial.read(min(waiting, 32768))
                if chunk:
                    process_chunk(bytes(chunk),
                                  int((time.monotonic() - started_at) * 1000),
                                  record_chunks)
            else:
                time.sleep(0.005)
    else:
        if not os.path.isfile(opts.replay_path):
            raise ValueError(f'回放文件不存在: {opts.replay_path}')
        print_fn(f'回放 {opts.replay_path} → {opts.out}')
        emit({'t': stamp(), 'ms': 0, 'dir': 'META', 'port': '', 'baud': opts.baud,
              'protocol': opts.protocol, 'replay': True})
        with open(opts.replay_path, encoding='utf-8') as f:
            for record_line in f:
                record_line = record_line.strip()
                if not record_line:
                    continue
                try:
                    record = json.loads(record_line)
                except ValueError:
                    continue
                if str(get_json_prop(record, 'dir', '')) != 'DATA':
                    continue
                b64 = str(get_json_prop(record, 'b64', ''))
                replay_ms = int(get_json_prop(record, 'ms', 0))
                if b64:
                    process_chunk(base64.b64decode(b64), replay_ms, False)

    # 期望测试阶段（真实串口才有意义）
    test_summary = None
    if opts.expect_file:
        if serial is None:
            raise ValueError('回放模式不支持期望测试（-ExpectFile 需要真实串口）。')
        with open(opts.expect_file, encoding='utf-8') as f:
            steps_config = json.load(f)

        expect_started = time.monotonic()

        def expect_send(text, as_hex, newline_mode):
            if as_hex:
                from core.hexutil import try_parse_hex_text
                payload = try_parse_hex_text(text)
                payload += {'CR': b'\r', 'LF': b'\n', 'CRLF': b'\r\n'}.get(newline_mode, b'')
                if payload:
                    serial.write(bytes(payload))
            else:
                payload = text + {'CR': '\r', 'LF': '\n', 'CRLF': '\r\n'}.get(newline_mode, '')
                if payload:
                    serial.write(payload.encode('utf-8'))
            emit({'t': stamp(), 'ms': int((time.monotonic() - expect_started) * 1000),
                  'dir': 'TX', 'text': text, 'hex': bool(as_hex)})

        read_state = {'sb': ''}

        def expect_read(timeout_ms: int) -> str:
            deadline = time.monotonic() + timeout_ms / 1000.0
            while time.monotonic() < deadline:
                try:
                    waiting = serial.in_waiting
                except Exception:
                    break
                if waiting:
                    read_state['sb'] += bytes(serial.read(waiting)) \
                        .decode('utf-8', errors='replace')
                else:
                    time.sleep(0.01)
            text = read_state['sb']
            read_state['sb'] = ''
            return text

        test_summary = invoke_expect_steps(steps_config, expect_send, expect_read)
        for step in test_summary['steps']:
            emit({'t': stamp(), 'ms': 0, 'dir': 'TEST', **step})
        emit({'t': stamp(), 'ms': 0, 'dir': 'TEST_SUMMARY',
              'total': test_summary['total'], 'fail': test_summary['fail'],
              'all_pass': test_summary['all_pass']})

    summary = {
        'rx_bytes': state['rx_bytes'], 'lines': state['lines'],
        'frames': wave.frame_total, 'buffered': wave.frame_count,
        'channels': len(wave.ch_values), 'error_hits': len(error_hits),
        'test_total': (test_summary or {}).get('total', 0),
        'test_fail': (test_summary or {}).get('fail', 0),
        'trigger_hits': (trigger_engine.hits if trigger_engine else 0),
        'out': opts.out,
    }
    emit({'t': stamp(), 'ms': 0, 'dir': 'SUMMARY', **summary})

    if serial is not None:
        try:
            serial.close()
        except Exception:
            pass
    out_fh.close()

    if error_hits:
        print_fn(f'结果: 命中错误模式 {len(error_hits)} 次 → 退出码 2')
        return EXIT_ERROR_HITS, summary
    if test_summary is not None and not test_summary['all_pass']:
        print_fn(f"结果: 期望测试失败 {test_summary['fail']}/{test_summary['total']}"
                 ' → 退出码 3')
        return EXIT_TEST_FAIL, summary
    print_fn(f"结果: 正常 → 退出码 0（RX {state['rx_bytes']} 字节 / "
             f"{state['lines']} 行 / {wave.frame_total} 帧）")
    return EXIT_OK, summary


def _reset_pulse(opts: HeadlessOptions, serial_factory, print_fn):
    """RTS/DTR 复位脉冲（板载自动复位电路极性两种都提供）。"""
    factory = serial_factory
    if factory is None:
        def factory(port, baud, dtr, rts):  # noqa: E306
            import serial as pyserial
            s = pyserial.serial_for_url(port, baud, timeout=0.1)
            s.dtr = dtr  # 同上：属性方式设线路电平
            s.rts = rts
            return s
    port = factory(opts.port, opts.baud, opts.reset_mode == 'dtr',
                   opts.reset_mode == 'rts')
    try:
        time.sleep(0.15)
    finally:
        try:
            port.close()
        except Exception:
            pass
    time.sleep(0.06)
    print_fn(f'复位({opts.reset_mode}): 已脉冲')
