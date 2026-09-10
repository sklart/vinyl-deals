"""Conservative parser for public, server-rendered vinyl catalogue pages.

The Wave 2 stores use different CMS themes (Bitrix, OpenCart and custom PHP),
but expose the same useful public card data.  This module deliberately avoids
browser automation and checkout/search endpoints: adapters only fetch public
catalogue and product URLs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from html import unescape
import json
import re
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from vinyl_deals.adapters.base import BaseStoreAdapter
from vinyl_deals.domain import Availability, RawOffer, ScrapeResult, StoreState


class PublicHtmlVinylAdapter(BaseStoreAdapter):
    """Base class for a public vinyl-only category in a conventional HTML shop."""

    source = "public_html"
    store_name = "Public store"
    catalog_url = ""
    catalog_urls: tuple[str, ...] = ()
    base_url = ""

    def __init__(self, *, timeout_seconds: float = 20.0, page_limit: int | None = None, delay_seconds: float = 0.25) -> None:
        self.timeout_seconds, self.page_limit, self.delay_seconds = timeout_seconds, page_limit, delay_seconds

    def get_catalog(self) -> ScrapeResult:
        try:
            first = self._fetch(self.catalog_url)
            if self._is_blocked(first):
                return self._degraded("returned a CAPTCHA or access-check page")
            pages = self._page_count(first)
            count = min(pages, self.page_limit) if self.page_limit is not None else pages
            offers: list[RawOffer] = []
            for page in range(1, count + 1):
                html = first if page == 1 else self._fetch(self._page_url(page))
                if self._is_blocked(html):
                    return ScrapeResult((), StoreState.DEGRADED, (f"{self.store_name} returned a CAPTCHA or access-check page; source paused.",), pages_processed=page - 1)
                offers.extend(self.parse_listing(html))
                if page < count and self.delay_seconds:
                    sleep(self.delay_seconds)
        except (HTTPError, URLError, OSError) as error:
            status = getattr(error, "code", None)
            detail = f"HTTP {status}" if status in {403, 429} else type(error).__name__
            return self._degraded(f"public catalogue unavailable ({detail})")
        unique = {offer.source_product_id: offer for offer in offers}
        if not unique:
            return ScrapeResult((), StoreState.DEGRADED, (f"{self.store_name} catalogue parsed zero vinyl offers; parser may be stale.",), pages_processed=pages)
        warning = (f"Catalogue intentionally limited to {self.page_limit} pages.",) if self.page_limit is not None and self.page_limit < pages else ()
        return ScrapeResult(tuple(unique.values()), warnings=warning, pages_processed=count)

    def enrich_offer(self, offer: RawOffer) -> RawOffer:
        return self.parse_product_page(self._fetch(offer.url), offer)

    def parse_listing(self, html: str, *, fetched_at: datetime | None = None) -> list[RawOffer]:
        timestamp = fetched_at or datetime.now(timezone.utc)
        offers = self._json_ld_products(html, timestamp)
        # JSON-LD is preferred when a store provides it.  Many category pages
        # instead expose ordinary product cards, handled as a public fallback.
        offers.extend(self._html_cards(html, timestamp))
        return list({offer.source_product_id: offer for offer in offers}.values())

    def parse_product_page(self, html: str, listing_offer: RawOffer) -> RawOffer:
        text = self._text(html)
        properties = self._properties(html)
        def named(*keys: str) -> str:
            return next((value for key, value in properties.items() if key in {item.casefold() for item in keys}), "")
        barcode = self._first(r"(\d{12,14})", named("EAN", "GTIN", "Штрих-код", "Barcode")) or self._first(r"(?:EAN|GTIN|Штрих[ -]?код|Barcode)\s*[:#]?\s*(\d{12,14})", text, re.I) or listing_offer.barcode
        catalog = named("Каталожный номер", "Catalog number") or self._first(r"(?:каталожн(?:ый|ого)\s+(?:номер|№)|catalog(?:ue)?\s*(?:number|no\.?))\s*[:#]?\s*([A-Za-z0-9._/-]+)", text, re.I) or listing_offer.catalog_number_raw
        label = named("Лейбл", "Label") or self._property(text, "лейбл", "label") or listing_offer.label
        country = named("Страна", "Country") or self._property(text, "страна", "country") or listing_offer.country
        year = self._year(named("Год выпуска", "Год", "Release year", "Year") or self._property(text, "год выпуска", "год", "release year", "year")) or listing_offer.release_year
        format_value = self._format(named("Формат", "Format")) or self._format(text) or listing_offer.format
        disc_count = self._disc_count(named("Количество дисков", "Disc count", "Количество пластинок")) or listing_offer.disc_count
        return RawOffer(**{**{name: getattr(listing_offer, name) for name in listing_offer.__dataclass_fields__},
            "barcode": barcode, "catalog_number_raw": catalog, "label": label, "country": country,
            "release_year": year, "format": format_value, "disc_count": disc_count,
            "raw_data": {**listing_offer.raw_data, "public_card": True},
        })

    def _json_ld_products(self, html: str, timestamp: datetime) -> list[RawOffer]:
        offers: list[RawOffer] = []
        for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
            try:
                payload = json.loads(unescape(raw).strip())
            except json.JSONDecodeError:
                continue
            entries = payload if isinstance(payload, list) else [payload]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                items = entry.get("itemListElement", ()) if entry.get("@type") == "ItemList" else (entry,)
                for item in items:
                    product = item.get("item", item) if isinstance(item, dict) else {}
                    product_type = product.get("@type") if isinstance(product, dict) else None
                    if not isinstance(product, dict) or (product_type != "Product" and product_type != ["Product"]):
                        continue
                    sku = str(product.get("sku") or "")
                    product_id = str(product.get("productID") or sku)
                    barcode = str(product.get("gtin13") or product.get("gtin") or product.get("ean") or "") or None
                    offer = self._product_from_values(
                        name=str(product.get("name") or ""), url=str(product.get("url") or ""),
                        product_id=product_id,
                        price=self._offer_price(product.get("offers")), availability_value=str((product.get("offers") or {}).get("availability") or ""),
                        timestamp=timestamp, raw_data={"json_ld": True}, barcode=barcode, store_sku=sku or None,
                    )
                    if offer:
                        offers.append(offer)
        return offers

    def _html_cards(self, html: str, timestamp: datetime) -> list[RawOffer]:
        # A bounded card fragment prevents prices/titles leaking from the next
        # product; these CMS class names cover the public Wave 2 templates.
        chunks = re.split(r'<(?=div\b[^>]*class=["\'][^"\']*(?:product-item|product-thumb|catalog-item|product-card)[^"\']*["\'])', html, flags=re.I)[1:]
        offers: list[RawOffer] = []
        for chunk in chunks:
            block = self._card_block(chunk)
            href = self._first(r'href=["\']([^"\']+)["\']', block)
            name = self._first(r'(?:itemprop=["\']name["\'][^>]*>|class=["\'][^"\']*(?:name|title)[^"\']*["\'][^>]*>)(.*?)</(?:a|div|span|h[1-6])>', block, re.I | re.S)
            if not name:
                name = self._first(r'<a[^>]+href=["\'][^"\']+["\'][^>]*>(.*?)</a>', block, re.I | re.S)
            sku = self._first(r'(?:data-(?:product-)?id|itemprop=["\']sku["\'][^>]*content|Артикул)\s*(?:=|:)["\'\s]*([A-Za-z0-9_-]+)', block, re.I)
            if not sku:
                # OpenCart themes, including Maximum Vinyl, encode their
                # stable public product id in a CSS class such as product_42.
                sku = self._first(r'\bproduct_(\d+)\b', block, re.I)
            price = self._money(self._first(r'(?:itemprop=["\']price["\'][^>]*content|data-price)=["\']([^"\']+)', block, re.I))
            if price is None:
                price = self._money(self._text(self._first(r'<(?:span|div)[^>]*class=["\'][^"\']*(?:price|cost)[^"\']*["\'][^>]*>(.*?)</(?:span|div)>', block, re.I | re.S)))
            # Keep the whole bounded card as classification evidence.  A mixed
            # catalogue can state the medium in a property row rather than in
            # the visible title; converting it to text also makes this safe for
            # adapters that do not need that extra evidence.
            offer = self._product_from_values(name=self._text(name), url=href, product_id=sku, price=price, availability_value=block, timestamp=timestamp, raw_data={"html_card": True})
            if offer:
                offers.append(offer)
        return offers

    def _product_from_values(self, *, name: str, url: str, product_id: str, price: Decimal | None, availability_value: str, timestamp: datetime, raw_data: dict[str, object], barcode: str | None = None, store_sku: str | None = None) -> RawOffer | None:
        # URL and bounded product-card properties are useful, independent
        # evidence for stores with mixed media catalogues.  Availability still
        # receives the original card below, so this does not broaden the
        # availability parser.
        classification_value = " ".join((name, url, self._text(availability_value)))
        if not name or not url or price is None or not self._looks_like_vinyl(classification_value):
            return None
        absolute_url = urljoin(self.base_url, url)
        stable_id = product_id or sha256(absolute_url.encode("utf-8")).hexdigest()[:20]
        artist, title = self._split_artist_title(name)
        unavailable = re.search(r"(?:нет\s+в\s+наличии|распродан|законч|out[- ]of[- ]stock)", availability_value, re.I)
        available = re.search(r"(?:в\s+наличии|достаточно|instock|in[- ]stock)", availability_value, re.I)
        return RawOffer(source=self.source, source_product_id=stable_id, store_sku=store_sku if store_sku is not None else product_id or None, url=absolute_url,
            fetched_at=timestamp, artist_raw=artist, title_raw=title, price=price,
            availability=Availability.OUT_OF_STOCK if unavailable else Availability.IN_STOCK if available else Availability.UNKNOWN,
            barcode=barcode, format=self._format(name), condition_media="UNKNOWN", condition_sleeve="UNKNOWN", raw_data=raw_data)

    def _page_url(self, page: int) -> str:
        """Override per store when its public catalogue uses another query key."""
        separator = "&" if "?" in self.catalog_url else "?"
        return f"{self.catalog_url}{separator}PAGEN_1={page}"

    def _fetch(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": "VinylDeals/0.1 (+public catalogue)"})
        with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310: fixed public HTTPS URLs
            return response.read().decode("utf-8", errors="replace")

    def _degraded(self, reason: str) -> ScrapeResult:
        return ScrapeResult((), StoreState.DEGRADED, (f"{self.store_name} {reason}; source paused.",))

    @staticmethod
    def _page_count(html: str) -> int:
        values = [int(value) for value in re.findall(r"(?:PAGEN_1=|[?&]page=|data-page=[\"'])(\d+)", html, re.I)]
        return max(values, default=1)

    @staticmethod
    def _first(pattern: str, value: str, flags: int = 0) -> str:
        match = re.search(pattern, value, flags)
        return match.group(1) if match else ""

    @staticmethod
    def _text(value: str) -> str:
        # Tags are separators, not empty strings: otherwise a property value
        # such as ``<td>LP</td>`` can be glued to its label and miss a word
        # boundary during media classification.
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()

    @staticmethod
    def _card_block(chunk: str) -> str:
        """Return one outer product div without leaking into its neighbour."""
        opening_end = chunk.find(">")
        if opening_end < 0:
            return chunk[:12000]
        depth = 1
        for tag in re.finditer(r"</?div\b[^>]*>", chunk[opening_end + 1:], re.I):
            if tag.group().startswith("</"):
                depth -= 1
                if depth == 0:
                    return chunk[:opening_end + 1 + tag.end()]
            else:
                depth += 1
        return chunk[:12000]

    @staticmethod
    def _money(value: str) -> Decimal | None:
        match = re.search(r"\d[\d\s]*(?:[.,]\d+)?", value.replace("\u00a0", " "))
        return Decimal(match.group().replace(" ", "").replace(",", ".")) if match else None

    @staticmethod
    def _offer_price(value: object) -> Decimal | None:
        if isinstance(value, list):
            value = value[0] if value else {}
        if isinstance(value, dict):
            value = value.get("price")
        return PublicHtmlVinylAdapter._money(str(value or ""))

    @staticmethod
    def _split_artist_title(value: str) -> tuple[str | None, str | None]:
        value = re.sub(r"^(?:виниловая\s+пластинка|vinyl)\s+", "", value, flags=re.I).strip()
        for separator in (" - ", " — ", " – ", " / "):
            if separator in value:
                artist, title = value.split(separator, 1)
                return artist.strip() or None, title.strip() or None
        return None, value or None

    @staticmethod
    def _format(value: str) -> str | None:
        match = re.search(r'(?<!\w)(?:\d+\s*LP|\d+LP|LP|EP|7["\']|10["\']|12["\'])(?!\w)', value, re.I)
        return re.sub(r"\s+", "", match.group()).upper() if match else None

    @staticmethod
    def _year(value: str) -> int | None:
        match = re.search(r"(?:19|20)\d{2}", value or "")
        return int(match.group()) if match else None

    @classmethod
    def _property(cls, text: str, *names: str) -> str | None:
        for name in names:
            value = cls._first(rf"{re.escape(name)}\s*[:#]?\s*([^|;]+)", text, re.I)
            if value:
                return value.strip()
        return None

    @classmethod
    def _properties(cls, html: str) -> dict[str, str]:
        pairs = re.findall(r'<(?:tr|dl|div)[^>]*>\s*<(?:th|dt|span)[^>]*>(.*?)</(?:th|dt|span)>\s*<(?:td|dd|span)[^>]*>(.*?)</(?:td|dd|span)>', html, re.I | re.S)
        return {cls._text(key).casefold(): cls._text(value) for key, value in pairs}

    @staticmethod
    def _disc_count(value: str) -> int | None:
        match = re.search(r"\d+", value)
        return int(match.group()) if match else None

    @staticmethod
    def _looks_like_vinyl(value: str) -> bool:
        return bool(re.search(r"(?:винил|vinyl|\b(?:\d+\s*)?LP\b|\bEP\b)", value, re.I))

    @staticmethod
    def _is_blocked(html: str) -> bool:
        text = html.casefold()
        return any(marker in text for marker in ("captcha", "smartcaptcha", "access-check", "проверка безопасности"))
