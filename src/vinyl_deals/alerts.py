"""Watchlist evaluation and alert persistence, independent from transports."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer, Release
from vinyl_deals.effective_price import calculate_effective_price
from vinyl_deals.pricing import DEFAULT_FRESHNESS_DAYS, DealClass, evaluate_offer
from vinyl_deals.search import discogs_search_url


class AlertEvent:
    NEW_STOCK = "NEW_STOCK"
    PRICE_DROP = "PRICE_DROP"
    HISTORICAL_LOW = "HISTORICAL_LOW"
    GOOD_DEAL = "GOOD_DEAL"
    LOCAL_NEW_STOCK = "LOCAL_NEW_STOCK"
    LOCAL_PRICE_DROP = "LOCAL_PRICE_DROP"
    LOCAL_HISTORICAL_LOW = "LOCAL_HISTORICAL_LOW"


@dataclass(frozen=True, slots=True)
class AlertCandidate:
    release_id: int
    offer_id: int
    event_type: str
    event_key: str
    payload: dict[str, object]


def _rank(value: str | None) -> int:
    ranks = {"NORMAL": 0, "INTERESTING": 1, "GOOD": 2, "HOT": 3, "VERY_HOT": 4}
    return ranks.get(value or "NORMAL", 0)


def _previous_stock(repository: SQLiteRepository, offer_id: int) -> bool:
    """First observed stock is alert-worthy; unchanged stock is not."""
    with repository._connect() as connection:
        values = [row[0] for row in connection.execute("SELECT availability FROM price_history WHERE offer_id=? ORDER BY observed_at", (offer_id,))]
    return len(values) == 1 or (len(values) > 1 and values[-2] != Availability.IN_STOCK.value)


def _passes_filters(entry: dict[str, object], offer: RawOffer) -> bool:
    if entry["local_only"] and not offer.local_store:
        return False
    if entry["city"] and offer.city != entry["city"]:
        return False
    if entry["pickup_only"] and not offer.pickup_available:
        return False
    maximum = Decimal(str(entry["max_price"])) if entry["max_price"] is not None else None
    if maximum is None:
        return True
    if offer.local_store:
        effective = calculate_effective_price(offer)
        return bool(effective and effective.delivery_known and effective.total is not None and effective.total <= maximum)
    return bool(offer.price is not None and offer.price <= maximum)


def _payload(release: Release, offer: RawOffer, deal, event_type: str) -> dict[str, object]:
    effective = calculate_effective_price(offer)
    return {
        "event_type": event_type, "artist": release.artist, "title": release.title,
        "label": release.label, "catalog_number": release.catalog_number, "release_year": release.release_year,
        "store": offer.source, "price": offer.price, "market_median": deal.market_median if deal else None,
        "discount_pct": deal.discount_pct if deal else None, "url": offer.url, "discogs_url": discogs_search_url(release),
        "local_store": offer.local_store, "city": offer.city, "pickup_available": offer.pickup_available,
        "effective_price": effective.total if effective else None,
        "effective_price_known": effective.delivery_known if effective else False,
    }


def evaluate_watchlist(repository: SQLiteRepository, *, now: datetime | None = None, freshness_days: int = DEFAULT_FRESHNESS_DAYS) -> list[AlertCandidate]:
    """Evaluate enabled watches and persist only alert states not seen before."""
    point = now or datetime.now(timezone.utc)
    candidates: list[AlertCandidate] = []
    for entry in repository.watchlist_entries(enabled_only=True):
        release = repository.release_by_id(int(entry["release_id"]))
        if release is None:
            continue
        for offer_id, offer in repository.offers_for_release(release.id):
            if offer.availability != Availability.IN_STOCK or offer.fetched_at < point - timedelta(days=freshness_days) or not _passes_filters(entry, offer):
                continue
            deal = evaluate_offer(repository, offer_id, now=point, freshness_days=freshness_days)
            local = offer.local_store
            events: list[tuple[str, str]] = []
            if _previous_stock(repository, offer_id):
                events.append((AlertEvent.LOCAL_NEW_STOCK if local else AlertEvent.NEW_STOCK, f"stock:{offer.fetched_at.isoformat()}"))
            if deal and deal.price_drop_pct is not None:
                events.append((AlertEvent.LOCAL_PRICE_DROP if local else AlertEvent.PRICE_DROP, f"price:{offer.fetched_at.isoformat()}:{offer.price}"))
            if deal and deal.is_historical_low:
                events.append((AlertEvent.LOCAL_HISTORICAL_LOW if local else AlertEvent.HISTORICAL_LOW, f"low:{offer.fetched_at.isoformat()}:{offer.price}"))
            if deal and _rank(str(deal.deal_class)) >= max(2, _rank(entry["min_deal_class"] if isinstance(entry["min_deal_class"], str) else None)):
                events.append((AlertEvent.GOOD_DEAL, f"deal:{deal.deal_class}:{deal.discount_pct}"))
            for event_type, state in events:
                candidate = AlertCandidate(release.id, offer_id, event_type, state, _payload(release, offer, deal, event_type))
                if repository.save_alert(release_id=candidate.release_id, offer_id=candidate.offer_id, event_type=candidate.event_type, event_key=candidate.event_key, payload=candidate.payload):
                    candidates.append(candidate)
    return candidates
