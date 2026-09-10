from datetime import datetime, timezone
from pathlib import Path

import pytest

from vinyl_deals.adapters.wave2 import AVSoundAdapter, MaximumVinylAdapter, PultAdapter, TishinaAdapter, VernoshopAdapter, VidikaAdapter, VinylmarktAdapter
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, StoreState
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.search import search_releases


FIXTURES = Path("tests/fixtures/wave2")
ACTIVE_ADAPTERS = (
    (VidikaAdapter, "vidika", "1268"),
    (MaximumVinylAdapter, "maximum_vinyl", "mv-77"),
    (VinylmarktAdapter, "vinylmarkt", "W-100700"),
    (VernoshopAdapter, "vernoshop", "vs-100"),
    (TishinaAdapter, "tishina", "t-42"),
    (AVSoundAdapter, "avsound", "av-5"),
)


@pytest.mark.parametrize(("adapter_type", "source", "sku"), ACTIVE_ADAPTERS)
def test_public_catalogue_card_parsing_and_stable_sku(adapter_type, source, sku):
    adapter = adapter_type()
    html = (FIXTURES / source / "listing.html").read_text(encoding="utf-8")
    offer = adapter.parse_listing(html, fetched_at=datetime(2026, 9, 10, tzinfo=timezone.utc))[0]
    assert offer.source == source and offer.source_product_id == sku and offer.store_sku == sku
    assert offer.artist_raw == "Opeth" and offer.title_raw.startswith("Blackwater Park")
    assert offer.price is not None and offer.availability == Availability.IN_STOCK
    assert offer.catalog_number_raw is None


@pytest.mark.parametrize(("adapter_type", "source", "_sku"), ACTIVE_ADAPTERS)
def test_public_product_enrichment_and_out_of_stock(adapter_type, source, _sku):
    adapter = adapter_type()
    listing = adapter.parse_listing((FIXTURES / source / "listing.html").read_text(encoding="utf-8"))[0]
    detail = adapter.parse_product_page("EAN: 1234567890123 | Каталожный номер: MOVLP001 | Лейбл: Music On Vinyl | Страна: EU | Год выпуска: 2021", listing)
    assert detail.barcode == "1234567890123" and detail.catalog_number_raw == "MOVLP001"
    assert detail.label == "Music On Vinyl" and detail.release_year == 2021
    sold = adapter.parse_listing((FIXTURES / source / "listing.html").read_text(encoding="utf-8").replace("В наличии", "out-of-stock").replace("Достаточно", "out-of-stock"))[0]
    assert sold.availability == Availability.OUT_OF_STOCK


@pytest.mark.parametrize(("adapter_type", "source", "_sku"), ACTIVE_ADAPTERS)
def test_pagination_dedup_and_zero_offers(adapter_type, source, _sku, monkeypatch):
    adapter = adapter_type(page_limit=1, delay_seconds=0)
    listing = (FIXTURES / source / "listing.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda _url: listing + listing)
    result = adapter.get_catalog()
    assert result.state == StoreState.ACTIVE and len(result.offers) == 1 and result.pages_processed == 1
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>no products</html>")
    assert adapter.get_catalog().state == StoreState.DEGRADED


def test_pult_is_explicitly_degraded_when_public_catalogue_is_access_restricted(monkeypatch):
    adapter = PultAdapter()
    monkeypatch.setattr(adapter, "_fetch", lambda _url: (FIXTURES / "pult" / "listing.html").read_text(encoding="utf-8"))
    result = adapter.get_catalog()
    assert result.state == StoreState.DEGRADED
    assert "access-check" in result.warnings[0].casefold() or "captcha" in result.warnings[0].casefold()


def test_wave2_offers_merge_into_one_release_and_choose_best_price(tmp_path):
    repository = SQLiteRepository(tmp_path / "wave2.sqlite3")
    for adapter_type, source, _sku in ACTIVE_ADAPTERS[:3]:
        adapter = adapter_type()
        listing = adapter.parse_listing((FIXTURES / source / "listing.html").read_text(encoding="utf-8"))[0]
        offer = adapter.parse_product_page("EAN: 1234567890123 | Каталожный номер: MOVLP001 | Лейбл: Music On Vinyl | Год выпуска: 2021", listing)
        repository.upsert_offer(offer)
    build_match_queue(repository)
    results = search_releases(repository, artist="opeth", title="blackwater")
    assert len(results) == 1 and len(results[0].offers) == 3
    assert results[0].lowest_price_offer is not None
    assert results[0].lowest_price_offer.store == "vinylmarkt"
