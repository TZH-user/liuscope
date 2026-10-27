# -*- coding: utf-8 -*-
"""server_rk — LiuScope v2 板端守护进程（RK356x Linux + Python 3.10+）。

职责划分（对应 docs/设计_Linux设备互联_v2.md §2/§4）：
- protocol       JSON 行协议编解码 + hello/challenge/pair 配对流程（§4 控制通道）
- control        TCP 9201 控制服务：每连接一线程 + 命令分发表（依赖可注入）
- video          ffmpeg-rkmpp 子进程生命周期 + NAL→UDP 打包（§4 视频面）
- input_inject   python-uinput 键鼠注入（Qt 键码 → Linux input 事件码）
- serial_bridge  板端串口透传（serial_for_url，M2+ 收拢 M1 的 ser2net）

平台相关能力（uinput/ffmpeg/pyserial）全部延迟探测或可注入：真实采集/注入只在
板上生效，Windows 上仅用于跑单元测试（对应能力自动降级，不抛异常）。
"""

SERVER_VERSION = "0.2.0-m2"
