# -*- coding: utf-8 -*-
"""__main__.py — server-rk 守护进程入口（设计稿 §2：python -m server_rk）。

板端用法：
    python3 -m server_rk --control 9201 --port 9202 [--serial /dev/ttyS3] [--verbose]

Windows 上亦可启动做联通性自测：无 ffmpeg/uinput 时对应能力自动降级（不影响控制
通道与串口透传），单测见 server-rk/test_server_rk.py。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from . import SERVER_VERSION
from .control import ControlServer, Deps
from .input_inject import InputInjector
from .serial_bridge import SerialBridge
from .video import FFmpegVideoSource


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="server_rk", description="LiuScope v2 板端守护进程（RK356x，M2 骨架）")
    parser.add_argument("--port", type=int, default=9202,
                        help="视频 UDP 发送端口（默认 9202）")
    parser.add_argument("--control", type=int, default=9201,
                        help="控制 TCP 监听端口（默认 9201）")
    parser.add_argument("--serial", default="",
                        help="预开串口 URL（如 /dev/ttyS3 或 /dev/ttyUSB0；可选，也可由 serial_open 动态开）")
    parser.add_argument("--verbose", action="store_true", help="开启 DEBUG 日志")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("server_rk")

    deps = Deps(video=FFmpegVideoSource(), injector=InputInjector(),
                serial=SerialBridge(), video_udp_port=args.port)
    if args.serial:
        try:
            deps.serial.open(args.serial)
        except Exception as exc:
            log.warning("预开串口失败 %s: %s", args.serial, exc)

    server = ControlServer(deps, port=args.control)
    server.start()
    log.info("server-rk %s 就绪：控制 tcp://0.0.0.0:%d ｜ 视频 udp://<PC>:%d ｜ "
             "ffmpeg=%s ｜ uinput=%s", SERVER_VERSION, server.port, args.port,
             deps.video.is_available, deps.injector.is_available)

    try:
        while True:
            time.sleep(1)  # 守护循环：所有工作在 daemon 线程里，Ctrl+C 优雅退出
    except KeyboardInterrupt:
        log.info("收到退出信号")
    finally:
        server.stop()
        deps.serial.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
