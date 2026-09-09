import sqlite3

import pytest

from vinyl_deals.database import SQLiteRepository
from vinyl_deals.database.migrations import CURRENT_VERSION
from vinyl_deals.database.repository import ManualDecisionConflict, ReleaseMergeConflict
from vinyl_deals.domain import RawOffer
from vinyl_deals.matching import MatchKind, match_offers
from vinyl_deals.matching.service import build_match_queue


def add(repository, source, product, barcode="4006381333931", **values):
    repository.upsert_offer(RawOffer.now(
        source=source, source_product_id=product, url=f"https://{source}/{product}",
        artist_raw="Artist", title_raw="Album", barcode=barcode, **values,
    ))


def release_ids(path):
    with sqlite3.connect(path) as connection:
        return [row[0] for row in connection.execute("SELECT release_id FROM offers ORDER BY id")]


def test_manual_different_rebuilds_an_existing_auto_cluster(tmp_path):
    path = tmp_path / "cluster.sqlite3"
    repository = SQLiteRepository(path)
    add(repository, "a", "1"); add(repository, "b", "2")
    build_match_queue(repository)
    assert release_ids(path) == [1, 1]
    repository.decide_match(1, 2, "different_release")
    assert len(set(release_ids(path))) == 2
    assert repository.manual_decision(2, 1) == "different_release"


def test_manual_different_blocks_cross_cluster_merge(tmp_path):
    path = tmp_path / "merge.sqlite3"
    repository = SQLiteRepository(path)
    add(repository, "a", "1", "4006381333931"); add(repository, "b", "2", "4006381333931")
    add(repository, "c", "3", "5901234123457"); add(repository, "d", "4", "5901234123457")
    build_match_queue(repository)
    repository.record_match(1, 3, "possible", 0.6, ())
    repository.decide_match(1, 3, "different_release")
    repository.record_match(2, 4, "possible", 0.6, ())
    with pytest.raises(ReleaseMergeConflict, match="manual DIFFERENT_RELEASE"):
        repository.decide_match(2, 4, "same_release")
    assert len(set(release_ids(path))) == 2


def test_contradictory_manual_decisions_are_rejected(tmp_path):
    repository = SQLiteRepository(tmp_path / "manual.sqlite3")
    add(repository, "a", "1"); add(repository, "b", "2"); add(repository, "c", "3")
    for left, right in ((1, 2), (2, 3)):
        repository.record_match(left, right, "possible", 0.6, ())
        repository.decide_match(left, right, "same_release")
    with pytest.raises(ManualDecisionConflict, match="manual SAME_RELEASE"):
        repository.decide_match(1, 3, "different_release")


def test_catalog_and_label_with_different_year_is_not_auto_match():
    left = RawOffer.now(source="a", source_product_id="1", url="x", artist_raw="Artist", title_raw="Album", label="Label", catalog_number_raw="CAT-1", release_year=2016)
    right = RawOffer.now(source="b", source_product_id="2", url="x", artist_raw="Artist", title_raw="Album", label="Label", catalog_number_raw="CAT-1", release_year=2023)
    assert match_offers(left, right).kind == MatchKind.POSSIBLE


def test_foreign_keys_are_enabled_and_cleanup_removes_empty_releases(tmp_path):
    path = tmp_path / "foreign.sqlite3"
    repository = SQLiteRepository(path)
    repository.initialize()
    with repository._connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        connection.execute("INSERT INTO releases(artist, title, edition_tags, created_at, updated_at) VALUES ('A', 'B', '[]', 'now', 'now')")
    assert repository.cleanup_empty_releases() == 1


def test_repeated_queue_build_is_idempotent(tmp_path):
    path = tmp_path / "idempotent.sqlite3"
    repository = SQLiteRepository(path)
    add(repository, "a", "1"); add(repository, "b", "2")
    repository.record_match(1, 2, "possible", 0.6, ())
    repository.decide_match(2, 1, "different_release")
    for _ in range(3):
        build_match_queue(repository)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM release_matches").fetchone()[0] == 1
    assert repository.manual_decision(1, 2) == "different_release"


@pytest.mark.parametrize("field, left, right", [("format", "LP", "CD"), ("disc_count", 1, 2)])
def test_transitive_bridge_never_creates_an_incompatible_cluster(tmp_path, field, left, right):
    path = tmp_path / "bridge.sqlite3"
    repository = SQLiteRepository(path)
    add(repository, "a", "1", **{field: left})
    add(repository, "b", "2")
    add(repository, "c", "3", **{field: right})
    build_match_queue(repository)
    with sqlite3.connect(path) as connection:
        releases = connection.execute("SELECT release_id FROM offers ORDER BY id").fetchall()
        assert len({row[0] for row in releases}) > 1
        assert connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
    for release_id in {row[0] for row in releases if row[0] is not None}:
        assert repository.validate_release_invariants(release_id) == []


def test_manual_same_unassigned_incompatible_offers_is_rejected(tmp_path):
    path = tmp_path / "unassigned.sqlite3"
    repository = SQLiteRepository(path)
    add(repository, "a", "1", format="LP")
    add(repository, "b", "2", format="CD")
    repository.record_match(1, 2, "possible", 0.6, ())
    with pytest.raises(ReleaseMergeConflict):
        repository.decide_match(1, 2, "same_release")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM offers WHERE release_id IS NOT NULL").fetchone()[0] == 0
        assert connection.execute("SELECT status FROM release_matches").fetchone()[0] == "pending"
    assert repository.manual_decision(1, 2) is None


def test_standalone_rebuild_commits_and_is_idempotent(tmp_path):
    path = tmp_path / "rebuild.sqlite3"
    repository = SQLiteRepository(path)
    add(repository, "a", "1"); add(repository, "b", "2")
    build_match_queue(repository)
    release_id = release_ids(path)[0]
    repository.rebuild_release_cluster(release_id)
    first = release_ids(path)
    repository.rebuild_release_cluster(release_id)
    repository.rebuild_release_cluster(release_id)
    assert release_ids(path) == first


def test_legacy_database_is_upgraded_without_losing_offer_or_history(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE raw_products (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_product_id TEXT NOT NULL, fetched_at TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(source, source_product_id));
            CREATE TABLE offers (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_product_id TEXT NOT NULL, url TEXT NOT NULL, artist_raw TEXT, title_raw TEXT, barcode TEXT, catalog_number_raw TEXT, label TEXT, country TEXT, release_year INTEGER, format TEXT, disc_count INTEGER, rpm INTEGER, vinyl_color TEXT, edition_tags TEXT NOT NULL DEFAULT '[]', condition_media TEXT, price TEXT, availability TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE(source, source_product_id));
            CREATE TABLE price_history (id INTEGER PRIMARY KEY, offer_id INTEGER NOT NULL, observed_at TEXT NOT NULL, price TEXT, old_price TEXT, availability TEXT NOT NULL);
            CREATE TABLE releases (id INTEGER PRIMARY KEY, artist TEXT NOT NULL, title TEXT NOT NULL, barcode TEXT, label TEXT, catalog_number TEXT, release_year INTEGER, country TEXT, format TEXT, disc_count INTEGER, vinyl_size TEXT, rpm INTEGER, vinyl_color TEXT, edition_tags TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE release_matches (id INTEGER PRIMARY KEY, offer_id INTEGER NOT NULL, release_id INTEGER, candidate_offer_id INTEGER, kind TEXT NOT NULL, confidence REAL NOT NULL, reasons TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, UNIQUE(offer_id, candidate_offer_id));
            CREATE TABLE manual_match_decisions (id INTEGER PRIMARY KEY, offer_id INTEGER NOT NULL, candidate_offer_id INTEGER NOT NULL, decision TEXT NOT NULL, note TEXT, decided_at TEXT NOT NULL, UNIQUE(offer_id, candidate_offer_id));
            INSERT INTO raw_products(source, source_product_id, fetched_at, payload_json) VALUES ('legacy', '1', '2025-01-01T00:00:00+00:00', '{"origin":"legacy"}');
            INSERT INTO offers(source, source_product_id, url, artist_raw, title_raw, barcode, edition_tags, price, availability, last_seen) VALUES ('legacy', '1', 'https://legacy/1', 'Artist', 'Album', '4006381333931', '["limited"]', '1000', 'in_stock', '2025-01-01T00:00:00+00:00');
            INSERT INTO offers(source, source_product_id, url, artist_raw, title_raw, edition_tags, price, availability, last_seen) VALUES ('legacy', '2', 'https://legacy/2', 'Artist', 'Album 2', '[]', '900', 'in_stock', '2025-01-01T00:00:00+00:00');
            INSERT INTO price_history(offer_id, observed_at, price, availability) VALUES (1, '2025-01-01T00:00:00+00:00', '1000', 'in_stock');
            INSERT INTO releases(artist, title, edition_tags, created_at, updated_at) VALUES ('Artist', 'Album', '[]', 'now', 'now');
            INSERT INTO release_matches(offer_id, release_id, candidate_offer_id, kind, confidence, reasons, created_at) VALUES (1, 1, 2, 'possible', 0.5, '[]', 'now');
            INSERT INTO manual_match_decisions(offer_id, candidate_offer_id, decision, decided_at) VALUES (1, 2, 'ignore', 'now');
        """)
    repository = SQLiteRepository(path)
    assert repository.schema_version() == CURRENT_VERSION
    assert repository.offers_for_matching()[0][1].store_sku is None
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(offers)")}
        assert {"store_sku", "first_seen", "last_seen", "offer_json"} <= columns
        assert connection.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM raw_products").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM release_matches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM manual_match_decisions").fetchone()[0] == 1
        assert connection.execute("SELECT offer_json FROM offers").fetchone()[0] != "{}"
    add(repository, "fresh", "2")
    assert len(repository.offers_for_matching()) == 3
