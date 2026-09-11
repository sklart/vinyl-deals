"""Release search API shared by CLI now and the desktop UI later."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer, Release
from vinyl_deals.effective_price import calculate_effective_price
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
    city: str | None = None
    local_store: bool = False
    pickup_available: bool = False
    effective_price: Decimal | None = None
    effective_price_known: bool = False
    market_median: Decimal | None = None
    comparable_count: int = 0


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
    best_new_offer: OfferSearchResult | None
    best_used_offer: OfferSearchResult | None
    lowest_price_offer: OfferSearchResult | None
    discogs_url: str | None
    best_effective_offer: OfferSearchResult | None = None
    discogs_confidence: str | None = None
    discogs_release_id: int | None = None
    offer_count: int = 0
    store_count: int = 0
    has_possible_matches: bool = False
    market_median: Decimal | None = None
    comparable_count: int = 0
    discount_pct: Decimal | None = None
    deal_class: DealClass | None = None

    @property
    def best_offer(self) -> OfferSearchResult | None:
        """Deprecated compatibility alias; prefer the explicit best fields."""
        return self.best_new_offer or self.best_used_offer


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
        effective = calculate_effective_price(offer)
        results.append(OfferSearchResult(
            offer_id, offer.source, offer.price, offer.availability, offer.condition_media, offer.url,
            deal.deal_class if deal else None, deal.discount_pct if deal else None,
            offer.city, offer.local_store, offer.pickup_available,
            effective.total if effective else None, effective.delivery_known if effective else False,
            deal.market_median if deal else None, deal.comparable_count if deal else 0,
        ))
    return tuple(sorted(results, key=lambda item: (item.availability != Availability.IN_STOCK, item.price is None, item.price or Decimal("0"), item.store.casefold(), item.offer_id)))


def _available(offers: tuple[OfferSearchResult, ...]) -> list[OfferSearchResult]:
    return [offer for offer in offers if offer.availability == Availability.IN_STOCK and offer.price is not None and offer.price > 0]


def _bucket(offer: OfferSearchResult) -> str:
    return condition_bucket(RawOffer(source=offer.store, source_product_id=str(offer.offer_id), url=offer.url, fetched_at=datetime.now(timezone.utc), condition_media=offer.condition))


def _lowest(offers: list[OfferSearchResult]) -> OfferSearchResult | None:
    return min(offers, key=lambda item: (item.price, item.store.casefold(), item.offer_id)) if offers else None


def find_best_offer(offers: tuple[OfferSearchResult, ...]) -> OfferSearchResult | None:
    """Deprecated alias for the previous trusted-best selection policy."""
    return find_best_new_offer(offers) or find_best_used_offer(offers)


def find_best_new_offer(offers: tuple[OfferSearchResult, ...]) -> OfferSearchResult | None:
    return _lowest([offer for offer in _available(offers) if _bucket(offer) == "new"])


def find_best_used_offer(offers: tuple[OfferSearchResult, ...]) -> OfferSearchResult | None:
    return _lowest([offer for offer in _available(offers) if _bucket(offer) in {"nm", "ex", "vg+", "vg", "good"}])


def find_lowest_price_offer(offers: tuple[OfferSearchResult, ...]) -> OfferSearchResult | None:
    """Lowest current available price; condition is deliberately not a filter."""
    return _lowest(_available(offers))


def find_best_effective_offer(offers: tuple[OfferSearchResult, ...]) -> OfferSearchResult | None:
    """Lowest complete effective price; unknown delivery is ineligible."""
    candidates = [offer for offer in _available(offers) if offer.effective_price_known and offer.effective_price is not None]
    return min(candidates, key=lambda item: (item.effective_price, item.store.casefold(), item.offer_id)) if candidates else None


def search_releases(repository: SQLiteRepository, *, artist: str | None = None, title: str | None = None, barcode: str | None = None, catalog: str | None = None, label: str | None = None, year: int | None = None, format: str | None = None, now: datetime | None = None, freshness_days: int = DEFAULT_FRESHNESS_DAYS) -> list[ReleaseSearchResult]:
    """Find Releases using normalized AND semantics for every supplied field."""
    results: list[ReleaseSearchResult] = []
    for release in repository.releases_for_search():
        if not _matches(release, artist=artist, title=title, barcode=barcode, catalog=catalog, label=label, year=year, format=format):
            continue
        offers = search_offers(repository, release.id, now=now, freshness_days=freshness_days)
        confirmed = repository.confirmed_discogs_match(release.id)
        possible_offer_ids = {offer_id for pair in repository.possible_matches() for offer_id in pair[:2]}
        lowest = find_lowest_price_offer(offers)
        results.append(ReleaseSearchResult(
            release.id, release.artist, release.title, release.label, release.catalog_number, release.barcode,
            release.release_year, release.format, offers, find_best_new_offer(offers), find_best_used_offer(offers),
            lowest, str(confirmed["discogs_url"]) if confirmed else discogs_search_url(release),
            find_best_effective_offer(offers), str(confirmed["confidence"]) if confirmed else None,
            int(confirmed["discogs_release_id"]) if confirmed else None,
            len(offers), len({offer.store for offer in offers}), any(offer.offer_id in possible_offer_ids for offer in offers),
            lowest.market_median if lowest else None, lowest.comparable_count if lowest else 0,
            lowest.discount_pct if lowest else None, lowest.deal_class if lowest else None,
        ))
    return results
