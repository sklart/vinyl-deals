import sys

from vinyl_deals import autostart
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.runtime import (
    SingleInstanceLock,
    application_database_path,
    bootstrap_application_data,
    configure_logging,
    install_exception_hook,
    portable_data_dir,
    portable_root,
    prepare_application_data,
    resource_path,
)


def test_portable_paths_ignore_localappdata(tmp_path):
    environment = {"VINYL_DEALS_PORTABLE_ROOT": str(tmp_path / "VinylDeals"), "LOCALAPPDATA": str(tmp_path / "must-not-use")}
    assert portable_root(environment) == tmp_path / "VinylDeals"
    assert portable_data_dir(environment) == tmp_path / "VinylDeals" / "data"
    assert application_database_path(environment) == tmp_path / "VinylDeals" / "data" / "vinyl_deals.sqlite3"


def test_prepare_data_copies_and_migrates_legacy_database(tmp_path):
    legacy = tmp_path / "legacy.sqlite3"
    SQLiteRepository(legacy).initialize()
    database = prepare_application_data(legacy_candidates=(legacy,), environ={"VINYL_DEALS_PORTABLE_ROOT": str(tmp_path / "portable")})
    assert database.exists()
    assert SQLiteRepository(database).schema_version() == SQLiteRepository(legacy).schema_version()
    assert legacy.exists()
    assert (database.parent / "settings.ini").is_file()


def test_bootstrap_copies_only_database_beside_portable_root(tmp_path, monkeypatch):
    root = tmp_path / "VinylDeals"
    root.mkdir()
    legacy = root / "vinyl_deals.sqlite3"
    SQLiteRepository(legacy).initialize()
    monkeypatch.setenv("VINYL_DEALS_PORTABLE_ROOT", str(root))
    database = bootstrap_application_data()
    assert database == root / "data" / "vinyl_deals.sqlite3"
    assert database.exists() and legacy.exists()


def test_single_instance_lock_and_stale_recovery(tmp_path):
    first = SingleInstanceLock(tmp_path / "gui.lock")
    second = SingleInstanceLock(tmp_path / "gui.lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()
    (tmp_path / "stale.lock").write_text("99999999", encoding="ascii")
    stale = SingleInstanceLock(tmp_path / "stale.lock")
    assert stale.acquire()
    stale.release()


def test_logging_rotates_and_redacts_telegram_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    logger = configure_logging(tmp_path, max_bytes=100, backup_count=2)
    logger.info("delivery secret-token failed")
    logger.info("x" * 200)
    for handler in logger.handlers:
        handler.flush()
    content = "".join(path.read_text(encoding="utf-8") for path in (tmp_path / "logs").glob("app.log*"))
    assert "secret-token" not in content and "[redacted]" in content
    assert (tmp_path / "logs" / "app.log.1").exists()


def test_crash_handler_logs_safe_message(tmp_path):
    logger = configure_logging(tmp_path)
    displayed = []
    previous = sys.excepthook
    install_exception_hook(logger, lambda title, message: displayed.append((title, message)))
    try:
        raise RuntimeError("boom")
    except RuntimeError as error:
        sys.excepthook(type(error), error, error.__traceback__)
    finally:
        sys.excepthook = previous
    assert displayed == [("Неожиданная ошибка", "Приложение столкнулось с ошибкой. Подробности сохранены в app.log.")]
    assert "RuntimeError: boom" in (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")


def test_resource_path_supports_pyinstaller_bundle(tmp_path, monkeypatch):
    bundled = tmp_path / "bundle" / "vinyl_deals"
    bundled.mkdir(parents=True)
    (bundled / "build_meta.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    assert resource_path("build_meta.json") == bundled / "build_meta.json"


def test_autostart_enable_disable_uses_user_run_key(monkeypatch):
    values = {}

    class Key:
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    class Registry:
        HKEY_CURRENT_USER = object(); REG_SZ = 1; KEY_SET_VALUE = 2
        def OpenKey(self, *_args):
            if autostart.VALUE_NAME not in values: raise FileNotFoundError()
            return Key()
        def CreateKey(self, *_args): return Key()
        def SetValueEx(self, _key, name, _zero, _kind, value): values[name] = value
        def QueryValueEx(self, _key, name): return values[name], self.REG_SZ
        def DeleteValue(self, _key, name): del values[name]

    monkeypatch.setattr(autostart, "_registry", lambda: Registry())
    autostart.set_enabled(True)
    assert autostart.is_enabled() and autostart.VALUE_NAME in values
    autostart.set_enabled(False)
    assert not autostart.is_enabled()
