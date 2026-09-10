"""Transparent effective-price calculation.

Only explicitly known delivery and unconditional discounts are included.  A
local offer with confirmed pickup has no delivery component by definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vinyl_deals.domain import RawOffer


@dataclass(frozen=True, slots=True)
class EffectivePrice:
    product_price: Decimal
    delivery_cost: Decimal | None
    unconditional_discount: Decimal
    total: Decimal | None
    is_pickup: bool
    delivery_known: bool


def calculate_effective_price(offer: RawOffer) -> EffectivePrice | None:
    """Calculate a price without inventing a delivery tariff.

    For a local pickup the total is final at the shop.  A missing delivery
    tariff deliberately produces no total at all: catalogue price is not an
    effective price and must not win an effective-price comparison.
    """
    if offer.price is None or offer.price <= 0:
        return None
    discount = offer.unconditional_discount or Decimal("0")
    delivery = offer.delivery_cost
    if discount < 0 or discount > offer.price:
        raise ValueError("unconditional discount must be between zero and product price")
    if delivery is not None and delivery < 0:
        raise ValueError("delivery cost cannot be negative")
    if offer.local_store and offer.pickup_available:
        return EffectivePrice(offer.price, Decimal("0"), discount, offer.price - discount, True, True)
    if delivery is None:
        return EffectivePrice(offer.price, None, discount, None, False, False)
    return EffectivePrice(
        offer.price,
        delivery,
        discount,
        offer.price + delivery - discount,
        False,
        True,
    )
