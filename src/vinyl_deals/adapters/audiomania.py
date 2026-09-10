"""Conservative public JSON-LD adapter for Audiomania vinyl records."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from vinyl_deals.adapters.base import BaseStoreAdapter
from vinyl_deals.domain import Availability, RawOffer, ScrapeResult, StoreState


class AudiomaniaAdapter(BaseStoreAdapter):
    source = "audiomania"
    catalog_url = "https://www.audiomania.ru/vinyl/"

    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def get_catalog(self) -> ScrapeResult:
        try:
            offers = self.parse_listing(self._fetch(self.catalog_url))
        except (HTTPError, URLError, OSError) as error:
            status = getattr(error, "code", None)
            detail = f"HTTP {status}" if status in {403, 429} else type(error).__name__
            return ScrapeResult((), StoreState.DEGRADED, (f"Audiomania public catalogue unavailable ({detail}); source paused.",))
        if not offers:
            return ScrapeResult((), StoreState.DEGRADED, ("Audiomania catalogue parsed zero offers; parser may be stale."), pages_processed=1)
        return ScrapeResult(tuple({offer.source_product_id: offer for offer in offers}.values()), pages_processed=1)

    def enrich_offer(self, offer: RawOffer) -> RawOffer:
        return self.parse_product_page(self._fetch(offer.url), offer)

    def parse_listing(self, html: str, *, fetched_at: datetime | None = None) -> list[RawOffer]:
        timestamp = fetched_at or datetime.now(timezone.utc)
        offers: list[RawOffer] = []
        for product in self._products_from_jsonld(html):
            product_id = str(product.get("sku") or product.get("productID") or "")
            name = str(product.get("name") or "")
            price_data = product.get("offers") if isinstance(product.get("offers"), dict) else {}
            price = self._decimal(price_data.get("price"))
            url = str(product.get("url") or "")
            if not product_id or not name or price is None or not url:
                continue
            artist, title = self._split_artist_title(name)
            availability = Availability.IN_STOCK if "instock" in str(price_data.get("availability", "")).casefold() else Availability.OUT_OF_STOCK
            offers.append(RawOffer(source=self.source, source_product_id=product_id, url=url, fetched_at=timestamp,
                artist_raw=artist, title_raw=title, price=price, availability=availability, format=self._format(name),
                condition_media="NEW", condition_sleeve="NEW", raw_data={"jsonld": True}))
        return offers

    def parse_product_page(self, html: str, listing_offer: RawOffer) -> RawOffer:
        product = next(iter(self._products_from_jsonld(html)), {})
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
    def _decimal(value: object) -> Decimal | None:
        try: return Decimal(str(value))
        except Exception: return None

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
