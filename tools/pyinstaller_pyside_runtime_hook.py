"""Make PySide6's bundled Qt DLL directory visible before Qt extension imports."""
from __future__ import annotations

import os
import sys
from pathlib import Path


if os.name == "nt" and hasattr(sys, "_MEIPASS"):
    for name in ("PySide6", "shiboken6"):
        directory = Path(sys._MEIPASS) / name
        if directory.is_dir():
            os.add_dll_directory(str(directory))
