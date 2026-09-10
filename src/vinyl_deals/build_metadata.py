"""Build metadata with source-tree fallbacks and optional PyInstaller data."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

from vinyl_deals import __version__
from vinyl_deals.runtime import resource_path


def metadata() -> dict[str, str]:
    resource = resource_path("build_meta.json")
    if resource.is_file():
        try:
            return json.loads(resource.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    commit = os.getenv("GITHUB_SHA", "")
    if not commit:
        try:
            commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            commit = "unknown"
    return {"version": __version__, "commit": commit, "build_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
