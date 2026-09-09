from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from vinyl_deals.adapters.vinyl_ru import VinylRuAdapter
from vinyl_deals.domain import Availability

def test_parses_public_catalog_fixture_without_network() -> None:
    payload = Path("tests/fixtures/vinyl_ru/catalog.csv").read_bytes(); offers = VinylRuAdapter().parse_catalog(payload, fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert len(offers) == 2; assert offers[0].source_product_id == "48915"; assert offers[0].artist_raw == "Nina Hagen"; assert offers[0].price == Decimal("5500"); assert offers[0].barcode == "5099746251081"; assert offers[1].availability == Availability.OUT_OF_STOCK; assert offers[1].old_price == Decimal("4990")

def test_missing_id_gets_stable_fallback() -> None:
    offers = VinylRuAdapter().parse_catalog("Название;Цена\nArtist - Album;1000\n"); assert len(offers[0].source_product_id) == 16

def test_detects_cp1251_even_when_bytes_also_form_valid_utf8() -> None:
    payload = "Исполнитель;Альбом;Артикул;Цена в руб.\nАлиса;Шабаш;00-1;1500\n".encode("cp1251")
    offer = VinylRuAdapter().parse_catalog(payload)[0]
    assert (offer.artist_raw, offer.title_raw, offer.source_product_id, offer.price) == ("Алиса", "Шабаш", "00-1", Decimal("1500"))
