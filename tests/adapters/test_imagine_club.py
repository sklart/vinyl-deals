from decimal import Decimal
from pathlib import Path
from vinyl_deals.adapters.imagine_club import ImagineClubAdapter
from vinyl_deals.domain import Availability

def test_parses_listing_fixture_without_network() -> None:
    html = Path("tests/fixtures/imagine_club/listing.html").read_text(encoding="utf-8"); adapter = ImagineClubAdapter(); offers = adapter.parse_listing(html)
    assert adapter._last_page(html) == 708; assert len(offers) == 1
    offer = offers[0]; assert (offer.source_product_id, offer.artist_raw, offer.title_raw, offer.price) == ("74605", "10 cc", "Bloody Tourists", Decimal("3000")); assert (offer.country, offer.format, offer.condition_media, offer.condition_sleeve, offer.availability) == ("USA", "LP", "EX", "EX", Availability.IN_STOCK)

def test_enriches_listing_from_product_fixture() -> None:
    adapter = ImagineClubAdapter(); listing = adapter.parse_listing(Path("tests/fixtures/imagine_club/listing.html").read_text(encoding="utf-8"))[0]; detail = adapter.parse_product_page(Path("tests/fixtures/imagine_club/product.html").read_text(encoding="utf-8"), listing)
    assert (detail.store_sku, detail.catalog_number_raw, detail.label, detail.release_year, detail.condition_media, detail.condition_sleeve) == ("00-00002589", None, "Polydor", 1978, "EX", "VG+"); assert "Gatefold" in detail.edition_tags
