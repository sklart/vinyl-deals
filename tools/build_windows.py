"""Reproducible onedir PyInstaller build for the two public entry points."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import PySide6
from vinyl_deals import __version__


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    commit = os.getenv("GITHUB_SHA") or subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    metadata = BUILD / "build_meta.json"
    metadata.write_text(json.dumps({"version": __version__, "commit": commit[:12], "build_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}), encoding="utf-8")
    data = f"{metadata}{os.pathsep}vinyl_deals"
    pyside_binaries = f"{Path(PySide6.__file__).resolve().parent / '*.dll'}{os.pathsep}PySide6"
    common = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--specpath", str(BUILD), "--runtime-hook", str(ROOT / "tools" / "pyinstaller_pyside_runtime_hook.py"), "--collect-all", "PySide6", "--add-binary", pyside_binaries, "--add-data", data]
    subprocess.check_call([*common, "--windowed", "--name", "vinyl-deals-gui", str(ROOT / "tools" / "gui_entry.py")], cwd=ROOT)
    subprocess.check_call([*common, "--name", "vinyl-deals", str(ROOT / "tools" / "cli_entry.py")], cwd=ROOT)


if __name__ == "__main__":
    main()
