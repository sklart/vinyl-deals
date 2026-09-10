"""Public server-rendered catalogue adapter for Audiomania vinyl records."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
import json
import re
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from vinyl_deals.adapters.base import BaseStoreAdapter
from vinyl_deals.domain import Availability, RawOffer, ScrapeResult, StoreState


class AudiomaniaAdapter(BaseStoreAdapter):
    source = "audiomania"
    # This is the public category for music vinyl records, not the site's
    # generic /vinyl/ section which also contains hi-fi equipment.
    catalog_url = "https://www.audiomania.ru/vinilovye_plastinki/"
    base_url = "https://www.audiomania.ru"

    def __init__(self, *, timeout_seconds: float = 20.0, page_limit: int | None = None, delay_seconds: float = 0.25) -> None:
        self.timeout_seconds, self.page_limit, self.delay_seconds = timeout_seconds, page_limit, delay_seconds

    def get_catalog(self) -> ScrapeResult:
        try:
            root = self._fetch(self.catalog_url)
        except (HTTPError, URLError, OSError) as error:
            status = getattr(error, "code", None)
            detail = f"HTTP {status}" if status in {403, 429} else type(error).__name__
            return ScrapeResult((), StoreState.DEGRADED, (f"Audiomania public catalogue unavailable ({detail}); source paused.",))
        if self._is_blocked(root):
            return ScrapeResult((), StoreState.DEGRADED, ("Audiomania returned a CAPTCHA or access-check page; source paused.",))
        product_urls = self._product_urls(root)
        listing_pages = 1
        visited_collection_urls = {self._collection_key(self.catalog_url)}
        pending_collection_urls = self._collection_urls(root)
        try:
            while pending_collection_urls:
                if self.page_limit is not None and listing_pages - 1 >= self.page_limit:
                    break
                collection_url = pending_collection_urls.pop(0)
                collection_key = self._collection_key(collection_url)
                if collection_key in visited_collection_urls:
                    continue
                visited_collection_urls.add(collection_key)
                collection = self._fetch(collection_url)
                if self._is_blocked(collection):
                    return ScrapeResult((), StoreState.DEGRADED, ("Audiomania returned a CAPTCHA or access-check page; source paused.",), pages_processed=listing_pages)
                product_urls.extend(self._product_urls(collection))
                pending_collection_urls.extend(self._collection_urls(collection))
                listing_pages += 1
                if self.delay_seconds:
                    sleep(self.delay_seconds)
        except (HTTPError, URLError, OSError) as error:
            status = getattr(error, "code", None)
            detail = f"HTTP {status}" if status in {403, 429} else type(error).__name__
            return ScrapeResult((), StoreState.DEGRADED, (f"Audiomania public catalogue unavailable ({detail}); source paused.",), pages_processed=listing_pages)
        product_urls = list(dict.fromkeys(product_urls))
        if self.page_limit is not None:
            product_urls = product_urls[:self.page_limit]
        offers: list[RawOffer] = []
        try:
            for index, product_url in enumerate(product_urls):
                product_html = self._fetch(product_url)
                if self._is_blocked(product_html):
                    return ScrapeResult((), StoreState.DEGRADED, ("Audiomania returned a CAPTCHA or access-check page; source paused.",), pages_processed=listing_pages + index)
                offer = self.parse_public_product_page(product_html, product_url)
                if offer:
                    offers.append(offer)
                if index + 1 < len(product_urls) and self.delay_seconds:
                    sleep(self.delay_seconds)
        except (HTTPError, URLError, OSError) as error:
            status = getattr(error, "code", None)
            detail = f"HTTP {status}" if status in {403, 429} else type(error).__name__
            return ScrapeResult((), StoreState.DEGRADED, (f"Audiomania public catalogue unavailable ({detail}); source paused.",))
        if not offers:
            return ScrapeResult((), StoreState.DEGRADED, ("Audiomania catalogue parsed zero vinyl offers; parser may be stale."), pages_processed=listing_pages + len(product_urls))
        warning = (f"Catalogue intentionally limited to {self.page_limit} collection pages and product pages.",) if self.page_limit is not None else ()
        return ScrapeResult(tuple({offer.source_product_id: offer for offer in offers}.values()), warnings=warning, pages_processed=listing_pages + len(product_urls))

    def enrich_offer(self, offer: RawOffer) -> RawOffer:
        if offer.raw_data.get("fully_enriched"):
            return offer
        detailed = self.parse_public_product_page(self._fetch(offer.url), offer.url)
        if detailed is None:
            return offer
        values = {name: getattr(offer, name) for name in offer.__dataclass_fields__}
        for name in ("artist_raw", "title_raw", "price", "availability", "barcode", "catalog_number_raw", "label", "release_year", "format", "disc_count", "country", "condition_media", "condition_sleeve"):
            value = getattr(detailed, name)
            if value is not None and value != Availability.UNKNOWN:
                values[name] = value
        values["raw_data"] = {**offer.raw_data, **detailed.raw_data, "public_card": True}
        return RawOffer(**values)

    def parse_public_product_page(self, html: str, url: str, *, fetched_at: datetime | None = None) -> RawOffer | None:
        """Parse the documented server-rendered music-record product layout."""
        content = self._text_with_spaces(html)
        recommendations = re.search(r"\u0420\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0443\u0435\u043c", content, re.I)
        if recommendations:
            content = content[:recommendations.start()]
        if not self._is_vinyl_url(url) or not re.search(r"\u0412\u0438\u043d\u0438\u043b\u043e\u0432\u0430\u044f\s+\u043f\u043b\u0430\u0441\u0442\u0438\u043d\u043a\u0430", content, re.I):
            return None
        product_id = self._first(r"\u0410\u0440\u0442\u0438\u043a\u0443\u043b\s*:\s*(\d+)", content, re.I)
        title = self._first(r"\u0412\u0438\u043d\u0438\u043b\u043e\u0432\u0430\u044f\s+\u043f\u043b\u0430\u0441\u0442\u0438\u043d\u043a\u0430\s+(.+?)\s*(?:\u0410\u0440\u0442\u0438\u043a\u0443\u043b\s*:|EAN/UPC\s*:)", content, re.I)
        if not product_id or not title:
            return None
        artist, album = self._split_artist_title(title)
        state = self._first(r"\u0421\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u0435\s*:\s*([^\.]+)", content, re.I).casefold()
        condition = "NEW" if state.startswith("\u043d\u043e\u0432") else None
        return RawOffer(
            source=self.source, source_product_id=product_id, url=url, fetched_at=fetched_at or datetime.now(timezone.utc),
            artist_raw=artist, title_raw=album, price=self._money(self._first(r"(\d[\d\s]*)\s*\u0440\u0443\u0431\.\s*/", content, re.I)),
            availability=self._availability(content), barcode=self._first(r"(?:EAN/UPC|EAN|UPC)\s*:\s*(\d{8,14})", content, re.I) or None,
            catalog_number_raw=self._first(r"(?:\u041a\u0430\u0442\u0430\u043b\u043e\u0436\u043d\u044b\u0439\s+\u043d\u043e\u043c\u0435\u0440|Catalog(?:ue)?\s+number)\s*:\s*([^\.]+)", content, re.I) or None,
            label=self._first(r"(?:\u041b\u0435\u0439\u0431\u043b|Label)\s*:\s*([^\.]+)", content, re.I) or None,
            release_year=self._year(self._first(r"\u0413\u043e\u0434\s+\u0438\u0437\u0434\u0430\u043d\u0438\u044f\s*:\s*((?:19|20)\d{2})", content, re.I)),
            format=self._format(content), disc_count=self._integer(self._first(r"\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e\s+\u043f\u043b\u0430\u0441\u0442\u0438\u043d\u043e\u043a\s*:\s*(\d+)", content, re.I)),
            country=self._first(r"\u0421\u0442\u0440\u0430\u043d\u0430\s*:\s*([^\.]+)", content, re.I) or None,
            condition_media=condition, condition_sleeve=condition,
            raw_data={"public_card": True, "fully_enriched": True, "audiomania_article": product_id},
        )

    def parse_listing(self, html: str, *, fetched_at: datetime | None = None) -> list[RawOffer]:
        timestamp = fetched_at or datetime.now(timezone.utc)
        offers: list[RawOffer] = []
        for product in self._products_from_jsonld(html):
            product_id = str(product.get("sku") or product.get("productID") or "")
            name = str(product.get("name") or "")
            price_data = product.get("offers") if isinstance(product.get("offers"), dict) else {}
            price = self._decimal(price_data.get("price"))
            url = str(product.get("url") or "")
            if not self._is_vinyl_product(product) or not product_id or not name or price is None or not url:
                continue
            artist, title = self._split_artist_title(name)
            availability = Availability.IN_STOCK if "instock" in str(price_data.get("availability", "")).casefold() else Availability.OUT_OF_STOCK
            offers.append(RawOffer(source=self.source, source_product_id=product_id, url=url, fetched_at=timestamp,
                artist_raw=artist, title_raw=title, price=price, availability=availability, format=self._format(name),
                condition_media="NEW", condition_sleeve="NEW", raw_data={"jsonld": True}))
        return offers

    def parse_product_page(self, html: str, listing_offer: RawOffer) -> RawOffer:
        products = self._products_from_jsonld(html)
        product = next((item for item in products
                        if str(item.get("sku") or item.get("productID") or "") == listing_offer.source_product_id), products[0] if len(products) == 1 else {})
        barcode = str(product.get("gtin13") or product.get("gtin") or "") or listing_offer.barcode
        catalog = str(product.get("mpn") or "") or listing_offer.catalog_number_raw
        brand = product.get("brand")
        label = str(brand.get("name")) if isinstance(brand, dict) and brand.get("name") else listing_offer.label
        return RawOffer(**{**{name: getattr(listing_offer, name) for name in listing_offer.__dataclass_fields__},
            "barcode": barcode, "catalog_number_raw": catalog, "label": label,
            "format": self._format(self._text(html)) or listing_offer.format,
            "raw_data": {**listing_offer.raw_data, "public_card": True},
        })

    def _fetch(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": "VinylDeals/0.1 (+public catalogue)"})
        with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310: fixed HTTPS origin
            return response.read().decode("utf-8")

    @staticmethod
    def _products_from_jsonld(html: str) -> list[dict[str, object]]:
        products: list[dict[str, object]] = []
        for script in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.I | re.S):
            try:
                payload = json.loads(unescape(script))
            except json.JSONDecodeError:
                continue
            queue = payload if isinstance(payload, list) else [payload]
            while queue:
                current = queue.pop()
                if not isinstance(current, dict):
                    continue
                kind = current.get("@type")
                if kind == "Product" or (isinstance(kind, list) and "Product" in kind):
                    products.append(current)
                for value in current.values():
                    if isinstance(value, dict): queue.append(value)
                    elif isinstance(value, list): queue.extend(value)
        return products

    @staticmethod
    def _is_vinyl_product(product: dict[str, object]) -> bool:
        """Accept products explicitly placed in Audiomania's record category.

        A product name mentioning vinyl is not sufficient: turntables and
        accessories use the same vocabulary.  The public category or product
        URL is the durable, unambiguous signal.
        """
        category = product.get("category")
        categories = category if isinstance(category, list) else [category]
        category_text = " ".join(str(value) for value in categories if value).casefold()
        url = str(product.get("url") or "").casefold()
        return "vinilovye_plastinki" in url or "виниловые пластинки" in category_text

    @staticmethod
    def _is_blocked(html: str) -> bool:
        text = html.casefold()
        return any(marker in text for marker in ("captcha", "smartcaptcha", "проверка безопасности", "access denied"))

    def _product_urls(self, html: str) -> list[str]:
        """Public root links have one artist directory plus a .html card."""
        urls = []
        for path in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
            url = urljoin(self.base_url, path)
            if self._is_vinyl_url(url) and url.split("?", 1)[0].casefold().endswith(".html"):
                urls.append(url)
        return list(dict.fromkeys(urls))

    def _collection_urls(self, html: str) -> list[str]:
        urls = []
        for path in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
            url = urljoin(self.base_url, path)
            no_query = url.split("?", 1)[0].rstrip("/")
            if self._is_vinyl_url(url) and not no_query.endswith(".html") and no_query != self.catalog_url.rstrip("/"):
                urls.append(url)
        return list(dict.fromkeys(urls))

    @staticmethod
    def _collection_key(url: str) -> str:
        return url.split("#", 1)[0].split("?", 1)[0].rstrip("/").casefold()

    def _is_vinyl_url(self, url: str) -> bool:
        return url.casefold().startswith(f"{self.base_url}/vinilovye_plastinki/")

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        try: return Decimal(str(value))
        except Exception: return None

    @staticmethod
    def _money(value: str) -> Decimal | None:
        compact = re.sub(r"\s+", "", value)
        return Decimal(compact) if compact.isdigit() else None

    @staticmethod
    def _first(pattern: str, value: str, flags: int = 0) -> str:
        match = re.search(pattern, value, flags)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _integer(value: str) -> int | None:
        return int(value) if value.isdigit() else None

    @staticmethod
    def _year(value: str) -> int | None:
        return int(value) if re.fullmatch(r"(?:19|20)\d{2}", value) else None

    @staticmethod
    def _availability(value: str) -> Availability:
        if re.search(r"\u041d\u0435\u0442\s+\u0432\s+\u043d\u0430\u043b\u0438\u0447\u0438\u0438|OutOfStock", value, re.I):
            return Availability.OUT_OF_STOCK
        if re.search(r"\b\u0412\s+\u043d\u0430\u043b\u0438\u0447\u0438\u0438\b|\bInStock\b", value, re.I):
            return Availability.IN_STOCK
        return Availability.UNKNOWN

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
    def _text(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(value))).strip()

    @staticmethod
    def _text_with_spaces(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()
