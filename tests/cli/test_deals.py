from datetime import datetime, timezone
from decimal import Decimal
import sys

from vinyl_deals.cli import main
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer
from vinyl_deals.matching.service import build_match_queue


def _offer(source: str, product: str, price: str) -> RawOffer:
    return RawOffer(
        source=source,
        source_product_id=product,
        url=f"https://example.test/{product}",
        fetched_at=datetime.now(timezone.utc),
        artist_raw="Artist",
        title_raw="Album",
        barcode="4006381333931",
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
