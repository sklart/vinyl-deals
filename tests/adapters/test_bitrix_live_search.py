from pathlib import Path

import pytest

from vinyl_deals.adapters.wave2 import AVSoundAdapter, TishinaAdapter, VinylmarktAdapter
from vinyl_deals.domain import StoreSearchQuery, StoreState


@pytest.mark.parametrize(("adapter_type", "fixture", "expected_url"), [
    (VinylmarktAdapter, "vinylmarkt/listing.html", "https://vinylmarkt.ru/catalog/?q=Opeth+Blackwater+Park"),
    (TishinaAdapter, "tishina/listing.html", "https://msk.tishina.shop/catalog/?type=catalog&q=Opeth+Blackwater+Park"),
    (AVSoundAdapter, "avsound/listing.html", "https://avsound.ru/catalog/?q=Opeth+Blackwater+Park"),
])
def test_bitrix_adapters_use_their_public_catalog_search_form(monkeypatch, adapter_type, fixture, expected_url) -> None:
    adapter = adapter_type()
    calls: list[str] = []
    html = Path("tests/fixtures/wave2", fixture).read_text(encoding="utf-8")
    monkeypatch.setattr(adapter, "_fetch", lambda url: calls.append(url) or html)
    result = adapter.search_offers(StoreSearchQuery(artist="Opeth", title="Blackwater Park"))
    assert result.state == StoreState.ACTIVE
    assert calls == [expected_url]
    assert result.offers and result.offers[0].artist_raw == "Opeth"
