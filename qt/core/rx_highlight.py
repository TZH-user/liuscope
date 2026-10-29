# -*- coding: utf-8 -*-
"""RX 行关键词高亮/告警（MobaXterm trigger 的简化版，纯逻辑离屏可测）。

两张表：alert（错误级，整行红+粗）优先于 watch（关注级，琥珀）——同一行同时
命中按 alert。匹配对象是剥掉 ANSI 转义后的可见文本（转义字节会把关键词拆散），
大小写不敏感；关键词按 逗号/空白 分隔写入设置键 log/kw_alert、log/kw_watch。
"""
from __future__ import annotations

from core.ansi_render import strip_ansi


def parse_keywords(raw: str) -> list[str]:
    """逗号/空白分隔 → 关键词表（去空、去重、保序；全角逗号兼容）。"""
    items: list[str] = []
    for chunk in (raw or '').replace('，', ',').split(','):
        for word in chunk.split():
            if word and word not in items:
                items.append(word)
    return items


def match_line(text: str, alert: list[str], watch: list[str]) -> str | None:
    """'alert' | 'watch' | None。两表皆空直接 None（零开销路径）。"""
    if not alert and not watch:
        return None
    visible = strip_ansi(text or '').lower()
    if any(k.lower() in visible for k in alert):
        return 'alert'
    if any(k.lower() in visible for k in watch):
        return 'watch'
    return None
