from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Slot

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.updates import refresh_catalogs


class UpdateWorker(QObject):
    progress = Signal(str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, repository: SQLiteRepository, update_service: Callable = refresh_catalogs) -> None:
        super().__init__()
        self.repository = repository
        self.update_service = update_service

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(self.update_service(self.repository, progress=self.progress.emit))
        except Exception as error:
            self.failed.emit(str(error))
