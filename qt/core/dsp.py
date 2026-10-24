# -*- coding: utf-8 -*-
"""波形 DSP：滤波与频谱 —— 移植自主线 serial_terminal_v6.ps1 的 C# WaveDsp 类。

语义逐字对齐（主线靠 C# 提速，这里靠 numpy）：
- 所有滤波先做 NaN/Inf 防护（用最近一个有效值填充，避免 NaN 扩散）
- 滑动平均：前缀和 O(n)，窗口内均值（不足窗口按已有样本数平均）
- 零相位低通：前向 EMA + 反向 EMA（filtfilt 思路），无滞后
"""
from __future__ import annotations

import numpy as np


def sanitize(x) -> np.ndarray:
    """NaN/Inf 防护：非有限值用最近一个有效值填充（开头没有就用 0）。"""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x
    valid = np.isfinite(x)
    if valid.all():
        return x.copy()
    idx = np.where(valid, np.arange(x.size), -1)
    np.maximum.accumulate(idx, out=idx)
    return np.where(idx >= 0, x[np.maximum(idx, 0)], 0.0)


def moving_average(x, window: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return np.zeros(0)
    s = sanitize(x)
    if window < 2:
        return s
    cs = np.concatenate(([0.0], np.cumsum(s)))
    i = np.arange(s.size)
    cnt = np.minimum(i + 1, window)
    lo = np.maximum(i + 1 - window, 0)
    return (cs[i + 1] - cs[lo]) / cnt


def zero_phase_lowpass(x, alpha: float) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return np.zeros(0)
    s = sanitize(x)
    if alpha >= 1.0:
        return s
    if alpha < 0.005:
        alpha = 0.005
    f = np.empty_like(s)
    acc = s[0]
    for i in range(s.size):
        acc += alpha * (s[i] - acc)
        f[i] = acc
    y = np.empty_like(s)
    acc = f[-1]
    for i in range(s.size - 1, -1, -1):
        acc += alpha * (f[i] - acc)
        y[i] = acc
    return y
