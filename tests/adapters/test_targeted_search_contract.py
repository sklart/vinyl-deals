"""Every production source must opt into search or explain its safe fallback."""
from __future__ import annotations

import pytest

from vinyl_deals.adapters.respublica import RespublicaAdapter
from vinyl_deals.adapters.rio_rostov import RioRostovAdapter
from vinyl_deals.adapters.wave2 import (
    AVSoundAdapter, OnlineTradeAdapter, PultAdapter,
    TishinaAdapter, VernoshopAdapter, VidikaAdapter, VinylmarktAdapter,
)
from vinyl_deals.domain import StoreSearchQuery, StoreSearchStatus, StoreState


@pytest.mark.parametrize(("factory", "status"), [
    (RioRostovAdapter, StoreSearchStatus.UNSUPPORTED),
    (RespublicaAdapter, StoreSearchStatus.UNSUPPORTED),
    (OnlineTradeAdapter, StoreSearchStatus.UNSUPPORTED),
    (PultAdapter, StoreSearchStatus.RESTRICTED),
])
def test_unverified_sources_report_structured_targeted_search_status(factory, status) -> None:
    adapter = factory()
    result = adapter.search_offers(StoreSearchQuery(artist="Pink Floyd", title="Wish You Were Here"))
    assert result.state == StoreState.DEGRADED
    assert result.status == status
