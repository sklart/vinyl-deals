from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from vinyl_deals.adapters.rio_rostov import RioRostovAdapter
from vinyl_deals.domain import Availability
from vinyl_deals.pricing import condition_bucket


def test_parses_rio_public_listing_as_local_offers() -> None:
    offers = RioRostovAdapter().parse_listing(
        Path("tests/fixtures/rio_rostov/listing.html").read_text(encoding="utf-8"),
        fetched_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    assert len(offers) == 2
    first, unavailable = offers
    assert (first.source, first.source_product_id, first.artist_raw, first.title_raw, first.price) == (
        "rio_rostov", "c84871", "Billie Eilish", "Dont Smile At Me (LP)", Decimal("4650"),
    )
    assert (first.city, first.local_store, first.pickup_available, first.delivery_available) == ("Ростов-на-Дону", True, True, True)
    assert unavailable.availability == Availability.OUT_OF_STOCK


def test_enriches_rio_public_card_without_changing_local_flags() -> None:
    adapter = RioRostovAdapter()
    listing = adapter.parse_listing(Path("tests/fixtures/rio_rostov/listing.html").read_text(encoding="utf-8"))[0]
    offer = adapter.parse_product_page(Path("tests/fixtures/rio_rostov/product.html").read_text(encoding="utf-8"), listing)
    assert (offer.barcode, offer.release_year, offer.country, offer.format, offer.condition_media) == (
        "00199957236263", 2026, "Europe", "LP", "SEALED",
    )
    assert offer.local_store and offer.pickup_available


def test_parses_rio_embedded_public_catalogue_payload() -> None:
    html = '<script>{"data":{"products":[{"title":"Artist - Album","product_id":255,"price":2500,"amount":1}]}}</script>'
    offer = RioRostovAdapter().parse_listing(html)[0]
    assert (offer.source_product_id, offer.url, offer.price, offer.availability) == ("ff", "https://rio-music.online/o/ff/", Decimal("2500"), Availability.IN_STOCK)


def test_rio_condition_parser_handles_discogs_style_pairs() -> None:
    adapter = RioRostovAdapter()
    assert adapter._condition("NM/NM") == "NM"
    assert adapter._condition("VG+/VG+") == "VG+"
    assert adapter._condition("S/S") == "SEALED"
    listing = adapter.parse_listing(Path("tests/fixtures/rio_rostov/listing.html").read_text(encoding="utf-8"))[0]
    for raw, bucket in (("NM/NM", "nm"), ("VG+/VG+", "vg+"), ("S/S", "new")):
        parsed = adapter.parse_product_page(f"<p>{raw}</p>", listing)
        assert condition_bucket(parsed) == bucket
