from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from time import sleep

from vinyl_deals.domain import Availability, RawOffer, StoreSearchQuery, StoreSearchResult, StoreState
from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.live_search import live_search


def offer(source: str, identifier: str, price: int, *, barcode: str = "4006381333931") -> RawOffer:
    return RawOffer.now(
        source=source, source_product_id=identifier, url=f"https://{source}.test/{identifier}",
        artist_raw="Pink Floyd", title_raw="Wish You Were Here", barcode=barcode,
        price=Decimal(price), availability=Availability.IN_STOCK, condition_media="NEW",
    )


class Adapter:
    def __init__(self, source: str, offers: tuple[RawOffer, ...], delay: float = 0, state: StoreState = StoreState.ACTIVE) -> None:
        self.source, self.offers, self.delay, self.state = source, offers, delay, state

    def search_offers(self, _query):
        sleep(self.delay)
        return StoreSearchResult(self.source, self.offers, self.state, ("blocked",) if self.state != StoreState.ACTIVE else ())

    def enrich_offer(self, item):
        return replace(item, raw_data={"fully_enriched": True})


def test_live_search_runs_stores_in_parallel_persists_and_matches(tmp_path):
    repository = SQLiteRepository(tmp_path / "live.sqlite3")
    reports = []
    result = live_search(
        repository, StoreSearchQuery(artist="Pink Floyd", title="Wish You Were Here"),
        adapter_factories={
            "one": lambda: Adapter("one", (offer("one", "1", 5000),), .08),
            "two": lambda: Adapter("two", (offer("two", "2", 4000),), .08),
        },
        per_store_timeout=1, global_timeout=1, progress=reports.append,
    )
    assert len(reports) == 2
    assert len(result.releases) == 1
    release = result.releases[0]
    assert release.lowest_price_offer and release.lowest_price_offer.price == Decimal("4000")
    assert {item.source for item in result.stores} == {"one", "two"}
    assert len(repository.price_history(1)) == 1


def test_timeout_and_degraded_store_do_not_hide_partial_results(tmp_path):
    repository = SQLiteRepository(tmp_path / "partial.sqlite3")
    result = live_search(
        repository, StoreSearchQuery(barcode="4006381333931"),
        adapter_factories={
            "fast": lambda: Adapter("fast", (offer("fast", "1", 5000),)),
            "slow": lambda: Adapter("slow", (offer("slow", "1", 3000),), .25),
            "blocked": lambda: Adapter("blocked", (), state=StoreState.DEGRADED),
        },
        per_store_timeout=.05, global_timeout=.2,
    )
    by_source = {item.source: item for item in result.stores}
    assert result.releases and by_source["fast"].offers == 1
    assert by_source["slow"].state == StoreState.DEGRADED
    assert by_source["blocked"].state == StoreState.DEGRADED


def test_live_search_does_not_call_full_catalogue(tmp_path):
    class SearchOnly(Adapter):
        def get_catalog(self):
            raise AssertionError("full catalogue must not run")
    result = live_search(
        SQLiteRepository(tmp_path / "targeted.sqlite3"), StoreSearchQuery(catalog_number="PFRLP9"),
        adapter_factories={"only": lambda: SearchOnly("only", (offer("only", "1", 5000),))},
    )
    assert result.stores[0].offers == 1
