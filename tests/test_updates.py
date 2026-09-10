from datetime import datetime, timezone
from decimal import Decimal

from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer, ScrapeResult
from vinyl_deals.updates import refresh_catalogs


class ActiveAdapter:
    def get_catalog(self):
        return ScrapeResult((RawOffer(source="active", source_product_id="1", url="https://example.test", fetched_at=datetime.now(timezone.utc), artist_raw="Artist", title_raw="Album", barcode="4006381333931", price=Decimal("100"), availability=Availability.IN_STOCK),))

    def enrich_offer(self, offer):
        return offer


class FailingAdapter:
    def get_catalog(self):
        raise RuntimeError("offline")


class ReportingAdapter(ActiveAdapter):
    progress_callback = None

    def get_catalog(self):
        assert self.progress_callback is not None
        self.progress_callback("Store: страницы 2/5, осталось 3")
        return super().get_catalog()


class FetchingAdapter(ActiveAdapter):
    def _fetch(self, url):
        return "catalogue"

    def get_catalog(self):
        self._fetch("https://example.test/catalogue")
        return super().get_catalog()


def test_refresh_continues_after_one_store_error(tmp_path):
    statuses = []
    result = refresh_catalogs(SQLiteRepository(tmp_path / "updates.sqlite3"), progress=statuses.append, adapter_factories={"bad": FailingAdapter, "good": ActiveAdapter}, enrich=False)
    assert [(item.source, item.status) for item in result] == [("bad", "error"), ("good", "active")]
    assert any("Магазин временно недоступен" in status for status in statuses)
    assert statuses[-1] == "Обновление завершено"


def test_refresh_forwards_store_page_progress(tmp_path):
    statuses = []

    refresh_catalogs(
        SQLiteRepository(tmp_path / "progress.sqlite3"),
        progress=statuses.append,
        adapter_factories={"reporting": ReportingAdapter},
        enrich=False,
    )

    assert "Store: страницы 2/5, осталось 3" in statuses


def test_refresh_reports_network_activity_for_adapter_without_page_total(tmp_path):
    statuses = []

    refresh_catalogs(
        SQLiteRepository(tmp_path / "request-progress.sqlite3"),
        progress=statuses.append,
        adapter_factories={"fetching": FetchingAdapter},
        enrich=False,
    )

    assert "fetching: запросов 1, осталось неизвестно" in statuses
