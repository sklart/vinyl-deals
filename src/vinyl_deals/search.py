"""Release search API shared by CLI now and the desktop UI later."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer, Release
from vinyl_deals.matching.normalize import barcode as normalized_barcode
from vinyl_deals.matching.normalize import catalog_number, text
from vinyl_deals.pricing import DEFAULT_FRESHNESS_DAYS, DealClass, condition_bucket, evaluate_offer


@dataclass(frozen=True, slots=True)
class OfferSearchResult:
    offer_id: int
    store: str
    price: Decimal | None
    availability: Availability
    condition: str | None
    url: str
    deal_class: DealClass | None
    discount_pct: Decimal | None


@dataclass(frozen=True, slots=True)
class ReleaseSearchResult:
    release_id: int
    artist: str
    title: str
    label: str | None
    catalog_number: str | None
    barcode: str | None
    release_year: int | None
    format: str | None
    offers: tuple[OfferSearchResult, ...]
    best_offer: OfferSearchResult | None
    discogs_url: str | None


def discogs_search_url(release: Release) -> str | None:
    """Build a search URL only; this never claims a specific Discogs release."""
    if release.barcode:
        query = normalized_barcode(release.barcode) or release.barcode
    elif release.catalog_number:
        query = " ".join(part for part in (release.catalog_number, release.label) if part)
    else:
        query = " ".join(str(part) for part in (release.artist, release.title, release.release_year) if part)
    return f"https://www.discogs.com/search/?{urlencode({'q': query, 'type': 'all'})}" if query else None


def _matches(release: Release, *, artist: str | None, title: str | None, barcode: str | None, catalog: str | None, label: str | None, year: int | None, format: str | None) -> bool:
    def includes(value: str | None, query: str | None) -> bool:
        return not query or text(query) in text(value)

    barcode_query = normalized_barcode(barcode) if barcode else None
    catalog_query = catalog_number(catalog) if catalog else ""
    return (
        includes(release.artist, artist)
        and includes(release.title, title)
        and (not barcode_query or normalized_barcode(release.barcode) == barcode_query)
        and (not catalog_query or catalog_query in catalog_number(release.catalog_number))
        and includes(release.label, label)
        and (year is None or release.release_year == year)
        and includes(release.format, format)
    )


def search_offers(repository: SQLiteRepository, release_id: int, *, now: datetime | None = None, freshness_days: int = DEFAULT_FRESHNESS_DAYS) -> tuple[OfferSearchResult, ...]:
    """Return fresh offers for a release, enriched with conservative pricing data."""
    point = now or datetime.now(timezone.utc)
    cutoff = point - timedelta(days=freshness_days)
    results: list[OfferSearchResult] = []
    for offer_id, offer in repository.offers_for_release(release_id):
        if offer.fetched_at < cutoff:
            continue
        deal = evaluate_offer(repository, offer_id, now=point, freshness_days=freshness_days)
        results.append(OfferSearchResult(offer_id, offer.source, offer.price, offer.availability, offer.condition_media, offer.url, deal.deal_class if deal else None, deal.discount_pct if deal else None))
    return tuple(sorted(results, key=lambda item: (item.availability != Availability.IN_STOCK, item.price is None, item.price or Decimal("0"), item.store.casefold(), item.offer_id)))


def find_best_offer(offers: tuple[OfferSearchResult, ...]) -> OfferSearchResult | None:
    """Select the lowest price within one trusted media-condition grade.

    New/sealed is preferred when present; otherwise grades are considered from
    strongest to weakest. Unknown condition never becomes a price benchmark.
    """
    buckets: dict[str, list[OfferSearchResult]] = {}
    for result in offers:
        if result.availability != Availability.IN_STOCK or result.price is None or result.price <= 0:
            continue
        offer_bucket = condition_bucket(RawOffer(source=result.store, source_product_id=str(result.offer_id), url=result.url, fetched_at=datetime.now(timezone.utc), condition_media=result.condition))
        if offer_bucket != "unknown":
            buckets.setdefault(offer_bucket, []).append(result)
    for bucket in ("new", "nm", "ex", "vg+", "vg", "good"):
        if candidates := buckets.get(bucket):
            return min(candidates, key=lambda item: (item.price, item.store.casefold(), item.offer_id))
    return None


def search_releases(repository: SQLiteRepository, *, artist: str | None = None, title: str | None = None, barcode: str | None = None, catalog: str | None = None, label: str | None = None, year: int | None = None, format: str | None = None, now: datetime | None = None, freshness_days: int = DEFAULT_FRESHNESS_DAYS) -> list[ReleaseSearchResult]:
    """Find Releases using normalized AND semantics for every supplied field."""
    return [
        ReleaseSearchResult(release.id, release.artist, release.title, release.label, release.catalog_number, release.barcode, release.release_year, release.format, offers := search_offers(repository, release.id, now=now, freshness_days=freshness_days), find_best_offer(offers), discogs_search_url(release))
        for release in repository.releases_for_search()
        if _matches(release, artist=artist, title=title, barcode=barcode, catalog=catalog, label=label, year=year, format=format)
    ]
