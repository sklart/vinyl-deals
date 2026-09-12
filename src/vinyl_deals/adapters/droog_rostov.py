"""Safe source descriptor for the public pages of Rostov shop «Друг»."""

from __future__ import annotations

from vinyl_deals.adapters.base import BaseStoreAdapter
from vinyl_deals.domain import ScrapeResult, StoreState


class DroogRostovAdapter(BaseStoreAdapter):
    """Do not scrape non-authoritative directories or a protected VK market.

    The official public profile is retained as the source URL.  Until the shop
    exposes a fetchable public product catalogue, this is intentionally a
    degraded source rather than fabricated offers.
    """

    source = "droog_rostov"
    targeted_search_reason = "«Друг» does not expose a verified public targeted-search endpoint."
    catalog_url = "https://taplink.cc/droog_music"
    city = "Ростов-на-Дону"

    def get_catalog(self) -> ScrapeResult:
        return ScrapeResult((), StoreState.DEGRADED, (
            "«Друг» does not expose a fetchable public catalogue in this environment; no offers were imported.",
        ))
