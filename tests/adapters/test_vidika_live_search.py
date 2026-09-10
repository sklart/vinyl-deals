from pathlib import Path

from vinyl_deals.adapters.wave2 import VidikaAdapter
from vinyl_deals.domain import StoreSearchQuery, StoreState


def test_vidika_uses_the_public_webasyst_search_form(monkeypatch) -> None:
    adapter = VidikaAdapter()
    calls: list[str] = []
    # This saved production card structure is used by Vidika's public
    # Webasyst search page as well as its category pages.
    html = Path("tests/fixtures/wave2/vidika/production_listing.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda url: calls.append(url) or html)
    result = adapter.search_offers(StoreSearchQuery(artist="Opeth", title="Blackwater Park"))
    assert result.state == StoreState.ACTIVE
    assert calls == ["https://vidika.su/search/?query=Opeth+Blackwater+Park"]
    assert [(offer.source_product_id, offer.store_sku) for offer in result.offers] == [("2202", "1268")]
