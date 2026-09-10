"""Every production source must opt into search or explain its safe fallback."""
from __future__ import annotations

import pytest

from vinyl_deals.adapters.respublica import RespublicaAdapter
from vinyl_deals.adapters.rio_rostov import RioRostovAdapter
from vinyl_deals.adapters.wave2 import (
    AVSoundAdapter, OnlineTradeAdapter, PultAdapter,
    TishinaAdapter, VernoshopAdapter, VidikaAdapter, VinylmarktAdapter,
)
from vinyl_deals.domain import StoreSearchQuery, StoreState


@pytest.mark.parametrize("factory", [
    RioRostovAdapter, RespublicaAdapter,
    OnlineTradeAdapter, PultAdapter,
])
def test_unverified_sources_explicitly_degrade_targeted_search(factory) -> None:
    adapter = factory()
    result = adapter.search_offers(StoreSearchQuery(artist="Pink Floyd", title="Wish You Were Here"))
    assert result.state == StoreState.DEGRADED
    assert result.warnings and "verified" in result.warnings[0].casefold() or "restricted" in result.warnings[0].casefold() or "export" in result.warnings[0].casefold() or "collections" in result.warnings[0].casefold()
