# -*- coding: utf-8 -*-
"""AI 接入说明文档生成（移植主线 ui/wave_form.ps1 get_ai_format_doc 逐字文案）。

给 AI/同事的单协议数据格式说明，一键复制到剪贴板用。
"""
from __future__ import annotations

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
