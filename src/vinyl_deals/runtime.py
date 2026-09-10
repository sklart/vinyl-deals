"""Windows-oriented production runtime helpers, independent of GUI business logic."""
from __future__ import annotations

import logging
import os
import shutil
import sys
import hashlib
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import Callable

from PySide6.QtCore import QLockFile

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


def bootstrap_application_data() -> Path:
    """Shared GUI/CLI bootstrap for app-data, legacy copy and migrations."""
    return prepare_application_data(legacy_candidates=(Path.cwd() / DATABASE_NAME, Path(sys.executable).resolve().parent / DATABASE_NAME))


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
    """Per-user single instance: named mutex on Windows, QLockFile elsewhere."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._owned = False
        self._handle: int | None = None
        self._file_lock = QLockFile(str(path))
        self._file_lock.setStaleLockTime(30_000)

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            import ctypes
            name = "Local\\VinylDeals-" + hashlib.sha256(str(self.path.resolve()).encode("utf-8")).hexdigest()[:24]
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel32.CloseHandle.restype = ctypes.c_bool
            handle = kernel32.CreateMutexW(None, False, name)
            if not handle:
                raise OSError("Cannot create Windows single-instance mutex")
            if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)
                return False
            self._handle = int(handle)
            self._owned = True
            return True
        self._owned = self._file_lock.tryLock(0)
        return self._owned

    def release(self) -> None:
        if self._owned:
            if self._handle is not None:
                import ctypes
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
                kernel32.CloseHandle.restype = ctypes.c_bool
                kernel32.CloseHandle(self._handle)
                self._handle = None
            else:
                self._file_lock.unlock()
            self._owned = False


def install_exception_hook(logger: logging.Logger, show_error: Callable[[str, str], None]) -> None:
    def handle(exception_type: type[BaseException], value: BaseException, traceback: TracebackType | None) -> None:
        logger.error("Unhandled GUI exception", exc_info=(exception_type, value, traceback))
        show_error("Неожиданная ошибка", "Приложение столкнулось с ошибкой. Подробности сохранены в app.log.")
    sys.excepthook = handle
