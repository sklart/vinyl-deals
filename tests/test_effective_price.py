from datetime import datetime, timezone
from decimal import Decimal

from vinyl_deals.domain import RawOffer
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.effective_price import calculate_effective_price


def offer(**values) -> RawOffer:
    return RawOffer(source="shop", source_product_id="1", url="https://example.test", fetched_at=datetime.now(timezone.utc), price=Decimal("1000"), **values)


def test_local_pickup_is_product_price_and_does_not_invent_delivery() -> None:
    result = calculate_effective_price(offer(local_store=True, pickup_available=True))
    assert (result.total, result.is_pickup, result.delivery_known) == (Decimal("1000"), True, True)


def test_known_delivery_and_unconditional_discount_are_explicit() -> None:
    result = calculate_effective_price(offer(delivery_cost=Decimal("300"), unconditional_discount=Decimal("50")))
    assert (result.total, result.delivery_known) == (Decimal("1250"), True)


def test_unknown_delivery_is_not_represented_as_known_zero_cost() -> None:
    result = calculate_effective_price(offer())
    assert result.total == Decimal("1000")
    assert not result.delivery_known


def test_effective_price_fields_survive_offer_persistence(tmp_path) -> None:
    source = offer(delivery_cost=Decimal("300"), unconditional_discount=Decimal("50"))
    repository = SQLiteRepository(tmp_path / "effective.sqlite3")
    repository.upsert_offer(source)
    _, restored = repository.offer_by_id(1)
    assert (restored.delivery_cost, restored.unconditional_discount) == (Decimal("300"), Decimal("50"))
