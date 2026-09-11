from __future__ import annotations

import sqlite3

from vinyl_deals.database.migrations import SCHEMA_V1, migrate, migrate_v5


def test_v5_multiple_confirmed_discogs_matches_are_safely_downgraded(tmp_path):
    path = tmp_path / "legacy-v5.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_V1)
        migrate_v5(connection)
        connection.execute("INSERT INTO releases(id,artist,title,edition_tags,created_at,updated_at) VALUES (1,'Artist','Title','[]','now','now')")
        for identifier in (10, 20):
            connection.execute("INSERT INTO discogs_release_matches(release_id,discogs_release_id,discogs_url,confidence,match_kind,status,matched_at,metadata_json) VALUES (?,?,?,?,?,?,?,?)", (1, identifier, f"https://www.discogs.com/release/{identifier}", "HIGH", "catalog_and_label", "confirmed", "now", "{}"))
        connection.execute("PRAGMA user_version = 5")
        migrate(connection)
        assert connection.execute("SELECT COUNT(*) FROM discogs_release_matches WHERE status='confirmed'").fetchone()[0] == 0
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='ux_discogs_one_confirmed_per_release'").fetchone()
