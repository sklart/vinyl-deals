"""Targeted live refresh for enabled watchlist releases.

This module deliberately composes the existing federated ``live_search``
service.  It never enumerates shop catalogues, so a scheduler cycle scales
with the user's watchlist rather than the size of every store.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from inspect import Parameter, signature

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import RawOffer, Release, StoreSearchQuery, StoreSearchStatus
from vinyl_deals.live_search import LiveSearchResult, live_search
from vinyl_deals.matching.normalize import catalog_number, format_and_disc_count, normalize_barcode, text


@dataclass(frozen=True, slots=True)
class WatchRefreshResult:
    watched: int
    checked: int
    partial: int
    offers_updated: int
    fresh_confirmed_offers: int = 0
    possible_offers: int = 0
    cached_offers: int = 0
    fresh_confirmed_offer_ids: tuple[int, ...] = ()
    possible_offer_ids: tuple[int, ...] = ()

    @property
    def fresh_offers(self) -> int:
        """Compatibility name: only confirmed fresh evidence is fresh."""
        return self.fresh_confirmed_offers

    @property
    def fresh_offer_ids(self) -> tuple[int, ...]:
        """Compatibility name for alert callers."""
        return self.fresh_confirmed_offer_ids


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


def _status(stores: tuple[object, ...], confirmed_count: int, possible_count: int, cached_count: int) -> tuple[str, int, bool]:
    """Translate structured per-store outcomes into a persisted watch state."""
    offer_count = confirmed_count + possible_count + cached_count
    succeeded = [store for store in stores if store.status_kind in {StoreSearchStatus.FOUND.value, StoreSearchStatus.EMPTY.value, StoreSearchStatus.CACHED.value}]
    unavailable = [store for store in stores if store.status_kind not in {StoreSearchStatus.FOUND.value, StoreSearchStatus.EMPTY.value, StoreSearchStatus.CACHED.value}]
    needs_user_action = any(store.status_kind == StoreSearchStatus.NEEDS_USER_ACTION.value for store in stores)
    if needs_user_action:
        # Preserve this actionable state rather than hiding it behind the
        # generic ERROR/PARTIAL summary. Fresh IDs still remain separately
        # filtered before alert evaluation.
        return "NEEDS_USER_ACTION", offer_count, bool(unavailable)
    if not succeeded:
        return "ERROR", offer_count, False
    if unavailable:
        return "PARTIAL", offer_count, True
    if offer_count == 0:
        return "NO_RESULTS", 0, False
    return "OK", offer_count, False


def _service_supports_sources(service: Callable[..., LiveSearchResult]) -> bool:
    """Keep pre-Phase-13 injected test services source-compatible."""
    try:
        parameters = signature(service).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(parameter.name == "sources" or parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters)


def _search_sources(service: Callable[..., LiveSearchResult], repository: SQLiteRepository,
                    query: StoreSearchQuery, sources: tuple[str, ...] | None) -> LiveSearchResult:
    if sources is not None and _service_supports_sources(service):
        return service(repository, query, sources=sources)
    return service(repository, query)


def _conflicts(release: Release, offer: RawOffer) -> bool:
    """Reject positive conflicting identifiers; absent metadata is neutral."""
    release_barcode, offer_barcode = normalize_barcode(release.barcode), normalize_barcode(offer.barcode)
    if release_barcode and offer_barcode and release_barcode != offer_barcode:
        return True
    release_catalog, offer_catalog = catalog_number(release.catalog_number), catalog_number(offer.catalog_number_raw)
    if release_catalog and offer_catalog and release_catalog != offer_catalog:
        return True
    if release.label and offer.label and text(release.label) != text(offer.label):
        return True
    release_format, release_count = format_and_disc_count(release.format, release.disc_count)
    offer_format, offer_count = format_and_disc_count(offer.format, offer.disc_count)
    for left, right in (
        (release.release_year, offer.release_year), (release.country, offer.country),
        (release_format, offer_format), (release_count, offer_count),
        (release.vinyl_size, offer.vinyl_size), (release.rpm, offer.rpm),
        (release.vinyl_color, offer.vinyl_color),
    ):
        if left not in (None, "") and right not in (None, "") and text(str(left)) != text(str(right)):
            return True
    return False


def _broad_identity(release: Release, offer: RawOffer) -> str:
    """Classify artist/title fallback as confirmed, possible, or rejected."""
    if _conflicts(release, offer):
        return "rejected"
    release_barcode, offer_barcode = normalize_barcode(release.barcode), normalize_barcode(offer.barcode)
    if release_barcode and release_barcode == offer_barcode:
        return "confirmed"
    release_catalog, offer_catalog = catalog_number(release.catalog_number), catalog_number(offer.catalog_number_raw)
    if (release_catalog and offer_catalog and release_catalog == offer_catalog and release.label and offer.label
            and text(release.label) == text(offer.label)):
        return "confirmed"
    release_format, release_count = format_and_disc_count(release.format, release.disc_count)
    offer_format, offer_count = format_and_disc_count(offer.format, offer.disc_count)
    physical = (
        release.release_year, offer.release_year, release.country, offer.country,
        release_format, offer_format, release_count, offer_count,
    )
    if all(value not in (None, "") for value in physical):
        return "confirmed"
    return "possible"


def _ids_for_source(repository: SQLiteRepository, offer_ids: tuple[int, ...], source: str) -> set[int]:
    """Resolve persisted IDs without making a sparse result an alert input."""
    found: set[int] = set()
    for offer_id in offer_ids:
        try:
            if repository.offer_by_id(offer_id)[1].source == source:
                found.add(offer_id)
        except (KeyError, ValueError):
            continue
    return found


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

    checked = partial = offers_updated = confirmed_offers = possible_offers = cached_offers = 0
    confirmed_ids: set[int] = set()
    possible_ids: set[int] = set()
    total = len(groups)
    for index, grouped_entries in enumerate(groups.values(), start=1):
        release = repository.release_by_id(int(grouped_entries[0]["release_id"]))
        if release is None:  # Defensive: a concurrent deletion cannot stop batch work.
            continue
        queries = fallback_queries(release)
        emit(f"Проверка {index}/{total}: {release.artist} — {release.title}")
        try:
            # Each store progresses through GTIN -> catalog -> artist/title
            # independently. A hit from Store A must not suppress the
            # fallback needed by Store B.
            selected: dict[str, object] = {}
            cached_by_source: dict[str, set[int]] = {}
            release_results: dict[int, object] = {}
            unresolved: tuple[str, ...] | None = None
            group_confirmed: set[int] = set()
            group_possible: set[int] = set()
            for fallback_index, query in enumerate(queries):
                result = _search_sources(live_search_service, repository, query, unresolved)
                for item in result.stores:
                    if item.source in selected and getattr(selected[item.source], "status_kind") == StoreSearchStatus.FOUND.value:
                        continue
                    selected[item.source] = item
                    if item.status_kind == StoreSearchStatus.FOUND.value and item.offers:
                        # A narrow GTIN/catalog response is independently
                        # confirmed by the query; artist/title needs physical
                        # evidence after detail enrichment.
                        offer_ids = set(result.fresh_offer_ids)
                        source_ids = _ids_for_source(repository, tuple(offer_ids), item.source)
                        if fallback_index < 2:
                            group_confirmed.update(source_ids)
                        else:
                            for offer_id in source_ids:
                                evidence = _broad_identity(release, repository.offer_by_id(offer_id)[1])
                                if evidence == "confirmed":
                                    group_confirmed.add(offer_id)
                                elif evidence == "possible":
                                    group_possible.add(offer_id)
                        # A source that reports found is resolved even when a
                        # sparse test/dry-run result lacks persisted IDs.
                    elif item.status_kind == StoreSearchStatus.CACHED.value:
                        cached_by_source.setdefault(item.source, set()).update(
                            _ids_for_source(repository, result.cached_offer_ids, item.source)
                        )
                for found_release in result.releases:
                    release_results[found_release.release_id] = found_release
                unresolved = tuple(
                    source for source, item in selected.items()
                    if item.status_kind != StoreSearchStatus.FOUND.value or not item.offers
                )
                if not unresolved:
                    break
            stores = tuple(selected.values())
            cached_ids = set().union(*cached_by_source.values()) if cached_by_source else set()
            cached_ids -= group_confirmed | group_possible
            # A fake/non-persisting service can still report a confirmed
            # narrow result for GUI progress, but it can never create an
            # alert: downstream only receives persisted confirmed IDs.
            possible_sources = {
                repository.offer_by_id(offer_id)[1].source for offer_id in group_possible
            }
            report_confirmed = len(group_confirmed) or sum(
                item.offers for item in stores
                if item.status_kind == StoreSearchStatus.FOUND.value and item.source not in possible_sources
            )
            state, count, is_partial = _status(stores, report_confirmed, len(group_possible), len(cached_ids))
            result = LiveSearchResult(
                query_for_release(release), tuple(release_results.values()), stores,
                fresh_confirmed_offer_ids=tuple(sorted(group_confirmed)),
                possible_offer_ids=tuple(sorted(group_possible)),
                fresh_offer_ids=tuple(sorted(group_confirmed)), cached_offer_ids=tuple(sorted(cached_ids)),
            )
            fresh_count, possible_count, cached_count = report_confirmed, len(group_possible), len(cached_ids)
        except Exception:
            # A failed lookup never mutates or removes older offers.  Its only
            # durable effect is diagnostic state for the associated watches.
            state, count, fresh_count, possible_count, cached_count, is_partial = "ERROR", 0, 0, 0, 0, False
        for entry in grouped_entries:
            repository.record_watch_refresh(
                int(entry["release_id"]), status=state, offer_count=count,
                fresh_offer_count=fresh_count, cached_offer_count=cached_count,
            )
            checked += 1
        offers_updated += count
        confirmed_offers += fresh_count
        possible_offers += possible_count
        cached_offers += cached_count
        if result is not None:
            confirmed_ids.update(result.fresh_confirmed_offer_ids)
            possible_ids.update(result.possible_offer_ids)
        partial += len(grouped_entries) if is_partial else 0
    return WatchRefreshResult(
        len(entries), checked, partial, offers_updated,
        confirmed_offers, possible_offers, cached_offers,
        tuple(sorted(confirmed_ids)), tuple(sorted(possible_ids)),
    )
