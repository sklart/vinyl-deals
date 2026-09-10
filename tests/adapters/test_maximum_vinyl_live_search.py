from decimal import Decimal
from pathlib import Path

from vinyl_deals.adapters.wave2 import MaximumVinylAdapter
from vinyl_deals.domain import Availability, StoreSearchQuery, StoreState


def test_maximum_vinyl_uses_public_vinyl_autocomplete_json(monkeypatch) -> None:
    adapter = MaximumVinylAdapter()
    calls: list[str] = []
    payload = Path("tests/fixtures/wave2/maximum_vinyl_live_search.json").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda url: calls.append(url) or payload)
    result = adapter.search_offers(StoreSearchQuery(artist="Dire Straits", title="Communiqué"))
    assert result.state == StoreState.ACTIVE
    assert calls == [
        "https://maximumvinyl.ru/index.php?route=common%2Fsearch%2FajaxLiveSearch&filter_name=Dire+Straits+Communiqu%C3%A9&filter_category_id=60"
    ]
    assert [(offer.source_product_id, offer.store_sku, offer.price, offer.availability) for offer in result.offers] == [
        ("8618", "8618", Decimal("4950"), Availability.IN_STOCK),
        ("14574", "14574", None, Availability.OUT_OF_STOCK),
    ]
