"""Public Wave 2 store adapters; no authenticated or anti-bot endpoints."""
from __future__ import annotations

import re

from urllib.error import HTTPError, URLError

from vinyl_deals.adapters.public_html import PublicHtmlVinylAdapter
from vinyl_deals.domain import ScrapeResult, StoreState


class VidikaAdapter(PublicHtmlVinylAdapter):
    source, store_name = "vidika", "Vidika"
    catalog_url, base_url = "https://vidika.su/catalog/zarubezhnyy-vinil/", "https://vidika.su"
    def _page_url(self, page): return f"{self.catalog_url}?page={page}"


class MaximumVinylAdapter(PublicHtmlVinylAdapter):
    source, store_name = "maximum_vinyl", "Maximum Vinyl"
    catalog_url, base_url = "https://maximumvinyl.ru/vinilovye-plastinki", "https://maximumvinyl.ru"
    def _page_url(self, page): return f"{self.catalog_url}?page={page}"


class VinylmarktAdapter(PublicHtmlVinylAdapter):
    source, store_name = "vinylmarkt", "Vinylmarkt"
    catalog_url, base_url = "https://vinylmarkt.ru/catalog/vinilovye_plastinki/", "https://vinylmarkt.ru"


class VernoshopAdapter(PublicHtmlVinylAdapter):
    source, store_name = "vernoshop", "Vernoshop"
    catalog_url, base_url = "https://vernoshop.com/", "https://vernoshop.com"


class TishinaAdapter(PublicHtmlVinylAdapter):
    source, store_name = "tishina", "Тишина"
    catalog_url, base_url = "https://msk.tishina.shop/catalog/vinilovye-plastinki/", "https://msk.tishina.shop"


class AVSoundAdapter(PublicHtmlVinylAdapter):
    source, store_name = "avsound", "AVSound"
    catalog_url, base_url = "https://avsound.ru/catalog/vinyls/vinilovye-plastinki/ar/", "https://avsound.ru"


class OnlineTradeAdapter(PublicHtmlVinylAdapter):
    source, store_name = "onlinetrade", "OnlineTrade"
    catalog_url, base_url = "https://www.onlinetrade.ru/catalogue/vinilovye_plastinki_cd_blu_ray_kassety-c3594/", "https://www.onlinetrade.ru"

    @staticmethod
    def _looks_like_vinyl(value: str) -> bool:
        lowered = value.casefold()
        if re.search(r"(?:\bcd\b|audio cd|sacd|dvd|blu-ray|кассет)", lowered) and not re.search(r"(?:виниловая\s+пластинка|\blp\b)", lowered):
            return False
        return bool(re.search(r"(?:виниловая\s+пластинка|\blp\b|\b\d+lp\b)", lowered))


class PultAdapter(PublicHtmlVinylAdapter):
    """Pult.ru currently denies its public catalogue to this client (HTTP 403)."""

    source, store_name = "pult", "Pult.ru"
    catalog_url, base_url = "https://www.pult.ru/catalog/vinilovye-plastinki/", "https://www.pult.ru"

    def get_catalog(self) -> ScrapeResult:
        try:
            return super().get_catalog()
        except (HTTPError, URLError, OSError):
            return ScrapeResult((), StoreState.DEGRADED, ("Pult.ru public catalogue is access-restricted; no bypass is attempted.",))
