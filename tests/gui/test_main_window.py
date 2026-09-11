from decimal import Decimal
from threading import Event

from PySide6.QtTest import QTest

from vinyl_deals.domain import Availability
from vinyl_deals.gui.main_window import MainWindow
from vinyl_deals.domain import StoreSearchQuery, StoreSearchStatus, StoreState
from vinyl_deals.live_search import LiveSearchResult, LiveStoreResult
from vinyl_deals.search import OfferSearchResult, ReleaseSearchResult
from vinyl_deals.pricing import DealClass


def offer(identifier, store, price, *, availability=Availability.IN_STOCK, condition="NEW", url=None, city=None, local_store=False, pickup_available=False, effective_price=None, effective_price_known=False):
    return OfferSearchResult(identifier, store, Decimal(str(price)), availability, condition, url or f"https://example.test/{identifier}", None, None, city, local_store, pickup_available, effective_price, effective_price_known)


def release():
    new = offer(1, "Imagine Club", 8000)
    used = offer(2, "Vinyl.ru", 4000, condition="NM")
    sold = offer(3, "Collectomania", 100, availability=Availability.OUT_OF_STOCK)
    return ReleaseSearchResult(7, "Opeth", "Blackwater Park", "Music On Vinyl", "MOVLP001", "4006381333931", 2021, "2LP", (new, used, sold), new, used, used, "https://www.discogs.com/search/?q=4006381333931&type=all")


def wait_until(qapp, predicate):
    for _ in range(50):
        if predicate():
            return True
        qapp.processEvents()
        QTest.qWait(10)
    return predicate()


def test_window_search_fields_results_and_offer_selection(qapp, tmp_path):
    received = {}
    item = release()
    def search_service(repository, **criteria):
        received.update(criteria)
        return [item]
    window = MainWindow(tmp_path / "gui.sqlite3", search_service=search_service)
    window.fields["artist"].setText("Opeth")
    window.fields["title"].setText("Blackwater Park")
    window.perform_search()
    qapp.processEvents()

    assert received["artist"] == "Opeth"
    assert received["title"] == "Blackwater Park"
    assert window.release_table.rowCount() == 1
    assert window.offer_table.rowCount() == 3
    assert [window.offer_table.item(row, 0).text() for row in range(3)] == ["Vinyl.ru", "Imagine Club", "Collectomania"]
    assert "Lowest price" in window.offer_table.item(0, 1).toolTip()
    assert "Best used" in window.offer_table.item(0, 1).toolTip()
    assert window.offer_table.item(2, 5).text() == "Нет в наличии"
    assert not window.offer_table.item(2, 1).toolTip()
    assert not window.open_store_button.isEnabled()
    window.offer_table.selectRow(1)
    qapp.processEvents()
    assert window.open_store_button.isEnabled()
    window.offer_table.clearSelection()
    qapp.processEvents()
    assert not window.open_store_button.isEnabled()
    window.close()


def test_window_shows_local_pickup_and_effective_price(qapp, tmp_path):
    local = offer(1, "РИО", 5000, city="Ростов-на-Дону", local_store=True, pickup_available=True, effective_price=Decimal("5000"), effective_price_known=True)
    remote = offer(2, "Imagine", 4650)
    item = ReleaseSearchResult(7, "Opeth", "Blackwater Park", None, None, None, None, None, (local, remote), local, None, remote, None, local)
    window = MainWindow(tmp_path / "gui-local.sqlite3", search_service=lambda *_args, **_kwargs: [item])
    window.perform_search()
    assert window.offer_table.rowCount() == 2
    local_row = next(row for row in range(2) if window.offer_table.item(row, 0).text() == "📍 РИО")
    remote_row = 1 - local_row
    assert window.offer_table.item(local_row, 2).text() == "5000 RUB"
    assert window.offer_table.item(local_row, 3).text() == "Да"
    assert window.offer_table.item(remote_row, 2).text() == "?"
    assert "Best effective" in window.offer_table.item(local_row, 2).toolTip()
    window.close()


def test_window_shows_release_prices_median_and_deal_without_selecting_offer(qapp, tmp_path):
    new = offer(1, "Imagine Club", 4290, effective_price=Decimal("4790"), effective_price_known=True)
    second = offer(2, "Vinyl.ru", 5850)
    item = ReleaseSearchResult(
        7, "Opeth", "Blackwater Park", None, None, None, None, "LP", (new, second), new, None, new, None,
        new, offer_count=2, store_count=2, market_median=Decimal("5850"), comparable_count=3,
        discount_pct=Decimal("26.7"), deal_class=DealClass.GOOD,
    )
    window = MainWindow(tmp_path / "gui-prices.sqlite3", search_service=lambda *_args, **_kwargs: [item])
    window.perform_search()
    assert window.release_table.item(0, 7).text() == "4290 ₽"
    assert window.release_table.item(0, 8).text() == "4790 ₽"
    assert window.release_table.item(0, 9).text() == "5850 ₽"
    assert window.release_table.item(0, 10).text() == "26.7%"
    assert window.release_table.item(0, 11).text() == "2"
    assert window.release_table.item(0, 12).text() == "2"
    assert "Лучшая цена: 4290 ₽ · Imagine Club" in window.release_summary.text()
    assert "Выгода: 26.7% · GOOD" in window.release_summary.text()
    window.close()


def test_window_does_not_invent_effective_price_or_deal_for_single_offer(qapp, tmp_path):
    single = offer(1, "Imagine Club", 4290)
    item = ReleaseSearchResult(7, "Opeth", "Blackwater Park", None, None, None, None, "LP", (single,), single, None, single, None, offer_count=1, store_count=1)
    window = MainWindow(tmp_path / "gui-insufficient.sqlite3", search_service=lambda *_args, **_kwargs: [item])
    window.perform_search()
    assert window.release_table.item(0, 8).text() == "—"
    assert window.release_table.item(0, 9).text() == "—"
    assert window.release_table.item(0, 10).text() == "—"
    assert "Оценка: недостаточно данных" in window.release_summary.text()
    window.close()


def test_empty_result_and_open_actions_use_service_urls(qapp, tmp_path):
    opened = []
    item = release()
    window = MainWindow(tmp_path / "gui.sqlite3", search_service=lambda *_args, **_kwargs: [], url_opener=lambda url: opened.append(url.toString()) or True)
    window.perform_search()
    assert window.status_label.text() == "Ничего не найдено"
    window.search_service = lambda *_args, **_kwargs: [item]
    window.perform_search()
    window.offer_table.selectRow(1)
    assert window.open_store_button.isEnabled()
    window.open_store_button.click()
    window.open_discogs()
    assert opened == ["https://example.test/1", item.discogs_url]
    window.close()


def test_refresh_repeats_an_empty_search_and_restores_controls(qapp, tmp_path):
    refreshed = {"value": False}
    item = release()
    def search_service(*_args, **_kwargs):
        return [item] if refreshed["value"] else []
    def update_service(*_args, **_kwargs):
        refreshed["value"] = True
        return ()
    window = MainWindow(tmp_path / "gui.sqlite3", search_service=search_service, update_service=update_service)
    window.perform_search()
    assert window.release_table.rowCount() == 0
    window.start_refresh()
    assert wait_until(qapp, lambda: window.refresh_button.isEnabled())
    assert window.release_table.rowCount() == 1
    assert window.search_button.isEnabled()
    assert all(field.isEnabled() for field in window.fields.values())
    assert window.release_table.isEnabled() and window.offer_table.isEnabled()
    window.close()


def test_refresh_disables_controls_until_worker_finishes(qapp, tmp_path):
    started, release_worker = Event(), Event()
    def update_service(*_args, **_kwargs):
        started.set()
        release_worker.wait(1)
        return ()
    window = MainWindow(tmp_path / "gui.sqlite3", update_service=update_service)
    window.perform_search()
    window.start_refresh()
    assert wait_until(qapp, started.is_set)
    assert not window.search_button.isEnabled()
    assert not window.clear_button.isEnabled()
    assert not window.refresh_button.isEnabled()
    assert not window.release_table.isEnabled() and not window.offer_table.isEnabled()
    assert not window.open_store_button.isEnabled() and not window.open_discogs_button.isEnabled()
    assert all(not field.isEnabled() for field in window.fields.values())
    release_worker.set()
    assert wait_until(qapp, lambda: window.refresh_button.isEnabled())
    assert window.search_button.isEnabled() and window.clear_button.isEnabled()
    window.close()


def test_worker_error_keeps_window_alive(qapp, tmp_path):
    def failing_update(*_args, **_kwargs):
        raise RuntimeError("network down")
    window = MainWindow(tmp_path / "gui.sqlite3", update_service=failing_update)
    window.show()
    qapp.processEvents()
    window.start_refresh()
    assert wait_until(qapp, lambda: window.refresh_button.isEnabled())
    assert window.status_label.text() == "Ошибка обновления: network down"
    assert window.search_button.isEnabled() and window.clear_button.isEnabled()
    assert all(field.isEnabled() for field in window.fields.values())
    assert not window.isHidden()
    window.close()


def test_live_search_reports_store_progress_and_renders_partial_result(qapp, tmp_path):
    started, release_worker = Event(), Event()
    item = release()
    def live_service(_repository, query, *, progress):
        progress(LiveStoreResult("Imagine Club", StoreState.ACTIVE, 3, releases=(item,)))
        started.set(); release_worker.wait(1)
        return LiveSearchResult(query, (item,), (LiveStoreResult("Imagine Club", StoreState.ACTIVE, 3),))
    window = MainWindow(tmp_path / "live.sqlite3", live_search_service=live_service)
    window.fields["artist"].setText("Opeth")
    window.fields["title"].setText("Blackwater Park")
    window.start_live_search()
    assert wait_until(qapp, started.is_set)
    assert "Imagine Club: ✓ 3" in window.status_label.text()
    assert not window.search_button.isEnabled()
    assert window.release_table.rowCount() == 1
    release_worker.set()
    assert wait_until(qapp, lambda: window.search_button.isEnabled())
    assert window.release_table.rowCount() == 1
    window.close()


def test_stale_discogs_generation_cannot_replace_newer_results(qapp, tmp_path):
    newest = release()
    window = MainWindow(tmp_path / "generation.sqlite3", search_service=lambda *_args, **_kwargs: [newest])
    window.results = [newest]
    window._search_generation = 2
    # Completion from search A is ignored after search B acquired generation 2.
    window._discogs_completed((1, [], None))
    assert window.results == [newest]
    assert window._discogs_pending is None
    window.close()


def test_gui_uses_human_store_labels_and_keeps_possible_out_of_discogs_column(qapp, tmp_path):
    item = ReleaseSearchResult(
        7, "Pink Floyd", "Wish You Were Here", None, None, None, None, "LP", (),
        None, None, None, "https://www.discogs.com/search/?q=wish+you+were+here&type=all",
        has_possible_matches=True,
    )
    window = MainWindow(tmp_path / "labels.sqlite3", search_service=lambda *_args, **_kwargs: [item])
    window.perform_search()
    assert window.release_table.item(0, 13).text() == "Возможное совпадение"
    assert "possible" not in window.release_table.item(0, 14).text().casefold()
    report = LiveStoreResult("tishina", StoreState.ACTIVE, 0)
    window._live_store_finished(report)
    assert "Тишина: 0 результатов" in window.status_label.text()
    window.close()


def test_gui_localizes_raw_store_ids_and_structured_statuses(qapp, tmp_path):
    raw_offer = offer(1, "vinyl_ru", 5000)
    item = ReleaseSearchResult(7, "Pink Floyd", "Animals", None, None, None, None, "LP", (raw_offer,), None, None, raw_offer, None)
    window = MainWindow(tmp_path / "status.sqlite3", search_service=lambda *_args, **_kwargs: [item])
    window.perform_search()
    assert window.offer_table.item(0, 0).text() == "Vinyl.ru"
    window._live_store_finished(LiveStoreResult("pult", StoreState.DEGRADED, 0, status=StoreSearchStatus.RESTRICTED))
    assert "Pult.ru (публичный каталог ограничен): ⚠ Доступ ограничен" in window.status_label.text()
    window._live_store_finished(LiveStoreResult("rio_rostov", StoreState.DEGRADED, 0, status=StoreSearchStatus.UNSUPPORTED))
    assert "РИО: ⓘ Live-search не поддерживается" in window.status_label.text()
    window.close()
