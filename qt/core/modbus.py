# -*- coding: utf-8 -*-
"""Modbus RTU 纯逻辑 —— 移植自主线 plugins/modbus_rtu/plugin.ps1 的核心函数。

对齐点：CRC16-Modbus（poly 0xA001 反射、初值 0xFFFF、帧尾低字节在前）、
组帧规则（01/02 数量 1~2000、03/04 数量 1~125、05 置位 0xFF00、06 值 0~65535、
15/16 列表写入）、异常码文本表、响应解析（CRC/从站匹配/异常位/线圈 LSB 打包）、
寄存器多格式解码（十进制/HEX/有符号/float32 ABCD/CDAB）、
Watch 合并轮询（同从站同功能码按地址排序、重叠或间隙≤4 合并、跨度上限）。
"""
from __future__ import annotations

import struct

from core.hexutil import bytes_to_hex_text


def get_modbus_crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = ((crc >> 1) ^ 0xA001) & 0xFFFF
            else:
                crc = (crc >> 1) & 0xFFFF
    # RTU 帧尾 CRC 低字节在前
    return bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def test_modbus_crc(frame: bytes) -> bool:
    if not frame or len(frame) < 4:
        return False
    crc = get_modbus_crc16(frame[:-2])
    return crc[0] == frame[-2] and crc[1] == frame[-1]


def parse_modbus_number(text: str) -> int | None:
    """十进制或 0x 十六进制 → int；非法返回 None（对齐主线）。"""
    t = (text or '').strip()
    if not t:
        return None
    if t[:2].lower() == '0x':
        try:
            return int(t[2:], 16)
        except ValueError:
            return None
    try:
        return int(t, 10)
    except ValueError:
        return None


def build_modbus_request(slave: int, fc: int, start: int, count: int,
                         value_text: str = '', value_list: list[str] | None = None):
    """组 RTU 请求帧（含 CRC）。返回 (frame|None, error)。"""
    error = ''
    payload = bytearray((slave & 0xFF, fc & 0xFF))
    start &= 0xFFFF
    payload += bytes(((start >> 8) & 0xFF, start & 0xFF))

    if fc in (1, 2):
        if count < 1 or count > 2000:
            error = '线圈数量须在 1~2000' if fc == 1 else '离散输入数量须在 1~2000'
    elif fc in (3, 4):
        if count < 1 or count > 125:
            error = '寄存器数量须在 1~125'
    elif fc == 5:
        # 0xFF00 = 置位，0x0000 = 复位
        v = (value_text or '').strip().lower()
        payload += bytes((0xFF, 0x00)) if v in ('1', 'on', 'true') else bytes((0x00, 0x00))
    elif fc == 6:
        v = parse_modbus_number(value_text)
        if v is None or v < 0 or v > 0xFFFF:
            error = '写入值须为 0~65535（支持 0x 十六进制）'
        else:
            payload += bytes(((v >> 8) & 0xFF, v & 0xFF))
    elif fc == 15:
        if not value_list:
            error = '写多线圈需在值里给 0/1 列表，如 1,0,1,1'
        elif len(value_list) > 1968:
            error = '线圈最多 1968 个'
        else:
            bit_count = len(value_list)
            payload += bytes(((bit_count >> 8) & 0xFF, bit_count & 0xFF))
            payload.append((bit_count + 7) // 8)
            byte_val = 0
            bit_pos = 0
            for bit_text in value_list:
                if bit_text.strip().lower() in ('1', 'on', 'true'):
                    byte_val |= 1 << bit_pos
                bit_pos += 1
                if bit_pos == 8:
                    payload.append(byte_val)
                    byte_val = 0
                    bit_pos = 0
            if bit_pos > 0:
                payload.append(byte_val)
    elif fc == 16:
        if not value_list:
            error = '写多寄存器需在值里给逗号分隔列表，如 100,0x2000,-5'
        elif len(value_list) > 123:
            error = '寄存器最多 123 个'
        else:
            reg_count = len(value_list)
            payload += bytes(((reg_count >> 8) & 0xFF, reg_count & 0xFF, reg_count * 2))
            for reg_text in value_list:
                rv = parse_modbus_number(reg_text)
                if rv is None:
                    error = f'无法解析寄存器值: {reg_text}'
                    break
                rv &= 0xFFFF
                payload += bytes(((rv >> 8) & 0xFF, rv & 0xFF))
    else:
        error = '不支持的功能码'

    if error:
        return None, error

    if 1 <= fc <= 4:
        payload += bytes(((count >> 8) & 0xFF, count & 0xFF))

    body = bytes(payload)
    return body + get_modbus_crc16(body), ''


def get_modbus_exception_text(code: int) -> str:
    table = {
        1: '01 非法功能', 2: '02 非法数据地址', 3: '03 非法数据值',
        4: '04 从站设备故障', 5: '05 已接收，处理中', 6: '06 从站忙',
        8: '08 存储奇偶校验错误', 10: '0A 网关不可达', 11: '0B 网关目标无响应',
    }
    return table.get(code, f'{code:02X} 未知异常')


def parse_modbus_response(resp: bytes, fc: int, slave: int, quantity: int) -> dict:
    """校验并解包响应。返回 {ok, error, exception, reg_bytes, coils, byte_count}。"""
    out = {'ok': False, 'error': '', 'exception': -1, 'reg_bytes': b'',
           'coils': None, 'byte_count': 0}
    if not resp or len(resp) < 4:
        out['error'] = '响应太短'
        return out
    if not test_modbus_crc(resp):
        out['error'] = 'CRC 校验失败'
        return out
    if resp[0] != (slave & 0xFF):
        out['error'] = f'从站地址不匹配（收到 {resp[0]}，期望 {slave & 0xFF}）'
        return out

    if resp[1] & 0x80:
        code = resp[2] if len(resp) >= 3 else -1
        out['exception'] = code
        out['error'] = f'从站异常: {get_modbus_exception_text(code)}'
        return out

    if 1 <= fc <= 4:
        if len(resp) < 5:
            out['error'] = '读响应太短'
            return out
        bc = resp[2]
        if len(resp) < 3 + bc + 2:
            out['error'] = '响应字节数不完整'
            return out
        out['byte_count'] = bc
        reg_bytes = resp[3:3 + bc]
        out['reg_bytes'] = reg_bytes
        if fc <= 2:
            # 线圈打包：每字节 LSB 在前
            coils = []
            for i in range(quantity):
                byte_index = i // 8
                if byte_index >= bc:
                    break
                coils.append((reg_bytes[byte_index] >> (i % 8)) & 0x01)
            out['coils'] = coils
    out['ok'] = True
    return out


def decode_modbus_registers(reg_bytes: bytes, mode: str) -> str:
    """寄存器字节流 → 按所选格式解码为可读文本（对齐主线解码格式）。"""
    if not reg_bytes:
        return ''
    parts = []
    reg_count = len(reg_bytes) // 2
    for i in range(reg_count):
        u16 = (reg_bytes[i * 2] << 8) | reg_bytes[i * 2 + 1]
        if mode == 'HEX':
            parts.append(f'0x{u16:04X}')
        elif mode == '有符号':
            parts.append(str(u16 - 0x10000 if u16 > 0x7FFF else u16))
        elif mode == 'float32(ABCD)':
            if i * 4 + 4 <= len(reg_bytes):
                sub = bytes((reg_bytes[i * 4 + 3], reg_bytes[i * 4 + 2],
                             reg_bytes[i * 4 + 1], reg_bytes[i * 4]))
                parts.append(f'{struct.unpack("<f", sub)[0]:.9g}')
        elif mode == 'float32(CDAB)':
            if i * 4 + 4 <= len(reg_bytes):
                sub = bytes((reg_bytes[i * 4 + 1], reg_bytes[i * 4],
                             reg_bytes[i * 4 + 3], reg_bytes[i * 4 + 2]))
                parts.append(f'{struct.unpack("<f", sub)[0]:.9g}')
        else:
            parts.append(str(u16))
    return ', '.join(parts)


# ---------- 实时监视（live watch） ----------

class WatchItem:
    """监视项：键 = 从站:功能码:起始地址；同键覆盖（改数量/格式 = 原位更新）。"""

    def __init__(self, slave: int, fc: int, start: int, count: int,
                 decode: str = '十进制', name: str = ''):
        self.slave = slave
        self.fc = fc
        self.start = start
        self.count = count
        self.decode = decode
        self.name = name
        self.last_value = ''
        self.last_status = ''

    @property
    def key(self) -> str:
        return f'{self.slave}:{self.fc}:{self.start}'


def merge_watch_groups(items: list[WatchItem]) -> list[dict]:
    """按 (从站, 功能码) 分组，组内地址排序后合并重叠/近邻（间隙≤4）区间。

    单次读上限：寄存器 125 / 线圈 2000。返回组列表
    [{slave, fc, start, count, items}]，items 引用原 WatchItem。
    """
    groups: list[dict] = []
    for item in sorted(items, key=lambda it: (it.slave, it.fc, it.start)):
        limit = 125 if item.fc > 2 else 2000
        merged = False
        for g in groups:
            if g['slave'] != item.slave or g['fc'] != item.fc:
                continue
            group_end = g['start'] + g['count']
            item_end = item.start + item.count
            # 重叠或间隙 ≤4 才合并；合并后总跨度不得超上限
            if item.start <= group_end + 4 and item_end - g['start'] <= limit:
                if item_end > group_end:
                    g['count'] = item_end - g['start']
                g['items'].append(item)
                merged = True
                break
        if not merged:
            groups.append({'slave': item.slave, 'fc': item.fc, 'start': item.start,
                           'count': item.count, 'items': [item]})
    return groups


def slice_watch_value(reg_bytes: bytes, fc: int, group_start: int, item: WatchItem) -> tuple[str, str]:
    """从合并读响应里按地址偏移切回单个监视项的值。返回 (text, raw_hex)。"""
    if fc <= 2:
        offset = item.start - group_start
        bits = []
        for i in range(item.count):
            idx = offset + i
            byte_index = idx // 8
            if byte_index >= len(reg_bytes):
                break
            bits.append((reg_bytes[byte_index] >> (idx % 8)) & 0x01)
        return ','.join(str(b) for b in bits), ''
    offset = (item.start - group_start) * 2
    length = item.count * 2
    if offset < 0 or offset + length > len(reg_bytes):
        return '—', ''
    sl = reg_bytes[offset:offset + length]
    return decode_modbus_registers(sl, item.decode), bytes_to_hex_text(sl)


def watch_flash_tone(old_text: str, new_text: str) -> str:
    """值变化闪烁语义：数值可比较 → 'up'/'down'，其余变化 → 'changed'，不变 → 'same'。"""
    if old_text == new_text:
        return 'same'
    try:
        old_num, new_num = float(old_text), float(new_text)
    except ValueError:
        return 'changed'
    if new_num > old_num:
        return 'up'
    if new_num < old_num:
        return 'down'
    return 'changed'
