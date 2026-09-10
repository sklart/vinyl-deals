"""Concurrent targeted store search, independent from full catalogue refresh."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Callable

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import RawOffer, StoreSearchQuery, StoreSearchResult, StoreState
from vinyl_deals.matching.normalize import catalog_number, normalize_barcode, text
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.search import ReleaseSearchResult, search_releases
from vinyl_deals.updates import DEFAULT_ADAPTER_FACTORIES, STORE_LABELS


@dataclass(frozen=True, slots=True)
class LiveStoreResult:
    source: str
    state: StoreState
    offers: int
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    cached: bool = False


@dataclass(frozen=True, slots=True)
class LiveSearchResult:
    query: StoreSearchQuery
    releases: tuple[ReleaseSearchResult, ...]
    stores: tuple[LiveStoreResult, ...]
    possible_matches: tuple[tuple[int, int, float, str], ...] = ()


ProgressCallback = Callable[[LiveStoreResult], None]


def _cached_offers(repository: SQLiteRepository, source: str, query: StoreSearchQuery) -> tuple[RawOffer, ...]:
    """Small persisted fallback only; it never starts a catalogue crawl."""
    barcode = normalize_barcode(query.barcode) if query.barcode else None
    catalog = catalog_number(query.catalog_number) if query.catalog_number else ""
    terms = [text(value) for value in (query.artist, query.title) if value]
    found = []
    for _, offer in repository.offers_for_matching():
        if offer.source != source:
            continue
        if barcode and normalize_barcode(offer.barcode) != barcode:
            continue
        if catalog and catalog not in catalog_number(offer.catalog_number_raw):
            continue
        value = " ".join(part or "" for part in (offer.artist_raw, offer.title_raw))
        if terms and not all(term in text(value) for term in terms):
            continue
        found.append(offer)
    return tuple(found)


def _search_store(adapter: object, query: StoreSearchQuery, enrichment_limit: int) -> StoreSearchResult:
    result = adapter.search_offers(query)
    if result.state != StoreState.ACTIVE:
        return result
    offers: list[RawOffer] = []
    warnings = list(result.warnings)
    for offer in result.offers[:enrichment_limit]:
        try:
            # Some adapters parse every detail while searching.  They flag it
            # in raw data, avoiding a duplicate network request.
            offers.append(offer if offer.raw_data.get("fully_enriched") else adapter.enrich_offer(offer))
        except Exception as error:  # A broken card must not discard a store.
            offers.append(offer)
            warnings.append(f"detail enrichment failed for {offer.source_product_id}: {type(error).__name__}")
    return StoreSearchResult(result.source, tuple(offers), result.state, tuple(warnings), result.errors)


def live_search(
    repository: SQLiteRepository,
    query: StoreSearchQuery,
    *,
    adapter_factories: dict[str, Callable[[], object]] | None = None,
    per_store_timeout: float = 15.0,
    global_timeout: float = 45.0,
    enrichment_limit: int = 10,
    progress: ProgressCallback | None = None,
) -> LiveSearchResult:
    """Search stores concurrently and persist only discovered product cards."""
    if query.is_empty():
        raise ValueError("At least one live-search field is required.")
    if per_store_timeout <= 0 or global_timeout <= 0 or enrichment_limit < 1:
        raise ValueError("Live-search timeouts and enrichment_limit must be positive.")
    factories = adapter_factories or DEFAULT_ADAPTER_FACTORIES
    callback = progress or (lambda _result: None)
    started = monotonic()
    executor = ThreadPoolExecutor(max_workers=max(1, len(factories)), thread_name_prefix="vinyl-live-search")
    futures: dict[Future[StoreSearchResult], str] = {}
    launched: dict[str, float] = {}
    for source, factory in factories.items():
        try:
            adapter = factory()
            futures[executor.submit(_search_store, adapter, query, enrichment_limit)] = source
            launched[source] = monotonic()
        except Exception as error:
            item = LiveStoreResult(source, StoreState.DEGRADED, 0, errors=(f"adapter start failed: {type(error).__name__}",))
            callback(item)
    pending = set(futures)
    reports: dict[str, LiveStoreResult] = {}
    discovered: list[RawOffer] = []
    try:
        while pending and monotonic() - started < global_timeout:
            done, _ = wait(pending, timeout=0.05)
            for future in done:
                pending.remove(future)
                source = futures[future]
                try:
                    result = future.result()
                    discovered.extend(result.offers)
                    item = LiveStoreResult(source, result.state, len(result.offers), result.warnings, result.errors)
                except Exception as error:
                    item = LiveStoreResult(source, StoreState.DEGRADED, 0, errors=(f"live search failed: {type(error).__name__}",))
                reports[source] = item
                callback(item)
            now = monotonic()
            expired = [future for future in pending if now - launched[futures[future]] >= per_store_timeout]
            for future in expired:
                pending.remove(future)
                source = futures[future]
                future.cancel()
                item = LiveStoreResult(source, StoreState.DEGRADED, 0, errors=(f"live search timed out after {per_store_timeout:g}s",))
                reports[source] = item
                callback(item)
        for future in pending:
            source = futures[future]
            future.cancel()
            item = LiveStoreResult(source, StoreState.DEGRADED, 0, errors=(f"global live-search timeout after {global_timeout:g}s",))
            reports[source] = item
            callback(item)
    finally:
        # Do not make a single slow public endpoint block partial results.
        executor.shutdown(wait=False, cancel_futures=True)

    for source in factories:
        item = reports.get(source)
        if item is None:
            continue
        if item.state == StoreState.DEGRADED:
            cached = _cached_offers(repository, source, query)
            if cached:
                discovered.extend(cached)
                item = LiveStoreResult(source, item.state, len(cached), item.warnings, item.errors, cached=True)
                reports[source] = item
                callback(item)
    # Stable source identifiers de-duplicate overlapping search suggestions.
    unique = {(offer.source, offer.source_product_id): offer for offer in discovered}
    for offer in unique.values():
        repository.upsert_offer(offer)
    build_match_queue(repository)
    searched_ids = [offer_id for offer_id, offer in repository.offers_for_matching() if (offer.source, offer.source_product_id) in unique]
    searched_id_set = set(searched_ids)
    possible = tuple(
        row for row in repository.possible_matches()
        if row[0] in searched_id_set or row[1] in searched_id_set
    )
    repository.ensure_releases_for_unmatched_offers(searched_ids)
    releases = search_releases(
        repository,
        artist=query.artist,
        title=query.title,
        barcode=query.barcode,
        catalog=query.catalog_number,
        now=datetime.now(timezone.utc),
    )
    ordered = tuple(sorted(releases, key=lambda release: (release.artist.casefold(), release.title.casefold(), release.release_id)))
    return LiveSearchResult(query, ordered, tuple(reports.get(source, LiveStoreResult(source, StoreState.DEGRADED, 0)) for source in factories), possible)
