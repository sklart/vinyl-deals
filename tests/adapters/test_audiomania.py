from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from vinyl_deals.adapters.audiomania import AudiomaniaAdapter
from vinyl_deals.domain import Availability, StoreState


def test_parses_audiomania_public_jsonld_and_stable_ids() -> None:
    adapter = AudiomaniaAdapter()
    html = Path("tests/fixtures/audiomania/listing.html").read_text(encoding="utf-8")
    offers = adapter.parse_listing(html, fetched_at=datetime(2026, 9, 10, tzinfo=timezone.utc))
    first = next(item for item in offers if item.source_product_id == "AM-100")
    sold = next(item for item in offers if item.source_product_id == "AM-101")
    assert (first.source_product_id, first.artist_raw, first.title_raw, first.price, first.format) == ("AM-100", "Opeth", "Blackwater Park 2LP", Decimal("5990"), "2LP")
    assert first.availability == Availability.IN_STOCK and sold.availability == Availability.OUT_OF_STOCK
    assert next(item for item in adapter.parse_listing(html.replace('"5990"', '"5490"')) if item.source_product_id == "AM-100").source_product_id == "AM-100"


def test_audiomania_enrichment_and_unavailable_source_degrade(monkeypatch) -> None:
    adapter = AudiomaniaAdapter()
    offer = adapter.parse_listing(Path("tests/fixtures/audiomania/listing.html").read_text(encoding="utf-8"))[0]
    detail = adapter.parse_product_page(Path("tests/fixtures/audiomania/product.html").read_text(encoding="utf-8"), offer)
    assert (detail.barcode, detail.catalog_number_raw, detail.label) == ("4006381333931", "MOVLP001", "Music On Vinyl")
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>no catalogue</html>")
    assert adapter.get_catalog().state == StoreState.DEGRADED


def test_audiomania_ignores_malformed_and_non_vinyl_jsonld_products() -> None:
    adapter = AudiomaniaAdapter()
    html = '''<script type="application/ld+json">[
      {"@type":"Product","sku":"TURN-1","name":"Проигрыватель винила","url":"https://www.audiomania.ru/proigryvateli_vinila/a.html","category":"Проигрыватели винила","offers":{"price":"50000"}},
      {"@type":"Product","sku":"BROKEN","name":"Без цены","url":"https://www.audiomania.ru/vinilovye_plastinki/b.html","category":"Виниловые пластинки","offers":{}}
    ]</script>'''
    assert adapter.parse_listing(html) == []


def test_audiomania_captcha_page_is_degraded(monkeypatch) -> None:
    adapter = AudiomaniaAdapter()
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>CAPTCHA</html>")
    assert adapter.get_catalog().state == StoreState.DEGRADED


def test_audiomania_catalogue_deduplicates_repeated_public_product(monkeypatch) -> None:
    adapter = AudiomaniaAdapter()
    html = Path("tests/fixtures/audiomania/listing.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda _url: html + html)
    result = adapter.get_catalog()
    assert len(result.offers) == 2
