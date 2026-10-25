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


# ---------- 频谱仪（移植自主线 C# WaveDsp.MagnitudeSpectrum / ToDb / MaxBin） ----------

def magnitude_spectrum(x) -> tuple[np.ndarray, int]:
    """实数 FFT 幅度谱：取最长 2^k 段（≥32 点），加汉宁窗，返回前 n/2 个 bin 的
    单边幅度谱（已归一化）与实际参与 FFT 的点数 used。"""
    x = np.asarray(x, dtype=np.float64)
    if x.size < 32:
        return np.zeros(0), 0
    s = sanitize(x)
    n = 1
    while n * 2 <= s.size:
        n *= 2
    if n < 32:
        return np.zeros(0), 0
    window = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(n) / (n - 1)))
    spectrum = np.fft.rfft(s[:n] * window)
    half = n // 2
    mag = 2.0 * np.abs(spectrum[:half]) / n
    return mag, n


def to_db(mag: np.ndarray) -> np.ndarray:
    """幅度 → dB（20log10，满量程正弦 = 0 dB，底噪下限 -140）。"""
    mag = np.maximum(np.asarray(mag, dtype=np.float64), 1e-7)
    return np.maximum(20.0 * np.log10(mag), -140.0)


def update_max_hold(db: np.ndarray, max_hold: np.ndarray) -> np.ndarray:
    n = min(db.size, max_hold.size)
    np.maximum(max_hold[:n], db[:n], out=max_hold[:n])
    return max_hold


def update_ema(db: np.ndarray, avg: np.ndarray, alpha: float) -> np.ndarray:
    n = min(db.size, avg.size)
    avg[:n] += alpha * (db[:n] - avg[:n])
    return avg


def max_bin(db: np.ndarray, skip_bin: int = 1) -> int:
    """主频 bin（跳过 skip_bin 之前的 DC 附近）。"""
    idx = skip_bin if skip_bin < db.size else 0
    for i in range(idx, db.size):
        if db[i] > db[idx]:
            idx = i
    return idx


def color_map(t: float) -> tuple[int, int, int]:
    """瀑布色带：暗蓝 → 绿 → 黄橙 → 白（与主线 C# ColorMap 逐段一致）。"""
    t = min(1.0, max(0.0, t))
    if t < 0.30:
        k = t / 0.30
        return int(k * 10), int(k * 40), int(60 + k * 130)
    if t < 0.50:
        k = (t - 0.30) / 0.20
        return 10, int(40 + k * 180), int(190 - k * 60)
    if t < 0.70:
        k = (t - 0.50) / 0.20
        return int(10 + k * 100), int(220 - k * 40), int(130 - k * 100)
    if t < 0.88:
        k = (t - 0.70) / 0.18
        return int(110 + k * 145), int(180 + k * 60), 30
    k = (t - 0.88) / 0.12
    return 255, int(240 + k * 15), int(30 + k * 225)


def waterfall_row(db: np.ndarray, db_min: float, db_max: float, gamma: float = 1.0) -> np.ndarray:
    """一行 dB → RGB 色带（uint8[n,3]）。对数色标开启时 gamma=0.5 拉开低电平层次。"""
    db = np.asarray(db, dtype=np.float64)
    rng = db_max - db_min
    if rng <= 0:
        rng = 1.0
    if gamma <= 0:
        gamma = 1.0
    t = np.clip((db - db_min) / rng, 0.0, 1.0) ** gamma
    lut = np.array([color_map(tv) for tv in np.linspace(0, 1, 257)], dtype=np.uint8)
    return lut[(t * 256).astype(int)]
