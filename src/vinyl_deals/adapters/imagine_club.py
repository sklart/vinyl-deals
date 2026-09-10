"""Server-rendered catalogue adapter for Imagine Club.

The official catalogue is Drupal HTML, paginated at 16 records. Listing pages
provide reliable offer identifiers; optional card enrichment extracts the
edition metadata that is deliberately absent from the listing.
"""
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


class ImagineClubAdapter(BaseStoreAdapter):
    source = "imagine_club"
    reports_catalog_progress = True
    catalog_url = "https://imagine-club.com/catalog"
    base_url = "https://imagine-club.com"

    def __init__(self, *, timeout_seconds: float = 20.0, page_limit: int | None = None, delay_seconds: float = 0.25) -> None:
        self.timeout_seconds, self.page_limit, self.delay_seconds = timeout_seconds, page_limit, delay_seconds

    def get_catalog(self) -> ScrapeResult:
        try:
            first = self._fetch(self.catalog_url)
            last_page = self._last_page(first)
            pages = range(0, min(last_page + 1, self.page_limit) if self.page_limit is not None else last_page + 1)
            offers: list[RawOffer] = []
            for page in pages:
                self._emit_page_progress(page + 1, len(pages))
                html = first if page == 0 else self._fetch(f"{self.catalog_url}?page={page}")
                offers.extend(self.parse_listing(html))
                if page < last_page and self.delay_seconds:
                    sleep(self.delay_seconds)
            if not offers:
                return ScrapeResult(
                    (),
                    StoreState.DEGRADED,
                    ("Imagine Club catalogue parsed zero offers; parser may be stale.",),
                    pages_processed=len(pages),
                )
            warning = () if self.page_limit is None or self.page_limit > last_page else (f"Catalogue intentionally limited to {self.page_limit} pages.",)
            return ScrapeResult(tuple(offers), warnings=warning, pages_processed=len(pages))
        except HTTPError as error:
            if error.code in {403, 429}:
                return ScrapeResult((), StoreState.DEGRADED, (f"Imagine Club returned HTTP {error.code}; source paused.",))
            raise

    def get_product(self, source_product_id: str) -> RawOffer | None:
        # Product ids are stable only in listing HTML. Callers should retain
        # the listing URL and use parse_product_page for detail enrichment.
        return None

    def enrich_offer(self, offer: RawOffer) -> RawOffer:
        return self.parse_product_page(self._fetch(offer.url), offer)

    def parse_listing(self, html: str, *, fetched_at: datetime | None = None) -> list[RawOffer]:
        timestamp = fetched_at or datetime.now(timezone.utc)
        blocks = re.split(r'<div\s+about="(?=/)', html)[1:]
        offers: list[RawOffer] = []
        for block in blocks:
            block = '<div about="' + block
            product_id = self._first(r'name="product_id"\s+value="(\d+)"', block)
            path = self._first(r'<div\s+about="([^"]+)"', block)
            if not product_id or not path:
                continue
            title = self._text(self._first(r'field-name-title.*?<a[^>]*>(.*?)</a>', block, re.S))
            artist = self._text(self._first(r'field-name-field-artist-ref.*?<a[^>]*>(.*?)</a>', block, re.S))
            info = self._text(self._first(r'field-name-info">(.*?)</div>', block, re.S))
            country, _, fmt = info.partition("|")
            condition = self._text(self._first(r'condition-catalog">(.*?)</div>', block, re.S))
            price = self._money(self._first(r'<button[^>]+value="([^"]*₽)"', block))
            image = self._first(r'<img[^>]+src="([^"]+)"', block)
            status = Availability.IN_STOCK if re.search(r'commerce-cart-add-to-cart-form-\d+\s+in-stock', block) else Availability.OUT_OF_STOCK
            media, sleeve = self._conditions(condition)
            offers.append(RawOffer(source=self.source, source_product_id=product_id, url=urljoin(self.base_url, path), fetched_at=timestamp,
                artist_raw=artist or None, title_raw=title or None, price=price, availability=status,
                country=country.strip() or None, format=fmt.strip().upper() or None, condition_media=media, condition_sleeve=sleeve,
                image_url=image or None, raw_data={"listing_condition": condition, "listing_info": info, "path": path}))
        return offers

    def parse_product_page(self, html: str, listing_offer: RawOffer) -> RawOffer:
        """Merge a public card into a listing offer; absent fields stay absent."""
        fields = self._labelled_fields(html)
        sku = self._text(self._first(r'commerce-product-sku-label">\s*Артикул:\s*</div>\s*([^<]+)', html, re.S))
        media = self._text(self._first(r'lp-state.*?title="([^"]+)"', html, re.S)) or listing_offer.condition_media
        sleeve = self._text(self._first(r'cover-state.*?title="([^"]+)"', html, re.S)) or listing_offer.condition_sleeve
        description = fields.get("описание") or ""
        tags = tuple(part.strip() for part in (fields.get("категория", "") + "," + description).split(",") if part.strip())
        return RawOffer(**{**{name: getattr(listing_offer, name) for name in listing_offer.__dataclass_fields__},
            "artist_raw": fields.get("исполнитель") or listing_offer.artist_raw,
            "title_raw": fields.get("название") or listing_offer.title_raw,
            "label": fields.get("лейбл") or None,
            "store_sku": sku or None,
            "catalog_number_raw": listing_offer.catalog_number_raw,
            "release_year": self._year(fields.get("год", "")),
            "country": fields.get("страна") or listing_offer.country,
            "format": fields.get("тип носителя", "").upper() or listing_offer.format,
            "condition_media": media, "condition_sleeve": sleeve,
            "description": description or None, "edition_tags": tags,
            "raw_data": {**listing_offer.raw_data, "detail_fields": fields, "sku": sku},
        })

    def _fetch(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": "VinylDeals/0.1 (+local research)"})
        with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310: fixed HTTPS origin
            return response.read().decode("utf-8")

    def _emit_page_progress(self, current: int, total: int) -> None:
        self.emit_progress(f"Imagine Club: страницы {current}/{total}, осталось {total - current}")

    @staticmethod
    def _last_page(html: str) -> int:
        value = ImagineClubAdapter._first(r'pager-last.*?href="[^"]*page=(\d+)', html, re.S)
        return int(value) if value else 0

    @staticmethod
    def _labelled_fields(html: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for label, value in re.findall(r'field-label">\s*([^:<]+):.*?field-item[^>]*>(.*?)</div>', html, re.S):
            values[ImagineClubAdapter._text(label).casefold()] = ImagineClubAdapter._text(value)
        return values

    @staticmethod
    def _first(pattern: str, text: str, flags: int = 0) -> str:
        match = re.search(pattern, text, flags)
        return match.group(1) if match else ""

    @staticmethod
    def _text(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(value))).strip()

    @staticmethod
    def _money(value: str) -> Decimal | None:
        digits = re.sub(r"[^\d]", "", value)
        return Decimal(digits) if digits else None

    @staticmethod
    def _year(value: str) -> int | None:
        match = re.search(r"(?:19|20)\d{2}", value)
        return int(match.group()) if match else None

    @staticmethod
    def _conditions(value: str) -> tuple[str | None, str | None]:
        media, separator, sleeve = value.partition("/")
        return media.strip() or None, sleeve.strip() if separator else None
