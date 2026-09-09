"""Small SQLite persistence boundary for the foundation milestone."""
from __future__ import annotations
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal
from vinyl_deals.domain import Availability, RawOffer
from vinyl_deals.database.migrations import CURRENT_VERSION, migrate
from vinyl_deals.matching.normalize import normalize_barcode, tags
from vinyl_deals.matching import match_offers, MatchKind


class ManualDecisionConflict(ValueError):
    """A proposed manual decision contradicts existing manual constraints."""


class ReleaseMergeConflict(ValueError):
    """Two release clusters cannot safely be merged."""


def canonical_pair(offer_id: int, candidate_offer_id: int) -> tuple[int, int]:
    if offer_id == candidate_offer_id:
        raise ValueError("A release-match pair requires two different offers.")
    return tuple(sorted((offer_id, candidate_offer_id)))

def _serialize_offer(offer: RawOffer) -> str:
    payload = asdict(offer)
    payload["fetched_at"] = offer.fetched_at.isoformat()
    payload["price"] = str(offer.price) if offer.price is not None else None
    payload["old_price"] = str(offer.old_price) if offer.old_price is not None else None
    payload["availability"] = offer.availability.value
    return json.dumps(payload, ensure_ascii=False)

def _deserialize_offer(payload: str) -> RawOffer:
    data = json.loads(payload)
    data["fetched_at"] = datetime.fromisoformat(data["fetched_at"])
    data["price"] = Decimal(data["price"]) if data["price"] is not None else None
    data["old_price"] = Decimal(data["old_price"]) if data["old_price"] is not None else None
    data["availability"] = Availability(data["availability"])
    data["edition_tags"] = tuple(data["edition_tags"])
    return RawOffer(**data)

SCHEMA_VERSION = CURRENT_VERSION

class SQLiteRepository:
    def __init__(self, path: Path | str = "vinyl_deals.sqlite3") -> None: self.path = Path(path)
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    def initialize(self) -> None:
        with self._connect() as connection:
            migrate(connection)

    def schema_version(self) -> int:
        self.initialize()
        with self._connect() as connection:
            return connection.execute("PRAGMA user_version").fetchone()[0]

    def start_scrape_run(self, store: str) -> int:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute("INSERT INTO scrape_runs(store, started_at, status) VALUES (?, ?, 'running')", (store, datetime.now(timezone.utc).isoformat()))
            return cursor.lastrowid

    def finish_scrape_run(self, run_id: int, status: str, pages_processed: int, offers_found: int, warnings: tuple[str, ...] = (), errors: tuple[str, ...] = ()) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE scrape_runs SET finished_at=?, status=?, pages_processed=?, offers_found=?, warnings=?, errors=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), status, pages_processed, offers_found, "\n".join(warnings), "\n".join(errors), run_id))

    def latest_scrape_runs(self) -> list[tuple[str, str, str]]:
        self.initialize()
        with self._connect() as connection:
            return connection.execute("SELECT store, status, finished_at FROM scrape_runs ORDER BY id DESC LIMIT 10").fetchall()

    def integrity_diagnostics(self) -> list[str]:
        self.initialize()
        diagnostics: list[str] = []
        with self._connect() as connection:
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                diagnostics.append("foreign keys are disabled")
            empty = connection.execute("SELECT COUNT(*) FROM releases WHERE NOT EXISTS (SELECT 1 FROM offers WHERE offers.release_id=releases.id)").fetchone()[0]
            if empty:
                diagnostics.append(f"{empty} empty releases")
            dangling = connection.execute("SELECT COUNT(*) FROM offers LEFT JOIN releases ON offers.release_id=releases.id WHERE offers.release_id IS NOT NULL AND releases.id IS NULL").fetchone()[0]
            if dangling:
                diagnostics.append(f"{dangling} offers point to missing releases")
            differences = connection.execute("SELECT offer_id, candidate_offer_id FROM manual_match_decisions WHERE decision='different_release'").fetchall()
            for left, right in differences:
                if self._manual_same_component(connection, left) & self._manual_same_component(connection, right):
                    diagnostics.append(f"contradictory manual decisions between offers {left} and {right}")
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            stale = connection.execute("SELECT COUNT(*) FROM scrape_runs WHERE status='running' AND started_at < ?", (cutoff,)).fetchone()[0]
            if stale:
                diagnostics.append(f"{stale} stale running scrape runs")
            for release_id, in connection.execute("SELECT id FROM releases"):
                ids = self._release_offer_ids(connection, release_id)
                if conflict := self.find_cluster_conflict(connection, ids, ids):
                    diagnostics.append(f"Release {release_id} contains blocking pair: {conflict}")
            rows = connection.execute("SELECT offer_id, candidate_offer_id FROM manual_match_decisions WHERE decision='same_release'").fetchall()
            for left, right in rows:
                assigned = connection.execute("SELECT release_id FROM offers WHERE id IN (?, ?)", (left, right)).fetchall()
                if len(assigned) == 2 and assigned[0][0] != assigned[1][0]:
                    diagnostics.append(f"manual SAME_RELEASE {left}/{right} spans different releases")
            mismatches = connection.execute("SELECT id, offer_id, candidate_offer_id, release_id FROM release_matches WHERE release_id IS NOT NULL").fetchall()
            for match_id, left, right, release_id in mismatches:
                releases = [row[0] for row in connection.execute("SELECT release_id FROM offers WHERE id IN (?, ?)", (left, right))]
                if len(releases) != 2 or releases[0] != release_id or releases[1] != release_id:
                    diagnostics.append(f"release_match {match_id} points to Release {release_id} but its offers do not both belong there")
        return diagnostics
    def upsert_offer(self, offer: RawOffer) -> None:
        self.initialize(); occurred = offer.fetched_at.isoformat()
        with self._connect() as connection:
            connection.execute("INSERT INTO raw_products(source, source_product_id, fetched_at, payload_json) VALUES (?, ?, ?, ?) ON CONFLICT(source, source_product_id) DO UPDATE SET fetched_at=excluded.fetched_at, payload_json=excluded.payload_json", (offer.source, offer.source_product_id, occurred, json.dumps(offer.raw_data, ensure_ascii=False)))
            connection.execute("INSERT INTO offers(source, source_product_id, url, artist_raw, title_raw, barcode, catalog_number_raw, label, store_sku, country, release_year, format, disc_count, rpm, vinyl_color, edition_tags, condition_media, price, availability, first_seen, last_seen, offer_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source, source_product_id) DO UPDATE SET url=excluded.url, artist_raw=excluded.artist_raw, title_raw=excluded.title_raw, barcode=excluded.barcode, catalog_number_raw=excluded.catalog_number_raw, label=excluded.label, store_sku=excluded.store_sku, country=excluded.country, release_year=excluded.release_year, format=excluded.format, disc_count=excluded.disc_count, rpm=excluded.rpm, vinyl_color=excluded.vinyl_color, edition_tags=excluded.edition_tags, condition_media=excluded.condition_media, price=excluded.price, availability=excluded.availability, last_seen=excluded.last_seen, offer_json=excluded.offer_json", (offer.source, offer.source_product_id, offer.url, offer.artist_raw, offer.title_raw, offer.barcode, offer.catalog_number_raw, offer.label, offer.store_sku, offer.country, offer.release_year, offer.format, offer.disc_count, offer.rpm, offer.vinyl_color, json.dumps(offer.edition_tags, ensure_ascii=False), offer.condition_media, str(offer.price) if offer.price is not None else None, offer.availability, occurred, occurred, _serialize_offer(offer)))
            offer_id = connection.execute("SELECT id FROM offers WHERE source=? AND source_product_id=?", (offer.source, offer.source_product_id)).fetchone()[0]
            prior = connection.execute("SELECT 1 FROM price_history WHERE offer_id=? AND observed_at=?", (offer_id, occurred)).fetchone()
            if not prior: connection.execute("INSERT INTO price_history(offer_id, observed_at, price, old_price, availability) VALUES (?, ?, ?, ?, ?)", (offer_id, occurred, str(offer.price) if offer.price is not None else None, str(offer.old_price) if offer.old_price is not None else None, offer.availability))

    def record_match(self, offer_id: int, candidate_offer_id: int, kind: str, confidence: float, reasons: tuple[str, ...]) -> None:
        self.initialize()
        offer_id, candidate_offer_id = canonical_pair(offer_id, candidate_offer_id)
        with self._connect() as connection:
            connection.execute("INSERT INTO release_matches(offer_id, candidate_offer_id, kind, confidence, reasons, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(offer_id, candidate_offer_id) DO UPDATE SET kind=excluded.kind, confidence=excluded.confidence, reasons=excluded.reasons", (offer_id, candidate_offer_id, kind, confidence, json.dumps(reasons, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))

    def decide_match(self, offer_id: int, candidate_offer_id: int, decision: str, note: str | None = None) -> None:
        self.initialize()
        offer_id, candidate_offer_id = canonical_pair(offer_id, candidate_offer_id)
        with self._connect() as connection:
            self._validate_manual_decision(connection, offer_id, candidate_offer_id, decision)
            offers = self._offers_by_id(connection)
            if decision == "same_release":
                self.create_release_for_pair(offer_id, candidate_offer_id, offers[offer_id], offers[candidate_offer_id], connection)
            connection.execute("INSERT INTO manual_match_decisions(offer_id, candidate_offer_id, decision, note, decided_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(offer_id, candidate_offer_id) DO UPDATE SET decision=excluded.decision, note=excluded.note, decided_at=excluded.decided_at", (offer_id, candidate_offer_id, decision, note, datetime.now(timezone.utc).isoformat()))
            status = "confirmed_same" if decision == "same_release" else "confirmed_different" if decision == "different_release" else "ignored"
            connection.execute("UPDATE release_matches SET status=? WHERE offer_id=? AND candidate_offer_id=?", (status, offer_id, candidate_offer_id))
            release_ids = connection.execute("SELECT release_id FROM offers WHERE id IN (?, ?)", (offer_id, candidate_offer_id)).fetchall()
            shared_release = len(release_ids) == 2 and release_ids[0][0] and release_ids[0][0] == release_ids[1][0]
            if decision == "different_release" and shared_release:
                self.rebuild_release_cluster(release_ids[0][0], connection)

    def manual_decision(self, offer_id: int, candidate_offer_id: int) -> str | None:
        self.initialize()
        offer_id, candidate_offer_id = canonical_pair(offer_id, candidate_offer_id)
        with self._connect() as connection:
            row = connection.execute("SELECT decision FROM manual_match_decisions WHERE offer_id=? AND candidate_offer_id=?", (offer_id, candidate_offer_id)).fetchone()
            return row[0] if row else None

    def possible_matches(self) -> list[tuple[int, int, float, str]]:
        self.initialize()
        with self._connect() as connection:
            return connection.execute("SELECT offer_id, candidate_offer_id, confidence, reasons FROM release_matches WHERE kind='possible' AND status='pending' ORDER BY confidence DESC").fetchall()

    def offers_for_matching(self) -> list[tuple[int, RawOffer]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute("SELECT id, offer_json FROM offers").fetchall()
        return [(row[0], _deserialize_offer(row[1])) for row in rows]

    def create_release_for_pair(self, offer_id: int, candidate_offer_id: int, offer: RawOffer, candidate: RawOffer, connection: sqlite3.Connection | None = None) -> int:
        self.initialize(); now = datetime.now(timezone.utc).isoformat()
        offer_id, candidate_offer_id = canonical_pair(offer_id, candidate_offer_id)
        def pick(name: str): return getattr(offer, name) or getattr(candidate, name)
        owns_connection = connection is None
        if connection is None: connection = self._connect()
        completed = False
        try:
            releases = connection.execute("SELECT id, release_id FROM offers WHERE id IN (?, ?)", (offer_id, candidate_offer_id)).fetchall()
            release_by_offer = dict(releases); left_release, right_release = release_by_offer[offer_id], release_by_offer[candidate_offer_id]
            if left_release and right_release and left_release != right_release:
                release_id = self.merge_releases(left_release, right_release, connection)
            elif left_release or right_release:
                release_id = left_release or right_release
                other_id = candidate_offer_id if left_release else offer_id
                conflict = self.find_cluster_conflict(connection, {other_id}, self._release_offer_ids(connection, release_id))
                if conflict:
                    raise ReleaseMergeConflict(f"Cannot join Release {release_id}: {conflict}")
            else:
                cursor = connection.execute("INSERT INTO releases(artist,title,barcode,label,catalog_number,release_year,country,format,disc_count,vinyl_size,rpm,vinyl_color,edition_tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (pick("artist_raw"), pick("title_raw"), pick("barcode"), pick("label"), pick("catalog_number_raw"), pick("release_year"), pick("country"), pick("format"), pick("disc_count"), pick("vinyl_size"), pick("rpm"), pick("vinyl_color"), json.dumps(pick("edition_tags"), ensure_ascii=False), now, now))
                release_id = cursor.lastrowid
            connection.execute("UPDATE offers SET release_id=? WHERE id IN (?,?)", (release_id, offer_id, candidate_offer_id))
            connection.execute("UPDATE release_matches SET release_id=?, status=CASE WHEN status='confirmed_same' THEN status ELSE 'auto_matched' END WHERE offer_id=? AND candidate_offer_id=?", (release_id, offer_id, candidate_offer_id))
            self._refresh_release_metadata(connection, release_id)
            completed = True
            return release_id
        finally:
            if owns_connection:
                (connection.commit if completed else connection.rollback)()
                connection.close()

    def merge_releases(self, canonical_id: int, redundant_id: int, connection: sqlite3.Connection | None = None) -> int:
        if canonical_id == redundant_id: return canonical_id
        self.initialize() if connection is None else None
        owns_connection = connection is None
        if connection is None: connection = self._connect()
        try:
            self._validate_release_merge(connection, canonical_id, redundant_id)
            connection.execute("UPDATE offers SET release_id=? WHERE release_id=?", (canonical_id, redundant_id))
            connection.execute("UPDATE release_matches SET release_id=? WHERE release_id=?", (canonical_id, redundant_id))
            connection.execute("DELETE FROM releases WHERE id=?", (redundant_id,))
            self._refresh_release_metadata(connection, canonical_id)
            if owns_connection: connection.commit()
            return canonical_id
        finally:
            if owns_connection:
                connection.commit()
                connection.close()

    def _manual_same_component(self, connection: sqlite3.Connection, offer_id: int) -> set[int]:
        component, frontier = {offer_id}, [offer_id]
        while frontier:
            current = frontier.pop()
            rows = connection.execute(
                "SELECT offer_id, candidate_offer_id FROM manual_match_decisions "
                "WHERE decision='same_release' AND (offer_id=? OR candidate_offer_id=?)", (current, current)
            ).fetchall()
            for left, right in rows:
                other = right if left == current else left
                if other not in component:
                    component.add(other); frontier.append(other)
        return component

    @staticmethod
    def _manual_different_between(connection: sqlite3.Connection, left_ids: set[int], right_ids: set[int]) -> tuple[int, int] | None:
        for left in left_ids:
            for right in right_ids:
                if left == right:
                    continue
                first, second = canonical_pair(left, right)
                row = connection.execute(
                    "SELECT 1 FROM manual_match_decisions WHERE offer_id=? AND candidate_offer_id=? AND decision='different_release'",
                    (first, second),
                ).fetchone()
                if row:
                    return first, second
        return None

    @staticmethod
    def _release_offer_ids(connection: sqlite3.Connection, release_id: int) -> set[int]:
        return {row[0] for row in connection.execute("SELECT id FROM offers WHERE release_id=?", (release_id,))}

    @staticmethod
    def _offers_by_id(connection: sqlite3.Connection) -> dict[int, RawOffer]:
        return {offer_id: _deserialize_offer(payload) for offer_id, payload in connection.execute("SELECT id, offer_json FROM offers")}

    def find_cluster_conflict(self, connection: sqlite3.Connection, left_ids: set[int], right_ids: set[int]) -> str | None:
        manual = self._manual_different_between(connection, left_ids, right_ids)
        if manual:
            return f"manual DIFFERENT_RELEASE exists between offers {manual[0]} and {manual[1]}"
        offers = self._offers_by_id(connection)
        for left in left_ids:
            for right in right_ids:
                if left == right:
                    continue
                result = match_offers(offers[left], offers[right])
                if result.kind == MatchKind.DIFFERENT:
                    reason = result.blocking_conflicts[0] if result.blocking_conflicts else "blocking conflict"
                    return f"offers {left}/{right}: {reason}"
        return None

    def _validate_manual_decision(self, connection: sqlite3.Connection, offer_id: int, candidate_offer_id: int, decision: str) -> None:
        if decision not in {"same_release", "different_release", "ignore"}:
            raise ValueError(f"Unknown manual decision: {decision}")
        left = self._manual_same_component(connection, offer_id)
        right = self._manual_same_component(connection, candidate_offer_id)
        if decision == "different_release" and left & right:
            raise ManualDecisionConflict("Cannot mark DIFFERENT_RELEASE: offers are connected by manual SAME_RELEASE decisions.")
        if decision == "same_release":
            conflict = self._manual_different_between(connection, left, right)
            if conflict:
                raise ManualDecisionConflict(
                    f"Cannot mark SAME_RELEASE: manual DIFFERENT_RELEASE exists between offers {conflict[0]} and {conflict[1]}."
                )

    def _validate_release_merge(self, connection: sqlite3.Connection, canonical_id: int, redundant_id: int) -> None:
        left = self._release_offer_ids(connection, canonical_id)
        right = self._release_offer_ids(connection, redundant_id)
        conflict = self.find_cluster_conflict(connection, left, right)
        if conflict:
            raise ReleaseMergeConflict(f"Cannot merge Release {canonical_id} and {redundant_id}: {conflict}")
        left_data = connection.execute("SELECT * FROM releases WHERE id=?", (canonical_id,)).fetchone()
        right_data = connection.execute("SELECT * FROM releases WHERE id=?", (redundant_id,)).fetchone()
        columns = [item[0] for item in connection.execute("SELECT * FROM releases LIMIT 0").description]
        first, second = dict(zip(columns, left_data, strict=True)), dict(zip(columns, right_data, strict=True))
        for field in ("disc_count", "format", "rpm"):
            if first[field] is not None and second[field] is not None and first[field] != second[field]:
                raise ReleaseMergeConflict(f"Cannot merge releases: critical metadata {field} differs.")
        first_barcode, second_barcode = normalize_barcode(first["barcode"]), normalize_barcode(second["barcode"])
        if first_barcode and second_barcode and first_barcode != second_barcode:
            raise ReleaseMergeConflict("Cannot merge releases: valid barcodes differ.")

    def _merge_release_metadata(self, connection: sqlite3.Connection, canonical_id: int, redundant_id: int) -> None:
        cursor = connection.execute("SELECT * FROM releases WHERE id IN (?, ?) ORDER BY id", (canonical_id, redundant_id))
        columns = [item[0] for item in cursor.description]
        values = {row[0]: dict(zip(columns, row, strict=True)) for row in cursor.fetchall()}
        canonical, redundant = values[canonical_id], values[redundant_id]
        update = {}
        for field in ("barcode", "label", "catalog_number", "release_year", "country", "format", "disc_count", "vinyl_size", "rpm", "vinyl_color"):
            if canonical[field] is None and redundant[field] is not None:
                update[field] = redundant[field]
        canonical_tags = tuple(json.loads(canonical["edition_tags"] or "[]"))
        redundant_tags = tuple(json.loads(redundant["edition_tags"] or "[]"))
        merged_tags = tags(canonical_tags) | tags(redundant_tags)
        incompatible = ({"mono", "stereo"}, {"picture_disc", "black"}, {"colored", "black"}, {"box_set", "single"})
        if not any(len(merged_tags & group) > 1 for group in incompatible):
            update["edition_tags"] = json.dumps(sorted(merged_tags), ensure_ascii=False)
        if update:
            update["updated_at"] = datetime.now(timezone.utc).isoformat()
            assignments = ", ".join(f"{field}=?" for field in update)
            connection.execute(f"UPDATE releases SET {assignments} WHERE id=?", (*update.values(), canonical_id))

    def rebuild_release_cluster(self, release_id: int, connection: sqlite3.Connection | None = None) -> None:
        self.initialize() if connection is None else None
        owns_connection = connection is None
        if connection is None: connection = self._connect()
        try:
            offer_ids = [row[0] for row in connection.execute("SELECT id FROM offers WHERE release_id=?", (release_id,))]
            if not offer_ids:
                self.cleanup_empty_releases(connection)
                return
            parent = {offer_id: offer_id for offer_id in offer_ids}
            def find(item: int) -> int:
                while parent[item] != item:
                    parent[item] = parent[parent[item]]; item = parent[item]
                return item
            def union(left: int, right: int) -> None:
                left_root, right_root = find(left), find(right)
                if left_root == right_root:
                    return
                left_group = {item for item in offer_ids if find(item) == left_root}
                right_group = {item for item in offer_ids if find(item) == right_root}
                if not self.find_cluster_conflict(connection, left_group, right_group):
                    parent[right_root] = left_root
            placeholders = ",".join("?" for _ in offer_ids)
            rows = connection.execute(
                f"SELECT offer_id, candidate_offer_id, decision FROM manual_match_decisions WHERE offer_id IN ({placeholders}) AND candidate_offer_id IN ({placeholders})",
                (*offer_ids, *offer_ids),
            ).fetchall()
            for left, right, decision in rows:
                if decision == "same_release": union(left, right)
            strong = connection.execute(
                f"SELECT offer_id, candidate_offer_id FROM release_matches WHERE offer_id IN ({placeholders}) AND candidate_offer_id IN ({placeholders}) AND kind IN ('exact_barcode','catalog_and_label','weighted')",
                (*offer_ids, *offer_ids),
            ).fetchall()
            for left, right in strong: union(left, right)
            components: dict[int, list[int]] = {}
            for offer_id in offer_ids: components.setdefault(find(offer_id), []).append(offer_id)
            for index, ids in enumerate(components.values()):
                target = release_id if index == 0 else self._create_release_from_offer(connection, ids[0])
                marks = ",".join("?" for _ in ids)
                connection.execute(f"UPDATE offers SET release_id=? WHERE id IN ({marks})", (target, *ids))
                self._refresh_release_metadata(connection, target)
            connection.execute("UPDATE release_matches SET release_id=NULL, status=CASE WHEN status='auto_matched' THEN 'pending' ELSE status END WHERE offer_id IN ({0}) AND candidate_offer_id IN ({0})".format(placeholders), (*offer_ids, *offer_ids))
            for ids in components.values():
                release = connection.execute("SELECT release_id FROM offers WHERE id=?", (ids[0],)).fetchone()[0]
                marks = ",".join("?" for _ in ids)
                connection.execute(f"UPDATE release_matches SET release_id=? WHERE offer_id IN ({marks}) AND candidate_offer_id IN ({marks})", (release, *ids, *ids))
            self.cleanup_empty_releases(connection)
        finally:
            if owns_connection: connection.close()

    def _create_release_from_offer(self, connection: sqlite3.Connection, offer_id: int) -> int:
        payload = connection.execute("SELECT offer_json FROM offers WHERE id=?", (offer_id,)).fetchone()[0]
        offer = _deserialize_offer(payload)
        now = datetime.now(timezone.utc).isoformat()
        cursor = connection.execute("INSERT INTO releases(artist,title,barcode,label,catalog_number,release_year,country,format,disc_count,vinyl_size,rpm,vinyl_color,edition_tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (offer.artist_raw or "", offer.title_raw or "", offer.barcode, offer.label, offer.catalog_number_raw, offer.release_year, offer.country, offer.format, offer.disc_count, offer.vinyl_size, offer.rpm, offer.vinyl_color, json.dumps(offer.edition_tags), now, now))
        return cursor.lastrowid

    @staticmethod
    def merge_raw_edition_tags(offers: list[RawOffer]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(tag for offer in offers for tag in offer.edition_tags if tag))

    def aggregate_release_metadata(self, offers: list[RawOffer]) -> dict[str, object]:
        fields = {"barcode": "barcode", "label": "label", "catalog_number": "catalog_number_raw", "release_year": "release_year", "country": "country", "format": "format", "disc_count": "disc_count", "vinyl_size": "vinyl_size", "rpm": "rpm", "vinyl_color": "vinyl_color"}
        metadata: dict[str, object] = {"edition_tags": json.dumps(self.merge_raw_edition_tags(offers), ensure_ascii=False)}
        for output, attribute in fields.items():
            values = {getattr(offer, attribute) for offer in offers if getattr(offer, attribute) is not None}
            metadata[output] = next(iter(values)) if len(values) == 1 else None
        return metadata

    def _refresh_release_metadata(self, connection: sqlite3.Connection, release_id: int) -> None:
        offers = [offer for offer_id, offer in self._offers_by_id(connection).items() if offer_id in self._release_offer_ids(connection, release_id)]
        if not offers:
            return
        metadata = self.aggregate_release_metadata(offers)
        assignments = ", ".join(f"{field}=?" for field in metadata)
        connection.execute(f"UPDATE releases SET {assignments}, updated_at=? WHERE id=?", (*metadata.values(), datetime.now(timezone.utc).isoformat(), release_id))

    def validate_release_invariants(self, release_id: int) -> list[str]:
        self.initialize()
        with self._connect() as connection:
            ids = self._release_offer_ids(connection, release_id)
            if not ids:
                return ["release is empty"]
            return [conflict] if (conflict := self.find_cluster_conflict(connection, ids, ids)) else []

    def cleanup_empty_releases(self, connection: sqlite3.Connection | None = None) -> int:
        owns_connection = connection is None
        if connection is None:
            self.initialize(); connection = self._connect()
        try:
            cursor = connection.execute("DELETE FROM releases WHERE NOT EXISTS (SELECT 1 FROM offers WHERE offers.release_id=releases.id)")
            if owns_connection: connection.commit()
            return cursor.rowcount
        finally:
            if owns_connection: connection.close()
