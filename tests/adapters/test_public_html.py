from datetime import datetime, timezone
from hashlib import sha256

from vinyl_deals.adapters.public_html import PublicHtmlVinylAdapter
from vinyl_deals.domain import Availability, RawOffer, StoreSearchQuery, StoreSearchStatus
from vinyl_deals.adapters.wave2 import TishinaAdapter


class _Adapter(PublicHtmlVinylAdapter):
    source = "fixture"
    base_url = "https://shop.example"
    search_path = "/verified-search/"


def _json_product(product: str) -> str:
    return f'<script type="application/ld+json">{product}</script>'


def test_jsonld_gtin_only_keeps_barcode_out_of_identity_fields():
    url = "https://shop.example/vinyl/opeth"
    adapter = _Adapter()
    html = _json_product('{"@type":"Product","name":"Vinyl Opeth - Blackwater Park LP","url":"' + url + '","gtin13":"4006381333931","offers":{"price":"5000","availability":"InStock"}}')
    offer = adapter.parse_listing(html, fetched_at=datetime(2026, 9, 10, tzinfo=timezone.utc))[0]
    assert offer.barcode == "4006381333931" and offer.store_sku is None
    assert offer.source_product_id == sha256(url.encode("utf-8")).hexdigest()[:20]
    assert offer.condition_media == offer.condition_sleeve == "UNKNOWN"


def test_jsonld_sku_and_gtin_have_separate_roles():
    adapter = _Adapter()
    html = _json_product('{"@type":"Product","name":"Vinyl Opeth - Blackwater Park LP","url":"/vinyl/opeth","sku":"SHOP-42","productID":"PRODUCT-9","gtin":"4006381333931","offers":{"price":"5000","availability":"InStock"}}')
    offer = adapter.parse_listing(html)[0]
    assert (offer.source_product_id, offer.store_sku, offer.barcode, offer.availability) == ("PRODUCT-9", "SHOP-42", "4006381333931", Availability.IN_STOCK)


def test_jsonld_product_id_and_gtin_do_not_create_a_store_sku():
    adapter = _Adapter()
    html = _json_product('{"@type":"Product","name":"Vinyl Opeth - Blackwater Park LP","url":"/vinyl/opeth","productID":"PRODUCT-9","gtin":"4006381333931","offers":{"price":"5000"}}')
    offer = adapter.parse_listing(html)[0]
    assert (offer.source_product_id, offer.store_sku, offer.barcode) == ("PRODUCT-9", None, "4006381333931")


def test_jsonld_sku_becomes_source_id_only_without_product_id():
    adapter = _Adapter()
    html = _json_product('{"@type":"Product","name":"Vinyl Opeth - Blackwater Park LP","url":"/vinyl/opeth","sku":"SHOP-42","offers":{"price":"5000"}}')
    offer = adapter.parse_listing(html)[0]
    assert (offer.source_product_id, offer.store_sku, offer.barcode) == ("SHOP-42", "SHOP-42", None)


def test_targeted_search_uses_public_search_page_not_catalogue(monkeypatch):
    adapter = _Adapter()
    requested = []
    html = _json_product('{"@type":"Product","name":"Vinyl Opeth - Blackwater Park LP","url":"/vinyl/opeth","sku":"SHOP-42","offers":{"price":"5000","availability":"InStock"}}')
    monkeypatch.setattr(adapter, "_fetch", lambda url: requested.append(url) or html)
    result = adapter.search_offers(StoreSearchQuery(artist="Opeth", title="Blackwater Park"))
    assert result.state.value == "active" and len(result.offers) == 1
    assert requested == ["https://shop.example/verified-search/?q=Opeth+Blackwater+Park"]


def test_unconfigured_html_adapter_refuses_unverified_generic_search():
    class Unconfigured(PublicHtmlVinylAdapter):
        source = "unconfigured"; base_url = "https://shop.example"
    result = Unconfigured().search_offers(StoreSearchQuery(title="Communique"))
    assert result.state.value == "degraded"
    assert "no verified" in result.warnings[0]
    assert result.status == StoreSearchStatus.UNSUPPORTED


def test_verified_new_store_can_opt_into_new_condition_without_changing_generic_parser():
    generic = _Adapter().parse_listing(_json_product('{"@type":"Product","name":"Vinyl Opeth - Blackwater Park LP","url":"/vinyl/opeth","sku":"SHOP-42","offers":{"price":"5000"}}'))[0]
    assert generic.condition_media == "UNKNOWN"
    adapter = TishinaAdapter()
    new = adapter._product_from_values(
        name="Виниловая пластинка Opeth - Blackwater Park LP", url="/vinyl/opeth", product_id="42",
        price=5000, availability_value="В наличии", timestamp=datetime(2026, 9, 10, tzinfo=timezone.utc), raw_data={},
    )
    assert new is not None and new.condition_media == new.condition_sleeve == "NEW"


def test_captcha_script_on_a_normal_bitrix_page_is_not_treated_as_a_block() -> None:
    assert not PublicHtmlVinylAdapter._is_blocked('<script src="/captcha/widget.js"></script><main>Каталог</main>')


def test_visible_interactive_challenge_requires_user_action(monkeypatch) -> None:
    adapter = _Adapter()
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<main><h1>Servicepipe access-check</h1><js-challenge-loader></js-challenge-loader></main>")
    result = adapter.search_offers(StoreSearchQuery(title="Communique"))
    assert result.status == StoreSearchStatus.NEEDS_USER_ACTION


def test_plain_http_403_remains_restricted(monkeypatch) -> None:
    from urllib.error import HTTPError
    adapter = _Adapter()
    monkeypatch.setattr(adapter, "_fetch", lambda _url: (_ for _ in ()).throw(HTTPError("https://shop.example", 403, "forbidden", {}, None)))
    result = adapter.search_offers(StoreSearchQuery(title="Communique"))
    assert result.status == StoreSearchStatus.RESTRICTED


def test_live_search_ranks_exact_title_before_substring():
    adapter = _Adapter()
    offers = [
        RawOffer.now(source="fixture", source_product_id="space", url="https://shop.example/space", artist_raw="Flower Kings", title_raw="Space Revolver"),
        RawOffer.now(source="fixture", source_product_id="beatles", url="https://shop.example/beatles", artist_raw="Beatles", title_raw="Revolver"),
    ]
    assert [item.source_product_id for item in adapter._matching_search_cards(offers, StoreSearchQuery(title="Revolver"))] == ["beatles", "space"]
