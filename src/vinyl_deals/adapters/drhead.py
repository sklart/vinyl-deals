"""Public catalogue adapter for Dr.Head vinyl records."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
import json
import re
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from vinyl_deals.adapters.base import BaseStoreAdapter
from vinyl_deals.domain import Availability, RawOffer, ScrapeResult, StoreSearchQuery, StoreSearchResult, StoreState


class DrHeadAdapter(BaseStoreAdapter):
    source = "drhead"
    catalog_url = "https://doctorhead.ru/catalog/muzyka/vinilovye-plastinki/"
    base_url = "https://doctorhead.ru"

    def __init__(self, *, timeout_seconds: float = 20.0, page_limit: int | None = None, delay_seconds: float = 0.25) -> None:
        self.timeout_seconds, self.page_limit, self.delay_seconds = timeout_seconds, page_limit, delay_seconds

    def get_catalog(self) -> ScrapeResult:
        try:
            first = self._fetch(self.catalog_url)
            if self._is_blocked(first):
                return ScrapeResult((), StoreState.DEGRADED, ("Dr.Head returned a CAPTCHA or access-check page; source paused.",))
            pages = self._page_count(first)
            count = min(pages, self.page_limit) if self.page_limit is not None else pages
            offers: list[RawOffer] = []
            for page in range(1, count + 1):
                html = first if page == 1 else self._fetch(f"{self.catalog_url}?PAGEN_1={page}")
                if self._is_blocked(html):
                    return ScrapeResult((), StoreState.DEGRADED, ("Dr.Head returned a CAPTCHA or access-check page; source paused."), pages_processed=page - 1)
                offers.extend(self.parse_listing(html))
                if page < count and self.delay_seconds:
                    sleep(self.delay_seconds)
        except (HTTPError, URLError, OSError) as error:
            status = getattr(error, "code", None)
            detail = f"HTTP {status}" if status in {403, 429} else type(error).__name__
            return ScrapeResult((), StoreState.DEGRADED, (f"Dr.Head public catalogue unavailable ({detail}); source paused.",))
        unique = {offer.source_product_id: offer for offer in offers}
        if not unique:
            return ScrapeResult((), StoreState.DEGRADED, ("Dr.Head catalogue parsed zero offers; parser may be stale."), pages_processed=pages if 'pages' in locals() else 0)
        warning = (f"Catalogue intentionally limited to {self.page_limit} pages.",) if self.page_limit is not None and self.page_limit < pages else ()
        return ScrapeResult(tuple(unique.values()), warnings=warning, pages_processed=count)

    def search_offers(self, query: StoreSearchQuery) -> StoreSearchResult:
        """Public Doctorhead header form: GET /search/?q=… ."""
        if query.is_empty():
            return StoreSearchResult(self.source, (), StoreState.DEGRADED, ("Empty live-search query.",))
        try:
            html = self._fetch(f"{self.base_url}/search/?{urlencode({'q': query.text()})}")
            if self._is_blocked(html):
                return StoreSearchResult(self.source, (), StoreState.DEGRADED, ("Dr.Head returned a CAPTCHA/access-check page.",))
            return StoreSearchResult(self.source, tuple(self.parse_listing(html)))
        except (HTTPError, URLError, OSError) as error:
            return StoreSearchResult(self.source, (), StoreState.DEGRADED, (f"Dr.Head public search unavailable ({getattr(error, 'code', type(error).__name__)}).",))

    def enrich_offer(self, offer: RawOffer) -> RawOffer:
        return self.parse_product_page(self._fetch(offer.url), offer)

    def parse_listing(self, html: str, *, fetched_at: datetime | None = None) -> list[RawOffer]:
        timestamp = fetched_at or datetime.now(timezone.utc)
        offers: list[RawOffer] = []
        for match in re.finditer(r"data-gtagv4data='([^']+)'", html, re.S):
            try:
                payload = json.loads(unescape(match.group(1)))
                item = payload["items"][0]
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                continue
            product_id, name, price = str(item.get("item_id") or ""), str(item.get("item_name") or ""), item.get("price")
            if not product_id or not name or price is None:
                continue
            path = self._first(r'href="([^"?#]*/product/[^"?#]+/)"', html[match.end():match.end() + 2000])
            if not path:
                continue
            artist, title = self._split_artist_title(name)
            offers.append(RawOffer(
                source=self.source, source_product_id=product_id, url=urljoin(self.base_url, path), fetched_at=timestamp,
                artist_raw=artist, title_raw=title, price=Decimal(str(price)), old_price=self._decimal(item.get("full_price")) if item.get("full_price") != price else None,
                availability=Availability.IN_STOCK if item.get("quantity", 0) else Availability.OUT_OF_STOCK,
                label=str(item.get("item_brand") or "") or None, format=self._format(name), condition_media="NEW", condition_sleeve="NEW",
                raw_data={"item_id": product_id, "item_name": name},
            ))
        return offers

    def parse_product_page(self, html: str, listing_offer: RawOffer) -> RawOffer:
        content = self._text(html)
        barcode = self._first(r'(?:EAN|Штрих[ -]?код|Barcode)\s*[:#]?\s*(\d{12,14})', content, re.I) or listing_offer.barcode
        catalog = self._first(r'(?:Артикул|Каталожный\s+номер)\s*[:#]?\s*([A-Za-z0-9._/-]+)', content, re.I) or listing_offer.catalog_number_raw
        year = self._release_year(content)
        return RawOffer(**{**{name: getattr(listing_offer, name) for name in listing_offer.__dataclass_fields__},
            "barcode": barcode, "catalog_number_raw": catalog, "release_year": year,
            "format": self._format(content) or listing_offer.format, "raw_data": {**listing_offer.raw_data, "public_card": True},
        })

    def _fetch(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": "VinylDeals/0.1 (+public catalogue)"})
        with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310: fixed HTTPS origin
            return response.read().decode("utf-8")

    @staticmethod
    def _page_count(html: str) -> int:
        pages = [int(value) for value in re.findall(r'PAGEN_1=(\d+)', html)]
        return max(pages, default=1)

    @staticmethod
    def _first(pattern: str, value: str, flags: int = 0) -> str:
        match = re.search(pattern, value, flags)
        return match.group(1) if match else ""

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @staticmethod
    def _text(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(value))).strip()

    @staticmethod
    def _split_artist_title(value: str) -> tuple[str | None, str | None]:
        for separator in (" – ", " - ", " — "):
            if separator in value:
                artist, title = value.split(separator, 1)
                return artist.strip() or None, title.strip() or None
        return None, value or None

    @staticmethod
    def _format(value: str) -> str | None:
        match = re.search(r"(?<!\w)(?:\d+\s*LP|\d+LP|LP|EP)(?!\w)", value, re.I)
        return re.sub(r"\s+", "", match.group()).upper() if match else None

    @staticmethod
    def _release_year(value: str) -> int | None:
        # Do not infer a release year from copyright dates, biography text or
        # review prose.  Only a named product characteristic is trustworthy.
        match = re.search(
            r"(?:\u0413\u043e\u0434\s+\u0432\u044b\u043f\u0443\u0441\u043a\u0430|\u0413\u043e\u0434|\u0414\u0430\u0442\u0430\s+\u0440\u0435\u043b\u0438\u0437\u0430|Release\s+year)\s*[:#-]?\s*((?:19|20)\d{2})(?!\d)",
            value,
            re.I,
        )
        return int(match.group(1)) if match else None

    @staticmethod
    def _is_blocked(html: str) -> bool:
        # The normal search page embeds SmartCaptcha JavaScript.  It is a
        # block only when a challenge is present in document markup.
        text = html.casefold().strip()
        return (
            "проверка безопасности" in text
            or "access-check" in text
            or bool(re.fullmatch(r"<html>\s*(?:captcha|smartcaptcha)\s*</html>", text))
        )
