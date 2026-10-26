# -*- coding: utf-8 -*-
"""AI 接入说明文档生成。

两份职责：
- get_ai_format_doc(protocol)：单协议数据格式接入说明（波形页「复制AI接入说明」用，
  文案移植主线 ui/wave_form.ps1 get_ai_format_doc 逐字口径）
- get_control_channel_doc(port)：AI 控制通道接入信息（主窗「帮助→复制 AI 接入信息」用），
  命令表源自 core.control_channel.COMMANDS 单一事实，与 help 命令响应永远同步
"""
from __future__ import annotations

import json

from core import APP_NAME

_DOCS = {
    'JustFloat': """请让单片机/设备按 JustFloat 二进制协议输出数据，我的串口工具（兼容 VOFA+ JustFloat）会实时绘制波形：
- 每帧 = N 个 float32（小端）+ 4 字节帧尾 0x00 0x00 0x80 0x7F
- 通道数 = 帧长/4，可以逐帧变化；帧尾丢失会自动重新同步
- 适合高采样率多通道，正文无需任何文本
- C 示例（2 通道）：
  float ch[2] = { value0, value1 };
  uint8_t tail[4] = { 0x00, 0x00, 0x80, 0x7F };
  Serial.write((uint8_t *)ch, sizeof(ch));
  Serial.write(tail, 4);""",
    'RawData': """请让单片机/设备按 RawData 协议输出数据，我的串口工具会实时绘制单通道波形：
- 每个原始字节（0~255）直接作为一个采样点，无任何帧头帧尾
- 单通道连续波形，适合 ADC 等高速采样流
- C 示例：Serial.write(adc_byte);""",
}

DEFAULT_DOC = """请让单片机/设备按 VOFA+ FireWater 协议输出数据，我的串口工具会实时绘制波形：
- 每行一个采样帧：ch0,ch1,ch2,...（英文逗号分隔，以 \\n 结尾）
- 支持负数、小数、科学计数法；单行最多 32 通道
- 普通日志文本行会被自动忽略，不影响解析（行首以 $ 开头的命令行也会跳过）
- C 示例（2 通道）：printf("%.4f,%.4f\\n", ch0, ch1);"""


def get_ai_format_doc(protocol: str) -> str:
    return _DOCS.get(protocol, DEFAULT_DOC)


def _format_params(params: dict) -> str:
    if not params:
        return '无参数'
    return '；'.join(f'{k}: {v}' for k, v in params.items())


def get_control_channel_doc(port: int) -> str:
    """一键复制 AI 接入信息：控制通道地址 + 全量命令表（含示例 JSON 行）
    + follow 推送语义 + 无头模式入口。命令表与 help 响应同一份事实（COMMANDS）。"""
    from core.control_channel import COMMANDS, FOLLOW_DOC  # 局部导入避免环

    lines = [
        f'【{APP_NAME} AI 接入】',
        '',
        f'1) AI 控制通道（与 GUI 共存）: TCP 127.0.0.1:{port}，'
        '一行一个 JSON 请求（\\n 结尾），逐行收 JSON 响应（一条连接可连续发多条）。',
        '   命令表（示例 JSON 行可直接发送）：',
    ]
    for c in COMMANDS:
        example = json.dumps(c['example'], ensure_ascii=False, separators=(',', ':'))
        lines.append(f'   - {c["cmd"]}（{_format_params(c["params"])}）：{c["desc"]}')
        lines.append(f'     示例: {example}')
    lines += [
        '',
        '   follow 持续推送语义（read_lines follow=true）:',
        f'   - 节流间隔 interval 默认 {FOLLOW_DOC["default_interval_s"]}s'
        f'（范围 {FOLLOW_DOC["interval_range_s"][0]}~{FOLLOW_DOC["interval_range_s"][1]}s）；'
        f'单次推送行数上限 max_lines 默认 {FOLLOW_DOC["default_max_lines"]}'
        f'（上限 {FOLLOW_DOC["max_lines_cap"]}）',
        f'   - 首次响应含最新 count 行快照与游标；之后仅推送新行（时间升序）：'
        f'{FOLLOW_DOC["push_format"]}',
        f'   - 退出方式: {FOLLOW_DOC["exit"]}',
        '',
        '2) 无头模式（AI 独占串口不开界面）: '
        'python qt/headless_main.py --port COM9 --baud 115200 --protocol FireWater '
        '--duration 5 --record',
        '   协议: FireWater/JustFloat/RawData；退出码 0 正常 | 2 错误模式命中 | '
        '3 期望失败 | 4 复位失败 | 5 烧录失败',
        '   完整命令表与返回结构详见仓库根 AI_INTERFACE.md。',
    ]
    return '\n'.join(lines)
