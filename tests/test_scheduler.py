import os
from threading import Event
from threading import Timer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from vinyl_deals.database import SQLiteRepository
from vinyl_deals import scheduler as scheduler_module
from vinyl_deals.scheduler import ALLOWED_INTERVALS, Scheduler


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_until(qapp, predicate):
    for _ in range(100):
        if predicate():
            return True
        qapp.processEvents()
        QTest.qWait(10)
    return predicate()


def test_scheduler_is_disabled_by_default_and_uses_supported_intervals(qapp, tmp_path):
    scheduler = Scheduler(SQLiteRepository(tmp_path / "scheduler.sqlite3"))
    assert not scheduler.timer.isActive()
    scheduler.configure(enabled=True, interval_minutes=30, auto_send=True)
    assert scheduler.timer.isActive()
    assert scheduler.timer.interval() == 30 * 60_000
    assert scheduler.auto_send is True
    scheduler.configure(enabled=False, interval_minutes=15, auto_send=False)
    assert not scheduler.timer.isActive()
    assert set(ALLOWED_INTERVALS) == {15, 30, 60, 180, 360}
    scheduler.shutdown()


def test_scheduler_does_not_run_two_cycles_at_once(qapp, tmp_path, monkeypatch):
    started, unblock = Event(), Event()

    def cycle(*_args, **_kwargs):
        started.set()
        unblock.wait(1)
        return {"alerts": 0, "sent": 0, "failed": 0}

    monkeypatch.setattr(scheduler_module, "run_cycle", cycle)
    scheduler = Scheduler(SQLiteRepository(tmp_path / "scheduler.sqlite3"))
    assert scheduler.trigger() is True
    assert wait_until(qapp, started.is_set)
    assert scheduler.trigger() is False
    unblock.set()
    assert wait_until(qapp, lambda: not scheduler.running)
    assert scheduler.trigger() is True
    assert wait_until(qapp, lambda: not scheduler.running)
    scheduler.shutdown()


def test_scheduler_shutdown_waits_for_active_worker(qapp, tmp_path):
    started, unblock = Event(), Event()

    def cycle(*_args, **_kwargs):
        started.set()
        unblock.wait(1)
        return {"watched": 0, "checked": 0, "partial": 0, "offers_updated": 0, "alerts": 0, "sent": 0, "failed": 0}

    original = scheduler_module.run_cycle
    scheduler_module.run_cycle = cycle
    scheduler = Scheduler(SQLiteRepository(tmp_path / "shutdown.sqlite3"))
    assert scheduler.trigger()
    # A just-started QThread may not report isRunning() yet, but Scheduler
    # must keep ownership until its authoritative finished signal arrives.
    assert scheduler.running
    assert wait_until(qapp, started.is_set)
    worker = scheduler.worker
    Timer(0.05, unblock.set).start()
    scheduler.shutdown()
    scheduler_module.run_cycle = original
    assert worker is not None and not worker.isRunning()
    assert not scheduler.timer.isActive()
