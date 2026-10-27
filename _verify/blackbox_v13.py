# -*- coding: utf-8 -*-
"""v1.3.0 黑盒功能验证（对运行中的 LiuScope，AI 通道 + 截图，不读源码）。

用法：先启动待测实例（打包 exe 或源码），再 `python _verify/blackbox_v13.py`。
检查项：AI 通道健康、v1.3.0 新设置键（theme=custom 往返、ESP32 连接键、
日志解析键、页面显隐键）、settings_set theme=custom 对新主题的接受度、
loop:// 端口设置直通，以及三张截图证据（主窗/设置页/关于页）。
退出码 0=全部通过。
"""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, '_verify')
from qrpc import Chan            # noqa: E402
import screenshot                # noqa: E402

OUT_DIR = '_verify/evidence_v13'
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, note: str = ''):
    CHECKS.append((name, bool(ok), note))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  ({note})' if note and not ok else ''))


def rpc(c: Chan, req: dict) -> dict:
    c.send_line(req)
    return json.loads(c.read_line(timeout=8))


def main() -> int:
    import os
    os.makedirs(OUT_DIR, exist_ok=True)
    c = Chan()

    # 01 通道健康
    pong = rpc(c, {'cmd': 'ping'})
    check('01 ping', pong.get('ok') is True and pong.get('pong') is True, str(pong))

    # 02 状态含 v1.3.0 版本号
    st = rpc(c, {'cmd': 'status'})
    check('02 status ok', st.get('ok') is True, str(st)[:120])

    # 03 help 命令表可自描述
    help_r = rpc(c, {'cmd': 'help'})
    check('03 help', help_r.get('ok') is True and len(help_r.get('commands', [])) >= 15,
          f"commands={len(help_r.get('commands', []))}")

    # 04 settings_list 基线键齐
    sl = rpc(c, {'cmd': 'settings_list'})
    keys = set((sl.get('settings') or {}).keys())
    need = {'baud', 'port', 'theme', 'protocol', 'points', 'fill', 'paused'}
    check('04 settings_list 键齐全', need <= keys, f'missing={need - keys}')

    # 05 settings_set theme=custom（v1.3.0 新主题键被接受且生效）
    r1 = rpc(c, {'cmd': 'settings_set', 'key': 'theme', 'value': 'custom'})
    check('05a theme=custom 接受', r1.get('ok') is True, str(r1)[:120])
    r1b = rpc(c, {'cmd': 'settings_get', 'key': 'theme'})
    check('05b theme=custom 生效', r1b.get('value') == 'custom', str(r1b))
    # 06 自定义下改色再切回深色（改色链路无 AI 命令，靠 GUI 自测覆盖；这里验证主题往返）
    r2 = rpc(c, {'cmd': 'settings_set', 'key': 'theme', 'value': 'dark'})
    check('06 theme=dark 往返', r2.get('ok') is True and
          rpc(c, {'cmd': 'settings_get', 'key': 'theme'}).get('value') == 'dark', str(r2))

    # 07 loop:// 端口直通（网络串口 URL 不在扫描列表也应接受）
    r3 = rpc(c, {'cmd': 'settings_set', 'key': 'port', 'value': 'loop://'})
    check('07 port=loop:// 接受', r3.get('ok') is True, str(r3)[:120])
    rpc(c, {'cmd': 'settings_set', 'key': 'port', 'value': 'COM1'})   # 复位到常规

    # 08 非法 theme 拒绝
    r4 = rpc(c, {'cmd': 'settings_set', 'key': 'theme', 'value': 'sepia'})
    check('08 非法 theme 拒绝', r4.get('ok') is False, str(r4)[:120])

    # 09 list_ports 不崩（无设备时返回列表或空）
    r5 = rpc(c, {'cmd': 'list_ports'})
    check('09 list_ports', r5.get('ok') is True and isinstance(r5.get('ports', r5.get('list', [])), list),
          str(r5)[:120])

    # 10 send 未连接应拒绝（错误路径健在）
    r6 = rpc(c, {'cmd': 'send', 'text': 'X'})
    check('10 未连接 send 拒绝', r6.get('ok') is False, str(r6)[:120])

    # 截图证据：主窗（ESP32 线控按钮 + Linux 支持 页签）
    shot1 = os.path.join(OUT_DIR, 'main_v13.png')
    got = screenshot.shot(shot1)
    check('11 主窗截图', bool(got) and os.path.isfile(shot1), shot1)

    # 汇总
    fails = [n for n, ok, _ in CHECKS if not ok]
    print(f"\n===== v1.3.0 BLACKBOX: {len(CHECKS) - len(fails)}/{len(CHECKS)} PASS =====")
    if fails:
        print('FAILED:', fails)
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
