import sqlite3
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import RawOffer
from vinyl_deals.matching.service import build_match_queue

def add(repo, source, product, barcode): repo.upsert_offer(RawOffer.now(source=source, source_product_id=product, url="https://x", artist_raw="Artist", title_raw="Album", barcode=barcode))

def test_exact_chain_creates_one_release_for_three_offers(tmp_path):
    database = tmp_path / "chain.sqlite3"; repo = SQLiteRepository(database)
    add(repo, "a", "1", "4006381333931"); add(repo, "b", "2", "4006381333931"); add(repo, "c", "3", "4006381333931"); build_match_queue(repo)
    with sqlite3.connect(database) as c:
        assert c.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM offers WHERE release_id IS NOT NULL").fetchone()[0] == 3

def test_manual_different_blocks_rebuild(tmp_path):
    database = tmp_path / "manual.sqlite3"; repo = SQLiteRepository(database)
    add(repo, "a", "1", "4006381333931"); add(repo, "b", "2", "4006381333931"); repo.record_match(1, 2, "possible", .6, ())
    repo.decide_match(1, 2, "different_release"); build_match_queue(repo)
    with sqlite3.connect(database) as c:
        assert c.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0

def test_manual_same_merges_previously_separate_releases(tmp_path):
    database = tmp_path / "merge.sqlite3"; repo = SQLiteRepository(database)
    add(repo, "a", "1", "4006381333931"); add(repo, "b", "2", "4006381333931"); add(repo, "c", "c", "5901234123457"); add(repo, "d", "4", "5901234123457"); build_match_queue(repo)
    repo.record_match(2, 3, "possible", .6, ()); repo.decide_match(2, 3, "same_release")
    with sqlite3.connect(database) as c:
        assert c.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(DISTINCT release_id) FROM offers").fetchone()[0] == 1

def test_ignore_stays_out_of_pending_queue(tmp_path):
    database = tmp_path / "ignore.sqlite3"; repo = SQLiteRepository(database)
    repo.upsert_offer(RawOffer.now(source="a", source_product_id="1", url="x", artist_raw="Artist", title_raw="Album")); repo.upsert_offer(RawOffer.now(source="b", source_product_id="2", url="x", artist_raw="Artist", title_raw="Album")); repo.record_match(1, 2, "possible", .6, ())
    repo.decide_match(1, 2, "ignore"); build_match_queue(repo)
    assert repo.possible_matches() == []
