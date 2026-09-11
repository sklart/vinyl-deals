"""Stable domain types shared by store adapters and later persistence layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Availability(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


class StoreState(StrEnum):
    ACTIVE = "active"
    DEGRADED = "degraded"


class StoreSearchStatus(StrEnum):
    """Structured outcome of a targeted store search.

    ``StoreState`` remains useful for persistence, while this value is what a
    person needs to understand in the GUI.  It deliberately never depends on
    parsing an English warning message.
    """

    FOUND = "found"
    EMPTY = "empty"
    CACHED = "cached"
    UNSUPPORTED = "unsupported"
    RESTRICTED = "restricted"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class StoreSearchQuery:
    """A small, store-neutral query for a targeted public product search."""

    artist: str | None = None
    title: str | None = None
    barcode: str | None = None
    catalog_number: str | None = None
    label: str | None = None

    def text(self) -> str:
        """Prefer identifiers; otherwise provide a compact human query."""
        return self.barcode or " ".join(value for value in (self.catalog_number, self.label) if value) or " ".join(value for value in (self.artist, self.title) if value)

    def is_empty(self) -> bool:
        return not bool(self.text().strip())


@dataclass(frozen=True, slots=True)
class Release:
    """Canonical metadata aggregated from matched store offers."""

    id: int
    artist: str
    title: str
    barcode: str | None = None
    label: str | None = None
    catalog_number: str | None = None
    release_year: int | None = None
    format: str | None = None
    country: str | None = None
    disc_count: int | None = None
    vinyl_size: str | None = None
    rpm: int | None = None
    vinyl_color: str | None = None
    edition_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RawOffer:
    """Source-faithful offer. Normalisation and matching belong outside adapters."""

    source: str
    source_product_id: str
    url: str
    fetched_at: datetime
    artist_raw: str | None = None
    title_raw: str | None = None
    edition_raw: str | None = None
    price: Decimal | None = None
    old_price: Decimal | None = None
    currency: str = "RUB"
    availability: Availability = Availability.UNKNOWN
    stock_quantity: int | None = None
    stock_text: str | None = None
    city: str | None = None
    local_store: bool = False
    pickup_available: bool = False
    delivery_available: bool = True
    # Delivery is intentionally nullable: an unknown tariff must never be
    # silently presented as a zero-cost delivery.
    delivery_cost: Decimal | None = None
    unconditional_discount: Decimal | None = None
    condition_media: str | None = None
    condition_sleeve: str | None = None
    format: str | None = None
    vinyl_size: str | None = None
    rpm: int | None = None
    disc_count: int | None = None
    label: str | None = None
    store_sku: str | None = None
    catalog_number_raw: str | None = None
    barcode: str | None = None
    release_year: int | None = None
    country: str | None = None
    vinyl_color: str | None = None
    edition_tags: tuple[str, ...] = ()
    description: str | None = None
    image_url: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def now(cls, **values: Any) -> "RawOffer":
        return cls(fetched_at=datetime.now(timezone.utc), **values)


@dataclass(frozen=True, slots=True)
class ScrapeResult:
    offers: tuple[RawOffer, ...]
    state: StoreState = StoreState.ACTIVE
    warnings: tuple[str, ...] = ()
    pages_processed: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StoreSearchResult:
    """Isolated result of one store in a federated live search."""

    source: str
    offers: tuple[RawOffer, ...]
    state: StoreState = StoreState.ACTIVE
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    status: StoreSearchStatus | None = None
