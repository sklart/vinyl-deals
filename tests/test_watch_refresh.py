from datetime import datetime, timezone
from decimal import Decimal

from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer, StoreSearchQuery, StoreSearchStatus, StoreState
from vinyl_deals.live_search import LiveSearchResult, LiveStoreResult
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.scheduler import run_cycle
from vinyl_deals.watch_refresh import query_for_release, refresh_watchlist


def _release(repository, identifier: str, *, barcode="4006381333931", catalog="MOVLP001", label="Music On Vinyl"):
    repository.upsert_offer(RawOffer.now(
        source="seed", source_product_id=identifier, url=f"https://seed.test/{identifier}",
        artist_raw="Opeth", title_raw="Blackwater Park", barcode=barcode,
        catalog_number_raw=catalog, label=label, format="2LP", disc_count=2,
        condition_media="NEW", price=Decimal("5000"), availability=Availability.IN_STOCK,
    ))
    build_match_queue(repository)
    repository.ensure_releases_for_unmatched_offers()
    return repository.releases_for_search()[-1]


def _result(query, *, offers=1, statuses=(StoreSearchStatus.FOUND,)):
    stores = tuple(
        LiveStoreResult(f"store_{index}", StoreState.ACTIVE if status in {StoreSearchStatus.FOUND, StoreSearchStatus.EMPTY} else StoreState.DEGRADED,
                        offers if status == StoreSearchStatus.FOUND else 0, status=status)
        for index, status in enumerate(statuses)
    )
    return LiveSearchResult(query, (), stores)


def test_watch_query_prefers_gtin_but_keeps_other_pressing_identifiers(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    release = _release(repository, "1")
    query = query_for_release(release)
    assert query.barcode == "04006381333931"
    assert query.catalog_number == "MOVLP001" and query.label == "Music On Vinyl"
    assert query.artist == "Opeth" and query.title == "Blackwater Park"
    assert query.text() == "04006381333931"


def test_watch_query_falls_back_to_catalog_then_artist_title(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    catalog_release = _release(repository, "1", barcode=None)
    catalog_query = query_for_release(catalog_release)
    assert catalog_query.text() == "MOVLP001 Music On Vinyl"
    text_release = _release(repository, "2", barcode=None, catalog=None, label=None)
    text_query = query_for_release(text_release)
    assert text_query.text() == "Opeth Blackwater Park"


def test_watch_refresh_is_targeted_deduplicated_and_persists_partial_state(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    first = _release(repository, "1", barcode=None, catalog=None, label=None)
    # Two local Release rows with equal metadata model a legacy duplicate.
    second = _release(repository, "2", barcode=None, catalog=None, label=None)
    repository.add_watchlist(first.id)
    repository.add_watchlist(second.id)
    calls, progress = [], []

    def search(_repository, query):
        calls.append(query)
        return _result(query, offers=3, statuses=(StoreSearchStatus.FOUND, StoreSearchStatus.RESTRICTED))

    refreshed = refresh_watchlist(repository, live_search_service=search, progress=progress.append)
    assert len(calls) == 1
    assert refreshed.watched == refreshed.checked == 2
    assert refreshed.partial == 2 and refreshed.offers_updated == 3
    assert all(row["last_check_status"] == "PARTIAL" and row["last_offer_count"] == 3 for row in repository.watchlist_entries())
    assert progress == ["Проверка 1/1: Opeth — Blackwater Park"]


def test_multiple_distinct_watches_are_processed_sequentially(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    first = _release(repository, "1", barcode=None, catalog="CAT-A", label="Label")
    second = _release(repository, "2", barcode=None, catalog="CAT-B", label="Label")
    repository.add_watchlist(first.id)
    repository.add_watchlist(second.id)
    calls, progress = [], []
    def search(_repository, query):
        calls.append(query)
        return _result(query, offers=1)
    result = refresh_watchlist(repository, live_search_service=search, progress=progress.append)
    assert result.watched == result.checked == 2 and len(calls) == 2
    assert [line.split(":", 1)[0] for line in progress] == ["Проверка 1/2", "Проверка 2/2"]


def test_no_watchlist_causes_zero_network_calls_and_scheduler_skips_full_refresh(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    calls = []
    result = refresh_watchlist(repository, live_search_service=lambda *_args: calls.append(True))
    assert result.watched == result.checked == result.offers_updated == 0 and not calls
    cycle = run_cycle(repository, live_search_service=lambda *_args: calls.append(True))
    assert cycle == {"watched": 0, "checked": 0, "partial": 0, "offers_updated": 0, "alerts": 0, "sent": 0, "failed": 0}
    assert not calls


def test_error_or_restricted_watch_refresh_keeps_existing_offer_and_no_false_alert(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    release = _release(repository, "1")
    repository.add_watchlist(release.id)
    def search(_repository, query):
        return _result(query, offers=0, statuses=(StoreSearchStatus.RESTRICTED,))
    refresh_watchlist(repository, live_search_service=search)
    entry = repository.watchlist_entries()[0]
    assert entry["last_check_status"] == "ERROR"
    assert repository.offers_for_release(release.id)
    assert not run_cycle(repository, live_search_service=search)["alerts"]


def test_scheduler_refreshes_pricing_before_evaluating_and_deduplicates_alerts(tmp_path, monkeypatch):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    release = _release(repository, "1")
    repository.add_watchlist(release.id)
    order = []

    def search(repo, query):
        order.append("live")
        repo.upsert_offer(RawOffer.now(
            source="fresh", source_product_id="1", url="https://fresh.test/1",
            artist_raw="Opeth", title_raw="Blackwater Park", barcode="4006381333931",
            catalog_number_raw="MOVLP001", label="Music On Vinyl", format="2LP", disc_count=2,
            condition_media="NEW", price=Decimal("3000"), availability=Availability.IN_STOCK,
        ))
        build_match_queue(repo)
        return _result(query, offers=1)

    from vinyl_deals import scheduler as scheduler_module
    original = scheduler_module.evaluate_watchlist
    def evaluated(*args, **kwargs):
        assert order
        return original(*args, **kwargs)
    monkeypatch.setattr(scheduler_module, "evaluate_watchlist", evaluated)
    first = run_cycle(repository, live_search_service=search)
    second = run_cycle(repository, live_search_service=search)
    assert first["offers_updated"] == 1
    assert second["alerts"] == 0
