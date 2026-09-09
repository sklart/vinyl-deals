"""Conservative market and historical deal evaluation for matched releases."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from statistics import median

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import RawOffer


class DealClass(StrEnum):
    NORMAL = "NORMAL"
    INTERESTING = "INTERESTING"
    GOOD = "GOOD"
    HOT = "HOT"
    VERY_HOT = "VERY_HOT"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class DealResult:
    offer_id: int
    release_id: int
    current_price: Decimal
    market_median: Decimal | None
    comparable_count: int
    discount_pct: Decimal | None
    deal_class: DealClass
    historical_min: Decimal | None
    median_30d: Decimal | None
    median_90d: Decimal | None
    previous_price: Decimal | None
    is_historical_low: bool
    reasons: tuple[str, ...]


def median_price(prices: list[Decimal]) -> Decimal | None:
    return Decimal(str(median(prices))) if prices else None


def condition_bucket(offer: RawOffer) -> str:
    value = (offer.condition_media or "").casefold()
    return "new" if value.startswith("new") else "used" if value else "unknown"


def classify(discount: Decimal | None, comparable_count: int) -> DealClass:
    if discount is None or comparable_count < 2:
        return DealClass.INSUFFICIENT
    if discount < 10: return DealClass.NORMAL
    if discount < 15: return DealClass.INTERESTING
    if discount < 25: return DealClass.GOOD
    if comparable_count < 3: return DealClass.GOOD
    if discount < 35: return DealClass.HOT
    return DealClass.VERY_HOT


def evaluate_offer(repository: SQLiteRepository, offer_id: int, *, now: datetime | None = None) -> DealResult | None:
    target = repository.offer_by_id(offer_id)
    if not target or target[1].price is None or target[0] is None:
        return None
    release_id, offer = target
    comparables = repository.comparable_release_offers(release_id, condition_bucket(offer), exclude_offer_id=offer_id)
    # One store contributes at most one current price; lowest is the useful offer.
    by_store: dict[str, Decimal] = {}
    for _, other in comparables:
        if other.price is not None:
            by_store[other.source] = min(by_store.get(other.source, other.price), other.price)
    prices = list(by_store.values())
    market = median_price(prices)
    discount = ((market - offer.price) / market * 100) if market else None
    history = repository.price_history(offer_id)
    observed = [(datetime.fromisoformat(timestamp), price) for timestamp, price in history if price is not None]
    point = now or datetime.now(timezone.utc)
    def window(days: int) -> Decimal | None:
        return median_price([price for timestamp, price in observed if timestamp >= point - timedelta(days=days)])
    historical = min((price for _, price in observed), default=None)
    previous = observed[-2][1] if len(observed) > 1 else None
    historical_low = bool(historical is not None and offer.price <= historical)
    reasons = [f"{len(prices)} comparable stores"]
    if market is None: reasons.append("market sample unavailable")
    if historical_low: reasons.append("new historical low")
    if previous is not None and offer.price < previous: reasons.append("price dropped")
    return DealResult(offer_id, release_id, offer.price, market, len(prices), discount, classify(discount, len(prices)), historical, window(30), window(90), previous, historical_low, tuple(reasons))


def evaluate_deals(repository: SQLiteRepository, release_id: int | None = None) -> list[DealResult]:
    rows = repository.release_offer_ids(release_id)
    return [result for offer_id in rows if (result := evaluate_offer(repository, offer_id)) is not None]
