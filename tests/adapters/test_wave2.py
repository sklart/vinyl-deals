from datetime import datetime, timezone
from pathlib import Path

import pytest

from vinyl_deals.adapters.wave2 import AVSoundAdapter, MaximumVinylAdapter, OnlineTradeAdapter, PultAdapter, TishinaAdapter, VernoshopAdapter, VidikaAdapter, VinylmarktAdapter
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, StoreState
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.search import search_releases


FIXTURES = Path("tests/fixtures/wave2")
ACTIVE_ADAPTERS = (
    (VidikaAdapter, "vidika", "2202"),
    (MaximumVinylAdapter, "maximum_vinyl", "mv-77"),
    (VinylmarktAdapter, "vinylmarkt", "W-100700"),
    (VernoshopAdapter, "vernoshop", "vs-100"),
    (TishinaAdapter, "tishina", "t-42"),
    (AVSoundAdapter, "avsound", "av-5"),
)


@pytest.mark.parametrize(("adapter_type", "source", "sku"), ACTIVE_ADAPTERS)
def test_public_catalogue_card_parsing_and_stable_sku(adapter_type, source, sku):
    adapter = adapter_type()
    filename = "production_listing.html" if source == "vidika" else "listing.html"
    html = (FIXTURES / source / filename).read_text(encoding="utf-8")
    offer = adapter.parse_listing(html, fetched_at=datetime(2026, 9, 10, tzinfo=timezone.utc))[0]
    assert offer.source == source and offer.source_product_id == sku
    if source == "vidika":
        assert offer.store_sku == "1268" and offer.raw_data["vidika_sku_id"] == "3700"
    else:
        assert offer.store_sku == sku
    assert offer.artist_raw == "Opeth" and offer.title_raw.startswith("Blackwater Park")
    assert offer.price is not None and offer.availability == Availability.IN_STOCK
    assert offer.catalog_number_raw is None


@pytest.mark.parametrize(("adapter_type", "source", "_sku"), ACTIVE_ADAPTERS)
def test_public_product_enrichment_and_out_of_stock(adapter_type, source, _sku):
    adapter = adapter_type()
    filename = "production_listing.html" if source == "vidika" else "listing.html"
    listing = adapter.parse_listing((FIXTURES / source / filename).read_text(encoding="utf-8"))[0]
    detail = adapter.parse_product_page("EAN: 1234567890123 | Каталожный номер: MOVLP001 | Лейбл: Music On Vinyl | Страна: EU | Год выпуска: 2021", listing)
    assert detail.barcode == "1234567890123" and detail.catalog_number_raw == "MOVLP001"
    assert detail.label == "Music On Vinyl" and detail.release_year == 2021
    sold = adapter.parse_listing((FIXTURES / source / filename).read_text(encoding="utf-8").replace("В наличии", "out-of-stock").replace("Достаточно", "out-of-stock"))[0]
    assert sold.availability == Availability.OUT_OF_STOCK


@pytest.mark.parametrize(("adapter_type", "source", "_sku"), ACTIVE_ADAPTERS)
def test_pagination_dedup_and_zero_offers(adapter_type, source, _sku, monkeypatch):
    adapter = adapter_type(page_limit=1, delay_seconds=0)
    filename = "production_listing.html" if source == "vidika" else "listing.html"
    listing = (FIXTURES / source / filename).read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda _url: listing + listing)
    result = adapter.get_catalog()
    assert result.state == StoreState.ACTIVE and len(result.offers) == 1 and result.pages_processed == (2 if source == "vidika" else 1)
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>no products</html>")
    assert adapter.get_catalog().state == StoreState.DEGRADED


def test_pult_is_explicitly_degraded_when_public_catalogue_is_access_restricted(monkeypatch):
    adapter = PultAdapter()
    monkeypatch.setattr(adapter, "_fetch", lambda _url: (FIXTURES / "pult" / "listing.html").read_text(encoding="utf-8"))
    result = adapter.get_catalog()
    assert result.state == StoreState.DEGRADED
    assert "access-check" in result.warnings[0].casefold() or "captcha" in result.warnings[0].casefold()


def test_vidika_enumerates_both_real_category_roots_with_a_safe_limit(monkeypatch):
    adapter = VidikaAdapter(page_limit=1, delay_seconds=0)
    seen = []
    listing = (FIXTURES / "vidika" / "production_listing.html").read_text(encoding="utf-8")

    def fetch(url):
        seen.append(url)
        return listing

    monkeypatch.setattr(adapter, "_fetch", fetch)
    result = adapter.get_catalog()
    assert result.state == StoreState.ACTIVE and len(result.offers) == 1
    assert seen == [
        "https://vidika.su/category/zarubezhnyy-vinil/",
        "https://vidika.su/category/russkiy-vinil/",
    ]
    assert all("/catalog/" not in url for url in seen)


def test_vidika_aggregates_multi_root_page_limit_warnings_and_errors(monkeypatch):
    adapter = VidikaAdapter(page_limit=1, delay_seconds=0)
    listing = (FIXTURES / "vidika" / "production_listing.html").read_text(encoding="utf-8") + '<a href="?page=2">2</a>'
    monkeypatch.setattr(adapter, "_fetch", lambda _url: listing)
    result = adapter.get_catalog()
    assert result.pages_processed == 2 and len(result.offers) == 1
    assert len(result.warnings) == 2
    assert all("limited" in warning for warning in result.warnings)
    assert result.errors == ()


def test_maximum_vinyl_real_catalogue_fragment_has_stable_ids_and_isolated_cards():
    adapter = MaximumVinylAdapter()
    html = (FIXTURES / "maximum_vinyl" / "production_listing.html").read_text(encoding="utf-8")
    offers = adapter.parse_listing(html)
    assert [(offer.source_product_id, offer.title_raw, offer.price, offer.availability) for offer in offers] == [
        ("20667", "The Good Earth LP", pytest.approx(3490), Availability.IN_STOCK),
        ("20668", "Blackwater Park (2LP)", pytest.approx(5100), Availability.IN_STOCK),
    ]
    assert offers[0].url.endswith("manfred-mann-good-earth-lp")
    assert adapter._page_url(2).endswith("?page=2")


@pytest.mark.parametrize("name, expected", [
    ("Виниловая пластинка Opeth - Blackwater Park (LP)", True), ("Opeth - Blackwater Park (LP+CD)", True),
    ("Opeth - Blackwater Park Audio CD", False), ("Opeth vinyl sticker DVD", False), ("Кассета Opeth", False),
])
def test_onlinetrade_strict_mixed_media_classifier(name, expected):
    assert OnlineTradeAdapter._looks_like_vinyl(name) is expected


def test_onlinetrade_fixture_filters_mixed_catalogue_and_keeps_sku():
    adapter = OnlineTradeAdapter()
    offers = adapter.parse_listing((FIXTURES / "onlinetrade" / "mixed_media.html").read_text(encoding="utf-8"))
    # Confirmation may come from title, semantic product URL, or a structured
    # product property; mixed non-vinyl media stay out.
    assert [offer.source_product_id for offer in offers] == ["ot-vinyl", "ot-hybrid", "ot-url", "ot-property"]
    assert all(offer.catalog_number_raw is None for offer in offers)


def test_onlinetrade_representative_catalogue_filters_mixed_media_and_access_check(monkeypatch):
    adapter = OnlineTradeAdapter(delay_seconds=0)
    html = (FIXTURES / "onlinetrade" / "production_catalogue.html").read_text(encoding="utf-8")
    assert [offer.source_product_id for offer in adapter.parse_listing(html)] == ["ot-real-vinyl", "ot-real-hybrid"]
    monkeypatch.setattr(adapter, "_fetch", lambda _url: (FIXTURES / "onlinetrade" / "access_check.html").read_text(encoding="utf-8"))
    result = adapter.get_catalog()
    assert result.state == StoreState.DEGRADED
    assert "captcha" in result.warnings[0].casefold() or "access-check" in result.warnings[0].casefold()


def test_structured_properties_take_priority_and_do_not_bleed_into_neighbours():
    adapter = OnlineTradeAdapter()
    listing = adapter.parse_listing('<div class="product-item" data-product-id="sku1"><a class="product-title" href="/vinilovaya_plastinka_opeth">Виниловая пластинка Opeth - Blackwater Park (2LP)</a><span class="price">5000</span><span>В наличии</span></div>')[0]
    html = '<table><tr><th>GTIN</th><td>1234567890123</td></tr><tr><th>Каталожный номер</th><td>MOVLP001</td></tr><tr><th>Лейбл</th><td>Music On Vinyl</td></tr><tr><th>Количество дисков</th><td>2</td></tr></table>'
    result = adapter.parse_product_page(html, listing)
    assert result.barcode == "1234567890123" and result.catalog_number_raw == "MOVLP001" and result.store_sku == "sku1" and result.disc_count == 2


def test_public_html_marks_unconfirmed_availability_unknown():
    adapter = OnlineTradeAdapter()
    html = '<div class="product-item" data-product-id="unknown"><a class="product-title" href="/vinilovaya_plastinka_opeth">Виниловая пластинка Opeth - Blackwater Park</a><span class="price">5000</span></div>'
    assert adapter.parse_listing(html)[0].availability == Availability.UNKNOWN


def test_wave2_offers_merge_into_one_release_and_choose_best_price(tmp_path):
    repository = SQLiteRepository(tmp_path / "wave2.sqlite3")
    for adapter_type, source, _sku in ACTIVE_ADAPTERS[:3]:
        adapter = adapter_type()
        filename = "production_listing.html" if source == "vidika" else "listing.html"
        listing = adapter.parse_listing((FIXTURES / source / filename).read_text(encoding="utf-8"))[0]
        offer = adapter.parse_product_page("EAN: 1234567890123 | Каталожный номер: MOVLP001 | Лейбл: Music On Vinyl | Год выпуска: 2021", listing)
        repository.upsert_offer(offer)
    online = OnlineTradeAdapter()
    online_listing = online.parse_listing((FIXTURES / "onlinetrade" / "mixed_media.html").read_text(encoding="utf-8"))[0]
    repository.upsert_offer(online.parse_product_page("EAN: 1234567890123 | Каталожный номер: MOVLP001 | Лейбл: Music On Vinyl | Год выпуска: 2021", online_listing))
    build_match_queue(repository)
    results = search_releases(repository, artist="opeth", title="blackwater")
    assert len(results) == 1 and len(results[0].offers) == 4
    assert results[0].lowest_price_offer is not None
    assert results[0].lowest_price_offer.store == "vinylmarkt"
