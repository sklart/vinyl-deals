"""In-process periodic refresh and alert cycle for the desktop application."""
from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from vinyl_deals.alerts import evaluate_watchlist
from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.alert_delivery import deliver_pending_alerts
from vinyl_deals.live_search import live_search
from vinyl_deals.watch_refresh import refresh_watchlist


ALLOWED_INTERVALS = (15, 30, 60, 180, 360)
logger = logging.getLogger("vinyl_deals.scheduler")


def run_cycle(repository: SQLiteRepository, *, live_search_service: Callable = live_search, auto_send: bool = False, progress: Callable[[str], None] | None = None) -> dict[str, int]:
    emit = progress or (lambda _message: None)
    logger.info("Scheduler cycle started")
    refreshed = refresh_watchlist(repository, live_search_service=live_search_service, progress=emit)
    emit("Проверка watchlist...")
    eligible = [
        int(entry["release_id"])
        for entry in repository.watchlist_entries(enabled_only=True)
        if entry["last_check_status"] in {"OK", "PARTIAL"} and int(entry["last_offer_count"] or 0) > 0
    ]
    alerts = evaluate_watchlist(repository, release_ids=eligible)
    sent = failed = 0
    if auto_send:
        try:
            result = deliver_pending_alerts(repository)
            sent, failed = result.sent, result.failed
        except RuntimeError:
            # Telegram configuration is optional; an unavailable transport
            # must never roll back alerts produced by this cycle.
            pass
    logger.info("Scheduler cycle completed: watched=%s checked=%s partial=%s offers=%s alerts=%s sent=%s failed=%s", refreshed.watched, refreshed.checked, refreshed.partial, refreshed.offers_updated, len(alerts), sent, failed)
    return {"watched": refreshed.watched, "checked": refreshed.checked, "partial": refreshed.partial, "offers_updated": refreshed.offers_updated, "alerts": len(alerts), "sent": sent, "failed": failed}


class CycleWorker(QThread):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, repository: SQLiteRepository, *, live_search_service: Callable = live_search, auto_send: bool = False) -> None:
        super().__init__(); self.repository = repository; self.live_search_service = live_search_service; self.auto_send = auto_send

    def run(self) -> None:
        try: self.completed.emit(run_cycle(self.repository, live_search_service=self.live_search_service, auto_send=self.auto_send, progress=self.progress.emit))
        except Exception as error:
            logger.exception("Scheduler cycle failed")
            self.failed.emit(str(error))


class Scheduler(QObject):
    status = Signal(str)
    cycle_completed = Signal(object)
    cycle_failed = Signal(str)
    running_changed = Signal(bool)

    def __init__(self, repository: SQLiteRepository, *, live_search_service: Callable = live_search, parent: QObject | None = None) -> None:
        super().__init__(parent); self.repository = repository; self.live_search_service = live_search_service; self.timer = QTimer(self); self.timer.timeout.connect(self.trigger); self.worker: CycleWorker | None = None; self.interval_minutes = 60; self.auto_send = False; self.enabled = False; self.shutting_down = False; self.start_guard: Callable[[], bool] = lambda: True

    @property
    def running(self) -> bool:
        # ``QThread.isRunning()`` can still be false in the brief interval
        # immediately after ``start()``. Reaping from this polling property
        # then deletes a worker before its ``run`` method gets CPU time (seen
        # on Python 3.13). ``finished`` is the authoritative lifecycle event.
        return self.worker is not None

    def configure(self, *, enabled: bool, interval_minutes: int, auto_send: bool) -> None:
        if interval_minutes not in ALLOWED_INTERVALS: raise ValueError("unsupported scheduler interval")
        self.interval_minutes, self.auto_send, self.enabled = interval_minutes, auto_send, enabled
        if enabled: self.timer.start(interval_minutes * 60_000)
        else: self.timer.stop()

    def trigger(self) -> bool:
        if self.shutting_down or self.running or not self.start_guard(): return False
        worker = CycleWorker(self.repository, live_search_service=self.live_search_service, auto_send=self.auto_send)
        self.worker = worker; worker.progress.connect(self.status); worker.completed.connect(self._completed); worker.failed.connect(self.cycle_failed); worker.finished.connect(lambda: self._finished(worker)); self.running_changed.emit(True); worker.start(); return True

    def _completed(self, result: object) -> None: self.cycle_completed.emit(result)
    def _finished(self, worker: CycleWorker) -> None:
        # An old queued ``finished`` signal must never clean up a new cycle.
        if worker is not self.worker:
            return
        worker.deleteLater()
        self.worker = None
        self.running_changed.emit(False)

    def shutdown(self) -> None:
        self.timer.stop()
        self.shutting_down = True
        worker = self.worker
        if worker is not None:
            # The worker remains owned until this point. No termination is
            # used: a refresh/Telegram operation finishes cooperatively.
            worker.wait()
            self._finished(worker)
