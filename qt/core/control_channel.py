# -*- coding: utf-8 -*-
"""AI 控制通道：TCP JSON 行协议（对齐主线 core/control_channel.ps1 的命令族）。

- 监听 127.0.0.1:9101，一行一个 JSON 请求，一行一个 JSON 响应
- 命令：ping/help/status/list_ports/send/read_lines/wave_snapshot/
        set_protocol/wave_clear/wave_sim/record
- 协议处理是纯函数（api 为注入的调用集），网络壳在 ControlChannel
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from PySide6 import QtCore
from PySide6.QtNetwork import QHostAddress, QTcpServer

from core import APP_NAME


@dataclass
class ControlAPI:
    """控制通道可调用的宿主能力集（全部可注入，便于离线测试）。"""
    status: Callable[[], dict]
    list_ports: Callable[[], list]
    send: Callable[..., dict]              # (text, hex, newline) -> dict
    read_lines: Callable[..., dict]        # (count, filter) -> dict
    wave_snapshot: Callable[..., dict]     # (count) -> dict
    set_protocol: Callable[[str], dict]
    wave_clear: Callable[[], dict]
    wave_sim: Callable[[bool], dict]
    record: Callable[[bool], dict]


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
            return {
                'ok': True,
                'tool': APP_NAME,
                'docs': 'AI_INTERFACE.md',
                'commands': {
                    'ping': '存活探测',
                    'status': '连接/协议/帧数/录制状态',
                    'list_ports': '可用串口列表',
                    'send': '发送数据: text/hex/newline',
                    'read_lines': '读最近记录: count/filter(正则)',
                    'wave_snapshot': '最近波形: count',
                    'set_protocol': '切换协议: FireWater/JustFloat/RawData',
                    'wave_clear': '清空波形缓冲',
                    'wave_sim': '开关模拟数据源: on(true/false)',
                    'record': '会话录制: on',
                },
            }
        if cmd == 'list_ports':
            return {'ok': True, 'ports': api.list_ports()}
        if cmd == 'status':
            return {'ok': True, **api.status()}
        if cmd == 'send':
            return api.send(text=str(req.get('text', '')),
                            hex=bool(req.get('hex', False)),
                            newline=str(req.get('newline', 'CRLF')))
        if cmd == 'read_lines':
            count = max(1, int(req.get('count', 50)))
            return api.read_lines(count=count, filter=str(req.get('filter', '')))
        if cmd == 'wave_snapshot':
            count = max(1, int(req.get('count', 500)))
            return api.wave_snapshot(count=count)
        if cmd == 'set_protocol':
            return api.set_protocol(str(req.get('protocol', '')))
        if cmd == 'wave_clear':
            return api.wave_clear()
        if cmd == 'wave_sim':
            return api.wave_sim(bool(req.get('on', True)))
        if cmd == 'record':
            return api.record(bool(req.get('on', True)))
        return {'ok': False, 'error': f'未知命令: {cmd}'}
    except Exception as exc:  # 命令执行异常 → 错误响应不断链
        return {'ok': False, 'error': str(exc)}


class ControlChannel(QtCore.QObject):
    """TCP 壳：QTcpServer + 每连接行缓冲；一条连接可连续发多条请求。"""

    def __init__(self, api: ControlAPI, port: int = 9101, parent=None):
        super().__init__(parent)
        self._api = api
        self._port = port
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._buffers: dict[object, bytearray] = {}

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
        for socket in list(self._buffers):
            try:
                socket.disconnectFromHost()
            except RuntimeError:
                pass  # socket 的 C++ 对象可能已被回收
        self._buffers = {}
        if self._server.isListening():
            self._server.close()

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))

    def _on_disconnected(self, socket):
        self._buffers.pop(socket, None)
        try:
            socket.deleteLater()
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
            response = handle_control_request(line, self._api)
            payload = (json.dumps(response, ensure_ascii=False,
                                  separators=(',', ':')) + '\n').encode('utf-8')
            socket.write(payload)
