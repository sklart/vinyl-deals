from datetime import datetime, timezone
from decimal import Decimal
import sys

from vinyl_deals.cli import main
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer


def test_local_command_shows_fresh_pickup_offer_and_effective_price(tmp_path, monkeypatch, capsys) -> None:
    database = tmp_path / "local.sqlite3"
    SQLiteRepository(database).upsert_offer(RawOffer(
        source="rio_rostov", source_product_id="1", url="https://example.test/1", fetched_at=datetime.now(timezone.utc),
        artist_raw="Artist", title_raw="Album", price=Decimal("2990"), availability=Availability.IN_STOCK,
        city="Ростов-на-Дону", local_store=True, pickup_available=True,
    ))
    monkeypatch.setattr(sys, "argv", ["vinyl-deals", "local", "--database", str(database), "--pickup"])
    assert main() == 0
    output = capsys.readouterr().out
    assert "Effective price: 2990 RUB (самовывоз)" in output
