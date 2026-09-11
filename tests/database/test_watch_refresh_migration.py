import sqlite3

from vinyl_deals.database.migrations import SCHEMA_V1, migrate, migrate_v3


def test_v6_to_v7_adds_persisted_watch_refresh_state(tmp_path):
    path = tmp_path / "v6.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_V1)
        migrate_v3(connection)
        connection.execute("PRAGMA user_version = 6")
        migrate(connection)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(watchlist)")}
        assert {"last_checked_at", "last_check_status", "last_offer_count"} <= columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
