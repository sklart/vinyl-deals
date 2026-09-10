"""Windows-oriented production runtime helpers, independent of GUI business logic."""
from __future__ import annotations

import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import Callable

from vinyl_deals.database.repository import SQLiteRepository


APP_NAME = "VinylDeals"
DATABASE_NAME = "vinyl_deals.sqlite3"


def app_data_dir(environ: dict[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    root = env.get("LOCALAPPDATA")
    return Path(root) / APP_NAME if root else Path.home() / "AppData" / "Local" / APP_NAME


def application_database_path(environ: dict[str, str] | None = None) -> Path:
    return app_data_dir(environ) / DATABASE_NAME


def resource_path(name: str) -> Path:
    """Find a bundled PyInstaller data file or a source-tree resource."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root) / "vinyl_deals" / name
    return Path(__file__).resolve().parent / name


def migrate_legacy_database(target: Path, candidates: tuple[Path, ...] = ()) -> bool:
    """Copy, never move, a prior local database if app-data has none yet."""
    if target.exists():
        return False
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            return True
    return False


def prepare_application_data(*, legacy_candidates: tuple[Path, ...] = (), environ: dict[str, str] | None = None) -> Path:
    directory = app_data_dir(environ)
    directory.mkdir(parents=True, exist_ok=True)
    logs = directory / "logs"
    logs.mkdir(exist_ok=True)
    database = directory / DATABASE_NAME
    migrate_legacy_database(database, legacy_candidates)
    # Verifies writability and applies all SQLite migrations before a window is shown.
    SQLiteRepository(database).initialize()
    return database


class _SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for value in (os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")):
            if value:
                message = message.replace(value, "[redacted]")
        record.msg, record.args = message, ()
        return True


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for value in (os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")):
            if value:
                rendered = rendered.replace(value, "[redacted]")
        return rendered


def configure_logging(directory: Path | None = None, *, max_bytes: int = 1_000_000, backup_count: int = 5) -> logging.Logger:
    logs = (directory or app_data_dir()) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("vinyl_deals")
    logger.setLevel(logging.INFO)
    if not any(isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == logs / "app.log" for handler in logger.handlers):
        handler = RotatingFileHandler(logs / "app.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(_SecretFilter())
        logger.addHandler(handler)
    return logger


class SingleInstanceLock:
    """Atomic per-user lock; a second GUI process exits cleanly."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._owned = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._is_stale():
                    self.path.unlink(missing_ok=True)
                    continue
                return False
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(str(os.getpid()))
            self._owned = True
            return True
        return False

    def _is_stale(self) -> bool:
        try:
            pid = int(self.path.read_text(encoding="ascii").strip())
            os.kill(pid, 0)
        except (FileNotFoundError, ValueError, ProcessLookupError):
            return True
        except PermissionError:
            return False
        return False

    def release(self) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False


def install_exception_hook(logger: logging.Logger, show_error: Callable[[str, str], None]) -> None:
    def handle(exception_type: type[BaseException], value: BaseException, traceback: TracebackType | None) -> None:
        logger.error("Unhandled GUI exception", exc_info=(exception_type, value, traceback))
        show_error("Неожиданная ошибка", "Приложение столкнулось с ошибкой. Подробности сохранены в app.log.")
    sys.excepthook = handle
