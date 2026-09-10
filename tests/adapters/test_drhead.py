from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from vinyl_deals.adapters.drhead import DrHeadAdapter
from vinyl_deals.domain import Availability, StoreState


def test_parses_drhead_embedded_public_product_json() -> None:
    adapter = DrHeadAdapter()
    offers = adapter.parse_listing(Path("tests/fixtures/drhead/listing.html").read_text(encoding="utf-8"), fetched_at=datetime(2026, 9, 10, tzinfo=timezone.utc))
    assert len(offers) == 2
    first, sold = offers
    assert (first.source_product_id, first.artist_raw, first.price, first.old_price, first.format) == ("162248", "Pink Floyd", Decimal("4490"), Decimal("4990"), "LP")
    assert first.availability == Availability.IN_STOCK
    assert sold.availability == Availability.OUT_OF_STOCK
    changed = adapter.parse_listing(Path("tests/fixtures/drhead/listing.html").read_text(encoding="utf-8").replace('"price":4490', '"price":3990'))[0]
    assert changed.source_product_id == first.source_product_id


def test_enriches_drhead_card_and_degrades_zero_products(monkeypatch) -> None:
    adapter = DrHeadAdapter(page_limit=1)
    listing = adapter.parse_listing(Path("tests/fixtures/drhead/listing.html").read_text(encoding="utf-8"))[0]
    detail = adapter.parse_product_page(Path("tests/fixtures/drhead/product.html").read_text(encoding="utf-8"), listing)
    assert (detail.barcode, detail.catalog_number_raw, detail.release_year) == ("4600000000002", "PF-2025", 2025)
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>empty</html>")
    result = adapter.get_catalog()
    assert result.state == StoreState.DEGRADED


def test_drhead_uses_only_named_release_year_property() -> None:
    adapter = DrHeadAdapter()
    listing = adapter.parse_listing(Path("tests/fixtures/drhead/listing.html").read_text(encoding="utf-8"))[0]
    no_property = adapter.parse_product_page("<main>© 2026. Альбом 1994 года.</main>", listing)
    english_property = adapter.parse_product_page("<main>Release year: 2021</main>", listing)
    assert no_property.release_year is None
    assert english_property.release_year == 2021


def test_drhead_captcha_page_is_degraded(monkeypatch) -> None:
    adapter = DrHeadAdapter(page_limit=1)
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>smartcaptcha</html>")
    assert adapter.get_catalog().state == StoreState.DEGRADED


def test_drhead_catalogue_deduplicates_repeated_embedded_items(monkeypatch) -> None:
    adapter = DrHeadAdapter(page_limit=1)
    html = Path("tests/fixtures/drhead/listing.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda _url: html + html)
    result = adapter.get_catalog()
    assert len(result.offers) == 2
