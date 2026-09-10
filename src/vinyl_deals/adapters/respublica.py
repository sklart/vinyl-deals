"""Public server-rendered catalogue adapter for Respublica vinyl records."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
import re
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from vinyl_deals.adapters.base import BaseStoreAdapter
from vinyl_deals.domain import Availability, RawOffer, ScrapeResult, StoreState


class RespublicaAdapter(BaseStoreAdapter):
    source = "respublica"
    catalog_url = "https://www.respublica.ru/muzyka-na-vinile/vinilovye-plastinki?order=new"
    base_url = "https://www.respublica.ru"

    def __init__(self, *, timeout_seconds: float = 20.0, page_limit: int | None = None, delay_seconds: float = 0.25) -> None:
        self.timeout_seconds, self.page_limit, self.delay_seconds = timeout_seconds, page_limit, delay_seconds

    def get_catalog(self) -> ScrapeResult:
        try:
            offers: list[RawOffer] = []
            first = self._fetch(self.catalog_url)
            if self._is_blocked(first):
                return ScrapeResult((), StoreState.DEGRADED, ("Respublica returned a CAPTCHA or access-check page; source paused.",))
            pages = self._page_count(first)
            count = min(pages, self.page_limit) if self.page_limit is not None else pages
            for page in range(1, count + 1):
                html = first if page == 1 else self._fetch(f"{self.catalog_url}&page={page}")
                if self._is_blocked(html):
                    return ScrapeResult((), StoreState.DEGRADED, ("Respublica returned a CAPTCHA or access-check page; source paused."), pages_processed=page - 1)
                offers.extend(self.parse_listing(html))
                if page < count and self.delay_seconds:
                    sleep(self.delay_seconds)
        except (HTTPError, URLError, OSError) as error:
            status = getattr(error, "code", None)
            detail = f"HTTP {status}" if status in {403, 429} else type(error).__name__
            return ScrapeResult((), StoreState.DEGRADED, (f"Respublica public catalogue unavailable ({detail}); source paused.",))
        unique = {offer.source_product_id: offer for offer in offers}
        if not unique:
            return ScrapeResult((), StoreState.DEGRADED, ("Respublica catalogue parsed zero offers; parser may be stale."), pages_processed=pages if 'pages' in locals() else 0)
        warning = (f"Catalogue intentionally limited to {self.page_limit} pages.",) if self.page_limit is not None and self.page_limit < pages else ()
        return ScrapeResult(tuple(unique.values()), warnings=warning, pages_processed=count)

    def enrich_offer(self, offer: RawOffer) -> RawOffer:
        return self.parse_product_page(self._fetch(offer.url), offer)

    def parse_listing(self, html: str, *, fetched_at: datetime | None = None) -> list[RawOffer]:
        timestamp = fetched_at or datetime.now(timezone.utc)
        blocks = re.split(r'<div(?=[^>]+itemtype="http://schema\.org/Product")', html, flags=re.I)[1:]
        offers: list[RawOffer] = []
        for block in blocks:
            sku = self._first(r'<meta\s+content="([^"]+)"\s+itemprop="sku"', block)
            path = self._first(r'<a\s+href="([^"]+)"\s+title="([^"]+)"', block)
            title = self._first(r'<a\s+href="[^"]+"\s+title="([^"]+)"', block)
            price = self._money(self._first(r'(?:itemprop="price"[^>]*content|content)="([^"]+)"[^>]*itemprop="price"', block))
            if price is None:
                price = self._money(self._first(r'itemprop="price"[^>]*content="([^"]+)"', block))
            if price is None:
                price = self._money(self._text(self._first(r'<(?:span|div)[^>]*class="[^"]*(?:price|cost)[^"]*"[^>]*>(.*?)</(?:span|div)>', block, re.S)))
            if not sku or not path or not title or price is None:
                continue
            artist, album = self._split_artist_title(self._text(title))
            sold = bool(re.search(r'(?:нет\s+в\s+наличии|распродано|out[- ]of[- ]stock)', self._text(block), re.I))
            offers.append(RawOffer(
                source=self.source, source_product_id=sku, url=urljoin(self.base_url, path), fetched_at=timestamp,
                artist_raw=artist, title_raw=album, price=price, availability=Availability.OUT_OF_STOCK if sold else Availability.IN_STOCK,
                format=self._format(title), condition_media="NEW", condition_sleeve="NEW", raw_data={"listing_title": self._text(title)},
            ))
        return offers

    def parse_product_page(self, html: str, listing_offer: RawOffer) -> RawOffer:
        content = self._text(html)
        barcode = self._first(r'(?:EAN|Штрих[ -]?код|Barcode)\s*[:#]?\s*(\d{12,14})', content, re.I) or listing_offer.barcode
        catalog = self._first(r'(?:Каталожный\s+номер|Артикул)\s*[:#]?\s*([A-Za-z0-9._/-]+)', content, re.I) or listing_offer.catalog_number_raw
        label = self._text(self._first(r'(?:Лейбл|Label)\s*[:#]?\s*(.*?)</p>', html, re.I | re.S)).strip() or listing_offer.label
        year = self._year(self._text(self._first(r'(?:Год|Year)\s*[:#]?\s*(.*?)</p>', html, re.I | re.S))) or listing_offer.release_year
        return RawOffer(**{**{name: getattr(listing_offer, name) for name in listing_offer.__dataclass_fields__},
            "barcode": barcode, "catalog_number_raw": catalog, "label": label, "release_year": year,
            "format": self._format(content) or listing_offer.format,
            "raw_data": {**listing_offer.raw_data, "public_card": True},
        })

    def _fetch(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": "VinylDeals/0.1 (+public catalogue)"})
        with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310: fixed HTTPS origin
            return response.read().decode("utf-8")

    @staticmethod
    def _page_count(html: str) -> int:
        pages = [int(value) for value in re.findall(r'(?:[?&]page=|Перейти на страницу\s+)(\d+)', html)]
        return max(pages, default=1)

    @staticmethod
    def _first(pattern: str, value: str, flags: int = 0) -> str:
        match = re.search(pattern, value, flags)
        return match.group(1) if match else ""

    @staticmethod
    def _text(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(value))).strip()

    @staticmethod
    def _money(value: str) -> Decimal | None:
        match = re.search(r"\d[\d\s]*(?:[.,]\d+)?", value)
        return Decimal(match.group().replace(" ", "").replace(",", ".")) if match else None

    @staticmethod
    def _split_artist_title(value: str) -> tuple[str | None, str | None]:
        for separator in (" - ", " — ", " – "):
            if separator in value:
                artist, title = value.split(separator, 1)
                return artist.strip() or None, title.strip() or None
        return None, value or None

    @staticmethod
    def _format(value: str) -> str | None:
        match = re.search(r"(?<!\w)(?:\d+\s*LP|\d+LP|LP|EP)(?!\w)", value, re.I)
        return re.sub(r"\s+", "", match.group()).upper() if match else None

    @staticmethod
    def _year(value: str) -> int | None:
        match = re.search(r"(?:19|20)\d{2}", value)
        return int(match.group()) if match else None

    @staticmethod
    def _is_blocked(html: str) -> bool:
        text = html.casefold()
        return any(marker in text for marker in ("captcha", "smartcaptcha", "проверка безопасности"))
