"""Notification formatting and optional Telegram Bot API transport."""
from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _number(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _display(value: object) -> str:
    number = _number(value)
    return str(number) if number is not None else str(value)


def format_alert(payload: dict[str, object]) -> str:
    event = str(payload["event_type"]).replace("_", " ")
    lines = [f"🔥 {event}", "", f"{payload['artist']} — {payload['title']}"]
    metadata = " / ".join(str(value) for value in (payload.get("label"), payload.get("catalog_number"), payload.get("release_year")) if value)
    if metadata: lines.extend([metadata, ""])
    if payload.get("local_store"): lines.append("📍 РОСТОВ" if payload.get("city") == "Ростов-на-Дону" else "📍 ЛОКАЛЬНЫЙ МАГАЗИН")
    lines.extend([f"Магазин: {payload['store']}", f"Цена: {_display(payload['price'])} ₽"])
    if payload.get("effective_price_known"): lines.append(f"Итоговая цена: {_display(payload['effective_price'])} ₽")
    if payload.get("market_median") is not None: lines.append(f"Рынок: {_display(payload['market_median'])} ₽")
    discount = _number(payload.get("discount_pct"))
    if discount is not None: lines.append(f"Выгода: {discount:.0f}%")
    lines.extend(["", str(payload["url"])])
    if payload.get("discogs_url"): lines.append(f"Discogs: {payload['discogs_url']}")
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None, *, timeout_seconds: float = 15.0) -> None:
        self.token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID")
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.configured:
            raise RuntimeError("Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        body = urlencode({"chat_id": self.chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
        request = Request(f"https://api.telegram.org/bot{self.token}/sendMessage", data=body, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310: fixed Bot API origin
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError) as error:
            raise RuntimeError(f"Telegram delivery failed: {type(error).__name__}") from error
        if not payload.get("ok"):
            raise RuntimeError("Telegram delivery failed")
