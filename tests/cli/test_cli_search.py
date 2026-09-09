from datetime import datetime, timezone
from decimal import Decimal
import sys

from vinyl_deals.cli import main
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer
from vinyl_deals.matching.service import build_match_queue


def test_search_command_prints_release_offers_and_discogs(tmp_path, monkeypatch, capsys):
    database = tmp_path / "search-cli.sqlite3"
    repository = SQLiteRepository(database)
    for source, product, price in (("imagine", "1", "5490"), ("collectomania", "2", "5990")):
        repository.upsert_offer(RawOffer(source=source, source_product_id=product, url="https://example.test", fetched_at=datetime.now(timezone.utc), artist_raw="Opeth", title_raw="Blackwater Park", barcode="4006381333931", label="Music On Vinyl", catalog_number_raw="MOVLP001", release_year=2021, format="2LP", condition_media="NEW", price=Decimal(price), availability=Availability.IN_STOCK))
    build_match_queue(repository)

    monkeypatch.setattr(sys, "argv", ["vinyl-deals", "search", "--database", str(database), "--artist", "opeth", "--title", "blackwater"])
    assert main() == 0
    output = capsys.readouterr().out
    assert "Opeth — Blackwater Park" in output
    assert "BEST: imagine — 5490 RUB" in output
    assert "collectomania" in output
    assert "Discogs: https://www.discogs.com/search/?q=4006381333931&type=all" in output
