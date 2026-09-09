"""Interfaces for store-specific data retrieval only."""

from __future__ import annotations

from abc import ABC, abstractmethod

from vinyl_deals.domain import RawOffer, ScrapeResult


class BaseStoreAdapter(ABC):
    source: str

    @abstractmethod
    def get_catalog(self) -> ScrapeResult:
        """Fetch the store's public catalogue without bypassing protections."""

    def get_changed_products(self) -> ScrapeResult:
        return self.get_catalog()

    def get_product(self, source_product_id: str) -> RawOffer | None:
        return None

    def normalize_product(self, offer: RawOffer) -> RawOffer:
        return offer

    def get_stock(self, source_product_id: str) -> RawOffer | None:
        return self.get_product(source_product_id)
