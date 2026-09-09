"""Small SQLite persistence boundary for the foundation milestone."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal
from vinyl_deals.domain import Availability, RawOffer

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_products (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_product_id TEXT NOT NULL, fetched_at TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(source, source_product_id));
CREATE INDEX IF NOT EXISTS ix_raw_products_source_product ON raw_products(source, source_product_id);
CREATE INDEX IF NOT EXISTS ix_raw_products_fetched_at ON raw_products(fetched_at);
CREATE TABLE IF NOT EXISTS offers (id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_product_id TEXT NOT NULL, url TEXT NOT NULL, release_id INTEGER REFERENCES releases(id), artist_raw TEXT, title_raw TEXT, barcode TEXT, catalog_number_raw TEXT, label TEXT, country TEXT, release_year INTEGER, format TEXT, disc_count INTEGER, rpm INTEGER, vinyl_color TEXT, edition_tags TEXT NOT NULL DEFAULT '[]', condition_media TEXT, price TEXT, availability TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE(source, source_product_id));
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

class SQLiteRepository:
    def __init__(self, path: Path | str = "vinyl_deals.sqlite3") -> None: self.path = Path(path)
    def initialize(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.executescript(SCHEMA)
            existing = {row[1] for row in connection.execute("PRAGMA table_info(offers)")}
            for column, definition in {"release_id": "INTEGER REFERENCES releases(id)", "label": "TEXT", "country": "TEXT", "release_year": "INTEGER", "format": "TEXT", "disc_count": "INTEGER", "rpm": "INTEGER", "vinyl_color": "TEXT", "edition_tags": "TEXT NOT NULL DEFAULT '[]'", "condition_media": "TEXT"}.items():
                if column not in existing: connection.execute(f"ALTER TABLE offers ADD COLUMN {column} {definition}")
    def upsert_offer(self, offer: RawOffer) -> None:
        self.initialize(); occurred = offer.fetched_at.isoformat()
        with sqlite3.connect(self.path) as connection:
            connection.execute("INSERT INTO raw_products(source, source_product_id, fetched_at, payload_json) VALUES (?, ?, ?, ?) ON CONFLICT(source, source_product_id) DO UPDATE SET fetched_at=excluded.fetched_at, payload_json=excluded.payload_json", (offer.source, offer.source_product_id, occurred, json.dumps(offer.raw_data, ensure_ascii=False)))
            connection.execute("INSERT INTO offers(source, source_product_id, url, artist_raw, title_raw, barcode, catalog_number_raw, label, country, release_year, format, disc_count, rpm, vinyl_color, edition_tags, condition_media, price, availability, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source, source_product_id) DO UPDATE SET url=excluded.url, artist_raw=excluded.artist_raw, title_raw=excluded.title_raw, barcode=excluded.barcode, catalog_number_raw=excluded.catalog_number_raw, label=excluded.label, country=excluded.country, release_year=excluded.release_year, format=excluded.format, disc_count=excluded.disc_count, rpm=excluded.rpm, vinyl_color=excluded.vinyl_color, edition_tags=excluded.edition_tags, condition_media=excluded.condition_media, price=excluded.price, availability=excluded.availability, last_seen=excluded.last_seen", (offer.source, offer.source_product_id, offer.url, offer.artist_raw, offer.title_raw, offer.barcode, offer.catalog_number_raw, offer.label, offer.country, offer.release_year, offer.format, offer.disc_count, offer.rpm, offer.vinyl_color, json.dumps(offer.edition_tags, ensure_ascii=False), offer.condition_media, str(offer.price) if offer.price is not None else None, offer.availability, occurred))
            offer_id = connection.execute("SELECT id FROM offers WHERE source=? AND source_product_id=?", (offer.source, offer.source_product_id)).fetchone()[0]
            connection.execute("INSERT INTO price_history(offer_id, observed_at, price, old_price, availability) VALUES (?, ?, ?, ?, ?)", (offer_id, occurred, str(offer.price) if offer.price is not None else None, str(offer.old_price) if offer.old_price is not None else None, offer.availability))

    def record_match(self, offer_id: int, candidate_offer_id: int, kind: str, confidence: float, reasons: tuple[str, ...]) -> None:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            connection.execute("INSERT INTO release_matches(offer_id, candidate_offer_id, kind, confidence, reasons, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(offer_id, candidate_offer_id) DO UPDATE SET kind=excluded.kind, confidence=excluded.confidence, reasons=excluded.reasons", (offer_id, candidate_offer_id, kind, confidence, json.dumps(reasons, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))

    def decide_match(self, offer_id: int, candidate_offer_id: int, decision: str, note: str | None = None) -> None:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            connection.execute("INSERT INTO manual_match_decisions(offer_id, candidate_offer_id, decision, note, decided_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(offer_id, candidate_offer_id) DO UPDATE SET decision=excluded.decision, note=excluded.note, decided_at=excluded.decided_at", (offer_id, candidate_offer_id, decision, note, datetime.now(timezone.utc).isoformat()))
            status = "confirmed_same" if decision == "same_release" else "confirmed_different" if decision == "different_release" else "ignored"
            connection.execute("UPDATE release_matches SET status=? WHERE offer_id=? AND candidate_offer_id=?", (status, offer_id, candidate_offer_id))

    def possible_matches(self) -> list[tuple[int, int, float, str]]:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            return connection.execute("SELECT offer_id, candidate_offer_id, confidence, reasons FROM release_matches WHERE kind='possible' AND status='pending' ORDER BY confidence DESC").fetchall()

    def offers_for_matching(self) -> list[tuple[int, RawOffer]]:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute("SELECT id, source, source_product_id, url, artist_raw, title_raw, barcode, catalog_number_raw, label, country, release_year, format, disc_count, rpm, vinyl_color, edition_tags, condition_media, price, availability, last_seen FROM offers").fetchall()
        return [(row[0], RawOffer(source=row[1], source_product_id=row[2], url=row[3], artist_raw=row[4], title_raw=row[5], barcode=row[6], catalog_number_raw=row[7], label=row[8], country=row[9], release_year=row[10], format=row[11], disc_count=row[12], rpm=row[13], vinyl_color=row[14], edition_tags=tuple(json.loads(row[15])), condition_media=row[16], price=Decimal(row[17]) if row[17] else None, availability=Availability(row[18]), fetched_at=datetime.fromisoformat(row[19]))) for row in rows]

    def create_release_for_pair(self, offer_id: int, candidate_offer_id: int, offer: RawOffer, candidate: RawOffer) -> int:
        self.initialize(); now = datetime.now(timezone.utc).isoformat()
        def pick(name: str): return getattr(offer, name) or getattr(candidate, name)
        with sqlite3.connect(self.path) as connection:
            existing = connection.execute("SELECT release_id FROM release_matches WHERE offer_id=? AND candidate_offer_id=?", (offer_id, candidate_offer_id)).fetchone()
            if existing and existing[0]:
                return existing[0]
            cursor = connection.execute("INSERT INTO releases(artist,title,barcode,label,catalog_number,release_year,country,format,disc_count,vinyl_size,rpm,vinyl_color,edition_tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (pick("artist_raw"), pick("title_raw"), pick("barcode"), pick("label"), pick("catalog_number_raw"), pick("release_year"), pick("country"), pick("format"), pick("disc_count"), pick("vinyl_size"), pick("rpm"), pick("vinyl_color"), json.dumps(pick("edition_tags"), ensure_ascii=False), now, now))
            release_id = cursor.lastrowid
            connection.execute("UPDATE offers SET release_id=? WHERE id IN (?,?)", (release_id, offer_id, candidate_offer_id))
            connection.execute("UPDATE release_matches SET release_id=?, status='auto_matched' WHERE offer_id=? AND candidate_offer_id=?", (release_id, offer_id, candidate_offer_id))
            return release_id
