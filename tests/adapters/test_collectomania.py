from decimal import Decimal
from pathlib import Path
from vinyl_deals.adapters.collectomania import CollectomaniaAdapter
from vinyl_deals.domain import Availability
from vinyl_deals.database import SQLiteRepository

def test_parses_collectomania_listing_offline() -> None:
    adapter = CollectomaniaAdapter(); html = Path("tests/fixtures/collectomania/listing.html").read_text(encoding="utf-8"); offers = adapter.parse_listing(html)
    assert adapter._next_page(html) == "/collection/vinil?page=2"; assert len(offers) == 1
    item = offers[0]; assert (item.source_product_id, item.artist_raw, item.title_raw, item.price, item.format, item.label, item.release_year, item.availability) == ("470406636", "Kanye West", "Vultures 1", Decimal("5990.0"), "2LP", "YZY", 2024, Availability.IN_STOCK); assert "Coloured Vinyl" in item.edition_tags

def test_enriches_collectomania_card_offline() -> None:
    adapter = CollectomaniaAdapter(); listing = adapter.parse_listing(Path("tests/fixtures/collectomania/listing.html").read_text(encoding="utf-8"))[0]; item = adapter.parse_product_page(Path("tests/fixtures/collectomania/product.html").read_text(encoding="utf-8"), listing)
    assert (item.barcode, item.format, item.vinyl_size, item.disc_count, item.condition_media, item.condition_sleeve) == ("684514255738", "LP", '12" Vinyl', 2, "NEW_SEALED", "NEW_SEALED")


def test_enriched_collectomania_offer_survives_persistence(tmp_path) -> None:
    adapter = CollectomaniaAdapter()
    listing = adapter.parse_listing(Path("tests/fixtures/collectomania/listing.html").read_text(encoding="utf-8"))[0]
    enriched = adapter.parse_product_page(Path("tests/fixtures/collectomania/product.html").read_text(encoding="utf-8"), listing)
    repository = SQLiteRepository(tmp_path / "offers.sqlite3")
    repository.upsert_offer(enriched)
    assert repository.offers_for_matching() == [(1, enriched)]
