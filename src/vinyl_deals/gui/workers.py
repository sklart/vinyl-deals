from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import StoreSearchQuery
from vinyl_deals.live_search import live_search
from vinyl_deals.updates import refresh_catalogs


class UpdateWorker(QThread):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, repository: SQLiteRepository, update_service: Callable = refresh_catalogs) -> None:
        super().__init__()
        self.repository = repository
        self.update_service = update_service

    def run(self) -> None:
        try:
            self.completed.emit(self.update_service(self.repository, progress=self.progress.emit))
        except Exception as error:
            self.failed.emit(str(error))


class TaskWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, task: Callable[[], object]) -> None:
        super().__init__(); self.task = task

    def run(self) -> None:
        try: self.completed.emit(self.task())
        except Exception as error: self.failed.emit(str(error))


class LiveSearchWorker(QThread):
    """Network-only federated search; every finished store reaches the GUI."""

    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, repository: SQLiteRepository, query: StoreSearchQuery, search_service: Callable = live_search) -> None:
        super().__init__()
        self.repository, self.query, self.search_service = repository, query, search_service

    def run(self) -> None:
        try:
            self.completed.emit(self.search_service(self.repository, self.query, progress=self.progress.emit))
        except Exception as error:
            self.failed.emit(str(error))
