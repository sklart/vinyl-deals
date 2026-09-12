"""Conservative market and historical deal evaluation for matched releases."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
import re
from statistics import median

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import RawOffer

DEFAULT_FRESHNESS_DAYS = 7


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
    minimum_90d: Decimal | None
    previous_price: Decimal | None
    price_drop_pct: Decimal | None
    is_historical_low: bool
    reasons: tuple[str, ...]


def median_price(prices: list[Decimal]) -> Decimal | None:
    return Decimal(str(median(prices))) if prices else None


def condition_bucket(offer: RawOffer) -> str:
    value = re.sub(r"[_-]+", " ", (offer.condition_media or "").casefold()).strip()
    if re.search(r"\b(new|sealed)\b", value): return "new"
    if value in {"m", "nm", "m/nm"} or "mint" in value: return "nm"
    if value.startswith("ex") or "excellent" in value: return "ex"
    if value.startswith("vg+") or "very good plus" in value: return "vg+"
    if value.startswith("vg") or "very good" in value: return "vg"
    if re.search(r"\bgood\b", value) or value in {"g", "g+"}: return "good"
    return "unknown"


def classify(discount: Decimal | None, comparable_count: int) -> DealClass:
    if discount is None or comparable_count < 2:
        return DealClass.INSUFFICIENT
    if discount < 10: return DealClass.NORMAL
    if discount < 15: return DealClass.INTERESTING
    if discount < 25: return DealClass.GOOD
    if comparable_count < 3: return DealClass.GOOD
    if discount < 35: return DealClass.HOT
    return DealClass.VERY_HOT


def evaluate_offer(repository: SQLiteRepository, offer_id: int, *, now: datetime | None = None, freshness_days: int = DEFAULT_FRESHNESS_DAYS) -> DealResult | None:
    target = repository.offer_by_id(offer_id)
    if not target or target[1].provenance != "AUTOMATIC" or target[1].price is None or target[1].price <= 0 or target[1].availability.value != "in_stock" or target[0] is None:
        return None
    release_id, offer = target
    point = now or datetime.now(timezone.utc)
    if offer.fetched_at < point - timedelta(days=freshness_days):
        return None
    comparables = repository.comparable_release_offers(release_id, exclude_offer_id=offer_id)
    target_condition = condition_bucket(offer)
    # One store contributes at most one current price; lowest is the useful offer.
    by_store: dict[str, Decimal] = {}
    for _, other in comparables:
        if (
            target_condition != "unknown"
            and other.provenance == "AUTOMATIC"
            and condition_bucket(other) == target_condition
            and other.source != offer.source
            and other.price is not None
            and other.price > 0
            and other.fetched_at >= point - timedelta(days=freshness_days)
        ):
            by_store[other.source] = min(by_store.get(other.source, other.price), other.price)
    prices = list(by_store.values())
    market = median_price(prices)
    discount = ((market - offer.price) / market * 100) if market else None
    history = repository.price_history(offer_id)
    observed = [(datetime.fromisoformat(timestamp), price) for timestamp, price in history if price is not None]
    def window(days: int) -> Decimal | None:
        return median_price([price for timestamp, price in observed if timestamp >= point - timedelta(days=days)])
    def window_minimum(days: int) -> Decimal | None:
        return min((price for timestamp, price in observed if timestamp >= point - timedelta(days=days)), default=None)
    previous_observed = observed[:-1]
    historical = min((price for _, price in previous_observed), default=None)
    previous = observed[-2][1] if len(observed) > 1 else None
    historical_low = bool(historical is not None and offer.price < historical)
    price_drop = ((previous - offer.price) / previous * 100) if previous is not None and previous > offer.price else None
    deal_class = classify(discount, len(prices))
    reasons = [f"{len(prices)} comparable stores"]
    if market is None: reasons.append("market sample unavailable")
    if len(prices) == 2: reasons.append("small market sample")
    if len(prices) < 2: reasons.append("insufficient market sample")
    if historical_low: reasons.append("new historical low")
    if price_drop is not None: reasons.append("price dropped")
    if deal_class == DealClass.INSUFFICIENT and (historical_low or (price_drop is not None and price_drop >= 10)):
        reasons.append("historical signal only")
    return DealResult(offer_id, release_id, offer.price, market, len(prices), discount, deal_class, historical, window(30), window(90), window_minimum(90), previous, price_drop, historical_low, tuple(reasons))


def evaluate_deals(repository: SQLiteRepository, release_id: int | None = None) -> list[DealResult]:
    rows = repository.release_offer_ids(release_id)
    return [result for offer_id in rows if (result := evaluate_offer(repository, offer_id)) is not None]
