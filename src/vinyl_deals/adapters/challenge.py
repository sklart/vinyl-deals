"""Conservative detector for visible, interactive access challenges.

It classifies a response only. It does not open a browser, retain a browser
profile, or transfer browser state into an HTTP adapter.
"""
from __future__ import annotations

import re


def interactive_challenge_present(html: str) -> bool:
    """Recognise a visible access challenge, not an ordinary HTTP 403.

    Script/config blobs are ignored so a normal product page mentioning an
    anti-bot provider in third-party configuration does not demand action.
    """
    visible = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        " ",
        html or "",
        flags=re.I | re.S,
    ).casefold()
    return any(token in visible for token in (
        "captcha", "servicepipe", "access-check", "js-challenge-loader",
        "checking your browser", "проверка безопасности", "interactive challenge",
    ))
