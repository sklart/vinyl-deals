from decimal import Decimal
from pathlib import Path
from vinyl_deals.adapters.imagine_club import ImagineClubAdapter
from vinyl_deals.domain import Availability, StoreSearchQuery

def test_parses_listing_fixture_without_network() -> None:
    html = Path("tests/fixtures/imagine_club/listing.html").read_text(encoding="utf-8"); adapter = ImagineClubAdapter(); offers = adapter.parse_listing(html)
    assert adapter._last_page(html) == 708; assert len(offers) == 1
    offer = offers[0]; assert (offer.source_product_id, offer.artist_raw, offer.title_raw, offer.price) == ("74605", "10 cc", "Bloody Tourists", Decimal("3000")); assert (offer.country, offer.format, offer.condition_media, offer.condition_sleeve, offer.availability) == ("USA", "LP", "EX", "EX", Availability.IN_STOCK)

def test_enriches_listing_from_product_fixture() -> None:
    adapter = ImagineClubAdapter(); listing = adapter.parse_listing(Path("tests/fixtures/imagine_club/listing.html").read_text(encoding="utf-8"))[0]; detail = adapter.parse_product_page(Path("tests/fixtures/imagine_club/product.html").read_text(encoding="utf-8"), listing)
    assert (detail.store_sku, detail.catalog_number_raw, detail.label, detail.release_year, detail.condition_media, detail.condition_sleeve) == ("00-00002589", None, "Polydor", 1978, "EX", "VG+"); assert "Gatefold" in detail.edition_tags


def test_enrichment_tolerates_a_card_without_description() -> None:
    adapter = ImagineClubAdapter()
    listing = adapter.parse_listing(Path("tests/fixtures/imagine_club/listing.html").read_text(encoding="utf-8"))[0]
    detail = adapter.parse_product_page("", listing)
    assert detail.description is None


def test_catalog_reports_current_page_and_remaining_work() -> None:
    adapter = ImagineClubAdapter(page_limit=2, delay_seconds=0)
    html = Path("tests/fixtures/imagine_club/listing.html").read_text(encoding="utf-8")
    calls: list[str] = []
    adapter._fetch = lambda url: html  # type: ignore[method-assign]
    adapter.progress_callback = calls.append

    adapter.get_catalog()

    assert calls == [
        "Imagine Club: страницы 1/2, осталось 1",
        "Imagine Club: страницы 2/2, осталось 0",
    ]


def test_targeted_search_uses_verified_drupal_endpoint(monkeypatch) -> None:
    adapter = ImagineClubAdapter()
    html = Path("tests/fixtures/imagine_club/live_search_communique.html").read_text(encoding="utf-8")
    calls = []
    monkeypatch.setattr(adapter, "_fetch", lambda url: calls.append(url) or html)
    result = adapter.search_offers(StoreSearchQuery(title="Communique"))
    assert result.offers and calls == ["https://imagine-club.com/search?search_api_views_fulltext=Communique"]
