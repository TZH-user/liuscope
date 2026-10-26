# -*- coding: utf-8 -*-
"""AI 控制通道：TCP JSON 行协议（127.0.0.1:9101，一行一个 JSON 请求，逐行回 JSON 响应）。

- 协议处理是纯函数 handle_control_request（api 为注入的宿主能力集），网络壳在 ControlChannel
- 命令分三类：探测/文档（ping/help/status/list_ports）、数据获取（send/read_lines/
  wave_snapshot/spectrum_snapshot/xy_snapshot/modbus_recent/stm32_status）、
  控制命令（set_protocol/wave_clear/wave_sim/record/settings_*——发出后 UI 前端同步跟随）
- read_lines 的 follow=true 进入持续推送模式：壳按 interval 节流轮询宿主
  read_lines(since=游标)，只推新行（时间升序、max_lines 上限）；客户端发
  {"cmd":"stop"} / 一行纯文本 stop / 断开连接即退出
- COMMANDS 命令表是 help 响应与 ai_doc「复制 AI 接入信息」的唯一来源（单一事实）
- 线程口径：QTcpServer 信号全部在 GUI 线程事件循环派发，命令闭包可直接读写 UI
  （与主窗 _build_control_api 的既有先例一致）；follow 推送同样跑在 GUI 线程 QTimer 上
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from PySide6 import QtCore
from PySide6.QtNetwork import QHostAddress, QTcpServer

from core import APP_NAME

# ---- follow 推送语义（写进 help 与 AI_INTERFACE.md 的同一份事实） ----
FOLLOW_DEFAULT_INTERVAL_S = 0.5
FOLLOW_MIN_INTERVAL_S = 0.05
FOLLOW_MAX_INTERVAL_S = 60.0
FOLLOW_DEFAULT_MAX_LINES = 200
FOLLOW_MAX_LINES_CAP = 2000

FOLLOW_DOC = {
    'default_interval_s': FOLLOW_DEFAULT_INTERVAL_S,
    'interval_range_s': [FOLLOW_MIN_INTERVAL_S, FOLLOW_MAX_INTERVAL_S],
    'default_max_lines': FOLLOW_DEFAULT_MAX_LINES,
    'max_lines_cap': FOLLOW_MAX_LINES_CAP,
    'push_format': '{"ok":true,"follow":true,"lines":[…],"cursor":N}',
    'exit': '发送 {"cmd":"stop"} 或一行纯文本 stop，或断开连接',
    'note': '首次响应已含最新 count 行快照与游标；之后仅在出现新行时推送（时间升序）',
}

# ---- 命令表（cmd/params/desc/example；help 响应与 ai_doc 文案共用） ----
COMMANDS: list[dict] = [
    {'cmd': 'ping', 'params': {}, 'desc': '存活探测',
     'example': {'cmd': 'ping'}},
    {'cmd': 'help', 'params': {}, 'desc': '结构化命令表（命令名/参数/说明/示例）+ follow 语义',
     'example': {'cmd': 'help'}},
    {'cmd': 'status', 'params': {}, 'desc': '连接/协议/帧数/录制状态快照',
     'example': {'cmd': 'status'}},
    {'cmd': 'list_ports', 'params': {}, 'desc': '可用串口列表',
     'example': {'cmd': 'list_ports'}},
    {'cmd': 'send', 'params': {'text': 'str 文本内容（hex=true 时为 HEX 串，如 "A1 55"）',
                               'hex': 'bool=false 按 HEX 解析',
                               'newline': '"None"/"CR"/"LF"/"CRLF"，默认 CRLF'},
     'desc': '发送数据（进 TX 记录与日志；未连接报错）',
     'example': {'cmd': 'send', 'text': 'version\\r', 'newline': 'LF'}},
    {'cmd': 'read_lines',
     'params': {'count': 'int=20 最新 N 行（快照）；follow 时为单次推送上限的初始值',
                'filter': 'str="" 正则过滤（RX/TX 文本）',
                'follow': 'bool=false true=持续推送新行（语义见响应 follow 字段）',
                'interval': 'float=0.5 推送节流秒（0.05~60，仅 follow）',
                'max_lines': 'int=200 单次推送行数上限（1~2000，仅 follow）'},
     'desc': '读最近记录（时间升序）；follow=true 进入持续推送，退出见 stop',
     'example': {'cmd': 'read_lines', 'count': 50, 'filter': 'err'}},
    {'cmd': 'stop', 'params': {},
     'desc': '退出 read_lines follow 推送（也可发一行纯文本 stop，或直接断开连接）',
     'example': {'cmd': 'stop'}},
    {'cmd': 'wave_snapshot', 'params': {'count': 'int=500 每通道最新 N 点（1~200000）'},
     'desc': '各通道最新 N 点波形数值 + 元信息（协议/总帧/暂停/显示点数/通道启用）',
     'example': {'cmd': 'wave_snapshot', 'count': 100}},
    {'cmd': 'spectrum_snapshot', 'params': {'count': 'int=0 频点数上限（0=全部；>0 截高频端最新 N 点）'},
     'desc': 'FFT 频点+dB 数组、主频、频谱参数（采样率/FFT长度/窗/RBW/峰值保持/平均）、'
             '瀑布最近帧（每频点 RGB）与累计采集时长',
     'example': {'cmd': 'spectrum_snapshot', 'count': 512}},
    {'cmd': 'xy_snapshot', 'params': {'count': 'int=512 每轴最新 N 点'},
     'desc': 'XY 相位图各窗口最新轨迹（x/y 数值数组）与窗口参数（源通道/量程/余晖/画法）',
     'example': {'cmd': 'xy_snapshot', 'count': 256}},
    {'cmd': 'modbus_recent', 'params': {'count': 'int=20 最近 N 条'},
     'desc': '最近 Modbus 事务摘要（请求/响应 HEX、字节数、成功与否）',
     'example': {'cmd': 'modbus_recent', 'count': 10}},
    {'cmd': 'stm32_status', 'params': {},
     'desc': 'STM32 页状态（页未挂接/未连接返回明确提示，不报错断链）',
     'example': {'cmd': 'stm32_status'}},
    {'cmd': 'settings_list', 'params': {},
     'desc': '关键设置一览（波特率/串口/协议/主题/显示点数/曲线填充/暂停等）',
     'example': {'cmd': 'settings_list'}},
    {'cmd': 'settings_get', 'params': {'key': 'str 设置键（见 settings_list；常用键有中英文别名）'},
     'desc': '读单个设置值',
     'example': {'cmd': 'settings_get', 'key': 'baud'}},
    {'cmd': 'settings_set',
     'params': {'key': 'str 可写键：baud/port/protocol/theme/points/fill/paused',
                'value': 'any 新值（bool/int/str，按键解析）'},
     'desc': '改设置并同步 UI 控件（下拉/复选框/按钮与内部状态一致）；'
             '波特率/串口在已连接时拒绝（先断开）；返回改前/改后值',
     'example': {'cmd': 'settings_set', 'key': 'baud', 'value': 921600}},
    {'cmd': 'set_protocol', 'params': {'protocol': 'FireWater/JustFloat/RawData'},
     'desc': '切换协议（清空波形缓冲；等价 settings_set key=protocol，向后兼容保留）',
     'example': {'cmd': 'set_protocol', 'protocol': 'JustFloat'}},
    {'cmd': 'wave_clear', 'params': {}, 'desc': '清空波形缓冲',
     'example': {'cmd': 'wave_clear'}},
    {'cmd': 'wave_sim', 'params': {'on': 'bool 开/关内置模拟数据源'},
     'desc': '模拟数据源开关（无需真实硬件即可测试波形/频谱链路）',
     'example': {'cmd': 'wave_sim', 'on': True}},
    {'cmd': 'record', 'params': {'on': 'bool 开始/停止'},
     'desc': '会话录制（原始字节流 JSONL，可回放）',
     'example': {'cmd': 'record', 'on': True}},
]


def help_response() -> dict:
    """help 命令响应：结构化命令表 + follow 语义（COMMANDS 单一事实来源）。

    注意 follow 语义块的键名是 follow_semantics（不是 follow）——壳层靠
    response['follow'] is True 识别 read_lines 推送模式的初始响应，不能撞名。
    """
    return {'ok': True, 'tool': APP_NAME, 'docs': 'AI_INTERFACE.md',
            'commands': [dict(c) for c in COMMANDS],
            'follow_semantics': dict(FOLLOW_DOC)}


@dataclass
class ControlAPI:
    """控制通道可调用的宿主能力集（全部可注入，便于离线测试）。

    扩展命令带缺省 None：纯协议测试只需基础九件套；GUI 主窗体全部注入。
    """
    status: Callable[[], dict]
    list_ports: Callable[[], list]
    send: Callable[..., dict]              # (text, hex, newline) -> dict
    read_lines: Callable[..., dict]        # (count, filter, since=-1) -> dict
    wave_snapshot: Callable[..., dict]     # (count) -> dict
    set_protocol: Callable[[str], dict]
    wave_clear: Callable[[], dict]
    wave_sim: Callable[[bool], dict]
    record: Callable[[bool], dict]
    # —— 扩展命令（缺省 None：GUI 主窗体注入；纯协议测试可不给）——
    spectrum_snapshot: Callable[..., dict] | None = None   # (count=0) -> dict
    xy_snapshot: Callable[..., dict] | None = None         # (count=512) -> dict
    modbus_recent: Callable[..., dict] | None = None       # (count=20) -> dict
    stm32_status: Callable[[], dict] | None = None
    settings_list: Callable[[], dict] | None = None
    settings_get: Callable[..., dict] | None = None        # (key) -> dict
    settings_set: Callable[..., dict] | None = None        # (key, value) -> dict


def _call_host(fn, *args, **kwargs):
    """调用宿主能力；未注入（纯协议测试）给出可读错误而不是 NoneType 崩栈。"""
    if fn is None:
        raise RuntimeError('该命令需要 GUI 主窗体宿主（当前 api 未注入此能力）')
    return fn(*args, **kwargs)


def handle_control_request(request_line: str, api: ControlAPI) -> dict:
    try:
        req = json.loads(request_line)
    except ValueError as exc:
        return {'ok': False, 'error': f'JSON 解析失败: {exc}'}
    if not isinstance(req, dict):
        return {'ok': False, 'error': '请求必须是 JSON 对象'}

    cmd = str(req.get('cmd', ''))
    try:
        if cmd == 'ping':
            return {'ok': True, 'tool': APP_NAME, 'control': 'on', 'docs': 'AI_INTERFACE.md'}
        if cmd == 'help':
            return help_response()
        if cmd == 'stop':
            # 无 follow 在途时也给出说明（真正的拦截在壳层 _consume_follow_stop）
            return {'ok': True, 'follow': False,
                    'note': '当前没有进行中的 follow 推送（stop 用于退出 read_lines 推送模式）'}
        if cmd == 'list_ports':
            return {'ok': True, 'ports': api.list_ports()}
        if cmd == 'status':
            return {'ok': True, **api.status()}
        if cmd == 'send':
            return api.send(text=str(req.get('text', '')),
                            hex=bool(req.get('hex', False)),
                            newline=str(req.get('newline', 'CRLF')))
        if cmd == 'read_lines':
            count = max(1, int(req.get('count', 20)))
            filt = str(req.get('filter', ''))
            if not bool(req.get('follow', False)):
                return api.read_lines(count=count, filter=filt)
            interval = float(req.get('interval', FOLLOW_DEFAULT_INTERVAL_S))
            interval = min(max(interval, FOLLOW_MIN_INTERVAL_S), FOLLOW_MAX_INTERVAL_S)
            max_lines = min(max(1, int(req.get('max_lines', FOLLOW_DEFAULT_MAX_LINES))),
                            FOLLOW_MAX_LINES_CAP)
            first = api.read_lines(count=count, filter=filt, since=-1)
            return {'ok': True, 'follow': True,
                    'lines': first.get('lines', []),
                    'cursor': first.get('cursor', -1),
                    'filter': filt, 'interval': interval, 'max_lines': max_lines,
                    'note': '已进入推送：出现新行时按 interval 推送；'
                            '发 {"cmd":"stop"} 或一行 stop 或断开退出'}
        if cmd == 'wave_snapshot':
            count = max(1, int(req.get('count', 500)))
            return api.wave_snapshot(count=count)
        if cmd == 'spectrum_snapshot':
            count = max(0, int(req.get('count', 0)))
            return _call_host(api.spectrum_snapshot, count=count)
        if cmd == 'xy_snapshot':
            count = max(1, int(req.get('count', 512)))
            return _call_host(api.xy_snapshot, count=count)
        if cmd == 'modbus_recent':
            count = max(1, int(req.get('count', 20)))
            return _call_host(api.modbus_recent, count=count)
        if cmd == 'stm32_status':
            return _call_host(api.stm32_status)
        if cmd == 'settings_list':
            return _call_host(api.settings_list)
        if cmd == 'settings_get':
            return _call_host(api.settings_get, key=str(req.get('key', '')))
        if cmd == 'settings_set':
            return _call_host(api.settings_set, key=str(req.get('key', '')),
                              value=req.get('value'))
        if cmd == 'set_protocol':
            return api.set_protocol(str(req.get('protocol', '')))
        if cmd == 'wave_clear':
            return api.wave_clear()
        if cmd == 'wave_sim':
            return api.wave_sim(bool(req.get('on', True)))
        if cmd == 'record':
            return api.record(bool(req.get('on', True)))
        return {'ok': False, 'error': f'未知命令: {cmd}（发送 {{"cmd":"help"}} 获取命令表）'}
    except Exception as exc:  # 命令执行异常 → 错误响应不断链
        return {'ok': False, 'error': str(exc)}


class ControlChannel(QtCore.QObject):
    """TCP 壳：QTcpServer + 每连接行缓冲；一条连接可连续发多条请求。

    follow 推送：read_lines follow=true 的响应带 cursor/interval/max_lines/filter，
    壳据此为该连接起 QTimer 轮询宿主 read_lines(since=游标)，只推新行；
    客户端发 {"cmd":"stop"} / 一行 stop / 断开连接即退出（重复 follow 请求=按新参数重启）。
    """

    def __init__(self, api: ControlAPI, port: int = 9101, parent=None):
        super().__init__(parent)
        self._api = api
        self._port = port
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._buffers: dict[object, bytearray] = {}
        self._follows: dict[object, dict] = {}

    @property
    def listening(self) -> bool:
        return self._server.isListening()

    @property
    def port(self) -> int:
        return self._server.serverPort()

    def start(self) -> bool:
        if self._server.isListening():
            return True
        return self._server.listen(QHostAddress.LocalHost, self._port)

    def stop(self):
        for state in self._follows.values():
            state['timer'].stop()
        self._follows = {}
        # 逐个 popitem 再拆链：_teardown_socket 的 abort() 会同步触发 disconnected
        # → _on_disconnected，必须保证同一条 socket 只 teardown 一次
        #（deleteLater 发两次 = C++ 对象析构两次 = access violation，已实测复现）
        while self._buffers:
            socket, _buf = self._buffers.popitem()
            try:
                self._teardown_socket(socket)
            except RuntimeError:
                pass  # socket 的 C++ 对象可能已被回收
        if self._server.isListening():
            self._server.close()

    @staticmethod
    def _teardown_socket(socket):
        """销毁一条连接：abort() 复位（丢弃未写出的缓冲、同步关闭 winsock 句柄），
        脱开 QTcpServer 父子关系后立即析构（所有权回到 Python，函数返回即析构）。
        不用 deleteLater——挂到下一轮事件循环的延迟析构在反复建/拆大量 MainWindow +
        TCP 服务的自测进程里会触发 Windows access violation（已实测，见交付记录）。
        只许调用一次（见 stop/_on_disconnected 的 _buffers 成员守卫）。"""
        socket.abort()
        QtCore.QObject.disconnect(socket)  # 摘掉 readyRead/disconnected 等连接（释放 lambda 引用）
        socket.setParent(None)     # 所有权移交 Python：本地引用消亡时立即析构
        del socket

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))

    def _on_disconnected(self, socket):
        self._stop_follow(socket)
        if self._buffers.pop(socket, None) is not None:
            # 只有仍登记在 _buffers 里（尚未 teardown）才销毁一次；
            # stop()/abort() 触发的重复 disconnected 信号在此守卫下成为空操作
            try:
                self._teardown_socket(socket)
            except RuntimeError:
                pass

    def _on_ready_read(self, socket):
        buf = self._buffers.setdefault(socket, bytearray())
        buf.extend(bytes(socket.readAll()))
        while True:
            nl = buf.find(b'\n')
            if nl < 0:
                break
            line = bytes(buf[:nl]).decode('utf-8', errors='replace').strip()
            del buf[:nl + 1]
            if not line:
                continue
            if self._consume_follow_stop(socket, line):
                continue  # follow 在途的退出指令已被消费
            response = handle_control_request(line, self._api)
            self._write_line(socket, response)
            if response.get('follow') is True:  # 严格 is True：help 等响应不进推送模式
                self._start_follow(socket, response)

    # ---------- follow 推送 ----------

    def _consume_follow_stop(self, socket, line: str) -> bool:
        """follow 在途时拦截退出指令；返回 True 表示该行已消费（不进命令分发）。"""
        if socket not in self._follows:
            return False
        wanted = line.lower() == 'stop'
        if not wanted:
            try:
                req = json.loads(line)
            except ValueError:
                req = None
            wanted = isinstance(req, dict) and str(req.get('cmd', '')) == 'stop'
        if wanted:
            self._stop_follow(socket)
            self._write_line(socket, {'ok': True, 'follow': False, 'note': '已停止推送'})
        return wanted

    def _start_follow(self, socket, response: dict):
        self._stop_follow(socket)  # 重复 follow 请求 = 按新参数重启
        timer = QtCore.QTimer(self)
        timer.setInterval(max(20, int(round(float(response.get('interval', 0.5)) * 1000))))
        state = {'timer': timer,
                 'cursor': response.get('cursor', -1),
                 'max_lines': int(response.get('max_lines', FOLLOW_DEFAULT_MAX_LINES)),
                 'filter': str(response.get('filter', ''))}
        timer.timeout.connect(lambda s=socket, st=state: self._follow_push(s, st))
        self._follows[socket] = state
        timer.start()

    def _stop_follow(self, socket):
        state = self._follows.pop(socket, None)
        if state:
            state['timer'].stop()

    def _follow_push(self, socket, state: dict):
        if socket not in self._buffers:  # 已断开（disconnected 清理前的兜底）
            self._stop_follow(socket)
            return
        try:
            result = self._api.read_lines(count=state['max_lines'],
                                          filter=state['filter'],
                                          since=state['cursor'])
        except Exception:  # 宿主异常/测试替身不支持 since → 结束推送，不刷错误
            self._stop_follow(socket)
            return
        lines = result.get('lines', []) if isinstance(result, dict) else []
        if lines:
            state['cursor'] = result.get('cursor', state['cursor'])
            self._write_line(socket, {'ok': True, 'follow': True, 'lines': lines,
                                      'cursor': state['cursor']})

    # ---------- 写行 ----------

    def _write_line(self, socket, response: dict):
        payload = (json.dumps(response, ensure_ascii=False,
                              separators=(',', ':')) + '\n').encode('utf-8')
        try:
            socket.write(payload)
        except RuntimeError:
            pass  # socket 的 C++ 对象可能已被回收
