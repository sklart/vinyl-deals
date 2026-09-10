"""Optional user-level Windows Run-key integration; never requires elevation."""
from __future__ import annotations

import os
import sys


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "VinylDeals"


def command_for_current_gui() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m vinyl_deals.gui.app'


def _registry():
    if os.name != "nt":
        return None
    import winreg
    return winreg


def is_enabled() -> bool:
    registry = _registry()
    if registry is None:
        return False
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY) as key:
            registry.QueryValueEx(key, VALUE_NAME)
        return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    registry = _registry()
    if registry is None:
        raise RuntimeError("Автозапуск поддерживается только в Windows.")
    if enabled:
        with registry.CreateKey(registry.HKEY_CURRENT_USER, RUN_KEY) as key:
            registry.SetValueEx(key, VALUE_NAME, 0, registry.REG_SZ, command_for_current_gui())
        return
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
            registry.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return
