"""Every production source must opt into search or explain its safe fallback."""
from __future__ import annotations

import pytest

from vinyl_deals.adapters.rio_rostov import RioRostovAdapter
from vinyl_deals.adapters.droog_rostov import DroogRostovAdapter
from vinyl_deals.adapters.wave2 import (
    AVSoundAdapter, OnlineTradeAdapter, PultAdapter,
    TishinaAdapter, VernoshopAdapter, VidikaAdapter, VinylmarktAdapter,
)
from vinyl_deals.domain import StoreSearchQuery, StoreSearchStatus, StoreState


@pytest.mark.parametrize(("factory", "status"), [
    (RioRostovAdapter, StoreSearchStatus.UNSUPPORTED),
    (DroogRostovAdapter, StoreSearchStatus.UNSUPPORTED),
    (PultAdapter, StoreSearchStatus.RESTRICTED),
])
def test_unverified_sources_report_structured_targeted_search_status(factory, status) -> None:
    adapter = factory()
    result = adapter.search_offers(StoreSearchQuery(artist="Pink Floyd", title="Wish You Were Here"))
    assert result.state == StoreState.DEGRADED
    assert result.status == status


def test_onlinetrade_reports_real_access_check_as_restricted(monkeypatch) -> None:
    adapter = OnlineTradeAdapter()
    monkeypatch.setattr(adapter, "_fetch", lambda _url: "<script src='https://servicepipe.tech/check.js'></script><js-challenge-loader></js-challenge-loader>")
    result = adapter.search_offers(StoreSearchQuery(title="Wish You Were Here"))
    assert result.state == StoreState.DEGRADED
    assert result.status == StoreSearchStatus.RESTRICTED
