"""Reproducible onedir PyInstaller build for the two public entry points."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from vinyl_deals import __version__


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    commit = os.getenv("GITHUB_SHA") or subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    metadata = BUILD / "build_meta.json"
    metadata.write_text(json.dumps({"version": __version__, "commit": commit[:12], "build_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}), encoding="utf-8")
    icon = ROOT / "src" / "vinyl_deals" / "assets" / "vinyl-deals.ico"
    data = f"{metadata}{os.pathsep}vinyl_deals"
    icon_data = f"{icon}{os.pathsep}vinyl_deals/assets"
    portable = DIST / "VinylDeals"
    gui_dist = BUILD / "gui-dist"
    cli_dist = BUILD / "cli-dist"
    # A portable installation owns ``data/``.  Rebuilding application binaries
    # must therefore never erase the complete directory (and a user's SQLite
    # database, settings or logs with it).  Only replace known build outputs.
    portable.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(portable / "_internal", ignore_errors=True)
    for executable in ("vinyl-deals-gui.exe", "vinyl-deals.exe"):
        (portable / executable).unlink(missing_ok=True)
    shutil.rmtree(gui_dist, ignore_errors=True)
    shutil.rmtree(cli_dist, ignore_errors=True)
    common = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--specpath",
        str(BUILD),
        "--runtime-hook",
        str(ROOT / "tools" / "pyinstaller_pyside_runtime_hook.py"),
        "--add-data",
        data,
        "--add-data",
        icon_data,
    ]
    for module in ("QtCore", "QtGui", "QtWidgets", "QtNetwork"):
        common.extend(["--hidden-import", f"PySide6.{module}"])
    # PyInstaller's PySide6 hooks collect the Qt extensions, their dependent
    # DLLs and the required platform plugins in one consistent layout.  Do not
    # add those files manually: duplicate Qt DLLs lead Windows to load an
    # incompatible copy and QtWidgets then fails before the GUI starts.
    subprocess.check_call([*common, "--windowed", "--icon", str(icon), "--distpath", str(gui_dist), "--name", "vinyl-deals-gui", str(ROOT / "tools" / "gui_entry.py")], cwd=ROOT)
    shutil.copytree(gui_dist / "vinyl-deals-gui", portable, dirs_exist_ok=True)
    # PyInstaller can collect ICU 78 from an unrelated dependency into
    # ``_internal``. Qt's Windows build links against the system ICU ABI and
    # requires unversioned exports such as ``ucnv_open``; ICU 78 provides only
    # versioned names and makes Qt6Core fail with WinError 127. Windows ships
    # the compatible system ICU, so do not distribute the unrelated copies.
    for library in ("icuuc.dll", "icudt78.dll"):
        (portable / "_internal" / library).unlink(missing_ok=True)
    subprocess.check_call([*common, "--distpath", str(cli_dist), "--name", "vinyl-deals", str(ROOT / "tools" / "cli_entry.py")], cwd=ROOT)
    shutil.copy2(cli_dist / "vinyl-deals" / "vinyl-deals.exe", portable / "vinyl-deals.exe")
    shutil.make_archive(str(DIST / "VinylDeals-portable"), "zip", root_dir=DIST, base_dir="VinylDeals")


if __name__ == "__main__":
    main()
