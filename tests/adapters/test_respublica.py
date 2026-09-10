from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from vinyl_deals.adapters.respublica import RespublicaAdapter
from vinyl_deals.domain import Availability, StoreState


def test_parses_respublica_public_listing_with_stable_sku() -> None:
    adapter = RespublicaAdapter()
    offers = adapter.parse_listing(Path("tests/fixtures/respublica/listing.html").read_text(encoding="utf-8"), fetched_at=datetime(2026, 9, 10, tzinfo=timezone.utc))
    assert len(offers) == 2
    first, sold = offers
    assert (first.source_product_id, first.artist_raw, first.title_raw, first.price, first.format) == ("1544912", "Various Artists", "Bjork In Jazz LP", Decimal("3267"), "LP")
    assert first.availability == Availability.IN_STOCK
    assert sold.availability == Availability.OUT_OF_STOCK
    # Price and stock changes must not alter the identity supplied by the shop.
    changed = adapter.parse_listing(Path("tests/fixtures/respublica/listing.html").read_text(encoding="utf-8").replace("3267", "2999").replace("В корзину", "Нет в наличии"))[0]
    assert changed.source_product_id == first.source_product_id


def test_enriches_respublica_public_card_metadata() -> None:
    adapter = RespublicaAdapter()
    offer = adapter.parse_listing(Path("tests/fixtures/respublica/listing.html").read_text(encoding="utf-8"))[0]
    detailed = adapter.parse_product_page(Path("tests/fixtures/respublica/product.html").read_text(encoding="utf-8"), offer)
    assert (detailed.barcode, detailed.catalog_number_raw, detailed.label, detailed.release_year) == ("4600000000001", "JAZZ-001", "Jazz Label", 2025)


def test_respublica_zero_or_malformed_listing_is_degraded(monkeypatch) -> None:
    adapter = RespublicaAdapter(page_limit=1)
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>no products</html>")
    result = adapter.get_catalog()
    assert result.state == StoreState.DEGRADED
    assert result.offers == ()


def test_respublica_catalogue_deduplicates_repeated_cards(monkeypatch) -> None:
    adapter = RespublicaAdapter(page_limit=1)
    html = Path("tests/fixtures/respublica/listing.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda _url: html + html)
    result = adapter.get_catalog()
    assert len(result.offers) == 2
