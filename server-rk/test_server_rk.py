# -*- coding: utf-8 -*-
"""server-rk 单元测试（直跑脚本，风格同 qt/selftest.py）。

运行（仓库根目录）：python server-rk/test_server_rk.py
全部纯逻辑/回环验证，不需要真板、ffmpeg、uinput：
- protocol  行编解码 + 配对全流程（错误配对码拒绝、未配对拒绝业务命令）
- control   命令分发表（DI fake 依赖）+ TCP 127.0.0.1 全链路（随机端口）
- video     build_args 快照 + packetize 对称性（模拟 PC 端拆包还原原流）+ 子进程生命周期（注入 fake Popen）
- serial    loop:// open/write/read/close 读写闭环
- input     Qt 键码→input 事件码映射（注入 fake 设备）+ 无 uinput 优雅降级
"""
from __future__ import annotations

import base64
import json
import logging
import os
import random
import socket
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)  # 允许从任意 cwd 直跑本脚本

try:  # Windows 控制台 GBK 兜底：中文输出不炸
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ---------- 测试用 fake 依赖 ----------

class _FakeVideo:
    def __init__(self, available=True):
        self.available = available
        self.started = None
        self.stopped = 0

    @property
    def is_available(self):
        return self.available

    def start(self, resolution=None, fps=None, bitrate=None, udp_target=None):
        if not self.available:
            return False, "ffmpeg 未找到（PATH 中无 'ffmpeg'），视频功能不可用"
        self.started = (resolution, fps, bitrate, udp_target)
        return True, "ffmpeg pid=11111"

    def stop(self):
        self.stopped += 1

    def describe(self):
        return {"running": self.started is not None, "available": self.available}


class _FakeInjector:
    def __init__(self):
        self.batches = []

    @property
    def is_available(self):
        return True

    def apply_events(self, events):
        self.batches.append(events)
        return len(events or []), 0


class _FakeProc:
    def __init__(self):
        self.pid = 4321
        self.terminated = False
        self.killed = False

    def poll(self):
        return None  # 恒"运行中"

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


class _FakeDevice:
    def __init__(self):
        self.calls = []

    def emit(self, event, value, **kw):
        self.calls.append((tuple(event), value))


def _make_deps():
    from server_rk.control import Deps
    from server_rk.input_inject import InputInjector
    from server_rk.serial_bridge import SerialBridge

    return Deps(video=_FakeVideo(), injector=_FakeInjector(),
                serial=SerialBridge(), video_udp_port=9202)


# ---------- protocol ----------

def test_protocol_codec():
    from server_rk import protocol as P

    raw = P.encode_msg({"cmd": "ping"})
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    assert P.decode_line(raw) == {"cmd": "ping"}
    assert P.decode_line('  {"cmd":"status"}\n') == {"cmd": "status"}
    for bad in (b"not json", b"[1,2]", b"{}"):  # 坏 JSON / 非对象 / 缺 cmd
        try:
            P.decode_line(bad)
            raise AssertionError("应抛 ProtocolError: %r" % bad)
        except P.ProtocolError:
            pass
    r = P.error_resp("boom", extra=1)
    assert r == {"ok": False, "error": "boom", "extra": 1}, r
    assert P.ok_resp(a=1) == {"ok": True, "a": 1}
    print("PROTOCOL CODEC OK")


def test_pairing_flow():
    from server_rk import protocol as P

    s = P.PairingSession(code="135790")
    assert not s.paired and s.code == "135790"

    # 未配对：业务命令一律 {"ok":false,"error":"未配对"}
    for cmd in ("ping", "status", "video_start", "video_stop", "video_params",
                "input", "serial_open", "serial_write", "serial_read", "close"):
        assert s.gate({"cmd": cmd}) == {"ok": False, "error": "未配对"}, cmd
    assert s.gate({"cmd": "hello"}) is None and s.gate({"cmd": "pair"}) is None

    # hello → challenge（回显 nonce）
    r = s.on_hello({"cmd": "hello", "v": 1, "nonce": "abc"})
    assert r["ok"] and len(r["challenge"]) >= 16 and r["nonce"] == "abc"
    assert r["server_version"]
    challenge = r["challenge"]

    # 未 hello 直接 pair → 拒绝
    s2 = P.PairingSession(code="135790")
    assert s2.on_pair({"cmd": "pair", "code": "135790"})["ok"] is False
    assert not s2.paired

    # 错误配对码 → 拒绝且仍未配对
    r = s.on_pair({"cmd": "pair", "code": "000000", "challenge": challenge})
    assert r["ok"] is False and "配对码" in r["error"] and not s.paired
    # challenge 不回显/不匹配 → 拒绝
    r = s.on_pair({"cmd": "pair", "code": "135790", "challenge": "deadbeef"})
    assert r["ok"] is False and not s.paired
    # 正确配对 → 会话建立
    r = s.on_pair({"cmd": "pair", "code": "135790", "challenge": challenge})
    assert r["ok"] and r.get("session") and s.paired and s.session_id == r["session"]
    # 配对后业务命令放行
    assert s.gate({"cmd": "serial_open"}) is None
    # 重复 pair 幂等：同一会话
    assert s.on_pair({"cmd": "pair"})["session"] == r["session"]

    # 配对码来源：环境变量覆盖（优先级高于默认常量）
    os.environ["SERVER_RK_PAIRING_CODE"] = " 998877 "
    try:
        assert P.PairingSession().code == "998877", "环境变量应去除空白后生效"
    finally:
        del os.environ["SERVER_RK_PAIRING_CODE"]
    # 环境变量未设 → 回落出厂默认
    assert P.PairingSession().code == P.DEFAULT_PAIRING_CODE
    print("PAIRING FLOW OK")


# ---------- control ----------

def test_control_dispatch():
    from server_rk import control as C
    from server_rk.protocol import PairingSession

    deps = _make_deps()
    s = PairingSession(code="135790")
    PEER = "192.0.2.7"

    def ask(line):
        resp, close = C.handle_line(deps, s, PEER, line)
        return resp, close

    # 未配对：video_start 被拒
    resp, close = ask('{"cmd":"video_start"}')
    assert resp == {"ok": False, "error": "未配对"} and close is False

    # 配对全流程（走 handle_line）
    resp, _ = ask('{"cmd":"hello","v":1,"nonce":"n1"}')
    assert resp["ok"] and resp["nonce"] == "n1"
    resp, _ = ask(json.dumps({"cmd": "pair", "code": "135790",
                              "challenge": resp["challenge"]}))
    assert resp["ok"] and resp["session"]

    # ping/status
    resp, _ = ask('{"cmd":"ping"}')
    assert resp["ok"] and resp["pong"]
    resp, _ = ask('{"cmd":"status"}')
    assert resp["ok"] and resp["paired"] and "video" in resp and "serial" in resp

    # video_start：target 缺省取控制连接对端 IP
    resp, _ = ask('{"cmd":"video_start","fps":30,"bitrate":"4M"}')
    assert resp["ok"] and resp["target"] == "192.0.2.7:9202", resp
    assert deps.video.started == (None, 30, "4M", "192.0.2.7:9202")
    resp, _ = ask('{"cmd":"video_stop"}')
    assert resp["ok"] and deps.video.stopped == 1
    resp, _ = ask('{"cmd":"video_params"}')
    assert resp["ok"] and resp["params"]["running"] is True

    # input 批 → 注入 fake
    resp, _ = ask('{"cmd":"input","events":[{"t":"key","key":65,"down":true},'
                  '{"t":"mouse_move","dx":1,"dy":2}]}')
    assert resp["ok"] and resp["applied"] == 2 and resp["skipped"] == 0
    assert deps.injector.batches[-1][0]["t"] == "key"

    # 串口透传（loop:// 回环：写进去立刻可读）
    resp, _ = ask('{"cmd":"serial_open","url":"loop://","baud":115200}')
    assert resp["ok"] and resp["url"] == "loop://"
    resp, _ = ask('{"cmd":"serial_write","data":"PING"}')
    assert resp["ok"] and resp["n"] == 4
    resp, _ = ask('{"cmd":"serial_read","count":64,"timeout":0.2}')
    assert resp["ok"] and resp["text"] == "PING" and resp["n"] == 4
    assert base64.b64decode(resp["data_b64"]) == b"PING"
    # 二进制写法：data_b64
    resp, _ = ask('{"cmd":"serial_write","data_b64":"%s"}'
                  % base64.b64encode(b"\x01\x02").decode("ascii"))
    assert resp["ok"] and resp["n"] == 2
    resp, _ = ask('{"cmd":"serial_read","count":8,"timeout":0.2}')
    assert base64.b64decode(resp["data_b64"]) == b"\x01\x02"
    deps.serial.close()

    # 未知命令 / 坏 JSON / 空行
    resp, _ = ask('{"cmd":"nope"}')
    assert resp["ok"] is False and "未知命令" in resp["error"]
    resp, _ = ask("oops")
    assert resp["ok"] is False
    assert ask("   \n") == (None, False)

    # close → 回 ok 后要求断开
    resp, close = ask('{"cmd":"close"}')
    assert resp["ok"] and resp["bye"] and close is True

    # handler 异常兜底（serial 未开就 read）
    resp, close = ask('{"cmd":"serial_read"}')
    assert resp["ok"] is False and "serial_read 执行异常" in resp["error"]

    # ffmpeg 不可用的 video_start 明确报错
    deps2 = _make_deps()
    deps2.video = _FakeVideo(available=False)
    s2 = PairingSession(code="135790")
    r1 = C.handle_line(deps2, s2, PEER, '{"cmd":"hello"}')
    resp, _ = C.handle_line(deps2, s2, PEER,
                            json.dumps({"cmd": "pair", "code": "135790",
                                        "challenge": r1[0]["challenge"]}))
    assert resp["ok"]
    resp, _ = C.handle_line(deps2, s2, PEER, '{"cmd":"video_start"}')
    assert resp["ok"] is False and "ffmpeg" in resp["error"], resp
    print("CONTROL DISPATCH OK")


def test_control_tcp():
    """真实 socket 全链路：127.0.0.1 随机端口，行协议 + 配对 + close 断开。"""
    from server_rk import control as C

    srv = C.ControlServer(_make_deps(), host="127.0.0.1", port=0,
                          pairing_code="246810")
    srv.start()
    assert srv.port > 0
    try:
        with socket.create_connection(("127.0.0.1", srv.port), timeout=3) as conn:
            f = conn.makefile("rwb")

            def ask(line):
                f.write((line + "\n").encode("utf-8"))
                f.flush()
                return json.loads(f.readline().decode("utf-8"))

            assert ask('{"cmd":"ping"}') == {"ok": False, "error": "未配对"}
            r = ask('{"cmd":"hello","v":1,"nonce":"x"}')
            assert r["ok"] and r["nonce"] == "x"
            r = ask(json.dumps({"cmd": "pair", "code": "246810",
                                "challenge": r["challenge"]}))
            assert r["ok"] and r["session"]
            r = ask('{"cmd":"ping"}')
            assert r["ok"] and r["pong"]
            r = ask('{"cmd":"close"}')
            assert r["ok"] and r["bye"]
            assert f.readline() == b"", "close 后服务端应主动断开（EOF）"
    finally:
        srv.stop()
    print("CONTROL TCP OK")


# ---------- video ----------

def test_video_build_args():
    from server_rk import video as V

    src = V.FFmpegVideoSource(ffmpeg_path="ffmpeg", finder=lambda: "ffmpeg")
    # 快照断言：设计稿 §5.3 命令行形状（kmsgrab → nv12 → h264_rkmpp 无 B 帧 → udp）
    args = src.build_args(resolution=None, fps=30, bitrate="4M",
                          udp_target="192.168.1.50:9202")
    assert args == [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-f", "kmsgrab", "-i", "-",
        "-vf", "hwdownload,format=nv12",
        "-c:v", "h264_rkmpp", "-bf", "0", "-g", "60", "-b:v", "4M",
        "-f", "h264", "udp://192.168.1.50:9202",
    ], args
    args2 = src.build_args(resolution=(1280, 720), fps=60, bitrate="8M",
                           udp_target="192.168.1.50:9202")
    assert args2[args2.index("-s") + 1] == "1280x720"
    assert args2[args2.index("-g") + 1] == "120", "GOP = fps*2"
    assert args2[args2.index("-b:v") + 1] == "8M"
    assert src.is_available
    try:
        src.build_args(udp_target=None)
        raise AssertionError("缺 target 应抛 ValueError")
    except ValueError:
        pass
    print("VIDEO BUILD_ARGS OK")


def test_video_lifecycle():
    from server_rk import video as V

    procs = []

    def factory(args, **kw):
        proc = _FakeProc()
        procs.append((args, proc))
        return proc

    src = V.FFmpegVideoSource(ffmpeg_path="ffmpeg", finder=lambda: "ffmpeg",
                              popen_factory=factory)
    ok, msg = src.start(fps=30, bitrate="4M", udp_target="127.0.0.1:9202")
    assert ok and procs[0][1].pid == 4321 and src.describe()["running"]
    # 重复 start 拒绝
    ok2, _ = src.start(udp_target="127.0.0.1:9202")
    assert not ok2 and len(procs) == 1
    src.stop()
    assert procs[0][1].terminated and not src.describe()["running"]
    # 不可用路径：finder 找不到 ffmpeg → is_available False → start 明确失败
    src2 = V.FFmpegVideoSource(finder=lambda: None)
    assert not src2.is_available
    ok3, msg3 = src2.start(udp_target="1.2.3.4:9202")
    assert not ok3 and "ffmpeg" in msg3
    # find_ffmpeg 在本机要么返回路径要么 None（类型契约）
    assert V.find_ffmpeg() is None or isinstance(V.find_ffmpeg(), str)
    print("VIDEO LIFECYCLE OK")


def test_packetize_symmetry():
    from server_rk import video as V

    rng = random.Random(20261026)
    nal = bytes(rng.randrange(256) for _ in range(3500))  # 跨 3 个 mtu=1200 分片

    # packetize → 模拟 PC 端 H264UdpSource 拆包 → 还原原流
    packets, nxt = V.packetize(nal, seq=4294967294, ts_ms=123456, mtu=1200,
                               keyframe=True)
    assert len(packets) == 3
    assert nxt == 1, "seq 每 UDP 包 +1，u32 回绕（2^32-2 + 3 → 1）"

    buf, saw_keyframe, seqs = b"", False, []
    for pkt in packets:
        assert len(pkt) <= 9 + 1200
        seq, ts, flags = struct.unpack(">IIB", pkt[:9])
        assert ts == 123456
        seqs.append(seq)
        saw_keyframe = saw_keyframe or bool(flags & V.FLAG_KEYFRAME)
        buf = pkt[9:] if flags & V.FLAG_FRAG_START else buf + pkt[9:]
    assert buf == nal, "PC 端按首包起/末包止拼接应还原原 NAL"
    assert saw_keyframe and seqs == [4294967294, 4294967295, 0]

    # 单包小 NAL：首末标志同置，keyframe 标志默认不置
    small = b"\x65\x88\x84\x00"
    pkts, nxt2 = V.packetize(small, seq=0, ts_ms=0)
    assert len(pkts) == 1 and nxt2 == 1
    flags = pkts[0][8]
    assert flags & V.FLAG_FRAG_START and flags & V.FLAG_FRAG_END
    assert not flags & V.FLAG_KEYFRAME
    # 空 NAL：零包零消耗
    assert V.packetize(b"", 5, 0) == ([], 5)
    print("PACKETIZE OK")


# ---------- serial ----------

def test_serial_bridge():
    from server_rk.serial_bridge import SerialBridge

    b = SerialBridge()
    assert not b.is_open and b.describe() == {"open": False, "url": None, "baud": None}
    info = b.open("loop://", baud=115200, timeout=0.2)
    assert info["url"] == "loop://" and info["baud"] == 115200 and b.is_open

    payload = "HELLO-LOOP\n".encode("utf-8")
    assert b.write(payload) == len(payload)
    assert b.read(64, timeout=0.2) == payload, "loop:// 写进去的字节立刻回读"

    assert b.describe()["open"]
    b.open("loop://", baud=9600)  # 重开 = 换波特率
    assert b.baud == 9600
    b.write(b"ok")
    assert b.read(8, timeout=0.2) == b"ok"
    b.close()
    assert not b.is_open and b.url is None

    for fn in (lambda: b.write(b"x"), lambda: b.read(8)):  # 未打开时明确报错
        try:
            fn()
            raise AssertionError("未打开应抛 RuntimeError")
        except RuntimeError:
            pass
    print("SERIAL BRIDGE OK")


# ---------- input ----------

def test_input_inject_mapping():
    from server_rk import input_inject as I

    dev = _FakeDevice()
    inj = I.InputInjector(device_factory=lambda events, name: dev)
    assert inj.is_available
    applied, skipped = inj.apply_events([
        {"t": "key", "key": 0x41, "down": True},        # A → KEY_A(30) down
        {"t": "key", "key": 0x41, "down": False},       # A 松开
        {"t": "key", "key": 0x01000004},                # Return → KEY_ENTER(28)
        {"t": "key", "key": 0x01000000, "down": True},  # Esc → KEY_ESC(1)
        {"t": "key", "key": 0x01000012, "down": True},  # Left → KEY_LEFT(105)
        {"t": "key", "key": 0x01000021, "down": True},  # Ctrl → KEY_LEFTCTRL(29)
        {"t": "key", "key": 0x30},                      # '0' → KEY_0(11)
        {"t": "mouse_move", "dx": 12, "dy": -3},
        {"t": "mouse_button", "btn": "left", "down": True},
        {"t": "wheel", "dy": 1},
        {"t": "key", "key": 0x1234},                    # 未知键 → skipped + 日志
        {"t": "mystery"},                               # 未知类型 → skipped
    ])
    assert applied == 10 and skipped == 2, (applied, skipped)
    calls = dev.calls
    assert calls[0] == ((I.EV_KEY, 30), 1) and calls[1] == ((I.EV_KEY, 30), 0)
    assert ((I.EV_KEY, 28), 1) in calls
    assert ((I.EV_KEY, 1), 1) in calls
    assert ((I.EV_KEY, 105), 1) in calls
    assert ((I.EV_KEY, 29), 1) in calls
    assert ((I.EV_KEY, 11), 1) in calls
    assert ((I.EV_REL, I.REL_X), 12) in calls and ((I.EV_REL, I.REL_Y), -3) in calls
    assert ((I.EV_KEY, I.BTN_LEFT), 1) in calls
    assert ((I.EV_REL, I.REL_WHEEL), 1) in calls
    print("INPUT INJECT MAPPING OK")


def test_input_inject_degraded():
    """无 uinput 环境（Windows / 未装权限的板）：is_available False，apply 只记日志不抛。"""
    from server_rk import input_inject as I

    inj = I.InputInjector()
    if inj.is_available:
        print("INPUT INJECT DEGRADED OK（本机装了 uinput，降级用例跳过）")
        return
    assert not inj.is_available
    with self_assert_logs("server_rk.input_inject"):
        applied, skipped = inj.apply_events([{"t": "key", "key": 0x41, "down": True},
                                             {"t": "unknown", "x": 1}])
    assert applied == 0 and skipped == 2, (applied, skipped)
    print("INPUT INJECT DEGRADED OK")


class self_assert_logs:
    """轻量 assertLogs（直跑脚本无 unittest 用例上下文）；临时把目标 logger 调到 DEBUG。"""

    def __init__(self, logger_name):
        self.logger_name = logger_name
        self.records = []

    def __enter__(self):
        self.logger = logging.getLogger(self.logger_name)
        self._old_level = self.logger.level
        self._old_propagate = self.logger.propagate
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False  # 捕获到的记录不再透传到根 handler（避免测试输出噪音）
        self.handler = _ListHandler(self.records)
        self.logger.addHandler(self.handler)
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self._old_level)
        self.logger.propagate = self._old_propagate
        assert self.records, "应有日志记录（降级/未知键告警）"
        return False


class _ListHandler(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.sink = sink

    def emit(self, record):
        self.sink.append(record)


# ---------- 入口 ----------

def run_tests() -> int:
    logging.basicConfig(level=logging.CRITICAL)  # 静默常规日志（异常路径的 traceback 属预期行为）；被测告警走 self_assert_logs
    test_protocol_codec()
    test_pairing_flow()
    test_control_dispatch()
    test_control_tcp()
    test_video_build_args()
    test_video_lifecycle()
    test_packetize_symmetry()
    test_serial_bridge()
    test_input_inject_mapping()
    test_input_inject_degraded()
    print("SERVER-RK TESTS OK")
    return 0


if __name__ == "__main__":
    sys.exit(run_tests())
