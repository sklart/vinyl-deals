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
    total: Decimal
    is_pickup: bool
    delivery_known: bool


def calculate_effective_price(offer: RawOffer) -> EffectivePrice | None:
    """Calculate a price without inventing a delivery tariff.

    For a local pickup the total is final at the shop.  For other offers a
    missing delivery tariff leaves ``total`` equal to the catalogue price, but
    callers can inspect ``delivery_known`` before making a final comparison.
    """
    if offer.price is None or offer.price <= 0:
        return None
    discount = offer.unconditional_discount or Decimal("0")
    if offer.local_store and offer.pickup_available:
        return EffectivePrice(offer.price, None, discount, offer.price - discount, True, True)
    delivery = offer.delivery_cost
    return EffectivePrice(
        offer.price,
        delivery,
        discount,
        offer.price + (delivery or Decimal("0")) - discount,
        False,
        delivery is not None,
    )
