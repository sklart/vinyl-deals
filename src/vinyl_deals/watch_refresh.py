"""Targeted live refresh for enabled watchlist releases.

This module deliberately composes the existing federated ``live_search``
service.  It never enumerates shop catalogues, so a scheduler cycle scales
with the user's watchlist rather than the size of every store.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import Release, StoreSearchQuery, StoreSearchStatus
from vinyl_deals.live_search import LiveSearchResult, live_search


@dataclass(frozen=True, slots=True)
class WatchRefreshResult:
    watched: int
    checked: int
    partial: int
    offers_updated: int
    fresh_offers: int = 0
    cached_offers: int = 0
    fresh_offer_ids: tuple[int, ...] = ()


def query_for_release(release: Release) -> StoreSearchQuery:
    """Build the strongest public search request available for a pressing.

    Identifier fields are retained alongside text metadata even when the
    external search text is a barcode.  The enrichment validator can then
    reject a similarly named but physically different pressing.
    """
    return StoreSearchQuery(
        artist=release.artist or None,
        title=release.title or None,
        barcode=release.barcode or None,
        catalog_number=release.catalog_number or None,
        label=release.label or None,
    )


def fallback_queries(release: Release) -> tuple[StoreSearchQuery, ...]:
    """Return progressively broader public terms without discarding IDs.

    ``StoreSearchQuery`` retains barcode/catalog/label in every attempt, so
    the detail-stage validator still excludes a different pressing even when
    a shop's search form can only find it by artist/title.
    """
    base = query_for_release(release)
    terms = (
        base.barcode,
        " ".join(part for part in (base.catalog_number, base.label) if part) or None,
        " ".join(part for part in (base.artist, base.title) if part) or None,
    )
    queries, seen = [], set()
    for term in terms:
        if term and term not in seen:
            queries.append(replace(base, search_text=term))
            seen.add(term)
    return tuple(queries) or (base,)


def _query_key(query: StoreSearchQuery) -> tuple[str | None, ...]:
    return (query.barcode, query.catalog_number, query.label, query.artist, query.title)


def _status(result: LiveSearchResult) -> tuple[str, int, int, int, bool]:
    """Translate structured per-store outcomes into a persisted watch state."""
    stores = result.stores
    fresh_count = result.fresh_offer_count
    cached_count = result.cached_offer_count
    offer_count = fresh_count + cached_count
    succeeded = [store for store in stores if store.status_kind in {StoreSearchStatus.FOUND.value, StoreSearchStatus.EMPTY.value, StoreSearchStatus.CACHED.value}]
    unavailable = [store for store in stores if store.status_kind not in {StoreSearchStatus.FOUND.value, StoreSearchStatus.EMPTY.value, StoreSearchStatus.CACHED.value}]
    if not succeeded:
        return "ERROR", offer_count, fresh_count, cached_count, False
    if unavailable:
        return "PARTIAL", offer_count, fresh_count, cached_count, True
    if offer_count == 0:
        return "NO_RESULTS", 0, 0, 0, False
    return "OK", offer_count, fresh_count, cached_count, False


def refresh_watchlist(
    repository: SQLiteRepository,
    *,
    live_search_service: Callable[..., LiveSearchResult] = live_search,
    progress: Callable[[str], None] | None = None,
) -> WatchRefreshResult:
    """Refresh enabled watches serially; each query fans out inside live_search.

    Equal release/query metadata is deduplicated so accidental duplicate
    Release rows do not multiply store requests. Every associated watch still
    receives its own persisted check result.
    """
    emit = progress or (lambda _message: None)
    entries = repository.watchlist_entries(enabled_only=True)
    if not entries:
        emit("Отслеживание: нет включённых пластинок.")
        return WatchRefreshResult(0, 0, 0, 0)

    groups: dict[tuple[str | None, ...], list[dict[str, object]]] = {}
    for entry in entries:
        release = repository.release_by_id(int(entry["release_id"]))
        if release is None:
            repository.record_watch_refresh(int(entry["release_id"]), status="ERROR", offer_count=0)
            continue
        groups.setdefault(_query_key(query_for_release(release)), []).append(entry)

    checked = partial = offers_updated = fresh_offers = cached_offers = 0
    fresh_offer_ids: set[int] = set()
    total = len(groups)
    for index, grouped_entries in enumerate(groups.values(), start=1):
        release = repository.release_by_id(int(grouped_entries[0]["release_id"]))
        if release is None:  # Defensive: a concurrent deletion cannot stop batch work.
            continue
        queries = fallback_queries(release)
        emit(f"Проверка {index}/{total}: {release.artist} — {release.title}")
        try:
            result = None
            cached_result = None
            for query in queries:
                result = live_search_service(repository, query)
                # A display-only cached answer intentionally does not stop
                # the fallback chain: it cannot confirm present stock.
                if result.fresh_offer_count:
                    break
                if result.cached_offer_count and cached_result is None:
                    cached_result = result
            # Preserve a useful cached answer for the UI if every broader
            # live lookup was empty/unavailable.  It still cannot create an
            # alert because only ``fresh_offer_ids`` are passed downstream.
            if result is not None and not result.fresh_offer_count and cached_result is not None:
                result = cached_result
            assert result is not None
            state, count, fresh_count, cached_count, is_partial = _status(result)
        except Exception:
            # A failed lookup never mutates or removes older offers.  Its only
            # durable effect is diagnostic state for the associated watches.
            state, count, fresh_count, cached_count, is_partial = "ERROR", 0, 0, 0, False
        for entry in grouped_entries:
            repository.record_watch_refresh(
                int(entry["release_id"]), status=state, offer_count=count,
                fresh_offer_count=fresh_count, cached_offer_count=cached_count,
            )
            checked += 1
        offers_updated += count
        fresh_offers += fresh_count
        cached_offers += cached_count
        if result is not None:
            fresh_offer_ids.update(result.fresh_offer_ids)
        partial += len(grouped_entries) if is_partial else 0
    return WatchRefreshResult(
        len(entries), checked, partial, offers_updated,
        fresh_offers, cached_offers, tuple(sorted(fresh_offer_ids)),
    )
