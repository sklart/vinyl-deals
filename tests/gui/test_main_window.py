from decimal import Decimal

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
    window.open_selected_offer()
    window.open_discogs()
    assert opened == ["https://example.test/1", item.discogs_url]
    window.close()


def test_worker_error_keeps_window_alive(qapp, tmp_path):
    def failing_update(*_args, **_kwargs):
        raise RuntimeError("network down")
    window = MainWindow(tmp_path / "gui.sqlite3", update_service=failing_update)
    window.show()
    qapp.processEvents()
    window.start_refresh()
    for _ in range(30):
        if window.refresh_button.isEnabled():
            break
        QTest.qWait(10)
    assert window.status_label.text() == "Ошибка обновления: network down"
    assert not window.isHidden()
    window.close()
