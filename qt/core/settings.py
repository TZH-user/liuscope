# -*- coding: utf-8 -*-
"""轻量设置存取：gui_settings_qt.ini（Qt 支线自己的 INI，不与主线 gui_settings.txt 混用）。"""
from __future__ import annotations

import os

from PySide6 import QtCore

_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'gui_settings_qt.ini')


def _settings() -> QtCore.QSettings:
    return QtCore.QSettings(_SETTINGS_PATH, QtCore.QSettings.IniFormat)


def read_setting(key: str, default: str = '') -> str:
    value = _settings().value(key, default)
    return default if value is None else str(value)


def write_setting(key: str, value: str):
    s = _settings()
    s.setValue(key, value)
    s.sync()
