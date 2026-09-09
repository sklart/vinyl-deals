from pathlib import Path
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import RawOffer
from vinyl_deals.matching.service import build_match_queue

def test_builds_possible_pair_from_persisted_offers(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "matching.sqlite3")
    repository.upsert_offer(RawOffer.now(source="a", source_product_id="1", url="https://a", artist_raw="Pink Floyd", title_raw="Animals"))
    repository.upsert_offer(RawOffer.now(source="b", source_product_id="2", url="https://b", artist_raw="Pink Floyd", title_raw="Animals"))
    assert build_match_queue(repository) == 1
    assert len(repository.possible_matches()) == 1
