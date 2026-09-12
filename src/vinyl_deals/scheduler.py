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
    emit("Проверка watchlist...")
    refreshed = refresh_watchlist(repository, live_search_service=live_search_service, progress=emit)
    eligible = [
        int(entry["release_id"])
        for entry in repository.watchlist_entries(enabled_only=True)
        if entry["last_check_status"] in {"OK", "PARTIAL", "NEEDS_USER_ACTION"} and int(entry["last_fresh_offer_count"] or 0) > 0
    ]
    # Possible broad artist/title hits and cache fallbacks are deliberately
    # excluded: only detail-validated fresh evidence can create an alert.
    alerts = evaluate_watchlist(repository, release_ids=eligible, offer_ids=refreshed.fresh_confirmed_offer_ids)
    sent = failed = 0
    if auto_send:
        try:
            result = deliver_pending_alerts(repository)
            sent, failed = result.sent, result.failed
        except RuntimeError:
            # Telegram configuration is optional; an unavailable transport
            # must never roll back alerts produced by this cycle.
            pass
    logger.info("Scheduler cycle completed: watched=%s checked=%s partial=%s confirmed=%s possible=%s cached=%s alerts=%s sent=%s failed=%s", refreshed.watched, refreshed.checked, refreshed.partial, refreshed.fresh_confirmed_offers, refreshed.possible_offers, refreshed.cached_offers, len(alerts), sent, failed)
    return {"watched": refreshed.watched, "checked": refreshed.checked, "partial": refreshed.partial, "offers_updated": refreshed.offers_updated, "fresh_offers": refreshed.fresh_confirmed_offers, "possible_offers": refreshed.possible_offers, "cached_offers": refreshed.cached_offers, "alerts": len(alerts), "sent": sent, "failed": failed}


class CycleWorker(QThread):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, repository: SQLiteRepository, *, live_search_service: Callable = live_search, auto_send: bool = False) -> None:
        super().__init__()
        self.repository, self.live_search_service, self.auto_send = repository, live_search_service, auto_send
        # Retain the terminal state until the owner has handled it.  QThread's
        # ``finished`` notification can be delivered to the GUI before a
        # queued custom ``completed`` signal, especially under a busy Qt test
        # event loop.
        self.result: object | None = None
        self.error: str | None = None
        self.terminal_delivered = False

    def run(self) -> None:
        try:
            self.result = run_cycle(
                self.repository, live_search_service=self.live_search_service,
                auto_send=self.auto_send, progress=self.progress.emit,
            )
            self.completed.emit(self.result)
        except Exception as error:
            logger.exception("Scheduler cycle failed")
            self.error = str(error)
            self.failed.emit(self.error)


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
        self.worker = worker
        worker.progress.connect(self.status)
        worker.completed.connect(lambda result: self._completed(worker, result))
        worker.failed.connect(lambda message: self._failed(worker, message))
        worker.finished.connect(lambda: self._finished(worker))
        self.running_changed.emit(True)
        worker.start()
        return True

    def _completed(self, worker: CycleWorker, result: object) -> None:
        if worker.terminal_delivered:
            return
        worker.terminal_delivered = True
        self.cycle_completed.emit(result)

    def _failed(self, worker: CycleWorker, message: str) -> None:
        if worker.terminal_delivered:
            return
        worker.terminal_delivered = True
        self.cycle_failed.emit(message)

    def _finished(self, worker: CycleWorker) -> None:
        # An old queued ``finished`` signal must never clean up a new cycle.
        if worker is not self.worker:
            return
        # Do not let an early ``finished`` delivery swallow the custom result
        # signal. Deliver the retained outcome here if needed, then defer
        # object disposal until the current GUI event batch has completed.
        if not worker.terminal_delivered:
            if worker.error is not None:
                self._failed(worker, worker.error)
            elif worker.result is not None:
                self._completed(worker, worker.result)
        self.worker = None
        self.running_changed.emit(False)
        QTimer.singleShot(0, worker.deleteLater)

    def shutdown(self) -> None:
        self.timer.stop()
        self.shutting_down = True
        worker = self.worker
        if worker is not None:
            # The worker remains owned until this point. No termination is
            # used: a refresh/Telegram operation finishes cooperatively.
            worker.wait()
            self._finished(worker)
