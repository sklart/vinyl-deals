from pathlib import Path

from vinyl_deals.adapters.wave2 import VernoshopAdapter
from vinyl_deals.domain import StoreSearchQuery, StoreState


def test_vernoshop_opencart_live_search_rejects_non_vinyl_candidates(monkeypatch) -> None:
    adapter = VernoshopAdapter()
    calls: list[str] = []
    payload = Path("tests/fixtures/wave2/vernoshop_live_search.json").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda url: calls.append(url) or payload)
    result = adapter.search_offers(StoreSearchQuery(title="Communique"))
    assert result.state == StoreState.ACTIVE
    assert calls == [
        "https://vernoshop.com/index.php?route=common%2Fsearch%2FajaxLiveSearch&filter_name=Communique&filter_category_id=0"
    ]
    assert result.offers == ()
