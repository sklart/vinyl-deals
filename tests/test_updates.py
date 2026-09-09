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


def test_refresh_continues_after_one_store_error(tmp_path):
    statuses = []
    result = refresh_catalogs(SQLiteRepository(tmp_path / "updates.sqlite3"), progress=statuses.append, adapter_factories={"bad": FailingAdapter, "good": ActiveAdapter}, enrich=False)
    assert [(item.source, item.status) for item in result] == [("bad", "error"), ("good", "active")]
    assert any("Магазин временно недоступен" in status for status in statuses)
    assert statuses[-1] == "Обновление завершено"
