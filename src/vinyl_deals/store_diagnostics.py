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
    search: str
    price: str
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
        reason = getattr(adapter, "targeted_search_reason", None)
        if source in {"pult", "onlinetrade"}:
            search, price = "RESTRICTED", "RESTRICTED"
        elif reason:
            search, price = "UNSUPPORTED", "UNSUPPORTED"
        else:
            search, price = "LIVE OK", "DETAIL OK"
        rows.append(StoreCoverage(
            source, AUDITED_LABELS.get(source, source), search, price,
            "NO" if type(adapter).enrich_offer is BaseStoreAdapter.enrich_offer else "YES",
            history.get(source, "unknown"),
        ))
    return tuple(rows)
