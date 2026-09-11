"""Small explicit SQLite migration registry for the MVP."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone

CURRENT_VERSION = 5

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS raw_products (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_product_id TEXT NOT NULL, fetched_at TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(source, source_product_id));
CREATE INDEX IF NOT EXISTS ix_raw_products_source_product ON raw_products(source, source_product_id);
CREATE INDEX IF NOT EXISTS ix_raw_products_fetched_at ON raw_products(fetched_at);
CREATE TABLE IF NOT EXISTS offers (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_product_id TEXT NOT NULL, url TEXT NOT NULL, release_id INTEGER REFERENCES releases(id), artist_raw TEXT, title_raw TEXT, barcode TEXT, catalog_number_raw TEXT, label TEXT, store_sku TEXT, country TEXT, release_year INTEGER, format TEXT, disc_count INTEGER, rpm INTEGER, vinyl_color TEXT, edition_tags TEXT NOT NULL DEFAULT '[]', condition_media TEXT, price TEXT, availability TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, offer_json TEXT NOT NULL DEFAULT '{}', UNIQUE(source, source_product_id));
CREATE INDEX IF NOT EXISTS ix_offers_barcode ON offers(barcode);
CREATE INDEX IF NOT EXISTS ix_offers_catalog_number ON offers(catalog_number_raw);
CREATE INDEX IF NOT EXISTS ix_offers_artist ON offers(artist_raw);
CREATE INDEX IF NOT EXISTS ix_offers_title ON offers(title_raw);
CREATE TABLE IF NOT EXISTS price_history (id INTEGER PRIMARY KEY, offer_id INTEGER NOT NULL REFERENCES offers(id), observed_at TEXT NOT NULL, price TEXT, old_price TEXT, availability TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_price_history_observed ON price_history(observed_at);
CREATE TABLE IF NOT EXISTS releases (id INTEGER PRIMARY KEY, artist TEXT NOT NULL, title TEXT NOT NULL, barcode TEXT, label TEXT, catalog_number TEXT, release_year INTEGER, country TEXT, format TEXT, disc_count INTEGER, vinyl_size TEXT, rpm INTEGER, vinyl_color TEXT, edition_tags TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_releases_barcode ON releases(barcode);
CREATE INDEX IF NOT EXISTS ix_releases_catalog_number ON releases(catalog_number);
CREATE TABLE IF NOT EXISTS release_matches (id INTEGER PRIMARY KEY, offer_id INTEGER NOT NULL REFERENCES offers(id), release_id INTEGER REFERENCES releases(id), candidate_offer_id INTEGER REFERENCES offers(id), kind TEXT NOT NULL, confidence REAL NOT NULL, reasons TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, UNIQUE(offer_id, candidate_offer_id));
CREATE INDEX IF NOT EXISTS ix_release_matches_release ON release_matches(release_id);
CREATE TABLE IF NOT EXISTS manual_match_decisions (id INTEGER PRIMARY KEY, offer_id INTEGER NOT NULL REFERENCES offers(id), candidate_offer_id INTEGER NOT NULL REFERENCES offers(id), decision TEXT NOT NULL, note TEXT, decided_at TEXT NOT NULL, UNIQUE(offer_id, candidate_offer_id));
CREATE TABLE IF NOT EXISTS scrape_runs (id INTEGER PRIMARY KEY, store TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, pages_processed INTEGER NOT NULL DEFAULT 0, offers_found INTEGER NOT NULL DEFAULT 0, errors TEXT NOT NULL DEFAULT '', warnings TEXT NOT NULL DEFAULT '');
"""

def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _canonicalize_pairs(connection: sqlite3.Connection, table: str) -> None:
    rows = connection.execute(
        f"SELECT id, offer_id, candidate_offer_id FROM {table} WHERE offer_id > candidate_offer_id"
    ).fetchall()
    for row_id, left, right in rows:
        forward = connection.execute(
            f"SELECT id FROM {table} WHERE offer_id=? AND candidate_offer_id=?", (right, left)
        ).fetchone()
        if forward:
            connection.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
        else:
            connection.execute(
                f"UPDATE {table} SET offer_id=?, candidate_offer_id=? WHERE id=?", (right, left, row_id)
            )


def _backfill_offer_json(connection: sqlite3.Connection) -> None:
    raw_by_key = {
        (source, product_id): (fetched_at, payload)
        for source, product_id, fetched_at, payload in connection.execute(
            "SELECT source, source_product_id, fetched_at, payload_json FROM raw_products"
        )
    }
    price_times = dict(connection.execute("SELECT offer_id, MIN(observed_at) FROM price_history GROUP BY offer_id"))
    cursor = connection.execute("SELECT * FROM offers")
    columns = [column[0] for column in cursor.description]
    for row in cursor.fetchall():
        value = dict(zip(columns, row, strict=True))
        raw_fetched_at, raw_data = raw_by_key.get((value["source"], value["source_product_id"]), (None, {}))
        timestamp = value.get("last_seen") or raw_fetched_at or price_times.get(value["id"]) or datetime.now(timezone.utc).isoformat()
        if not value.get("first_seen"):
            connection.execute("UPDATE offers SET first_seen=? WHERE id=?", (timestamp, value["id"]))
        if not value.get("last_seen"):
            connection.execute("UPDATE offers SET last_seen=? WHERE id=?", (timestamp, value["id"]))
        if value.get("offer_json") not in (None, "", "{}"):
            continue
        try:
            raw_data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
        except json.JSONDecodeError:
            raw_data = {}
        payload = {
            "source": value["source"], "source_product_id": value["source_product_id"], "url": value["url"],
            "fetched_at": timestamp, "artist_raw": value.get("artist_raw"), "title_raw": value.get("title_raw"),
            "edition_raw": None, "price": value.get("price"), "old_price": None, "currency": "RUB",
            "availability": value.get("availability") or "unknown", "stock_quantity": None, "stock_text": None,
            "city": None, "local_store": False, "pickup_available": False, "delivery_available": True,
            "condition_media": value.get("condition_media"), "condition_sleeve": None, "format": value.get("format"),
            "vinyl_size": value.get("vinyl_size"), "rpm": value.get("rpm"), "disc_count": value.get("disc_count"),
            "label": value.get("label"), "store_sku": value.get("store_sku"),
            "catalog_number_raw": value.get("catalog_number_raw"), "barcode": value.get("barcode"),
            "release_year": value.get("release_year"), "country": value.get("country"), "vinyl_color": value.get("vinyl_color"),
            "edition_tags": json.loads(value.get("edition_tags") or "[]"), "description": None,
            "image_url": None, "raw_data": raw_data,
        }
        connection.execute("UPDATE offers SET offer_json=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), value["id"]))


def migrate_v2(connection: sqlite3.Connection) -> None:
    additions = {
        "store_sku": "TEXT", "first_seen": "TEXT", "offer_json": "TEXT NOT NULL DEFAULT '{}'",
        "condition_sleeve": "TEXT", "vinyl_size": "TEXT", "release_id": "INTEGER REFERENCES releases(id)",
    }
    columns = _columns(connection, "offers")
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE offers ADD COLUMN {name} {definition}")
    _canonicalize_pairs(connection, "release_matches")
    _canonicalize_pairs(connection, "manual_match_decisions")
    _backfill_offer_json(connection)


def migrate_v3(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS watchlist (
        release_id INTEGER PRIMARY KEY REFERENCES releases(id) ON DELETE CASCADE,
        enabled INTEGER NOT NULL DEFAULT 1,
        max_price TEXT,
        min_deal_class TEXT,
        local_only INTEGER NOT NULL DEFAULT 0,
        city TEXT,
        pickup_only INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY,
        release_id INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
        offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        event_key TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        sent_at TEXT,
        send_error TEXT,
        UNIQUE(release_id, offer_id, event_type, event_key)
    );
    CREATE INDEX IF NOT EXISTS ix_alerts_unsent ON alerts(sent_at, id);
    CREATE INDEX IF NOT EXISTS ix_alerts_release ON alerts(release_id, created_at);
    """)


def migrate_v4(connection: sqlite3.Connection) -> None:
    if "delivery_claimed_at" not in _columns(connection, "alerts"):
        connection.execute("ALTER TABLE alerts ADD COLUMN delivery_claimed_at TEXT")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_alerts_delivery_claim ON alerts(sent_at, delivery_claimed_at, id)")


def migrate_v5(connection: sqlite3.Connection) -> None:
    """Keep Discogs evidence separate from store-to-store release matching."""
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS discogs_release_matches (
        id INTEGER PRIMARY KEY,
        release_id INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
        discogs_release_id INTEGER NOT NULL,
        discogs_master_id INTEGER,
        discogs_url TEXT NOT NULL,
        confidence TEXT NOT NULL,
        match_kind TEXT NOT NULL,
        status TEXT NOT NULL,
        matched_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        UNIQUE(release_id, discogs_release_id)
    );
    CREATE INDEX IF NOT EXISTS ix_discogs_matches_release ON discogs_release_matches(release_id, status);
    CREATE TABLE IF NOT EXISTS discogs_api_cache (
        cache_key TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    """)


def migrate(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > CURRENT_VERSION:
        raise RuntimeError(f"Unsupported database schema version: {version}")
    if version < 1:
        connection.executescript(SCHEMA_V1)
        connection.execute("PRAGMA user_version = 1")
        version = 1
    if version < 2:
        migrate_v2(connection)
        connection.execute("PRAGMA user_version = 2")
        version = 2
    if version < 3:
        migrate_v3(connection)
        connection.execute("PRAGMA user_version = 3")
        version = 3
    if version < 4:
        migrate_v4(connection)
        connection.execute("PRAGMA user_version = 4")
        version = 4
    if version < 5:
        migrate_v5(connection)
        connection.execute("PRAGMA user_version = 5")
