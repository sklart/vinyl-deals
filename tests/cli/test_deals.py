from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sys

from vinyl_deals.cli import main
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer
from vinyl_deals.matching.service import build_match_queue


def _offer(source: str, product: str, price: str, *, when=None, barcode="4006381333931") -> RawOffer:
    return RawOffer(
        source=source,
        source_product_id=product,
        url=f"https://example.test/{product}",
        fetched_at=when or datetime.now(timezone.utc),
        artist_raw="Artist",
        title_raw="Album",
        barcode=barcode,
        label="Label",
        catalog_number_raw="CAT-1",
        release_year=2024,
        price=Decimal(price),
        availability=Availability.IN_STOCK,
        condition_media="NEW",
    )


def test_deals_prints_explainable_price_fields(tmp_path, monkeypatch, capsys):
    database = tmp_path / "deals.sqlite3"
    repository = SQLiteRepository(database)
    repository.upsert_offer(_offer("target", "1", "70"))
    repository.upsert_offer(_offer("store-b", "2", "100"))
    repository.upsert_offer(_offer("store-c", "3", "100"))
    repository.upsert_offer(_offer("store-d", "4", "100"))
    build_match_queue(repository)

    monkeypatch.setattr(sys, "argv", ["vinyl-deals", "deals", "--database", str(database), "--min-class", "GOOD"])
    assert main() == 0
    output = capsys.readouterr().out
    assert "HOT 30%" in output
    assert "Release: Label / CAT-1 / 2024" in output
    assert "Market median: 100" in output
    assert "Comparisons: 3" in output
    assert "90d minimum:" in output
    assert "Reasons:" in output


def test_include_insufficient_only_shows_strong_historical_signal(tmp_path, monkeypatch, capsys):
    database = tmp_path / "historical-only.sqlite3"
    repository = SQLiteRepository(database)
    now = datetime.now(timezone.utc)
    repository.upsert_offer(_offer("target", "1", "100", when=now - timedelta(days=1)))
    repository.upsert_offer(_offer("target", "1", "70", when=now))
    repository.upsert_offer(_offer("store-b", "2", "100", when=now))
    repository.upsert_offer(_offer("weak", "3", "90", when=now, barcode="4012345678901"))
    repository.upsert_offer(_offer("weak-store", "4", "100", when=now, barcode="4012345678901"))
    build_match_queue(repository)

    monkeypatch.setattr(sys, "argv", ["vinyl-deals", "deals", "--database", str(database)])
    assert main() == 0
    assert "INSUFFICIENT" not in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["vinyl-deals", "deals", "--database", str(database), "--include-insufficient"])
    assert main() == 0
    output = capsys.readouterr().out
    assert "INSUFFICIENT" in output
    assert "historical signal only" in output
    assert "INSUFFICIENT 30%" not in output
    assert "weak" not in output
