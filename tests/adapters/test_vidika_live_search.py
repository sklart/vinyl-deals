from pathlib import Path

from vinyl_deals.adapters.wave2 import VidikaAdapter
from vinyl_deals.domain import StoreSearchQuery, StoreState


def test_vidika_uses_the_public_webasyst_search_form(monkeypatch) -> None:
    adapter = VidikaAdapter()
    calls: list[str] = []
    html = Path("tests/fixtures/wave2/vidika/live_search_communique.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda url: calls.append(url) or html)
    result = adapter.search_offers(StoreSearchQuery(title="Communique"))
    assert result.state == StoreState.ACTIVE
    assert calls == ["https://vidika.su/search/?query=Communique"]
    assert result.offers
