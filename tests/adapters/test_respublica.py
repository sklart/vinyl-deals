from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from vinyl_deals.adapters.respublica import RespublicaAdapter
from vinyl_deals.domain import Availability, StoreSearchQuery, StoreSearchStatus, StoreState


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


def test_respublica_store_article_is_not_treated_as_pressing_catalog_number() -> None:
    adapter = RespublicaAdapter()
    offer = adapter.parse_listing(Path("tests/fixtures/respublica/listing.html").read_text(encoding="utf-8"))[0]
    product = '<p>Артикул: SHOP-123</p><p>Лейбл: Jazz Label</p>'
    detailed = adapter.parse_product_page(product, offer)
    assert detailed.catalog_number_raw is None


def test_respublica_detail_confirms_selected_product_price_and_stock() -> None:
    adapter = RespublicaAdapter()
    offer = adapter.parse_listing(Path("tests/fixtures/respublica/listing.html").read_text(encoding="utf-8"))[0]
    product = '<main itemtype="https://schema.org/Product"><meta itemprop="price" content="2990"><button>Добавить в корзину</button></main>'
    detailed = adapter.parse_product_page(product, offer)
    assert detailed.price == Decimal("2990")
    assert detailed.availability == Availability.IN_STOCK
    assert detailed.raw_data["detail_price_confirmed"] is True


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


def test_respublica_captcha_page_is_degraded(monkeypatch) -> None:
    adapter = RespublicaAdapter(page_limit=1)
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>проверка безопасности</html>")
    assert adapter.get_catalog().state == StoreState.DEGRADED


def test_respublica_uses_public_nuxt_search_and_filters_non_vinyl(monkeypatch) -> None:
    adapter = RespublicaAdapter()
    html = Path("tests/fixtures/respublica/live_search_pink_floyd.html").read_text(encoding="utf-8")
    calls = []
    monkeypatch.setattr(adapter, "_fetch", lambda url: calls.append(url) or html)
    result = adapter.search_offers(StoreSearchQuery(artist="Pink Floyd", title="The Wall"))
    assert calls == ["https://www.respublica.ru/search?query=Pink+Floyd+The+Wall"]
    assert result.status == StoreSearchStatus.FOUND and len(result.offers) == 1
    offer = result.offers[0]
    assert (offer.source_product_id, offer.store_sku, offer.price, offer.old_price, offer.availability) == ("624342", "1505879", Decimal("5260"), Decimal("6750"), Availability.IN_STOCK)
    assert offer.url.endswith("/muzyka-na-vinile/vinilovye-plastinki/1505879-pink-floyd-the-wall-remastered-repress-2lp")


def test_respublica_parses_current_production_nuxt_iife_shape(monkeypatch) -> None:
    adapter = RespublicaAdapter()
    html = Path("tests/fixtures/respublica/live_search_production_iife.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda _url: html)
    result = adapter.search_offers(StoreSearchQuery(title="Pink Floyd"))
    assert result.status == StoreSearchStatus.FOUND and len(result.offers) == 1
    offer = result.offers[0]
    assert (offer.source_product_id, offer.store_sku, offer.price, offer.old_price) == (
        "624317", "1509830", Decimal("5260"), Decimal("6750"),
    )


def test_respublica_search_empty_and_blocked_are_structured(monkeypatch) -> None:
    adapter = RespublicaAdapter()
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>no matching products</html>")
    assert adapter.search_offers(StoreSearchQuery(title="Absent")).status == StoreSearchStatus.EMPTY
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<html>проверка безопасности</html>")
    assert adapter.search_offers(StoreSearchQuery(title="Pink Floyd")).status == StoreSearchStatus.RESTRICTED


def test_respublica_hcaptcha_configuration_in_normal_nuxt_page_is_not_a_block(monkeypatch) -> None:
    adapter = RespublicaAdapter()
    production = Path("tests/fixtures/respublica/live_search_production_iife.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda _url: production.replace("</script>", "</script><script>config={hcaptcha:'site-key'}</script>"))
    assert adapter.search_offers(StoreSearchQuery(title="Pink Floyd")).status == StoreSearchStatus.FOUND
