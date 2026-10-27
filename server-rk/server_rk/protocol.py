# -*- coding: utf-8 -*-
r"""protocol.py — 控制通道 JSON 行协议 + 配对流程（设计稿 §4）。

协议风格与 LiuScope AI 通道一致（AI_INTERFACE.md §1）：TCP 上**一行请求一行响应**
（UTF-8，\n 结尾），一条连接可连续多轮；错误统一 {"ok": false, "error": "…"}。

配对流程（M2 明文版；TODO(M3)：明文比对换 PSK 派生，challenge 参与 HMAC）：

    PC → {"cmd":"hello","v":1,"nonce":"…"}
    板 → {"ok":true,"challenge":"<随机hex>","server_version":…,"nonce":"<回显>"}
    PC → {"cmd":"pair","code":"<配对码>","challenge":"<回显>"}
    板 → {"ok":true,"session":"<会话id>"}            # 会话建立，业务命令放行

challenge 必须回显且匹配：M2 用于把 pair 绑定到 hello（防直接盲试配对码），
M3 作为 PSK 派生的盐。会话建立前除 hello/pair 外的命令一律 {"ok":false,"error":"未配对"}。

配对码来源（优先级从高到低）：load_pairing_code() 显式参数
→ 环境变量 SERVER_RK_PAIRING_CODE → 包目录 pairing_code.txt（可选文件）
→ 出厂默认常量 DEFAULT_PAIRING_CODE。
"""
from __future__ import annotations

import json
import logging
import os
import secrets

from . import SERVER_VERSION

log = logging.getLogger(__name__)

# 出厂默认配对码；真板部署时放 pairing_code.txt 或设 SERVER_RK_PAIRING_CODE 覆盖
DEFAULT_PAIRING_CODE = "246810"
PAIRING_FILE_NAME = "pairing_code.txt"

# 配对放行的命令白名单（其余命令未配对一律拒绝）
PAIRING_COMMANDS = ("hello", "pair")


class ProtocolError(ValueError):
    """请求行不合法（坏 JSON / 非 JSON 对象 / 缺 cmd）。"""


# ---------- 行编解码 ----------

def encode_msg(msg: dict) -> bytes:
    """dict → 一行 JSON 字节（UTF-8，\n 结尾；中文不转义便于排障）。"""
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


def decode_line(raw) -> dict:
    """一行字节/文本 → 请求 dict；不合法抛 ProtocolError。"""
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("请求必须是 JSON 对象")
    if "cmd" not in obj:
        raise ProtocolError('缺少 "cmd" 字段')
    return obj


def ok_resp(**kw) -> dict:
    """成功响应：{"ok": true, **kw}。"""
    return {"ok": True, **kw}


def error_resp(text: str, **kw) -> dict:
    """错误响应（全协议统一）：{"ok": false, "error": …}。"""
    return {"ok": False, "error": text, **kw}


# ---------- 配对码配置 ----------

def load_pairing_code(explicit_path: str | None = None) -> str:
    """按优先级解析配对码：显式路径 > 环境变量 > 包目录文件 > 默认常量。

    TODO(M3)：配对码改为引导用，会话密钥由 PSK + challenge 派生（设计稿 §4）。
    """
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    env_code = os.environ.get("SERVER_RK_PAIRING_CODE")
    if env_code and env_code.strip():
        return env_code.strip()
    candidates.append(os.environ.get("SERVER_RK_PAIRING_FILE") or
                      os.path.join(os.path.dirname(os.path.abspath(__file__)), PAIRING_FILE_NAME))
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fp:
                code = fp.read().strip()
            if code:
                return code
        except OSError:
            continue  # 文件不存在/不可读 → 落到下一优先级
    return DEFAULT_PAIRING_CODE


# ---------- 配对状态机（每条控制连接一个实例）----------

class PairingSession:
    """hello → challenge → pair 会话状态机；gate() 是业务命令的门禁。

    纯逻辑、无 socket 依赖，便于单测直驱（见 test_server_rk.py）。
    """

    def __init__(self, code: str | None = None, nonce_bytes: int = 16):
        self._code = load_pairing_code() if code is None else str(code)
        self._nonce_bytes = nonce_bytes
        self._challenge: str | None = None
        self._session: str | None = None

    @property
    def code(self) -> str:
        return self._code

    @property
    def paired(self) -> bool:
        return self._session is not None

    @property
    def session_id(self) -> str | None:
        return self._session

    def gate(self, req: dict) -> dict | None:
        """未配对门禁：除 hello/pair 外一律拒绝；返回错误响应或 None（放行）。"""
        if self.paired or req.get("cmd") in PAIRING_COMMANDS:
            return None
        return error_resp("未配对")

    def on_hello(self, req: dict) -> dict:
        """处理 hello：校验协议版本，发 challenge（同时回显客户端 nonce 便于对端对账）。"""
        version = req.get("v", 1)
        if version != 1:
            return error_resp(f"不支持的协议版本: {version!r}")
        self._challenge = secrets.token_hex(self._nonce_bytes)
        log.info("hello：发挑战（client_nonce=%r）", req.get("nonce"))
        return ok_resp(challenge=self._challenge, server_version=SERVER_VERSION,
                       nonce=req.get("nonce"))

    def on_pair(self, req: dict) -> dict:
        """处理 pair：challenge 回显校验 → 配对码明文比对（M3 换 PSK 派生）。"""
        if self.paired:  # 重复 pair 幂等：返回既有会话
            return ok_resp(session=self._session, server_version=SERVER_VERSION)
        if not self._challenge:
            return error_resp('未握手：先发 {"cmd":"hello"}')
        if req.get("challenge") != self._challenge:
            return error_resp("challenge 不匹配")
        if str(req.get("code", "")) != self._code:
            log.warning("配对码错误（peer 猜码已记录）")
            return error_resp("配对码错误")
        self._session = secrets.token_hex(8)
        self._challenge = None
        log.info("配对成功，会话已建立")
        return ok_resp(session=self._session, server_version=SERVER_VERSION)
