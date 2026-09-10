"""Reproducible onedir PyInstaller build for the two public entry points."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import PySide6
from vinyl_deals import __version__


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    commit = os.getenv("GITHUB_SHA") or subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    metadata = BUILD / "build_meta.json"
    metadata.write_text(json.dumps({"version": __version__, "commit": commit[:12], "build_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}), encoding="utf-8")
    data = f"{metadata}{os.pathsep}vinyl_deals"
    pyside = Path(PySide6.__file__).resolve().parent
    shiboken = pyside.parent / "shiboken6"
    portable = DIST / "VinylDeals"
    gui_dist = BUILD / "gui-dist"
    cli_dist = BUILD / "cli-dist"
    shutil.rmtree(portable, ignore_errors=True)
    shutil.rmtree(gui_dist, ignore_errors=True)
    shutil.rmtree(cli_dist, ignore_errors=True)
    common = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--specpath", str(BUILD), "--runtime-hook", str(ROOT / "tools" / "pyinstaller_pyside_runtime_hook.py"), "--add-data", data]
    for module in ("QtCore", "QtGui", "QtWidgets", "QtNetwork"):
        common.extend(["--hidden-import", f"PySide6.{module}"])
    for name in ("QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd", "QtNetwork.pyd", "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Network.dll", "pyside6.abi3.dll"):
        common.extend(["--add-binary", f"{pyside / name}{os.pathsep}PySide6"])
    for name in ("Shiboken.pyd", "shiboken6.abi3.dll"):
        common.extend(["--add-binary", f"{shiboken / name}{os.pathsep}shiboken6"])
    common.extend(["--add-binary", f"{pyside / 'plugins' / 'platforms' / 'qwindows.dll'}{os.pathsep}PySide6/plugins/platforms"])
    subprocess.check_call([*common, "--windowed", "--distpath", str(gui_dist), "--name", "vinyl-deals-gui", str(ROOT / "tools" / "gui_entry.py")], cwd=ROOT)
    shutil.copytree(gui_dist / "vinyl-deals-gui", portable)
    subprocess.check_call([*common, "--distpath", str(cli_dist), "--name", "vinyl-deals", str(ROOT / "tools" / "cli_entry.py")], cwd=ROOT)
    shutil.copy2(cli_dist / "vinyl-deals" / "vinyl-deals.exe", portable / "vinyl-deals.exe")
    shutil.make_archive(str(DIST / "VinylDeals-portable"), "zip", root_dir=DIST, base_dir="VinylDeals")


if __name__ == "__main__":
    main()
