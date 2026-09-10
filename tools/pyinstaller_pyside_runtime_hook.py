"""Expose both PySide6 and shiboken6 native DLL directories in a frozen app."""
from __future__ import annotations

import os
import sys
from pathlib import Path


_DLL_DIRECTORY_HANDLES: list[object] = []

if os.name == "nt" and hasattr(sys, "_MEIPASS"):
    directories = [
        Path(sys._MEIPASS),
        Path(sys._MEIPASS) / "PySide6",
        Path(sys._MEIPASS) / "shiboken6",
    ]
    values = [str(directory) for directory in directories if directory.is_dir()]
    for directory in values:
        # Keep handles alive: otherwise Windows removes directories from the
        # process DLL search path as soon as the handle is collected.
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(directory))
    if values:
        os.environ["PATH"] = os.pathsep.join([*values, os.environ.get("PATH", "")])
