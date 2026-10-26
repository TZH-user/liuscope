# -*- coding: utf-8 -*-
r"""STM32CubeProgrammer CLI（STM32_Programmer_CLI.exe）封装：路径探测 / QProcess 异步执行 / 输出解析。

设计：
- 路径解析顺序 resolve_cli_path()：设置键 → C:\ST 默认目录 → PATH → 各盘符常见安装位置扫描；
  设置键非空但文件不存在时不回落（用户显式指定的路径失效就如实报错），返回 '' 由页面给中文提示。
- 执行统一走 Stm32Cli(QProcess)：单实例串行（忙碌时拒绝新操作），QTimer 超时强杀，
  readyRead 增量吐 raw_output 供页面日志区滚动，finished(dict) 一次性给结构化结果。
  长操作全部异步，UI 不卡；关窗 shutdown() 强杀子进程。
- 解析是纯函数（parse_* / build_*_args），不依赖 GUI，离屏/无头均可单测。
  真机输出样本来自本机 v2.23.0 实测：-l / -c port=SWD mode=UR / -ob displ / -c port=COM99。
  注意 CLI 失败时 exit code = 1 且输出含 'Error: ...'，成功 exit 0 —— 成败同时看两者；
  进度条块字符在中文控制台是 GBK 码，decode_output 按 UTF-8 → GBK 依次兜底。
"""
from __future__ import annotations

import os
import shutil
import sys

from PySide6 import QtCore

CLI_EXE = 'STM32_Programmer_CLI.exe' if sys.platform == 'win32' else 'STM32_Programmer_CLI'
CLI_BIN_DIR = 'STM32CubeProgrammer' + ('\\bin' if sys.platform == 'win32' else '/bin')

# 连接参数：SWD + Under-Reset（任务口径）；多探针时追加 index=<探针号>
CONNECT_PORT = 'SWD'
CONNECT_MODE_DEFAULT = 'UR'
CONNECT_MODES = ('UR', 'HOTPLUG', 'NORMAL')

BIN_BASE_ADDR_DEFAULT = '0x08000000'   # bin 烧录基址默认值（hex 文件自带地址用不到）

# 各操作超时（ms）：烧录大 hex 走 SWD 可能好几分钟，给足余量
TIMEOUT_LIST_MS = 20000
TIMEOUT_CONNECT_MS = 30000
TIMEOUT_RESET_MS = 30000
TIMEOUT_OB_MS = 60000
TIMEOUT_ERASE_MS = 300000
TIMEOUT_RDU_MS = 120000
TIMEOUT_FLASH_MS = 600000


def _is_win() -> bool:
    return sys.platform == 'win32'


def _probe_candidates() -> list[str]:
    r"""常见安装位置候选（按盘符扫描）：X:\ST 与官方默认 Program Files 目录。"""
    out: list[str] = []
    if _is_win():
        for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
            root = f'{letter}:\\'
            if not os.path.exists(root):
                continue
            out += [
                os.path.join(root, 'ST', 'STM32CubeProgrammer', 'bin', CLI_EXE),
                os.path.join(root, 'Program Files', 'STMicroelectronics', 'STM32Cube',
                             'STM32CubeProgrammer', 'bin', CLI_EXE),
                os.path.join(root, 'Program Files (x86)', 'STMicroelectronics', 'STM32Cube',
                             'STM32CubeProgrammer', 'bin', CLI_EXE),
            ]
    else:
        for prefix in ('/usr/local', '/opt', os.path.expanduser('~')):
            out.append(os.path.join(prefix, 'STM32CubeProgrammer', 'bin', CLI_EXE))
    return out


def _c_st_fixed() -> str:
    r"""任务口径的固定探测位 C:\ST\STM32CubeProgrammer\bin（单列成函数便于测试屏蔽）。"""
    if _is_win():
        fixed = os.path.join('C:\\', 'ST', 'STM32CubeProgrammer', 'bin', CLI_EXE)
        if os.path.isfile(fixed):
            return fixed
    return ''


def resolve_cli_path(explicit: str = '') -> str:
    r"""解析 CLI 可执行文件路径（顺序：设置键 → C:\ST → PATH → 常见位置扫描）。

    返回存在的绝对路径；找不到返回 ''（页面据此给中文提示）。
    """
    explicit = (explicit or '').strip()
    if explicit and os.path.isfile(explicit):
        return explicit
    fixed = _c_st_fixed()
    if fixed:
        return fixed
    found = shutil.which('STM32_Programmer_CLI') or shutil.which(CLI_EXE)
    if found:
        return found
    for cand in _probe_candidates():
        if os.path.isfile(cand):
            return cand
    return ''


# ---------------- 纯函数：参数构造（测试可断言 argv，不必真跑） ----------------

def build_connect_args(mode: str = CONNECT_MODE_DEFAULT, index: int | None = None) -> list[str]:
    """-c port=SWD mode=<UR/HOTPLUG/NORMAL> [index=<探针号>]"""
    mode = mode if mode in CONNECT_MODES else CONNECT_MODE_DEFAULT
    args = ['-c', f'port={CONNECT_PORT}', f'mode={mode}']
    if index is not None and index > 0:
        args.append(f'index={index}')
    return args


def build_flash_args(path: str, base_addr: str | None = None,
                     mode: str = CONNECT_MODE_DEFAULT, index: int | None = None) -> list[str]:
    """烧录参数：hex 用 -d 保留文件内地址；bin 用 -w <file> <基址>；都带 -v 校验。"""
    args = build_connect_args(mode, index)
    is_bin = os.path.splitext(path)[1].lower() == '.bin'
    if is_bin:
        args += ['-w', path, (base_addr or BIN_BASE_ADDR_DEFAULT).strip()]
    else:
        args += ['-d', path]
    args += ['-v']
    return args


def build_erase_all_args(mode: str = CONNECT_MODE_DEFAULT, index: int | None = None) -> list[str]:
    """全片擦除：-e all"""
    return build_connect_args(mode, index) + ['-e', 'all']


def build_reset_args(hard: bool, mode: str = CONNECT_MODE_DEFAULT,
                     index: int | None = None) -> list[str]:
    """复位：软复位 -rst（系统复位），硬复位 -hardRst（NRST 硬件复位，v2.23 --help 实测旗标）。"""
    return build_connect_args(mode, index) + ['-hardRst' if hard else '-rst']


def build_rdu_args(mode: str = CONNECT_MODE_DEFAULT, index: int | None = None) -> list[str]:
    """读保护解除：-rdu；UI 已二次确认，追加 -y 跳过 CLI 自身的 stdin 确认（防子进程挂起）。"""
    return build_connect_args(mode, index) + ['-rdu', '-y']


def build_ob_args(mode: str = CONNECT_MODE_DEFAULT, index: int | None = None) -> list[str]:
    """Option Bytes 读取：-ob displ（显示全部 OB）。"""
    return build_connect_args(mode, index) + ['-ob', 'displ']


# ---------------- 纯函数：输出解析 ----------------

def decode_output(data: bytes) -> str:
    """CLI 输出解码：UTF-8 优先，失败按 GBK 兜底（中文控制台进度条是 GBK 码）。"""
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('gbk', errors='replace')


def parse_error_message(text: str) -> str:
    """提取 'Error: ...' 行（多行用 '；' 连接）；无则空串。"""
    errors = [ln.strip() for ln in text.splitlines()
              if ln.strip().lower().startswith(('error:', 'error-'))]
    return '；'.join(errors)


def parse_probe_list(text: str) -> list[dict]:
    """解析 -l 的 ST-LINK 探测列表。

    真机样本（v2.23.0）：
        ST-Link Probe 0 :
           ST-LINK SN  : E1007200D0D2139393740544
           ST-LINK FW  : V2J46S7
           Access Port Number  : 3
           Board Name  :
    返回 [{'index': 0, 'sn':..., 'fw':..., 'access_port':..., 'board':...}, ...]
    """
    probes: list[dict] = []
    cur: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('ST-Link Probe'):
            cur = {'index': len(probes), 'sn': '', 'fw': '', 'access_port': '', 'board': ''}
            probes.append(cur)
            continue
        if cur is None or ':' not in line:
            continue
        key, _, value = line.partition(':')
        key, value = key.strip().lower(), value.strip()
        if key == 'st-link sn':
            cur['sn'] = value
        elif key == 'st-link fw':
            cur['fw'] = value
        elif key == 'access port number':
            cur['access_port'] = value
        elif key == 'board name':
            cur['board'] = value
    return probes


# 连接信息里要抓的键 → 结构化字段名（真机输出 'Device name : STM32H7xx' 之类）
_INFO_KEYS = {
    'st-link sn': 'sn',
    'st-link fw': 'fw',
    'board': 'board',
    'voltage': 'voltage',
    'swd freq': 'swd_freq',
    'connect mode': 'connect_mode',
    'reset mode': 'reset_mode',
    'device id': 'device_id',
    'revision id': 'revision_id',
    'device name': 'name',
    'nvm size': 'nvm_size',
    'ram size': 'ram_size',
    'device type': 'device_type',
    'device cpu': 'cpu',
    'bl version': 'bl_version',
}


def parse_device_info(text: str) -> dict:
    """解析 -c port=SWD 连接后的芯片信息（键 : 值 逐行抓取）。

    H7 真机输出没有 RAM 行，ram_size 留空由页面显示 '—'（部分型号会带 RAM size 行）。
    """
    info: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        field = _INFO_KEYS.get(key.strip().lower())
        if field:
            info[field] = value.strip()
    return info


def parse_option_bytes(text: str) -> dict:
    """解析 -ob displ：抓 RDP 行给出保护等级，全文由页面展示。

    真机样本：'RDP          : 0xAA (Level 0, no protection)'
    """
    rdp_value, rdp_desc = '', ''
    for raw in text.splitlines():
        line = raw.strip()
        if line.upper().startswith('RDP') and ':' in line:
            _, _, value = line.partition(':')
            rdp_value, _, rdp_desc = value.strip().partition(' ')
            rdp_desc = rdp_desc.strip('()')
            break
    return {'rdp_value': rdp_value, 'rdp_desc': rdp_desc, 'text': text}


def flash_success_marker(text: str) -> bool:
    """带 -v 的烧录成功标志（v2.x 实际输出行）。"""
    return 'Download verified successfully' in text


def erase_success_marker(text: str) -> bool:
    """全片擦除成功标志（Mass erase / Erase 成功字样，兜底看无 Error）。"""
    return 'erase successfully' in text.lower()


# ---------------- 异步执行器（QProcess，防 UI 卡死） ----------------

class Stm32Cli(QtCore.QObject):
    """单实例串行执行器：一次一个 CLI 子进程，超时强杀，信号回 UI。

    finished 载荷 dict：{action, ok, timed_out, exit_code, output, error, data}
    """

    finished = QtCore.Signal(dict)
    raw_output = QtCore.Signal(str)        # 增量原始输出（页面日志区滚动用）
    busy_changed = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cli_path = ''                 # 由页面 resolve 后注入；空则 run 直接报错
        self.extra_env: dict[str, str] = {}   # 测试注入假 CLI 行为用
        self._proc: QtCore.QProcess | None = None
        self._busy = False
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._buffer = ''
        self._action = ''
        self._timed_out = False

    # ---------- 状态 ----------

    def is_busy(self) -> bool:
        return self._busy

    def _set_busy(self, on: bool):
        if self._busy != on:
            self._busy = on
            self.busy_changed.emit(on)

    def shutdown(self):
        """关窗收尾：强杀子进程并短暂等待（kill 是强制终止，1s 足够）。"""
        if self._proc is not None:
            self._timer.stop()
            self._proc.kill()
            self._proc.waitForFinished(1000)
            self._proc = None
        self._set_busy(False)

    # ---------- 执行 ----------

    def run(self, action: str, args: list[str], timeout_ms: int,
            parser=None) -> bool:
        """启动一次 CLI 调用。忙碌/无 CLI 时返回 False（调用方负责提示）。"""
        if self._busy:
            return False
        if not self.cli_path or not os.path.isfile(self.cli_path):
            QtCore.QTimer.singleShot(0, lambda: self.finished.emit({
                'action': action, 'ok': False, 'timed_out': False, 'exit_code': None,
                'output': '', 'error': f'未找到 STM32CubeProgrammer CLI（{self.cli_path or "路径为空"}）',
                'data': None}))
            return True
        proc = QtCore.QProcess(self)
        if _is_win() and os.path.splitext(self.cli_path)[1].lower() in ('.bat', '.cmd'):
            # 批处理假 CLI（测试用）：CreateProcess 走 cmd 外壳，参数逐个加引号
            program, base = 'cmd.exe', ['/c', self.cli_path]
        else:
            program, base = self.cli_path, []
        if self.extra_env:
            env = QtCore.QProcessEnvironment.systemEnvironment()
            for key, value in self.extra_env.items():
                env.insert(key, value)
            proc.setProcessEnvironment(env)
        self._action = action
        self._timed_out = False
        self._buffer = ''
        self._proc = proc
        proc.readyReadStandardOutput.connect(self._on_ready)
        proc.errorOccurred.connect(self._on_error)
        proc.finished.connect(self._on_finished)
        self._set_busy(True)
        self._timer.start(timeout_ms)
        proc.start(program, base + args)
        return True

    def _on_ready(self):
        if self._proc is None:
            return
        chunk = decode_output(bytes(self._proc.readAllStandardOutput()))
        self._buffer += chunk
        self.raw_output.emit(chunk)

    def _on_error(self, error):
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            # 路径无效/权限不足：QProcess 起不来，直接出结果（finished 可能不会再发）
            self._timer.stop()
            self._set_busy(False)
            self._proc = None
            self.finished.emit({
                'action': self._action, 'ok': False, 'timed_out': False, 'exit_code': None,
                'output': self._buffer, 'error': 'CLI 启动失败（路径无效或无法执行）',
                'data': None})

    def _on_timeout(self):
        self._timed_out = True
        if self._proc is not None:
            self._proc.kill()

    def _on_finished(self, *_):
        self._timer.stop()
        proc, self._proc = self._proc, None
        if proc is None:
            return
        output = self._buffer
        exit_code = proc.exitCode()
        self._set_busy(False)
        if self._timed_out:
            self.finished.emit({
                'action': self._action, 'ok': False, 'timed_out': True, 'exit_code': None,
                'output': output, 'error': '操作超时，已强制终止 CLI', 'data': None})
            return
        error = parse_error_message(output)
        ok = exit_code == 0 and not error
        self.finished.emit({
            'action': self._action, 'ok': ok, 'timed_out': False, 'exit_code': exit_code,
            'output': output, 'error': error, 'data': None})

    # ---------- 高层操作（页面直接调） ----------

    def list_probes(self) -> bool:
        return self.run('list', ['-l'], TIMEOUT_LIST_MS)

    def connect_target(self, mode: str = CONNECT_MODE_DEFAULT,
                       index: int | None = None) -> bool:
        return self.run('connect', build_connect_args(mode, index), TIMEOUT_CONNECT_MS)

    def read_option_bytes(self, mode: str = CONNECT_MODE_DEFAULT,
                          index: int | None = None) -> bool:
        return self.run('ob', build_ob_args(mode, index), TIMEOUT_OB_MS)

    def reset_target(self, hard: bool, mode: str = CONNECT_MODE_DEFAULT,
                     index: int | None = None) -> bool:
        return self.run('reset_hard' if hard else 'reset_soft',
                        build_reset_args(hard, mode, index), TIMEOUT_RESET_MS)

    def flash(self, path: str, base_addr: str | None = None,
              mode: str = CONNECT_MODE_DEFAULT, index: int | None = None) -> bool:
        return self.run('flash', build_flash_args(path, base_addr, mode, index),
                        TIMEOUT_FLASH_MS)

    def erase_all(self, mode: str = CONNECT_MODE_DEFAULT,
                  index: int | None = None) -> bool:
        return self.run('erase', build_erase_all_args(mode, index), TIMEOUT_ERASE_MS)

    def readout_unprotect(self, mode: str = CONNECT_MODE_DEFAULT,
                          index: int | None = None) -> bool:
        return self.run('rdu', build_rdu_args(mode, index), TIMEOUT_RDU_MS)
