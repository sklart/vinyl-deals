"""Adapter for Vinyl.ru's public catalogue export.

The CSV is Windows-1251 encoded and is intentionally parsed defensively: it is
a source record, not proof of a particular pressing.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from vinyl_deals.adapters.base import BaseStoreAdapter
from vinyl_deals.domain import Availability, RawOffer, ScrapeResult, StoreState


class VinylRuAdapter(BaseStoreAdapter):
    source = "vinyl_ru"
    catalog_url = "https://vinyl.ru/local/integration/data/vinyl_catalog_cp1251.csv"

    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def get_catalog(self) -> ScrapeResult:
        request = Request(self.catalog_url, headers={"User-Agent": "VinylDeals/0.1 (+local research)"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310: fixed HTTPS URL
                payload = response.read()
        except HTTPError as error:
            if error.code in {403, 429}:
                return ScrapeResult((), StoreState.DEGRADED, (f"Vinyl.ru returned HTTP {error.code}; source paused.",))
            raise
        offers = tuple(self.parse_catalog(payload))
        if not offers:
            return ScrapeResult(
                (),
                StoreState.DEGRADED,
                ("Vinyl.ru catalogue parsed zero offers; parser may be stale.",),
                pages_processed=1,
            )
        return ScrapeResult(offers, pages_processed=1)

    def parse_catalog(self, payload: bytes | str, *, fetched_at: datetime | None = None) -> list[RawOffer]:
        if isinstance(payload, bytes):
            # cp1251 byte sequences may be technically valid UTF-8 but decode
            # into mojibake, so choose by the known column vocabulary instead
            # of merely accepting the first decoding that does not raise.
            utf8 = payload.decode("utf-8-sig", errors="replace")
            cp1251 = payload.decode("cp1251", errors="replace")
            text = max((utf8, cp1251), key=lambda candidate: sum(
                marker in candidate.casefold() for marker in ("название", "исполнитель", "артикул", "artist", "name")
            ))
        else:
            text = payload
        timestamp = fetched_at or datetime.now(timezone.utc)
        dialect = csv.excel_tab if text.count("\t") > text.count(";") else csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect, delimiter=";" if dialect is csv.excel else "\t")
        offers: list[RawOffer] = []
        for row in reader:
            row = {self._key(key): (value or "").strip() for key, value in row.items() if key}
            product_id = self._value(row, "id", "product_id", "код", "артикул")
            if not product_id:
                product_id = hashlib.sha256(repr(sorted(row.items())).encode()).hexdigest()[:16]
            name = self._value(row, "name", "название", "товар", "альбом")
            artist = self._value(row, "artist", "исполнитель") or None
            artist, title = (artist, name or None) if artist else self._split_artist_title(name)
            price = self._price(self._value(row, "price", "цена", "цена_в_руб."))
            old_price = self._price(self._value(row, "old_price", "старая цена", "старая_цена"))
            stock_text = self._value(row, "availability", "наличие", "остаток", "stock")
            # The export has no individual product link. Retain the catalogue
            # URL rather than inventing a card URL from incomplete metadata.
            url = self._value(row, "url", "ссылка") or self.catalog_url
            offers.append(RawOffer(
                source=self.source, source_product_id=product_id, url=url, fetched_at=timestamp,
                artist_raw=artist, title_raw=title, edition_raw=self._value(row, "edition", "издание"),
                price=price, old_price=old_price, availability=self._availability(stock_text), stock_text=stock_text or None,
                format=self._value(row, "format", "формат") or None, label=self._value(row, "label", "лейбл") or None,
                catalog_number_raw=self._value(row, "catalog_number", "каталожный номер", "кат номер", "каталожный_номер", "артикул_производителя") or None,
                barcode=self._digits(self._value(row, "barcode", "ean", "штрихкод")) or None,
                release_year=self._year(self._value(row, "year", "год", "год_издания", "год_переиздания", "год_выпуска", "год_релиза_альбома")), country=self._value(row, "country", "страна") or None,
                image_url=self._value(row, "image", "image_url", "картинка") or None, raw_data=row,
            ))
        return offers

    @staticmethod
    def _key(value: str) -> str:
        return re.sub(r"\s+", "_", value.strip().casefold())

    @staticmethod
    def _value(row: dict[str, str], *names: str) -> str:
        return next((row.get(name, "") for name in names if row.get(name, "")), "")

    @staticmethod
    def _price(value: str) -> Decimal | None:
        cleaned = re.sub(r"[^\d,.-]", "", value).replace(",", ".")
        try:
            return Decimal(cleaned) if cleaned else None
        except InvalidOperation:
            return None

    @staticmethod
    def _digits(value: str) -> str:
        return re.sub(r"\D", "", value)

    @staticmethod
    def _year(value: str) -> int | None:
        match = re.search(r"(?:19|20)\d{2}", value)
        return int(match.group()) if match else None

    @staticmethod
    def _split_artist_title(name: str) -> tuple[str | None, str | None]:
        if not name:
            return None, None
        for separator in (" — ", " - ", " – "):
            if separator in name:
                artist, title = name.split(separator, 1)
                return artist.strip() or None, title.strip() or None
        return None, name

    @staticmethod
    def _availability(value: str) -> Availability:
        normalized = value.casefold()
        if any(token in normalized for token in ("нет", "out", "ожидается")):
            return Availability.OUT_OF_STOCK
        if normalized:
            return Availability.IN_STOCK
        # The public export is a live catalogue of offers for sale. It has no
        # separate stock column, so availability is documented as an inference.
        return Availability.IN_STOCK
