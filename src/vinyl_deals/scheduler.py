"""In-process periodic refresh and alert cycle for the desktop application."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from vinyl_deals.alerts import evaluate_watchlist
from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.notifications import TelegramNotifier, format_alert
from vinyl_deals.updates import refresh_catalogs


ALLOWED_INTERVALS = (15, 30, 60, 180, 360)


def run_cycle(repository: SQLiteRepository, *, refresh_service: Callable = refresh_catalogs, auto_send: bool = False, progress: Callable[[str], None] | None = None) -> dict[str, int]:
    emit = progress or (lambda _message: None)
    refresh_service(repository, progress=emit)
    emit("Проверка watchlist...")
    alerts = evaluate_watchlist(repository)
    sent = 0
    if auto_send:
        notifier = TelegramNotifier()
        if notifier.configured:
            for row in repository.alerts(unsent_only=True):
                try:
                    notifier.send(format_alert(row["payload"]))
                    repository.mark_alert_sent(int(row["id"])); sent += 1
                except Exception as error:
                    repository.mark_alert_error(int(row["id"]), f"delivery failed: {type(error).__name__}")
    return {"alerts": len(alerts), "sent": sent}


class CycleWorker(QThread):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, repository: SQLiteRepository, *, refresh_service: Callable = refresh_catalogs, auto_send: bool = False) -> None:
        super().__init__(); self.repository = repository; self.refresh_service = refresh_service; self.auto_send = auto_send

    def run(self) -> None:
        try: self.completed.emit(run_cycle(self.repository, refresh_service=self.refresh_service, auto_send=self.auto_send, progress=self.progress.emit))
        except Exception as error: self.failed.emit(str(error))


class Scheduler(QObject):
    status = Signal(str)
    cycle_completed = Signal(object)
    cycle_failed = Signal(str)
    running_changed = Signal(bool)

    def __init__(self, repository: SQLiteRepository, *, refresh_service: Callable = refresh_catalogs, parent: QObject | None = None) -> None:
        super().__init__(parent); self.repository = repository; self.refresh_service = refresh_service; self.timer = QTimer(self); self.timer.timeout.connect(self.trigger); self.worker: CycleWorker | None = None; self.interval_minutes = 60; self.auto_send = False

    @property
    def running(self) -> bool: return bool(self.worker and self.worker.isRunning())

    def configure(self, *, enabled: bool, interval_minutes: int, auto_send: bool) -> None:
        if interval_minutes not in ALLOWED_INTERVALS: raise ValueError("unsupported scheduler interval")
        self.interval_minutes, self.auto_send = interval_minutes, auto_send
        if enabled: self.timer.start(interval_minutes * 60_000)
        else: self.timer.stop()

    def trigger(self) -> bool:
        if self.running: return False
        worker = CycleWorker(self.repository, refresh_service=self.refresh_service, auto_send=self.auto_send)
        self.worker = worker; worker.progress.connect(self.status); worker.completed.connect(self._completed); worker.failed.connect(self.cycle_failed); worker.finished.connect(self._finished); self.running_changed.emit(True); worker.start(); return True

    def _completed(self, result: object) -> None: self.cycle_completed.emit(result)
    def _finished(self) -> None:
        if self.worker: self.worker.deleteLater()
        self.worker = None; self.running_changed.emit(False)

    def shutdown(self, timeout_ms: int = 3000) -> None:
        self.timer.stop()
        if self.worker and self.worker.isRunning(): self.worker.wait(timeout_ms)
