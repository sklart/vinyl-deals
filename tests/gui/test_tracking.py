from datetime import datetime, timezone
from decimal import Decimal
from threading import Event, Timer

from PySide6.QtTest import QTest

from vinyl_deals.domain import Availability, RawOffer
from vinyl_deals.gui.main_window import MainWindow
from vinyl_deals.live_search import LiveSearchResult
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.search import search_releases


def populated_repository(window):
    now = datetime.now(timezone.utc)
    first = RawOffer(
        source="store_a", source_product_id="1", url="https://example.test/a", fetched_at=now,
        artist_raw="Opeth", title_raw="Blackwater Park", barcode="4006381333931",
        catalog_number_raw="MOVLP001", label="Music On Vinyl", release_year=2021,
        format="2LP", condition_media="NEW", condition_sleeve="NEW", price=Decimal("5000"),
        availability=Availability.IN_STOCK,
    )
    second = RawOffer(**{**{name: getattr(first, name) for name in first.__dataclass_fields__}, "source": "store_b", "source_product_id": "2", "url": "https://example.test/b", "price": Decimal("7000")})
    window.repository.upsert_offer(first)
    window.repository.upsert_offer(second)
    build_match_queue(window.repository)
    return window.repository.offer_by_id(window.repository.offers_for_matching()[0][0])[0]


def test_tracking_add_edit_toggle_remove_and_show_alert(qapp, tmp_path):
    window = MainWindow(tmp_path / "tracking.sqlite3", search_service=search_releases)
    release_id = populated_repository(window)
    window.fields["artist"].setText("opeth")
    window.perform_search()
    assert window.selected_result and window.selected_result.release_id == release_id

    window.watch_add_button.click()
    assert window.watch_table.rowCount() == 1
    window.watch_table.selectRow(0)
    window.update_watch_parameters(release_id, max_price=Decimal("5500"), min_deal_class="HOT", local_only=True, city="Ростов-на-Дону", pickup_only=True)
    entry = window.repository.watchlist_entries()[0]
    assert entry["max_price"] == "5500" and entry["min_deal_class"] == "HOT"
    assert entry["local_only"] and entry["pickup_only"]
    window.watch_table.selectRow(0)
    window.watch_enable_button.click()
    assert not window.repository.watchlist_entries()[0]["enabled"]

    offer_id = window.repository.offers_for_release(release_id)[0][0]
    window.repository.save_alert(release_id=release_id, offer_id=offer_id, event_type="NEW_STOCK", event_key="gui-alert", payload={"artist": "Opeth", "title": "Blackwater Park", "store": "Store A", "price": "5000", "url": "https://example.test/a", "discogs_url": "https://discogs.test"})
    window.refresh_tracking()
    assert window.alert_table.rowCount() == 1
    assert window.alert_table.item(0, 5).text() == "pending"
    window.watch_table.selectRow(0)
    window.watch_remove_button.click()
    assert window.watch_table.rowCount() == 0
    window.close()


def test_tracking_shows_targeted_refresh_state(qapp, tmp_path):
    window = MainWindow(tmp_path / "tracking-state.sqlite3", search_service=search_releases)
    release_id = populated_repository(window)
    window.repository.add_watchlist(release_id)
    window.repository.record_watch_refresh(release_id, status="PARTIAL", offer_count=4, checked_at="2026-09-11T12:00:00+00:00")
    window.refresh_tracking()
    assert window.watch_table.item(0, 9).text() == "2026-09-11T12:00:00+00:00"
    assert window.watch_table.item(0, 10).text() == "PARTIAL"
    assert window.watch_table.item(0, 11).text() == "4"
    window.close()


def test_scheduler_controls_are_persisted_in_gui(qapp, tmp_path):
    window = MainWindow(tmp_path / "settings.sqlite3")
    assert not window.scheduler_enabled.isChecked()
    window.scheduler_interval.setCurrentText("180")
    window.scheduler_enabled.setChecked(True)
    window.scheduler_auto_send.setChecked(True)
    assert window.scheduler.timer.isActive()
    assert window.scheduler.interval_minutes == 180
    assert window.scheduler.auto_send is True
    window.close()
    assert (tmp_path / "settings.ini").is_file()
    restored = MainWindow(tmp_path / "settings.sqlite3")
    assert restored.scheduler_enabled.isChecked()
    assert restored.scheduler.interval_minutes == 180
    assert restored.scheduler.auto_send is True
    restored.close()


def test_scheduler_cycle_keeps_gui_controls_disabled_until_finished(qapp, tmp_path):
    started, unblock = Event(), Event()

    def targeted(_repository, query):
        started.set()
        unblock.wait(1)
        return LiveSearchResult(query, (), ())

    window = MainWindow(tmp_path / "cycle.sqlite3", live_search_service=targeted)
    window.repository.add_watchlist(populated_repository(window))
    window.run_scheduler_cycle()
    for _ in range(50):
        qapp.processEvents()
        if started.is_set():
            break
        QTest.qWait(10)
    assert started.is_set()
    assert not window.search_button.isEnabled()
    assert not window.watch_add_button.isEnabled()
    assert not window.scheduler_run_button.isEnabled()
    unblock.set()
    for _ in range(50):
        qapp.processEvents()
        if window.search_button.isEnabled():
            break
        QTest.qWait(10)
    assert window.search_button.isEnabled()
    assert window.scheduler_run_button.isEnabled()
    window.close()


def test_close_waits_for_active_manual_refresh_worker(qapp, tmp_path):
    started, unblock = Event(), Event()

    def refresh(*_args, **_kwargs):
        started.set()
        unblock.wait(1)
        return ()

    window = MainWindow(tmp_path / "close-refresh.sqlite3", update_service=refresh)
    window.start_refresh()
    for _ in range(50):
        qapp.processEvents()
        if started.is_set():
            break
        QTest.qWait(10)
    worker = window._worker
    Timer(0.05, unblock.set).start()
    window.close()
    assert worker is not None and not worker.isRunning()


def test_scheduler_and_manual_send_are_mutually_exclusive(qapp, tmp_path, monkeypatch):
    started, unblock = Event(), Event()

    class Result:
        sent = 0
        failed = 0

    def slow_delivery(_repository):
        started.set()
        unblock.wait(1)
        return Result()

    monkeypatch.setattr("vinyl_deals.alert_delivery.deliver_pending_alerts", slow_delivery)
    window = MainWindow(tmp_path / "exclusive.sqlite3", update_service=lambda *_args, **_kwargs: ())
    window.send_pending_alerts()
    for _ in range(50):
        qapp.processEvents()
        if started.is_set():
            break
        QTest.qWait(10)
    assert started.is_set()
    window.run_scheduler_cycle()
    assert not window.scheduler.running
    unblock.set()
    for _ in range(50):
        qapp.processEvents()
        if window._alert_worker is None:
            break
        QTest.qWait(10)
    window.run_scheduler_cycle()
    assert window.scheduler.running
    window.scheduler.shutdown()
    window.close()


def test_scheduler_cycle_blocks_manual_send_and_displays_next_check(qapp, tmp_path, monkeypatch):
    started, unblock = Event(), Event()

    def targeted(_repository, query):
        started.set()
        unblock.wait(1)
        return LiveSearchResult(query, (), ())

    window = MainWindow(tmp_path / "exclusive-cycle.sqlite3", live_search_service=targeted)
    window.repository.add_watchlist(populated_repository(window))
    window.scheduler_enabled.setChecked(True)
    window.scheduler_interval.setCurrentText("30")
    window.run_scheduler_cycle()
    for _ in range(50):
        qapp.processEvents()
        if started.is_set():
            break
        QTest.qWait(10)
    window.send_pending_alerts()
    assert window._alert_worker is None
    unblock.set()
    for _ in range(50):
        qapp.processEvents()
        if not window.scheduler.running and "Следующая проверка: через 30 мин." in window.status_label.text():
            break
        QTest.qWait(10)
    assert "Следующая проверка: через 30 мин." in window.status_label.text()
    assert window.scheduler.timer.isActive()
    window.close()


def test_close_waits_for_active_telegram_worker(qapp, tmp_path, monkeypatch):
    started, unblock = Event(), Event()

    class Result:
        sent = 0
        failed = 0

    def slow_delivery(_repository):
        started.set()
        unblock.wait(1)
        return Result()

    monkeypatch.setattr("vinyl_deals.alert_delivery.deliver_pending_alerts", slow_delivery)
    window = MainWindow(tmp_path / "close-telegram.sqlite3")
    window.send_pending_alerts()
    for _ in range(50):
        qapp.processEvents()
        if started.is_set():
            break
        QTest.qWait(10)
    worker = window._alert_worker
    Timer(0.05, unblock.set).start()
    window.close()
    assert worker is not None and not worker.isRunning()


def test_manual_refresh_blocks_scheduler_cycle(qapp, tmp_path):
    started, unblock = Event(), Event()

    def refresh(*_args, **_kwargs):
        started.set()
        unblock.wait(1)
        return ()

    window = MainWindow(tmp_path / "refresh-exclusive.sqlite3", update_service=refresh)
    window.start_refresh()
    for _ in range(50):
        qapp.processEvents()
        if started.is_set():
            break
        QTest.qWait(10)
    window.run_scheduler_cycle()
    assert not window.scheduler.running
    unblock.set()
    for _ in range(50):
        qapp.processEvents()
        if window.refresh_button.isEnabled():
            break
        QTest.qWait(10)
    window.close()
