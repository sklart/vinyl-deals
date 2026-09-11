"""Official Discogs API integration with conservative pressing identification.

The service deliberately treats a Discogs *master* as context, never as proof
that a particular physical pressing is the same release.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import Release
from vinyl_deals.matching.normalize import barcode as digits, catalog_number, text


class DiscogsConfidence(StrEnum):
    EXACT = "EXACT"
    HIGH = "HIGH"
    POSSIBLE = "POSSIBLE"
    DIFFERENT = "DIFFERENT"


@dataclass(frozen=True, slots=True)
class DiscogsCandidate:
    release_id: int
    master_id: int | None
    url: str
    confidence: DiscogsConfidence
    match_kind: str
    metadata: dict[str, object]


class DiscogsApiClient:
    """Tiny official API client; tests inject ``fetch`` and never use network."""

    base_url = "https://api.discogs.com"

    def __init__(self, *, token: str | None = None, timeout: float = 10, max_retries: int = 2,
                 fetch: Callable[[str, dict[str, str], float], dict[str, object]] | None = None) -> None:
        self.token = token if token is not None else os.getenv("DISCOGS_TOKEN")
        self.timeout, self.max_retries = timeout, max_retries
        self._fetch = fetch or self._http_fetch

    @property
    def available(self) -> bool:
        return bool(self.token)

    def _http_fetch(self, url: str, headers: dict[str, str], timeout: float) -> dict[str, object]:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 -- official API endpoint
            return json.loads(response.read().decode("utf-8"))

    def get(self, path: str, params: dict[str, object] | None = None) -> dict[str, object]:
        if not self.available:
            raise PermissionError("Discogs token is not configured")
        query = urlencode({key: value for key, value in (params or {}).items() if value not in (None, "")})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        headers = {"User-Agent": "VinylDeals/0.1 (+https://github.com/sklart/vinyl-deals)", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Discogs token={self.token}"
        for attempt in range(self.max_retries + 1):
            try:
                return self._fetch(url, headers, self.timeout)
            except HTTPError as exc:
                if exc.code != 429 or attempt >= self.max_retries:
                    raise
                time.sleep(min(2 ** attempt, 4))
            except URLError:
                raise
        raise RuntimeError("Discogs request retry loop exhausted")


def _first(values: object) -> str | None:
    if isinstance(values, list) and values:
        value = values[0]
        return str(value.get("name") or value.get("title") or "").strip() if isinstance(value, dict) else str(value).strip()
    return str(values).strip() if values else None


def _metadata(payload: dict[str, object]) -> dict[str, object]:
    labels = payload.get("labels") if isinstance(payload.get("labels"), list) else []
    label = _first(labels)
    catalog = next((str(item.get("catno")).strip() for item in labels if isinstance(item, dict) and item.get("catno") and str(item.get("catno")).strip().casefold() not in {"none", "n/a"}), None)
    identifiers = payload.get("identifiers") if isinstance(payload.get("identifiers"), list) else []
    barcode = next((digits(str(item.get("value"))) for item in identifiers if isinstance(item, dict) and "barcode" in str(item.get("type", "")).casefold() and digits(str(item.get("value")))), None)
    formats = payload.get("formats") if isinstance(payload.get("formats"), list) else []
    descriptions: list[str] = []
    quantity = 0
    for item in formats:
        if isinstance(item, dict):
            descriptions.extend(str(value) for value in item.get("descriptions", []) if value)
            try: quantity += int(item.get("qty") or 0)
            except (TypeError, ValueError): pass
    descriptor = ", ".join(descriptions)
    return {
        "barcode": barcode,
        "label": label,
        "catalog_number": catalog,
        "release_year": int(str(payload["year"])) if str(payload.get("year", "")).isdigit() else None,
        "country": payload.get("country") or None,
        "format": ", ".join(part for part in (_first(formats), *descriptions) if part) or None,
        "disc_count": quantity or None,
        "vinyl_size": next((value for value in descriptions if value in {'7\"', '10\"', '12\"'}), None),
        "rpm": next((int(value.split()[0]) for value in descriptions if value.casefold().endswith("rpm") and value.split()[0].isdigit()), None),
        "vinyl_color": next((value for value in descriptions if "color" in value.casefold() or "colour" in value.casefold()), None),
        "edition_tags": descriptions,
        "artist": _first(payload.get("artists")),
        "title": payload.get("title") or None,
    }


def _classify(release: Release, metadata: dict[str, object]) -> tuple[DiscogsConfidence, str]:
    remote_artist, remote_title = text(str(metadata.get("artist") or "")), text(str(metadata.get("title") or ""))
    if remote_artist and remote_artist != text(release.artist) or remote_title and remote_title != text(release.title):
        return DiscogsConfidence.DIFFERENT, "artist_title_conflict"
    def conflict(local: object, remote: object, *, contains: bool = False) -> bool:
        if local in (None, "", (), []) or remote in (None, "", (), []):
            return False
        left, right = text(str(local)), text(str(remote))
        return left not in right and right not in left if contains else left != right
    physical_conflicts = (
        conflict(release.format, metadata.get("format"), contains=True),
        conflict(release.disc_count, metadata.get("disc_count")),
        conflict(release.vinyl_size, metadata.get("vinyl_size")),
        conflict(release.rpm, metadata.get("rpm")),
        conflict(release.vinyl_color, metadata.get("vinyl_color")),
    )
    if any(physical_conflicts):
        return DiscogsConfidence.DIFFERENT, "physical_conflict"
    local_tags = {text(tag).replace(" ", "_") for tag in release.edition_tags}
    remote_tags = {text(str(tag)).replace(" ", "_") for tag in metadata.get("edition_tags", [])}
    tag_pairs = (("mono", "stereo"), ("colored", "black"), ("colour", "black"), ("picture_disc", "normal"), ("box_set", "single"))
    if any(left in local_tags and right in remote_tags or right in local_tags and left in remote_tags for left, right in tag_pairs):
        return DiscogsConfidence.DIFFERENT, "edition_conflict"
    local_catalog, remote_catalog = catalog_number(release.catalog_number), catalog_number(str(metadata.get("catalog_number") or ""))
    local_label, remote_label = text(release.label), text(str(metadata.get("label") or ""))
    if local_catalog and remote_catalog and local_catalog != remote_catalog:
        return DiscogsConfidence.DIFFERENT, "catalog_conflict"
    if local_label and remote_label and local_label != remote_label:
        return DiscogsConfidence.DIFFERENT, "label_conflict"
    local_barcode, remote_barcode = digits(release.barcode), digits(str(metadata.get("barcode") or ""))
    if local_barcode and remote_barcode:
        return (DiscogsConfidence.EXACT, "barcode") if local_barcode == remote_barcode else (DiscogsConfidence.DIFFERENT, "barcode_conflict")
    labels_match = bool(local_label and local_label == remote_label)
    if local_catalog and remote_catalog:
        artist_title_confirmed = bool(remote_artist and remote_title)
        return (DiscogsConfidence.HIGH if labels_match and artist_title_confirmed else DiscogsConfidence.POSSIBLE, "catalog_and_label" if labels_match and artist_title_confirmed else "catalog")
    artist_title = bool(remote_artist and remote_title)
    corroboration = sum((release.release_year is not None and release.release_year == metadata.get("release_year"), bool(release.format and metadata.get("format") and text(release.format) in text(str(metadata["format"]))),))
    return (DiscogsConfidence.HIGH, "artist_title_metadata") if corroboration else (DiscogsConfidence.POSSIBLE, "artist_title")


class DiscogsService:
    def __init__(self, repository: SQLiteRepository, client: DiscogsApiClient) -> None:
        self.repository, self.client = repository, client
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self.client.available

    @staticmethod
    def _key(path: str, params: dict[str, object] | None = None) -> str:
        return hashlib.sha256((path + "?" + urlencode(sorted((params or {}).items()))).encode()).hexdigest()

    def _get(self, path: str, params: dict[str, object] | None = None) -> dict[str, object]:
        key = self._key(path, params)
        if cached := self.repository.discogs_cache_get(key):
            return cached
        payload = self.client.get(path, params)
        self.repository.discogs_cache_put(key, payload)
        return payload

    def _queries(self, release: Release) -> list[dict[str, object]]:
        queries: list[dict[str, object]] = []
        if release.barcode: queries.append({"barcode": digits(release.barcode), "type": "release"})
        if release.catalog_number: queries.append({"catno": release.catalog_number, "label": release.label, "type": "release"})
        queries.append({"artist": release.artist, "release_title": release.title, "year": release.release_year, "type": "release"})
        # This is deliberately the weakest lookup: physical metadata can
        # corroborate a candidate but must not identify a pressing by itself.
        queries.append({"artist": release.artist, "release_title": release.title, "country": release.country, "format": release.format, "type": "release"})
        return queries

    def enrich_release(self, release_id: int) -> list[DiscogsCandidate]:
        self.last_error = None
        if not self.enabled:
            return []
        release = self.repository.release_by_id(release_id)
        if not release:
            return []
        # A confirmed pressing already has fresh, persisted provenance.  Do
        # not spend rate-limit budget repeatedly during GUI redraws.
        if self.repository.confirmed_discogs_match(release_id):
            return []
        candidates: dict[int, DiscogsCandidate] = {}
        for query in self._queries(release):
            try:
                search = self._get("/database/search", query)
                for item in list(search.get("results", []))[:10]:
                    if not isinstance(item, dict) or not item.get("id"):
                        continue
                    try:
                        detail = self._get(f"/releases/{int(item['id'])}")
                    except (HTTPError, URLError, TimeoutError, ValueError, PermissionError) as exc:
                        self._record_error(exc)
                        continue
                    meta = _metadata(detail)
                    confidence, kind = _classify(release, meta)
                    candidate = DiscogsCandidate(int(item["id"]), int(detail["master_id"]) if str(detail.get("master_id", "")).isdigit() else None, f"https://www.discogs.com/release/{int(item['id'])}", confidence, kind, meta)
                    previous = candidates.get(candidate.release_id)
                    if previous is None or list(DiscogsConfidence).index(candidate.confidence) < list(DiscogsConfidence).index(previous.confidence):
                        candidates[candidate.release_id] = candidate
            except (HTTPError, URLError, TimeoutError, ValueError, PermissionError) as exc:
                self._record_error(exc)
                continue
        strong = [candidate for candidate in candidates.values() if candidate.confidence in {DiscogsConfidence.EXACT, DiscogsConfidence.HIGH}]
        if len(strong) > 1:
            candidates = {candidate.release_id: replace(candidate, confidence=DiscogsConfidence.POSSIBLE, match_kind="ambiguous_strong_match") if candidate in strong else candidate for candidate in candidates.values()}
        for candidate in candidates.values():
            status = "confirmed" if candidate.confidence in {DiscogsConfidence.EXACT, DiscogsConfidence.HIGH} else "possible" if candidate.confidence == DiscogsConfidence.POSSIBLE else "different"
            self.repository.save_discogs_match(release_id, discogs_release_id=candidate.release_id, discogs_master_id=candidate.master_id, discogs_url=candidate.url, confidence=candidate.confidence.value, match_kind=candidate.match_kind, status=status, metadata=candidate.metadata)
            if status == "confirmed":
                self.repository.fill_missing_release_metadata(release_id, candidate.metadata)
        # Two provisional groups can become one only after the same concrete
        # Discogs pressing has been confirmed.  The repository rechecks all
        # store-card conflicts before it merges them.
        self.repository.consolidate_confirmed_discogs_releases()
        return list(candidates.values())

    def _record_error(self, error: BaseException) -> None:
        if isinstance(error, HTTPError) and error.code in {401, 403}:
            self.last_error = "auth"
        elif self.last_error is None:
            self.last_error = "unavailable"
