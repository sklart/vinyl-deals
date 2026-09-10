from pathlib import Path

from vinyl_deals.adapters.vinyl_ru import VinylRuAdapter
from vinyl_deals.domain import Availability, StoreSearchQuery, StoreState


def test_vinyl_ru_uses_public_autocomplete_then_only_returned_album_pages(monkeypatch) -> None:
    adapter = VinylRuAdapter()
    calls: list[str] = []
    suggestion = Path("tests/fixtures/vinyl_ru/live_search.json").read_text(encoding="utf-8")
    page = Path("tests/fixtures/vinyl_ru/live_search_album.html").read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch_text", lambda url: calls.append(url) or (suggestion if "smartSearch" in url else page))
    result = adapter.search_offers(StoreSearchQuery(artist="Dire Straits", title="Communique"))
    assert result.state == StoreState.ACTIVE
    assert calls == [
        "https://vinyl.ru/local/ajax/smartSearch.php?term=Dire+Straits+Communique",
        "https://vinyl.ru/catalog/album/communique/",
    ]
    assert [(offer.source_product_id, offer.artist_raw, offer.title_raw, offer.price, offer.availability) for offer in result.offers] == [
        ("99378", "Dire Straits", "Communique", 4500, Availability.IN_STOCK),
    ]
