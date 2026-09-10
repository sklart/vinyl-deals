"""One safe delivery path shared by CLI, GUI and the internal scheduler."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from vinyl_deals import notifications
from vinyl_deals.database.repository import SQLiteRepository


@dataclass(frozen=True)
class DeliveryResult:
    sent: int = 0
    failed: int = 0


def deliver_pending_alerts(repository: SQLiteRepository, *, notifier_factory: Callable[[], notifications.TelegramNotifier] | None = None) -> DeliveryResult:
    """Deliver alerts claimed atomically from SQLite, isolating each failure."""
    notifier = (notifier_factory or notifications.TelegramNotifier)()
    if not notifier.configured:
        raise RuntimeError("Telegram is not configured")
    sent = failed = 0
    for row in repository.claim_pending_alerts():
        try:
            notifier.send(notifications.format_alert(row["payload"]))
            repository.mark_alert_sent(int(row["id"]))
            sent += 1
        except Exception as error:
            # Provider exception text may contain sensitive response details.
            repository.mark_alert_error(int(row["id"]), f"delivery failed: {type(error).__name__}")
            failed += 1
    return DeliveryResult(sent=sent, failed=failed)
