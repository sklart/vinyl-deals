from pathlib import Path

from vinyl_deals.adapters.wave2 import VernoshopAdapter
from vinyl_deals.domain import StoreSearchQuery, StoreState


def test_vernoshop_opencart_live_search_rejects_non_vinyl_candidates(monkeypatch) -> None:
    adapter = VernoshopAdapter()
    calls: list[str] = []
    payload = Path("tests/fixtures/wave2/vernoshop/live_search_communique.json").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda url: calls.append(url) or payload)
    result = adapter.search_offers(StoreSearchQuery(title="Communique"))
    assert result.state == StoreState.ACTIVE
    assert calls == [
        "https://vernoshop.com/index.php?route=common%2Fsearch%2FajaxLiveSearch&filter_name=Communique&filter_category_id=0"
    ]
    assert result.offers == ()


def test_vernoshop_opencart_live_search_keeps_confirmed_vinyl_and_rejects_cd(monkeypatch) -> None:
    adapter = VernoshopAdapter()
    payload = Path("tests/fixtures/wave2/vernoshop/live_search_pink_floyd.json").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda _url: payload)

    result = adapter.search_offers(StoreSearchQuery(artist="Pink Floyd"))

    assert result.state == StoreState.ACTIVE
    assert result.offers
    assert {offer.source_product_id for offer in result.offers}.isdisjoint({"158068", "108098", "159794", "156747", "101170", "158089"})
    assert {offer.source_product_id for offer in result.offers} >= {"158600", "107735", "107728"}
