"""Concurrent targeted store search, independent from full catalogue refresh."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Callable

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import RawOffer, StoreSearchQuery, StoreSearchResult, StoreState
from vinyl_deals.matching import MatchKind, match_offers
from vinyl_deals.matching.normalize import catalog_number, normalize_barcode, text
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.database.repository import ReleaseMergeConflict
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
    releases: tuple[ReleaseSearchResult, ...] = ()


@dataclass(frozen=True, slots=True)
class LiveSearchResult:
    query: StoreSearchQuery
    releases: tuple[ReleaseSearchResult, ...]
    stores: tuple[LiveStoreResult, ...]
    possible_matches: tuple[tuple[int, int, float, str], ...] = ()


ProgressCallback = Callable[[LiveStoreResult], None]


def _consolidate_compatible_live_offers(
    repository: SQLiteRepository, offer_ids: list[int]
) -> None:
    """Join incomplete live cards only when their identity has no conflict.

    The normal matcher keeps artist/title-only pairs as ``POSSIBLE`` so a
    catalogue crawl never silently joins editions with sparse shop metadata.
    In one targeted live search, however, two cards with the *same normalized*
    artist and title and no known physical/edition conflict are the useful
    provisional representation of one pressing.  Detail enrichment or Discogs
    can still later split a cluster when a real conflicting identifier arrives.
    """
    wanted = set(offer_ids)
    candidates = [item for item in repository.offers_for_matching() if item[0] in wanted]
    for index, (left_id, left) in enumerate(candidates):
        for right_id, right in candidates[index + 1 :]:
            if left.source == right.source:
                continue
            if text(left.artist_raw) != text(right.artist_raw) or text(left.title_raw) != text(right.title_raw):
                continue
            result = match_offers(left, right)
            if result.kind != MatchKind.POSSIBLE or result.blocking_conflicts:
                continue
            try:
                repository.create_release_for_pair(left_id, right_id, left, right)
            except ReleaseMergeConflict:
                # A cluster may already contain an independently discovered
                # incompatible pressing.  Keep that ambiguity separate.
                continue


def _materialize(repository: SQLiteRepository, discovered: list[RawOffer], query: StoreSearchQuery) -> tuple[ReleaseSearchResult, ...]:
    """Persist a completed batch and expose its grouping immediately."""
    unique = {(offer.source, offer.source_product_id): offer for offer in discovered}
    for offer in unique.values():
        repository.upsert_offer(offer)
    build_match_queue(repository)
    searched_ids = [offer_id for offer_id, offer in repository.offers_for_matching() if (offer.source, offer.source_product_id) in unique]
    repository.ensure_releases_for_unmatched_offers(searched_ids)
    _consolidate_compatible_live_offers(repository, searched_ids)
    releases = search_releases(repository, artist=query.artist, title=query.title, barcode=query.barcode, catalog=query.catalog_number, now=datetime.now(timezone.utc))
    return tuple(sorted(releases, key=lambda release: (release.artist.casefold(), release.title.casefold(), release.release_id)))


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
    return StoreSearchResult(result.source, tuple(_validated_identifiers(offers, query)), result.state, tuple(warnings), result.errors)


def _validated_identifiers(offers: list[RawOffer], query: StoreSearchQuery) -> list[RawOffer]:
    """Use enriched identifiers as a high-confidence exclusion, not a prerequisite."""
    wanted_barcode = normalize_barcode(query.barcode) if query.barcode else None
    wanted_catalog = catalog_number(query.catalog_number) if query.catalog_number else ""
    accepted: list[RawOffer] = []
    for offer in offers:
        actual_barcode = normalize_barcode(offer.barcode)
        actual_catalog = catalog_number(offer.catalog_number_raw)
        if wanted_barcode and actual_barcode and actual_barcode != wanted_barcode:
            continue
        if wanted_catalog and actual_catalog and wanted_catalog not in actual_catalog:
            continue
        accepted.append(offer)
    return accepted


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
                if item.state == StoreState.ACTIVE:
                    item = LiveStoreResult(item.source, item.state, item.offers, item.warnings, item.errors, item.cached, _materialize(repository, discovered, query))
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
    releases = _materialize(repository, discovered, query)
    unique = {(offer.source, offer.source_product_id): offer for offer in discovered}
    searched_ids = [offer_id for offer_id, offer in repository.offers_for_matching() if (offer.source, offer.source_product_id) in unique]
    searched_id_set = set(searched_ids)
    possible = tuple(
        row for row in repository.possible_matches()
        if row[0] in searched_id_set or row[1] in searched_id_set
    )
    return LiveSearchResult(query, releases, tuple(reports.get(source, LiveStoreResult(source, StoreState.DEGRADED, 0)) for source in factories), possible)
