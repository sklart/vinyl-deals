"""Public Wave 2 store adapters; no authenticated or anti-bot endpoints."""
from __future__ import annotations

import re
from dataclasses import replace

from urllib.error import HTTPError, URLError

from vinyl_deals.adapters.public_html import PublicHtmlVinylAdapter
from vinyl_deals.domain import ScrapeResult, StoreState


class VidikaAdapter(PublicHtmlVinylAdapter):
    source, store_name = "vidika", "Vidika"
    catalog_url, base_url = "https://vidika.su/category/zarubezhnyy-vinil/", "https://vidika.su"
    catalog_urls = ("https://vidika.su/category/zarubezhnyy-vinil/", "https://vidika.su/category/russkiy-vinil/")
    def _page_url(self, page, **_kwargs): return f"{self.catalog_url}?page={page}"

    def _html_cards(self, html, timestamp):
        """Parse Vidika's public Webasyst ``products__item`` cards.

        This is deliberately separate from the generic theme fallback: the
        real category does not expose ``product-item`` classes and has nested
        containers, while every card carries an immutable product_id, a
        current-price block and an explicit availability marker.
        """
        offers = []
        for block in re.split(r'<div\s+class=["\']products__item["\']\s*>', html, flags=re.I)[1:]:
            url = self._first(r'(?:data-href|<a\s+href)=["\']([^"\']+)', block, re.I)
            name = self._first(r'products__item-info-name["\'][^>]*>(.*?)</span>', block, re.I | re.S)
            product_id = self._first(r'name=["\']product_id["\']\s+value=["\'](\d+)', block, re.I)
            sku_id = self._first(r'name=["\']sku_id["\']\s+value=["\'](\d+)', block, re.I)
            article = self._first(r'products__code-v["\'][^>]*>(.*?)</span>', block, re.I | re.S)
            price = self._money(self._first(r'products__pr-price-new.*?<span\s+class=["\']price["\']>(.*?)</span>', block, re.I | re.S))
            offer = self._product_from_values(
                name=self._text(name), url=url, product_id=product_id, price=price,
                availability_value=block, timestamp=timestamp,
                raw_data={"html_card": True, "vidika_products_item": True},
            )
            if offer:
                # Webasyst has three distinct identifiers.  product_id is
                # the immutable source key, the human-facing Артикул is a
                # store SKU, and sku_id remains diagnostic raw data only.
                offers.append(replace(
                    offer,
                    store_sku=self._text(article) or None,
                    raw_data={**offer.raw_data, "vidika_sku_id": sku_id or None},
                ))
        return offers

    def get_catalog(self):
        original, offers, processed = self.catalog_url, [], 0
        warnings, errors = [], []
        degraded = False
        try:
            for root in self.catalog_urls:
                self.catalog_url = root
                result = super().get_catalog()
                warnings.extend(result.warnings)
                errors.extend(result.errors)
                offers.extend(result.offers)
                processed += result.pages_processed
                degraded = degraded or result.state != StoreState.ACTIVE
            return ScrapeResult(
                tuple({item.source_product_id: item for item in offers}.values()),
                StoreState.DEGRADED if degraded else StoreState.ACTIVE,
                tuple(warnings), processed, tuple(errors),
            )
        finally:
            self.catalog_url = original


class MaximumVinylAdapter(PublicHtmlVinylAdapter):
    source, store_name = "maximum_vinyl", "Maximum Vinyl"
    catalog_url, base_url = "https://maximumvinyl.ru/vinilovye-plastinki", "https://maximumvinyl.ru"
    def _page_url(self, page): return f"{self.catalog_url}?page={page}"

    def parse_product_page(self, html, listing_offer):
        offer = super().parse_product_page(html, listing_offer)
        properties = self._maximum_properties(html)
        return replace(
            offer,
            condition_media=self._condition(properties.get("состояние пластинки")),
            condition_sleeve=self._condition(properties.get("состояние обложки")),
        )

    @classmethod
    def _maximum_properties(cls, html):
        pairs = re.findall(
            r'dotted-line_title[^>]*>(.*?)</span>.*?dotted-line_right[^>]*>(.*?)</div>\s*</li>',
            html, re.I | re.S,
        )
        return {cls._text(key).rstrip(":").casefold(): cls._text(value) for key, value in pairs}

    @staticmethod
    def _condition(value):
        normalized = re.sub(r"\s+", "", (value or "").upper())
        if normalized in {"NEW", "SEALED", "S"}: return "NEW"
        if normalized in {"M", "MINT"}: return "M"
        if normalized in {"NM", "NEARMINT"}: return "NM"
        if normalized.startswith("EX"): return "EX"
        if normalized in {"VG+", "VGPLUS"}: return "VG+"
        if normalized.startswith("VG"): return "VG"
        if normalized in {"G", "G+", "GOOD"}: return "GOOD"
        return "UNKNOWN"


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
        # DVD/Blu-ray/cassette are never a vinyl release.  CD/SACD is allowed
        # only for an explicitly confirmed hybrid such as LP+CD.
        if re.search(r"(?:\bdvd\b|blu[- ]?ray|кассет)", lowered):
            return False
        has_lp = bool(re.search(r"(?:\blp\b|\b\d+lp\b)", lowered))
        has_vinyl_product = bool(re.search(r"(?:виниловая\s+пластинка|vinilovaya_plastinka)", lowered))
        if re.search(r"(?:\bcd\b|audio cd|sacd)", lowered):
            return has_lp
        return has_vinyl_product or has_lp


class PultAdapter(PublicHtmlVinylAdapter):
    """Pult.ru currently denies its public catalogue to this client (HTTP 403)."""

    source, store_name = "pult", "Pult.ru"
    catalog_url, base_url = "https://www.pult.ru/catalog/vinilovye-plastinki/", "https://www.pult.ru"

    def get_catalog(self) -> ScrapeResult:
        try:
            return super().get_catalog()
        except (HTTPError, URLError, OSError):
            return ScrapeResult((), StoreState.DEGRADED, ("Pult.ru public catalogue is access-restricted; no bypass is attempted.",))
