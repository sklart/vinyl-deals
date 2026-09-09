from pathlib import Path
import sqlite3
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import RawOffer
from vinyl_deals.matching.service import build_match_queue

def test_exact_barcode_creates_release_but_possible_does_not(tmp_path: Path) -> None:
    database = tmp_path / "releases.sqlite3"; repository = SQLiteRepository(database)
    repository.upsert_offer(RawOffer.now(source="a", source_product_id="1", url="https://a", artist_raw="Artist", title_raw="Album", barcode="123")); repository.upsert_offer(RawOffer.now(source="b", source_product_id="2", url="https://b", artist_raw="Artist", title_raw="Album", barcode="123")); build_match_queue(repository); build_match_queue(repository)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM offers WHERE release_id IS NOT NULL").fetchone()[0] == 2
