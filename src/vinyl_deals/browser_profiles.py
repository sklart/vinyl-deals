"""Safe helpers for user-operated browser access checks.

This module intentionally has no code for solving or evading a challenge.
It only supplies a persistent, portable Chromium profile directory and a
conservative detector used to decide whether the user still sees a challenge.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from vinyl_deals.runtime import portable_data_dir


_SOURCE = re.compile(r"^[a-z0-9_]+$")


def browser_profile_path(source: str, *, data_dir: Path | None = None) -> Path:
    """Return the source-specific portable Chromium profile path."""
    if not _SOURCE.fullmatch(source):
        raise ValueError("invalid store source")
    root = (data_dir or portable_data_dir()).resolve()
    path = (root / "browser_profiles" / source).resolve()
    if path.parent.parent != root:
        raise ValueError("browser profile escaped portable data directory")
    return path


def clear_browser_profile(source: str, *, data_dir: Path | None = None) -> None:
    """Delete only the explicit source profile; never touch global data."""
    path = browser_profile_path(source, data_dir=data_dir)
    if path.exists():
        shutil.rmtree(path)


def interactive_challenge_present(html: str) -> bool:
    """Recognise a visible access challenge, not an ordinary HTTP 403.

    Script/config blobs are ignored so a normal page mentioning hCaptcha in a
    third-party configuration does not accidentally demand a user action.
    """
    visible = re.sub(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", " ", html or "", flags=re.I | re.S).casefold()
    return any(token in visible for token in (
        "captcha", "servicepipe", "access-check", "js-challenge-loader",
        "checking your browser", "проверка безопасности", "interactive challenge",
    ))
