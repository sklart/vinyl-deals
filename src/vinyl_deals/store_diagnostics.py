"""Static, network-free capability audit for the public store adapters."""
from __future__ import annotations

from dataclasses import dataclass

from vinyl_deals.adapters.base import BaseStoreAdapter
from vinyl_deals.adapters.droog_rostov import DroogRostovAdapter
from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.updates import DEFAULT_ADAPTER_FACTORIES, STORE_LABELS


@dataclass(frozen=True, slots=True)
class StoreCoverage:
    source: str
    label: str
    search_capability: str
    price_capability: str
    detail_enrichment: str
    last_known_status: str


AUDITED_FACTORIES = {**DEFAULT_ADAPTER_FACTORIES, "droog_rostov": DroogRostovAdapter}
AUDITED_LABELS = {**STORE_LABELS, "droog_rostov": "Друг (публичный профиль)"}


def store_coverage(repository: SQLiteRepository) -> tuple[StoreCoverage, ...]:
    """Describe public capability without contacting a store.

    The adapter itself is the source of truth for declared search support;
    scrape history merely adds the last observed operational state.
    """
    history = {store: status for store, status, _finished in repository.latest_scrape_runs()}
    rows = []
    for source, factory in AUDITED_FACTORIES.items():
        adapter = factory()
        search_implemented = type(adapter).search_offers is not BaseStoreAdapter.search_offers
        detail_implemented = type(adapter).enrich_offer is not BaseStoreAdapter.enrich_offer
        search = "PUBLIC SEARCH" if search_implemented else "UNSUPPORTED"
        price = "PRODUCT DETAIL" if detail_implemented else "LISTING ONLY"
        # This is the last audited access outcome, not a statement about
        # whether a public search URL is declared by the adapter. A future
        # store recovery therefore changes runtime status without rewriting
        # its capability.
        audited = "RESTRICTED" if source in {"pult", "onlinetrade"} else "not audited"
        rows.append(StoreCoverage(
            source, AUDITED_LABELS.get(source, source), search, price,
            "YES" if detail_implemented else "NO",
            history.get(source, audited),
        ))
    return tuple(rows)
