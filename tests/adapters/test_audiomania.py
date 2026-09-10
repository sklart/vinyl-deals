from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from vinyl_deals.adapters.audiomania import AudiomaniaAdapter
from vinyl_deals.domain import Availability, StoreState


def test_parses_audiomania_public_server_rendered_product_and_stable_id() -> None:
    adapter = AudiomaniaAdapter()
    html = Path("tests/fixtures/audiomania/miles_davis.html").read_text(encoding="utf-8")
    offer = adapter.parse_public_product_page(html, "https://www.audiomania.ru/vinilovye_plastinki/miles_davis/miles_davis_kind_of_blue.html", fetched_at=datetime(2026, 9, 10, tzinfo=timezone.utc))
    assert offer is not None
    assert (offer.source_product_id, offer.artist_raw, offer.title_raw, offer.price, offer.format) == ("96384", "MILES DAVIS", "KIND OF BLUE (LP)", Decimal("2300"), "LP")
    assert offer.availability == Availability.IN_STOCK
    assert adapter.parse_public_product_page(html.replace("2 300", "1 999"), offer.url).source_product_id == "96384"


def test_audiomania_enrichment_and_unavailable_source_degrade(monkeypatch) -> None:
    adapter = AudiomaniaAdapter()
    detail = adapter.parse_public_product_page(Path("tests/fixtures/audiomania/miles_davis.html").read_text(encoding="utf-8"), "https://www.audiomania.ru/vinilovye_plastinki/miles_davis/miles_davis_kind_of_blue.html")
    assert detail is not None
    assert (detail.barcode, detail.catalog_number_raw, detail.label, detail.release_year, detail.disc_count, detail.country) == ("3770024955316", "CL-001", "Columbia", 2021, 1, "Европа")
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>no catalogue</html>")
    assert adapter.get_catalog().state == StoreState.DEGRADED


def test_audiomania_ignores_malformed_and_non_vinyl_jsonld_products() -> None:
    adapter = AudiomaniaAdapter()
    html = '''<script type="application/ld+json">[
      {"@type":"Product","sku":"TURN-1","name":"Проигрыватель винила","url":"https://www.audiomania.ru/proigryvateli_vinila/a.html","category":"Проигрыватели винила","offers":{"price":"50000"}},
      {"@type":"Product","sku":"BROKEN","name":"Без цены","url":"https://www.audiomania.ru/vinilovye_plastinki/b.html","category":"Виниловые пластинки","offers":{}}
    ]</script>'''
    assert adapter.parse_public_product_page(html, "https://www.audiomania.ru/vinilovye_proigryvateli/a.html") is None


def test_audiomania_captcha_page_is_degraded(monkeypatch) -> None:
    adapter = AudiomaniaAdapter()
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>CAPTCHA</html>")
    assert adapter.get_catalog().state == StoreState.DEGRADED


def test_audiomania_catalogue_deduplicates_repeated_public_product(monkeypatch) -> None:
    adapter = AudiomaniaAdapter(page_limit=10, delay_seconds=0)
    catalogue = Path("tests/fixtures/audiomania/catalogue.html").read_text(encoding="utf-8")
    miles = Path("tests/fixtures/audiomania/miles_davis.html").read_text(encoding="utf-8")
    amy = Path("tests/fixtures/audiomania/amy_winehouse.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda url: catalogue if url == adapter.catalog_url else (miles if "miles_davis" in url else amy))
    result = adapter.get_catalog()
    assert len(result.offers) == 2


def test_audiomania_catalogue_page_limit_and_stock_states(monkeypatch) -> None:
    adapter = AudiomaniaAdapter(page_limit=1, delay_seconds=0)
    catalogue = Path("tests/fixtures/audiomania/catalogue.html").read_text(encoding="utf-8")
    miles = Path("tests/fixtures/audiomania/miles_davis.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda url: catalogue if url == adapter.catalog_url else miles)
    result = adapter.get_catalog()
    assert len(result.offers) == 1 and result.pages_processed == 2
    assert "limited to 1" in result.warnings[0]
    sold = adapter.parse_public_product_page(Path("tests/fixtures/audiomania/amy_winehouse.html").read_text(encoding="utf-8"), "https://www.audiomania.ru/vinilovye_plastinki/amy_winehouse/amy_winehouse_back_to_black.html")
    unknown_html = miles.replace("&#1042; &#1085;&#1072;&#1083;&#1080;&#1095;&#1080;&#1080;", "Soon").replace("В наличии", "Soon")
    unknown = adapter.parse_public_product_page(unknown_html, "https://www.audiomania.ru/vinilovye_plastinki/miles_davis/miles_davis_kind_of_blue.html")
    assert sold.availability == Availability.OUT_OF_STOCK and unknown.availability == Availability.UNKNOWN


def test_audiomania_discovers_collections_even_when_root_has_incidental_products(monkeypatch) -> None:
    adapter = AudiomaniaAdapter(delay_seconds=0)
    fixtures = {
        adapter.catalog_url: "discovery_root.html",
        "https://www.audiomania.ru/vinilovye_plastinki/miles_davis/": "collection_miles.html",
        "https://www.audiomania.ru/vinilovye_plastinki/john_coltrane/": "collection_coltrane.html",
        "https://www.audiomania.ru/vinilovye_plastinki/miles_davis/miles_davis_kind_of_blue.html": "miles_davis.html",
        "https://www.audiomania.ru/vinilovye_plastinki/amy_winehouse/amy_winehouse_back_to_black.html": "amy_winehouse.html",
        "https://www.audiomania.ru/vinilovye_plastinki/john_coltrane/john_coltrane_blue_train.html": "john_coltrane.html",
    }
    calls: list[str] = []
    def fetch(url):
        calls.append(url)
        return Path("tests/fixtures/audiomania", fixtures[url]).read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", fetch)
    result = adapter.get_catalog()
    assert {offer.source_product_id for offer in result.offers} == {"96384", "23771", "12888"}
    assert calls.count("https://www.audiomania.ru/vinilovye_plastinki/miles_davis/") == 1
    assert calls.count("https://www.audiomania.ru/vinilovye_plastinki/john_coltrane/") == 1
    assert calls.count("https://www.audiomania.ru/vinilovye_plastinki/john_coltrane/john_coltrane_blue_train.html") == 1


def test_audiomania_page_limit_bounds_collection_discovery(monkeypatch) -> None:
    adapter = AudiomaniaAdapter(page_limit=1, delay_seconds=0)
    root = Path("tests/fixtures/audiomania/discovery_root.html").read_text(encoding="utf-8")
    collection = Path("tests/fixtures/audiomania/collection_miles.html").read_text(encoding="utf-8")
    product = Path("tests/fixtures/audiomania/miles_davis.html").read_text(encoding="utf-8")
    calls: list[str] = []
    def fetch(url):
        calls.append(url)
        return root if url == adapter.catalog_url else (collection if url.endswith("/miles_davis/") else product)
    monkeypatch.setattr(adapter, "_fetch", fetch)
    adapter.get_catalog()
    assert "https://www.audiomania.ru/vinilovye_plastinki/miles_davis/" in calls
    assert "https://www.audiomania.ru/vinilovye_plastinki/john_coltrane/" not in calls


def test_audiomania_does_not_refetch_fully_enriched_offer(monkeypatch) -> None:
    adapter = AudiomaniaAdapter(page_limit=1, delay_seconds=0)
    catalogue = Path("tests/fixtures/audiomania/catalogue.html").read_text(encoding="utf-8")
    miles = Path("tests/fixtures/audiomania/miles_davis.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda url: catalogue if url == adapter.catalog_url else miles)
    offer = adapter.get_catalog().offers[0]
    assert offer.raw_data["fully_enriched"] is True
    monkeypatch.setattr(adapter, "_fetch", lambda _url: (_ for _ in ()).throw(AssertionError("unexpected repeat request")))
    assert adapter.enrich_offer(offer) is offer
