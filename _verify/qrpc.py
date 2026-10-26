# -*- coding: utf-8 -*-
"""Shared helper for AI-channel JSON line protocol (self-written, no doc copy)."""
import json
import socket
import sys
import threading
import time

HOST = "127.0.0.1"
PORT = 9101


class Chan:
    """One TCP connection to the AI control channel."""

    def __init__(self, host=HOST, port=PORT, timeout=5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""

    def send_raw(self, data: bytes):
        self.sock.sendall(data)

    def send_line(self, obj):
        line = (obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)) + "\n"
        self.sock.sendall(line.encode("utf-8"))

    def read_line(self, timeout=None):
        if timeout is not None:
            self.sock.settimeout(timeout)
        while b"\n" not in self._buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("peer closed")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("utf-8", errors="replace")

    def rpc(self, obj, timeout=5.0):
        """Send one JSON request, return one parsed JSON response (per-line protocol)."""
        self.sock.settimeout(timeout)
        self.send_line(obj)
        return json.loads(self.read_line())

    def drain(self, seconds, sink):
        """Receive raw lines for a duration; append (monotonic_time, line) tuples to sink."""
        end = time.monotonic() + seconds
        self.sock.settimeout(0.2)
        while time.monotonic() < end:
            try:
                while True:
                    if b"\n" not in self._buf:
                        try:
                            chunk = self.sock.recv(65536)
                            if not chunk:
                                break
                            self._buf += chunk
                        except socket.timeout:
                            break
                    if b"\n" in self._buf:
                        line, self._buf = self._buf.split(b"\n", 1)
                        sink.append((time.monotonic(), line.decode("utf-8", errors="replace")))
                    else:
                        break
            except (ConnectionError, OSError):
                break
            time.sleep(0.01)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def rpc(obj, timeout=5.0):
    """One-shot request on a fresh connection."""
    c = Chan(timeout=timeout)
    try:
        return c.rpc(obj, timeout=timeout)
    finally:
        c.close()


def save(name, data):
    import io
    path = r"F:\软件仓库_新\开发软件\串口调试助手-V6\_verify\evidence" + "\\" + name
    with io.open(path, "w", encoding="utf-8") as f:
        if isinstance(data, (dict, list)):
            json.dump(data, f, ensure_ascii=False, indent=1)
        else:
            f.write(data)
    print("saved:", path)
    return path
