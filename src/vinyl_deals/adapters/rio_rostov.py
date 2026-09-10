"""Public-catalogue adapter for RIO Music, Rostov-on-Don.

RIO's server-rendered catalogue and product pages are used as-is.  The parser
does not attempt checkout, login, or protected endpoints.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
import json
import re
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from vinyl_deals.adapters.base import BaseStoreAdapter
from vinyl_deals.domain import Availability, RawOffer, ScrapeResult, StoreState


class RioRostovAdapter(BaseStoreAdapter):
    source = "rio_rostov"
    catalog_url = "https://rio-music.online/m/110aa7/"
    vintage_catalog_url = "https://rio-music.online/m/175c6b/"
    base_url = "https://rio-music.online"
    city = "Ростов-на-Дону"

    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def get_catalog(self) -> ScrapeResult:
        try:
            offers = [*self.parse_listing(self._fetch(self.catalog_url)), *self.parse_listing(self._fetch(self.vintage_catalog_url))]
        except HTTPError as error:
            if error.code in {403, 429}:
                return ScrapeResult((), StoreState.DEGRADED, (f"RIO returned HTTP {error.code}; source paused.",))
            raise
        # A product can appear in both public collections; retain one source
        # record rather than manufacturing duplicate shop offers.
        unique = {offer.source_product_id: offer for offer in offers}
        if not unique:
            return ScrapeResult((), StoreState.DEGRADED, ("RIO catalogue parsed zero offers; parser may be stale."), pages_processed=2)
        return ScrapeResult(tuple(unique.values()), pages_processed=2)

    def enrich_offer(self, offer: RawOffer) -> RawOffer:
        return self.parse_product_page(self._fetch(offer.url), offer)

    def parse_listing(self, html: str, *, fetched_at: datetime | None = None) -> list[RawOffer]:
        timestamp = fetched_at or datetime.now(timezone.utc)
        offers: list[RawOffer] = []
        for match in re.finditer(r'<a[^>]+href="(?P<path>/o/(?P<id>[^/"?#]+)[^"]*)"[^>]*>(?P<body>.*?)</a>', html, re.S | re.I):
            body = self._text(match.group("body"))
            price = self._money(body)
            if price is None:
                continue
            name = re.sub(r"(?:Нет в наличии\s*)?[\d\s]+₽.*$", "", body, flags=re.I).strip()
            artist, title = self._split_artist_title(name)
            offers.append(RawOffer(
                source=self.source, source_product_id=match.group("id"), url=urljoin(self.base_url, match.group("path")),
                fetched_at=timestamp, artist_raw=artist, title_raw=title, price=price,
                availability=Availability.OUT_OF_STOCK if "нет в наличии" in body.casefold() else Availability.IN_STOCK,
                city=self.city, local_store=True, pickup_available=True, delivery_available=True,
                raw_data={"listing_name": name},
            ))
        if offers:
            return offers
        # The production page supplies the same public catalogue records in a
        # server-rendered JSON payload and constructs card links client-side.
        # Reading that payload is equivalent to reading the visible listing.
        payload = self._public_data(html)
        for product in payload.get("products", ()):
            product_id = product.get("product_id")
            price = product.get("price")
            if not isinstance(product_id, int) or price is None:
                continue
            name = str(product.get("title") or "").strip()
            artist, title = self._split_artist_title(name)
            amount = product.get("amount")
            offers.append(RawOffer(
                source=self.source, source_product_id=f"{product_id:x}", url=f"{self.base_url}/o/{product_id:x}/",
                fetched_at=timestamp, artist_raw=artist, title_raw=title, price=Decimal(str(price)),
                availability=Availability.IN_STOCK if amount is None or amount > 0 else Availability.OUT_OF_STOCK,
                stock_quantity=amount if isinstance(amount, int) else None,
                city=self.city, local_store=True, pickup_available=True, delivery_available=True,
                raw_data={"listing_name": name, "product_id": product_id},
            ))
        return offers

    def parse_product_page(self, html: str, listing_offer: RawOffer) -> RawOffer:
        content = self._text(html)
        barcode = next(iter(re.findall(r"(?<!\d)(\d{12,14})(?!\d)", content)), "") or None
        condition = self._condition(content)
        metadata = self._metadata(content, barcode)
        return RawOffer(**{**{name: getattr(listing_offer, name) for name in listing_offer.__dataclass_fields__},
            "barcode": barcode or listing_offer.barcode,
            "label": metadata[0] or listing_offer.label,
            "release_year": metadata[1] or listing_offer.release_year,
            "country": metadata[2] or listing_offer.country,
            "format": self._format(content) or listing_offer.format,
            "condition_media": condition or listing_offer.condition_media,
            "condition_sleeve": condition or listing_offer.condition_sleeve,
            "raw_data": {**listing_offer.raw_data, "public_card": True},
        })

    def _fetch(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": "VinylDeals/0.1 (+local research)"})
        with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310: fixed HTTPS origin
            return response.read().decode("utf-8")

    @staticmethod
    def _text(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(value))).strip()

    @staticmethod
    def _money(value: str) -> Decimal | None:
        match = re.search(r"(\d[\d\s]*)\s*₽", value)
        return Decimal(match.group(1).replace(" ", "")) if match else None

    @staticmethod
    def _split_artist_title(value: str) -> tuple[str | None, str | None]:
        for separator in (" — ", " - ", " – "):
            if separator in value:
                artist, title = value.split(separator, 1)
                return artist.strip() or None, title.strip() or None
        return None, value or None

    @staticmethod
    def _condition(content: str) -> str | None:
        match = re.search(r"\b([smvgex]{1,3})\s*/\s*([smvgex]{1,3})\b", content, re.I)
        if not match:
            return None
        value = match.group(1).casefold()
        return "SEALED" if value == "s" else value.upper()

    @staticmethod
    def _format(content: str) -> str | None:
        match = re.search(r"\b(?:\dLP|LP|EP|12['\"]|10['\"]|7['\"])\b", content, re.I)
        return match.group().upper() if match else None

    @staticmethod
    def _metadata(content: str, barcode: str | None) -> tuple[str | None, int | None, str | None]:
        if not barcode:
            return None, None, None
        before = content.split(barcode, 1)[0]
        years = re.findall(r"\b(?:19|20)\d{2}\b", before)
        label = before.rsplit(" ", 1)[-1] if False else None  # label is source-specific and optional.
        country_match = re.search(r"\b(Europe|USA|UK|Germany|Russia|Japan)\b", content, re.I)
        return label, int(years[-1]) if years else None, country_match.group(1) if country_match else None

    @staticmethod
    def _public_data(html: str) -> dict[str, object]:
        """Extract the page's embedded ``data`` object without regex JSON."""
        marker = '"data":'
        # Pages contain several component payloads.  Choose the data object
        # that actually owns the public products array.
        products_index = html.find('"products":[{')
        marker_index = html.rfind(marker, 0, products_index) if products_index >= 0 else -1
        start = html.find("{", marker_index + len(marker))
        if marker_index < 0 or start < 0:
            return {}
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(html)):
            character = html[index]
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(html[start:index + 1])
                    except json.JSONDecodeError:
                        return {}
                    return value if isinstance(value, dict) else {}
        return {}
