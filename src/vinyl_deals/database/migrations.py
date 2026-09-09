"""Small explicit SQLite migration registry for the MVP."""
from __future__ import annotations
import sqlite3

CURRENT_VERSION = 1

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

MIGRATIONS = {1: SCHEMA_V1}


def migrate(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > CURRENT_VERSION:
        raise RuntimeError(f"Unsupported database schema version: {version}")
    for target_version in range(version + 1, CURRENT_VERSION + 1):
        connection.executescript(MIGRATIONS[target_version])
        connection.execute(f"PRAGMA user_version = {target_version}")
