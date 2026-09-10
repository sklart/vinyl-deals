"""One safe delivery path shared by CLI, GUI and the internal scheduler."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from vinyl_deals import notifications
from vinyl_deals.database.repository import SQLiteRepository

logger = logging.getLogger("vinyl_deals.alerts")


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
            logger.info("Delivered alert id=%s", row["id"])
        except Exception as error:
            # Provider exception text may contain sensitive response details.
            repository.mark_alert_error(int(row["id"]), f"delivery failed: {type(error).__name__}")
            failed += 1
            logger.warning("Telegram delivery failed for alert id=%s: %s", row["id"], type(error).__name__)
    return DeliveryResult(sent=sent, failed=failed)
