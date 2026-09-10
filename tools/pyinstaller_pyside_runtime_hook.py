"""Make PySide6's bundled Qt DLL directory visible before Qt extension imports."""
from __future__ import annotations

import os
import sys
from pathlib import Path


# Each add_dll_directory() result removes its directory again when it is
# garbage-collected. Keep the handles alive until the frozen process exits.
_DLL_DIRECTORY_HANDLES: list[object] = []

if os.name == "nt" and hasattr(sys, "_MEIPASS"):
    for name in ("PySide6", "shiboken6"):
        directory = Path(sys._MEIPASS) / name
        if directory.is_dir():
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
