from __future__ import annotations

from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.discogs import DiscogsApiClient, DiscogsConfidence, DiscogsService
from vinyl_deals.domain import RawOffer
from vinyl_deals.search import search_releases


def _repo(tmp_path, *, barcode: str | None = "724349792891"):
    repository = SQLiteRepository(tmp_path / "vinyl.sqlite3")
    repository.upsert_offer(RawOffer.now(source="shop", source_product_id="1", url="https://shop/1", artist_raw="Pink Floyd", title_raw="Wish You Were Here", barcode=barcode, label="Harvest", catalog_number_raw="SHVL 814", release_year=1975, format="LP"))
    repository.ensure_releases_for_unmatched_offers()
    return repository, repository.releases_for_search()[0]


def _detail(*, release_id=1, barcode="724349792891", catalog="SHVL 814", title="Wish You Were Here"):
    return {"id": release_id, "master_id": 99, "title": title, "year": 1975, "country": "UK", "artists": [{"name": "Pink Floyd"}], "labels": [{"name": "Harvest", "catno": catalog}], "identifiers": [{"type": "Barcode", "value": barcode}], "formats": [{"name": "Vinyl", "qty": "1", "descriptions": ["LP", "Album"]}]}


def _service(repository, details, calls):
    def fetch(url, _headers, _timeout):
        calls.append(url)
        if "/database/search" in url:
            return {"results": [{"id": item["id"]} for item in details]}
        release_id = int(url.rsplit("/", 1)[-1])
        return next(item for item in details if item["id"] == release_id)
    return DiscogsService(repository, DiscogsApiClient(fetch=fetch))


def test_exact_barcode_is_confirmed_and_opens_specific_release(tmp_path):
    repository, release = _repo(tmp_path)
    service = _service(repository, [_detail()], [])
    candidates = service.enrich_release(release.id)
    assert candidates[0].confidence == DiscogsConfidence.EXACT
    result = search_releases(repository, artist="pink floyd")[0]
    assert result.discogs_url == "https://www.discogs.com/release/1"
    assert result.discogs_confidence == "EXACT"


def test_conflicting_barcode_is_different_and_never_overwrites(tmp_path):
    repository, release = _repo(tmp_path)
    candidates = _service(repository, [_detail(barcode="000000000000")], []).enrich_release(release.id)
    assert candidates[0].confidence == DiscogsConfidence.DIFFERENT
    assert repository.confirmed_discogs_match(release.id) is None
    assert search_releases(repository)[0].discogs_release_id is None


def test_catalog_label_is_high_and_possible_requires_manual_confirmation(tmp_path):
    repository, release = _repo(tmp_path, barcode=None)
    details = [_detail(release_id=1), _detail(release_id=2, catalog="OTHER")]
    service = _service(repository, details, [])
    candidates = service.enrich_release(release.id)
    assert {candidate.confidence for candidate in candidates} == {DiscogsConfidence.HIGH, DiscogsConfidence.DIFFERENT}
    repository.save_discogs_match(release.id, discogs_release_id=3, discogs_master_id=99, discogs_url="https://www.discogs.com/release/3", confidence="POSSIBLE", match_kind="artist_title", status="possible", metadata={"label": "Must not replace"})
    repository.confirm_discogs_match(release.id, 3)
    assert repository.confirmed_discogs_match(release.id)["discogs_release_id"] == 3


def test_cache_prevents_second_api_call(tmp_path):
    repository, release = _repo(tmp_path)
    calls: list[str] = []
    service = _service(repository, [_detail()], calls)
    service.enrich_release(release.id)
    first = len(calls)
    service.enrich_release(release.id)
    assert first and len(calls) == first


def test_discogs_failure_isolated_from_store_search_and_429_retries(tmp_path):
    repository, release = _repo(tmp_path)
    attempts = 0
    def rate_limited(_url, _headers, _timeout):
        nonlocal attempts
        attempts += 1
        raise HTTPError("https://api.discogs.com", 429, "rate", {}, None)
    service = DiscogsService(repository, DiscogsApiClient(fetch=rate_limited, max_retries=1))
    assert service.enrich_release(release.id) == []
    assert attempts == 2
    # Existing store/cache search remains usable without Discogs.
    assert search_releases(repository, title="wish")[0].title == "Wish You Were Here"
