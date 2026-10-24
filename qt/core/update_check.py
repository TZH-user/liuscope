# -*- coding: utf-8 -*-
"""检测更新 —— 移植自主线 core/serial_io.ps1 的 get_version_tuple / compare_app_versions / check_app_update。

网络失败不抛错，调用方只看 ok 字段（与主线同语义）。
版本号按点分段取数字比大小，'0.2.0-qt' 与 'v1.10.0' 都能比。
"""
from __future__ import annotations

import json
import re
import urllib.request

REPO_API_URL = 'https://api.github.com/repos/TZH-user/serialscope/releases/latest'
RELEASES_PAGE = 'https://github.com/TZH-user/serialscope/releases/latest'


def get_version_tuple(version_text: str) -> tuple[int, int, int]:
    """'v1.2.10' / '1.2.10' / '0.2.0-qt' → (1,2,10)；非数字段按 0 处理。"""
    nums = []
    text = (version_text or '').strip()
    if text[:1].lower() == 'v':
        text = text[1:]
    for part in text.split('.'):
        m = re.search(r'\d+', part)
        nums.append(int(m.group()) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def compare_app_versions(left: str, right: str) -> int:
    """返回 -1（left < right）/ 0（相等）/ 1（left > right）。"""
    a = get_version_tuple(left)
    b = get_version_tuple(right)
    for i in range(3):
        if a[i] < b[i]:
            return -1
        if a[i] > b[i]:
            return 1
    return 0


def check_app_update(current_version: str, url: str = REPO_API_URL,
                     timeout: int = 10, opener=None) -> dict:
    """查 GitHub 最新 Release 并与当前版本比较。

    opener 可注入（测试用假源）；返回
    {ok, has_update, current, latest, page_url, note}。
    """
    result = {'ok': False, 'has_update': False, 'current': current_version,
              'latest': '', 'page_url': RELEASES_PAGE, 'note': ''}
    try:
        req = urllib.request.Request(
            url, headers={'User-Agent': 'serialscope-qt',
                          'Accept': 'application/vnd.github+json'})
        opener_fn = opener or urllib.request.urlopen
        with opener_fn(req, timeout=timeout) as raw:
            release = json.loads(raw.read().decode('utf-8'))
        result['latest'] = str(release.get('tag_name', ''))
        if compare_app_versions(current_version, result['latest']) < 0:
            result['has_update'] = True
            result['note'] = str(release.get('body') or '')
        result['ok'] = True
    except Exception as exc:
        result['note'] = str(exc)
    return result
