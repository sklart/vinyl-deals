"""Concurrent targeted store search, independent from full catalogue refresh."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Callable

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import RawOffer, StoreSearchQuery, StoreSearchResult, StoreSearchStatus, StoreState
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
    releases: tuple[ReleaseSearchResult, ...] = ()
    status: StoreSearchStatus | None = None

    @property
    def status_kind(self) -> str:
        """Human-facing outcome; StoreState remains the persistence contract."""
        if self.status is not None:
            return self.status.value
        if self.cached:
            return StoreSearchStatus.CACHED.value
        if self.state == StoreState.ACTIVE:
            return StoreSearchStatus.FOUND.value if self.offers else StoreSearchStatus.EMPTY.value
        # Old third-party adapters which have not supplied structured status
        # are an error, rather than trying to guess from prose warnings.
        return StoreSearchStatus.ERROR.value

    @property
    def detail(self) -> str:
        return "; ".join((*self.warnings, *self.errors))


@dataclass(frozen=True, slots=True)
class LiveSearchResult:
    query: StoreSearchQuery
    releases: tuple[ReleaseSearchResult, ...]
    stores: tuple[LiveStoreResult, ...]
    possible_matches: tuple[tuple[int, int, float, str], ...] = ()
    # A normal live search has no watch-specific validation stage, so every
    # fresh persisted offer is confirmed by default.  Targeted watch refresh
    # may narrow this set after a broad artist/title lookup.
    fresh_confirmed_offer_ids: tuple[int, ...] = ()
    possible_offer_ids: tuple[int, ...] = ()
    fresh_offer_ids: tuple[int, ...] = ()
    cached_offer_ids: tuple[int, ...] = ()

    @property
    def fresh_offer_count(self) -> int:
        # Test/dry-run services may not have persisted IDs. Their structured
        # store reports still accurately describe whether a query was fresh.
        return len(self.fresh_offer_ids) or sum(
            store.offers for store in self.stores
            if not store.cached and store.status_kind == StoreSearchStatus.FOUND.value
        )

    @property
    def fresh_confirmed_offer_count(self) -> int:
        """Fresh evidence safe to use for a watchlist alert."""
        return len(self.fresh_confirmed_offer_ids) or len(self.fresh_offer_ids)

    @property
    def cached_offer_count(self) -> int:
        return len(self.cached_offer_ids) or sum(store.offers for store in self.stores if store.cached)


ProgressCallback = Callable[[LiveStoreResult], None]


def _materialize(repository: SQLiteRepository, discovered: list[RawOffer], query: StoreSearchQuery) -> tuple[ReleaseSearchResult, ...]:
    """Persist a completed batch and expose its grouping immediately."""
    unique = {(offer.source, offer.source_product_id): offer for offer in discovered}
    for offer in unique.values():
        repository.upsert_offer(offer)
    build_match_queue(repository)
    searched_ids = [offer_id for offer_id, offer in repository.offers_for_matching() if (offer.source, offer.source_product_id) in unique]
    repository.ensure_releases_for_unmatched_offers(searched_ids)
    releases = search_releases(repository, artist=query.artist, title=query.title, barcode=query.barcode, catalog=query.catalog_number, label=query.label, now=datetime.now(timezone.utc))
    return tuple(sorted(releases, key=lambda release: (release.artist.casefold(), release.title.casefold(), release.release_id)))


def _cached_offers(repository: SQLiteRepository, source: str, query: StoreSearchQuery) -> tuple[RawOffer, ...]:
    """Small persisted fallback only; it never starts a catalogue crawl."""
    barcode = normalize_barcode(query.barcode) if query.barcode else None
    catalog = catalog_number(query.catalog_number) if query.catalog_number else ""
    label = text(query.label) if query.label else ""
    terms = [text(value) for value in (query.artist, query.title) if value]
    found = []
    for _, offer in repository.offers_for_matching():
        if offer.source != source:
            continue
        if barcode and normalize_barcode(offer.barcode) != barcode:
            continue
        if catalog and catalog not in catalog_number(offer.catalog_number_raw):
            continue
        if label and label not in text(offer.label):
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
    # Search engines often put incidental substring hits ahead of the exact
    # album requested by the user.  Rank before the detail-request limit, so
    # an exact "Revolver" card is not displaced by "Space Revolver".
    for offer in _rank_live_offers(result.offers, query)[:enrichment_limit]:
        try:
            # Some adapters parse every detail while searching.  They flag it
            # in raw data, avoiding a duplicate network request.
            offers.append(offer if offer.raw_data.get("fully_enriched") else adapter.enrich_offer(offer))
        except Exception as error:  # A broken card must not discard a store.
            offers.append(offer)
            warnings.append(f"detail enrichment failed for {offer.source_product_id}: {type(error).__name__}")
    validated = tuple(_validated_identifiers(offers, query))
    return StoreSearchResult(
        result.source, validated, result.state, tuple(warnings), result.errors,
        StoreSearchStatus.FOUND if validated else StoreSearchStatus.EMPTY,
    )


def _rank_live_offers(offers: tuple[RawOffer, ...], query: StoreSearchQuery) -> list[RawOffer]:
    """Put exact artist/title hits ahead of broad text-search matches."""
    wanted_artist = text(query.artist) if query.artist else ""
    wanted_title = text(query.title) if query.title else ""

    def rank(offer: RawOffer) -> tuple[int, int, str, str]:
        artist = text(offer.artist_raw)
        title = text(offer.title_raw)
        exact_artist = bool(wanted_artist) and artist == wanted_artist
        exact_title = bool(wanted_title) and title == wanted_title
        prefix_title = bool(wanted_title) and title.startswith(wanted_title)
        return (
            0 if exact_artist and exact_title else 1 if exact_title else 2 if prefix_title else 3,
            0 if exact_artist else 1,
            artist,
            title,
        )

    return sorted(offers, key=rank)


def _validated_identifiers(offers: list[RawOffer], query: StoreSearchQuery) -> list[RawOffer]:
    """Use enriched identifiers as a high-confidence exclusion, not a prerequisite."""
    wanted_barcode = normalize_barcode(query.barcode) if query.barcode else None
    wanted_catalog = catalog_number(query.catalog_number) if query.catalog_number else ""
    wanted_label = text(query.label) if query.label else ""
    accepted: list[RawOffer] = []
    for offer in offers:
        actual_barcode = normalize_barcode(offer.barcode)
        actual_catalog = catalog_number(offer.catalog_number_raw)
        if wanted_barcode and actual_barcode and actual_barcode != wanted_barcode:
            continue
        if wanted_catalog and actual_catalog and wanted_catalog not in actual_catalog:
            continue
        if wanted_label and offer.label and wanted_label not in text(offer.label):
            continue
        accepted.append(offer)
    return accepted


def live_search(
    repository: SQLiteRepository,
    query: StoreSearchQuery,
    *,
    adapter_factories: dict[str, Callable[[], object]] | None = None,
    sources: tuple[str, ...] | None = None,
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
    if sources is not None:
        requested = set(sources)
        factories = {source: factory for source, factory in factories.items() if source in requested}
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
    fresh_discovered: list[RawOffer] = []
    cached_discovered: list[RawOffer] = []
    try:
        while pending and monotonic() - started < global_timeout:
            done, _ = wait(pending, timeout=0.05)
            for future in done:
                pending.remove(future)
                source = futures[future]
                try:
                    result = future.result()
                    fresh_discovered.extend(result.offers)
                    item = LiveStoreResult(source, result.state, len(result.offers), result.warnings, result.errors, status=result.status)
                except Exception as error:
                    item = LiveStoreResult(source, StoreState.DEGRADED, 0, errors=(f"live search failed: {type(error).__name__}",))
                if item.state == StoreState.ACTIVE:
                    item = LiveStoreResult(item.source, item.state, item.offers, item.warnings, item.errors, item.cached, _materialize(repository, fresh_discovered, query))
                reports[source] = item
                callback(item)
            now = monotonic()
            expired = [future for future in pending if now - launched[futures[future]] >= per_store_timeout]
            for future in expired:
                pending.remove(future)
                source = futures[future]
                future.cancel()
                item = LiveStoreResult(source, StoreState.DEGRADED, 0, errors=(f"live search timed out after {per_store_timeout:g}s",), status=StoreSearchStatus.TIMEOUT)
                reports[source] = item
                callback(item)
        for future in pending:
            source = futures[future]
            future.cancel()
            item = LiveStoreResult(source, StoreState.DEGRADED, 0, errors=(f"global live-search timeout after {global_timeout:g}s",), status=StoreSearchStatus.TIMEOUT)
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
                cached_discovered.extend(cached)
                item = LiveStoreResult(source, item.state, len(cached), item.warnings, item.errors, cached=True, status=StoreSearchStatus.CACHED)
                reports[source] = item
                callback(item)
    releases = _materialize(repository, fresh_discovered, query)
    fresh_keys = {(offer.source, offer.source_product_id) for offer in fresh_discovered}
    cached_keys = {(offer.source, offer.source_product_id) for offer in cached_discovered} - fresh_keys
    found_ids = {
        (offer.source, offer.source_product_id): offer_id
        for offer_id, offer in repository.offers_for_matching()
    }
    searched_ids = [offer_id for key, offer_id in found_ids.items() if key in fresh_keys | cached_keys]
    fresh_ids = tuple(sorted(found_ids[key] for key in fresh_keys if key in found_ids))
    cached_ids = tuple(sorted(found_ids[key] for key in cached_keys if key in found_ids))
    searched_id_set = set(searched_ids)
    possible = tuple(
        row for row in repository.possible_matches()
        if row[0] in searched_id_set or row[1] in searched_id_set
    )
    return LiveSearchResult(
        query, releases,
        tuple(reports.get(source, LiveStoreResult(source, StoreState.DEGRADED, 0)) for source in factories),
        possible, fresh_ids, (), fresh_ids, cached_ids,
    )
