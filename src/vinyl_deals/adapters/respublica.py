"""Public server-rendered catalogue adapter for Respublica vinyl records."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
import json
import re
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from vinyl_deals.adapters.base import BaseStoreAdapter
from vinyl_deals.domain import Availability, RawOffer, ScrapeResult, StoreSearchQuery, StoreSearchResult, StoreSearchStatus, StoreState


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

    def search_offers(self, query: StoreSearchQuery) -> StoreSearchResult:
        """Read the public server-rendered ``/search?query=`` result.

        Respublica's Nuxt storefront serialises product cards in the initial
        response.  This is a regular public GET, not an internal API or a
        catalogue crawl.  Details are still fetched by the shared live-search
        service before a price is persisted.
        """
        url = f"{self.base_url}/search?query={quote_plus(query.text())}"
        try:
            html = self._fetch(url)
        except (HTTPError, URLError, OSError) as error:
            status = getattr(error, "code", None)
            restricted = status in {403, 429}
            return StoreSearchResult(
                self.source, (), StoreState.DEGRADED,
                (f"Respublica public search unavailable ({'HTTP ' + str(status) if restricted else type(error).__name__}).",),
                status=StoreSearchStatus.RESTRICTED if restricted else StoreSearchStatus.ERROR,
            )
        if self._is_blocked(html):
            return StoreSearchResult(self.source, (), StoreState.DEGRADED, ("Respublica returned a CAPTCHA or access-check page; source paused.",), status=StoreSearchStatus.RESTRICTED)
        offers = tuple(self._parse_nuxt_search(html))
        return StoreSearchResult(
            self.source, offers, StoreState.ACTIVE,
            status=StoreSearchStatus.FOUND if offers else StoreSearchStatus.EMPTY,
        )

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
        # ``Артикул`` is a Respublica shop identifier, not a pressing
        # catalogue number.  Keep it out of matching metadata: a false
        # catalogue number can merge different editions across stores.
        catalog = self._first(r'(?:Каталожный\s+номер|Catalog(?:ue)?\s*(?:No\.?|number)?)\s*[:#]?\s*([A-Za-z0-9._/-]+)', content, re.I) or listing_offer.catalog_number_raw
        label = self._text(self._first(r'(?:Лейбл|Label)\s*[:#]?\s*(.*?)</p>', html, re.I | re.S)).strip() or listing_offer.label
        year = self._year(self._text(self._first(r'(?:Год|Year)\s*[:#]?\s*(.*?)</p>', html, re.I | re.S))) or listing_offer.release_year
        # Prefer a structured price exposed by this selected product page.
        # If a page has changed or lacks such a field, retain the price from
        # its own search card instead of guessing from recommendation blocks.
        detail_price = self._structured_price(html)
        availability = self._detail_availability(content, listing_offer.availability)
        return RawOffer(**{**{name: getattr(listing_offer, name) for name in listing_offer.__dataclass_fields__},
            "barcode": barcode, "catalog_number_raw": catalog, "label": label, "release_year": year,
            "format": self._format(content) or listing_offer.format,
            "price": detail_price or listing_offer.price,
            "availability": availability,
            "raw_data": {**listing_offer.raw_data, "public_card": True, "detail_price_confirmed": detail_price is not None},
        })

    def _structured_price(self, html: str) -> Decimal | None:
        values = (
            self._first(r'itemprop=["\']price["\'][^>]*content=["\']([^"\']+)', html, re.I),
            self._first(r'content=["\']([^"\']+)["\'][^>]*itemprop=["\']price["\']', html, re.I),
            self._first(r'["\']price["\']\s*:\s*["\']?([\d\s.,]+)', html, re.I),
            self._first(r'(?:Цена|Стоимость)\s*[:：]\s*([\d\s.,]+)\s*(?:₽|руб)', self._text(html), re.I),
        )
        for value in values:
            price = self._money(value)
            if price is not None:
                return price
        return None

    @staticmethod
    def _detail_availability(content: str, fallback: Availability) -> Availability:
        lowered = content.casefold()
        if re.search(r'(?:нет\s+в\s+наличии|распродано|out[- ]of[- ]stock)', lowered):
            return Availability.OUT_OF_STOCK
        if re.search(r'(?:добавить\s+в\s+корзину|в\s+наличии|in[- ]stock)', lowered):
            return Availability.IN_STOCK
        return fallback

    def _parse_nuxt_search(self, html: str, *, fetched_at: datetime | None = None) -> list[RawOffer]:
        """Parse the stable product attributes in Nuxt's SSR payload.

        Nuxt minifies repeated scalar values into function arguments (for
        example ``price:o``).  Resolve only simple literals; malformed or
        unexpected payloads simply yield no candidate rather than inventing a
        price from nearby markup.
        """
        aliases = self._nuxt_aliases(html)
        timestamp = fetched_at or datetime.now(timezone.utc)
        offers: list[RawOffer] = []
        for marker in re.finditer(r"attributes:\{", html):
            block = self._balanced_object(html, marker.end() - 1)
            if not block:
                continue
            product_id = self._nuxt_value(self._first(r"(?:^|[,{])id:([^,}]+)", block), aliases)
            sku = self._nuxt_value(self._first(r"(?:^|[,{])sku:([^,}]+)", block), aliases)
            slug = self._nuxt_value(self._first(r"(?:^|[,{])slug:([^,}]+)", block), aliases)
            title = self._nuxt_value(self._first(r"(?:^|[,{])title:([^,}]+)", block), aliases)
            category = self._nuxt_value(self._first(r"(?:^|[,{])category_cached_path:([^,}]+)", block), aliases)
            price = self._decimal_value(self._nuxt_value(self._first(r"(?:^|[,{])price:([^,}]+)", block), aliases))
            available = self._nuxt_value(self._first(r"(?:^|[,{])available:([^,}]+)", block), aliases)
            if not all((product_id, slug, title, price is not None)):
                continue
            # Search is global. The category provenance—not a casual word in
            # description text—is the vinyl classifier.
            if not isinstance(category, str) or "vinilovye-plastinki" not in category.casefold():
                continue
            artist, album = self._split_artist_title(str(title))
            old_price = self._decimal_value(self._nuxt_value(self._first(r"(?:^|[,{])last_price:([^,}]+)", block), aliases))
            # The public SSR card links use the shop SKU as a slug prefix;
            # ``/<category>/<slug>`` is a plausible but non-existent URL.
            product_path = f"{sku}-{slug}" if sku else str(slug)
            offers.append(RawOffer(
                source=self.source, source_product_id=str(product_id), store_sku=str(sku) if sku else None,
                url=f"{self.base_url}/{category.strip('/')}/{product_path.strip('/')}", fetched_at=timestamp,
                artist_raw=artist, title_raw=album, price=price,
                old_price=old_price if old_price and old_price != price else None,
                availability=Availability.IN_STOCK if available is True else Availability.OUT_OF_STOCK if available is False else Availability.UNKNOWN,
                format=self._format(str(title)), condition_media="NEW", condition_sleeve="NEW",
                raw_data={"respublica_nuxt_search": True, "listing_title": str(title)},
            ))
        return list({offer.source_product_id: offer for offer in offers}.values())

    @staticmethod
    def _balanced_object(value: str, start: int) -> str:
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(value)):
            char = value[index]
            if in_string:
                if escaped: escaped = False
                elif char == "\\": escaped = True
                elif char == '"': in_string = False
            elif char == '"': in_string = True
            elif char == "{": depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0: return value[start:index + 1]
        return ""

    @classmethod
    def _nuxt_aliases(cls, html: str) -> dict[str, object]:
        match = re.search(r"window\.__NUXT__=\(function\(([^)]*)\).*?\}\)\((.*?)\);</script>", html, re.S)
        # Nuxt has used both equivalent IIFE spellings in production:
        # ``(function(){...})(args);`` and ``(function(){...}(args));``.
        # The latter has one extra closing parenthesis after the argument
        # list, so accepting only the former silently turns a real public
        # result into an EMPTY search.
        if not match:
            match = re.search(r"window\.__NUXT__=\(function\(([^)]*)\).*?\}\((.*?)\)\);</script>", html, re.S)
        if not match: return {}
        names, values = match.group(1).split(","), cls._split_js_args(match.group(2))
        return {name.strip(): cls._nuxt_value(value, {}) for name, value in zip(names, values, strict=False)}

    @staticmethod
    def _split_js_args(value: str) -> list[str]:
        parts, start, depth, in_string, escaped = [], 0, 0, False, False
        for index, char in enumerate(value):
            if in_string:
                if escaped: escaped = False
                elif char == "\\": escaped = True
                elif char == '"': in_string = False
            elif char == '"': in_string = True
            elif char in "([{": depth += 1
            elif char in ")]}": depth -= 1
            elif char == "," and depth == 0:
                parts.append(value[start:index].strip()); start = index + 1
        parts.append(value[start:].strip())
        return parts

    @staticmethod
    def _nuxt_value(value: str, aliases: dict[str, object]) -> object | None:
        value = value.strip()
        if value in aliases: return aliases[value]
        if value in {"null", "void 0", ""}: return None
        if value == "true": return True
        if value == "false": return False
        if re.fullmatch(r"-?\d+(?:\.\d+)?", value): return Decimal(value) if "." in value else int(value)
        if value.startswith('"') and value.endswith('"'):
            try: return json.loads(value)
            except json.JSONDecodeError: return value[1:-1]
        return value if re.fullmatch(r"[\w./-]+", value) else None

    @staticmethod
    def _decimal_value(value: object | None) -> Decimal | None:
        try: return Decimal(str(value)) if value is not None else None
        except Exception: return None

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
        # The normal Nuxt response includes an ``hcaptcha`` configuration
        # value in a script. It is not itself a challenge. Restrict detection
        # to visible/document markup, as done by the generic public adapter.
        visible = re.sub(
            r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", "", html.casefold(),
            flags=re.I | re.S,
        )
        return any(marker in visible for marker in ("captcha", "smartcaptcha", "access-check", "проверка безопасности"))
