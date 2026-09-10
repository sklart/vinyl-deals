"""Interfaces for store-specific data retrieval only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from vinyl_deals.domain import RawOffer, ScrapeResult


class BaseStoreAdapter(ABC):
    source: str
    # Set by refresh orchestration.  Adapters with a known page total report
    # exact pagination; other public adapters still receive generic request
    # progress from the orchestrator.
    progress_callback: Callable[[str], None] | None = None
    reports_catalog_progress = False

    def emit_progress(self, message: str) -> None:
        if self.progress_callback:
            self.progress_callback(message)

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

    def enrich_offer(self, offer: RawOffer) -> RawOffer:
        """Optionally fetch one public product card; default keeps listing data."""
        return offer
