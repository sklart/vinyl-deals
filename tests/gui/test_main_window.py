from decimal import Decimal
from threading import Event

from PySide6.QtTest import QTest

from vinyl_deals.domain import Availability
from vinyl_deals.gui.main_window import MainWindow
from vinyl_deals.search import OfferSearchResult, ReleaseSearchResult


def offer(identifier, store, price, *, availability=Availability.IN_STOCK, condition="NEW", url=None):
    return OfferSearchResult(identifier, store, Decimal(str(price)), availability, condition, url or f"https://example.test/{identifier}", None, None)


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
    assert window.offer_table.item(2, 3).text() == "Нет в наличии"
    assert not window.offer_table.item(2, 1).toolTip()
    assert not window.open_store_button.isEnabled()
    window.offer_table.selectRow(1)
    qapp.processEvents()
    assert window.open_store_button.isEnabled()
    window.offer_table.clearSelection()
    qapp.processEvents()
    assert not window.open_store_button.isEnabled()
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
