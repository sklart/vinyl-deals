from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox

from vinyl_deals.runtime import SingleInstanceLock, configure_logging, install_exception_hook, prepare_application_data
from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    arguments = argv if argv is not None else sys.argv
    try:
        database = prepare_application_data(legacy_candidates=(Path.cwd() / "vinyl_deals.sqlite3", Path(sys.executable).resolve().parent / "vinyl_deals.sqlite3"))
    except Exception as error:
        QMessageBox.critical(None, "Vinyl Deals", "Не удалось подготовить пользовательские данные. Проверьте доступ к LocalAppData.")
        return 2
    logger = configure_logging(database.parent)
    if "--smoke-test" in arguments:
        logger.info("Packaged GUI smoke test completed")
        return 0
    lock = SingleInstanceLock(database.parent / "gui.lock")
    if not lock.acquire():
        QMessageBox.information(None, "Vinyl Deals", "Vinyl Deals уже запущен.")
        return 0
    install_exception_hook(logger, lambda title, message: QMessageBox.critical(None, title, message))
    window = MainWindow(database)
    app.aboutToQuit.connect(lock.release)
    window.show()
    return app.exec()
