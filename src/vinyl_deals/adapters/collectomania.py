"""Public InSales HTML adapter for Collectomania."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
import re
from time import sleep
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from vinyl_deals.adapters.base import BaseStoreAdapter
from vinyl_deals.domain import Availability, RawOffer, ScrapeResult, StoreState


class CollectomaniaAdapter(BaseStoreAdapter):
    source = "collectomania"
    catalog_url = "https://collectomania.ru/collection/vinil"
    base_url = "https://collectomania.ru"

    def __init__(self, *, timeout_seconds: float = 20.0, page_limit: int | None = None, delay_seconds: float = 0.25) -> None:
        self.timeout_seconds, self.page_limit, self.delay_seconds = timeout_seconds, page_limit, delay_seconds

    def get_catalog(self) -> ScrapeResult:
        try:
            url, pages, offers = self.catalog_url, 0, []
            while url and (self.page_limit is None or pages < self.page_limit):
                html = self._fetch(url)
                offers.extend(self.parse_listing(html))
                pages += 1
                next_url = self._next_page(html)
                url = urljoin(self.base_url, next_url) if next_url else ""
                if url and self.delay_seconds:
                    sleep(self.delay_seconds)
            warnings = (f"Catalogue intentionally limited to {self.page_limit} pages.",) if url else ()
            return ScrapeResult(tuple(offers), warnings=warnings)
        except HTTPError as error:
            if error.code in {403, 429}:
                return ScrapeResult((), StoreState.DEGRADED, (f"Collectomania returned HTTP {error.code}; source paused.",))
            raise

    def get_product(self, source_product_id: str) -> RawOffer | None:
        return None  # The source id has no independently addressable public URL.

    def parse_listing(self, html: str, *, fetched_at: datetime | None = None) -> list[RawOffer]:
        timestamp = fetched_at or datetime.now(timezone.utc)
        blocks = html.split('<div class="product-preview-elem">')[1:]
        offers: list[RawOffer] = []
        for block in blocks:
            product_id = self._first(r'data-product-id="(\d+)"', block)
            path = self._first(r'<a href="([^"]*/product/[^"]+)"', block)
            if not product_id or not path:
                continue
            artists = [self._text(value) for value in re.findall(r'product-preview__title-artist">(.*?)</span>', block, re.S)]
            title = self._text(self._first(r'product-preview__title-album[^>]*">(.*?)</span>', block, re.S))
            content = self._text(self._first(r'product-preview__title-content">(.*?)</span>', block, re.S))
            properties = self._properties(block, "property__name", "property__content")
            stickers = tuple(self._text(value) for value in re.findall(r'data-sticker-title="([^"]+)"', block))
            edition_raw = self._text(self._first(r'product-preview__title-format">(.*?)</span>', block, re.S))
            tags = tuple(dict.fromkeys((*stickers, edition_raw, *self._split_tags(properties.get("особенности издания", "")))))
            format_value = content.split(",", 1)[0].upper() if content else None
            offers.append(RawOffer(source=self.source, source_product_id=product_id, url=urljoin(self.base_url, path), fetched_at=timestamp,
                artist_raw=" / ".join(artists) or None, title_raw=title or None, edition_raw=edition_raw or None,
                price=self._money(self._first(r'data-product-price="([^"]+)"', block)), old_price=self._money(self._first(r'product-preview__price-old[^>]*>(.*?)</', block, re.S)),
                availability=self._availability(self._text(self._first(r'product-preview__available">(.*?)</div>', block, re.S))),
                format=format_value, label=properties.get("лейбл") or None, country=properties.get("страна издания") or properties.get("страна производства") or None,
                release_year=self._year(properties.get("год издания", "")), edition_tags=tags,
                image_url=self._first(r'<img[^>]+data-src="([^"]+)"', block) or None,
                raw_data={"path": path, "listing_properties": properties, "listing_content": content}))
        return offers

    def parse_product_page(self, html: str, listing_offer: RawOffer) -> RawOffer:
        properties = self._properties(html, "product__property-name", "product__property-value")
        barcode = re.sub(r"\D", "", properties.get("штрих-код", "")) or None
        condition = next((value for name, value in properties.items() if name.startswith("состояние конверта/пластинки")), "")
        media, sleeve = self._conditions(condition)
        discs = self._integer(properties.get("кол-во носителей", ""))
        tags = tuple(dict.fromkeys((*listing_offer.edition_tags, *self._split_tags(properties.get("особенности издания", "")), properties.get("упаковка", ""))))
        return RawOffer(**{**{name: getattr(listing_offer, name) for name in listing_offer.__dataclass_fields__},
            "barcode": barcode, "format": properties.get("формат пластинки", "").upper() or listing_offer.format,
            "vinyl_size": properties.get("носители") or None, "disc_count": discs,
            "condition_media": media, "condition_sleeve": sleeve,
            "edition_tags": tuple(tag for tag in tags if tag),
            "raw_data": {**listing_offer.raw_data, "detail_properties": properties},
        })

    def _fetch(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": "VinylDeals/0.1 (+local research)"})
        with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310: fixed HTTPS origin
            return response.read().decode("utf-8")

    @staticmethod
    def _next_page(html: str) -> str:
        return CollectomaniaAdapter._first(r'data-collection-infinity="([^"]+)"', html)

    @staticmethod
    def _properties(html: str, name_class: str, value_class: str) -> dict[str, str]:
        found = re.findall(fr'{name_class}">\s*(.*?)</div>\s*<div class="{value_class}">\s*(.*?)</div>', html, re.S)
        return {CollectomaniaAdapter._text(name).rstrip(":").casefold(): CollectomaniaAdapter._text(value) for name, value in found}

    @staticmethod
    def _first(pattern: str, text: str, flags: int = 0) -> str:
        match = re.search(pattern, text, flags)
        return match.group(1) if match else ""

    @staticmethod
    def _text(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(value))).strip()

    @staticmethod
    def _money(value: str) -> Decimal | None:
        match = re.search(r"\d[\d\s]*(?:[.,]\d+)?", value)
        if not match:
            return None
        return Decimal(match.group().replace(" ", "").replace(",", "."))

    @staticmethod
    def _year(value: str) -> int | None:
        match = re.search(r"(?:19|20)\d{2}", value)
        return int(match.group()) if match else None

    @staticmethod
    def _integer(value: str) -> int | None:
        match = re.search(r"\d+", value)
        return int(match.group()) if match else None

    @staticmethod
    def _split_tags(value: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in value.split(",") if item.strip())

    @staticmethod
    def _conditions(value: str) -> tuple[str | None, str | None]:
        if not value:
            return None, None
        if value.casefold() == "new":
            return "NEW_SEALED", "NEW_SEALED"
        sleeve, separator, media = value.partition("/")
        return media.strip() if separator else sleeve.strip(), sleeve.strip() if separator else None

    @staticmethod
    def _availability(value: str) -> Availability:
        if "в наличии" in value.casefold():
            return Availability.IN_STOCK
        return Availability.OUT_OF_STOCK
