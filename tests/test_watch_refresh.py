from datetime import datetime, timezone
from decimal import Decimal

from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer, StoreSearchQuery, StoreSearchStatus, StoreState
from vinyl_deals.live_search import LiveSearchResult, LiveStoreResult
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.scheduler import run_cycle
from vinyl_deals.watch_refresh import fallback_queries, query_for_release, refresh_watchlist


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


def _candidate(repository, source: str, product_id: str, *, barcode=None, catalog=None,
               label=None, year=None, country=None, format=None, disc_count=None):
    repository.upsert_offer(RawOffer.now(
        source=source, source_product_id=product_id, url=f"https://{source}.test/{product_id}",
        artist_raw="Opeth", title_raw="Blackwater Park", barcode=barcode,
        catalog_number_raw=catalog, label=label, release_year=year, country=country,
        format=format, disc_count=disc_count, condition_media="NEW",
        price=Decimal("4000"), availability=Availability.IN_STOCK,
    ))
    return next(offer_id for offer_id, offer in repository.offers_for_matching()
                if offer.source == source and offer.source_product_id == product_id)


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


def test_watch_fallbacks_keep_pressing_identifiers_and_become_broader(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    release = _release(repository, "1")
    queries = fallback_queries(release)
    assert [query.text() for query in queries] == ["04006381333931", "MOVLP001 Music On Vinyl", "Opeth Blackwater Park"]
    assert all(query.barcode == "04006381333931" for query in queries)
    assert all(query.catalog_number == "MOVLP001" and query.label == "Music On Vinyl" for query in queries)


def test_empty_strong_query_falls_back_without_relaxing_identifiers(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    release = _release(repository, "1")
    repository.add_watchlist(release.id)
    attempted = []

    def search(_repository, query):
        attempted.append(query)
        return _result(query, offers=0, statuses=(StoreSearchStatus.EMPTY,))

    result = refresh_watchlist(repository, live_search_service=search)
    assert [query.text() for query in attempted] == ["04006381333931", "MOVLP001 Music On Vinyl", "Opeth Blackwater Park"]
    assert all(query.barcode == "04006381333931" for query in attempted)
    assert result.checked == 1 and result.offers_updated == 0
    assert repository.watchlist_entries()[0]["last_check_status"] == "NO_RESULTS"


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


def test_timeout_marks_one_watch_partial_and_batch_continues(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    first = _release(repository, "1", barcode=None, catalog="CAT-A", label="Label")
    second = _release(repository, "2", barcode=None, catalog="CAT-B", label="Label")
    repository.add_watchlist(first.id)
    repository.add_watchlist(second.id)
    calls = []

    def search(_repository, query):
        calls.append(query.catalog_number)
        if query.catalog_number == "CAT-A":
            return _result(query, offers=2, statuses=(StoreSearchStatus.FOUND, StoreSearchStatus.TIMEOUT))
        return _result(query, offers=1)

    result = refresh_watchlist(repository, live_search_service=search)

    entries = {entry["release_id"]: entry for entry in repository.watchlist_entries()}
    # Store 0 found the first term, while the timed-out store independently
    # proceeds to the artist/title fallback before the next watch starts.
    assert calls == ["CAT-A", "CAT-A", "CAT-B"]
    assert result.checked == 2 and result.partial == 1
    assert entries[first.id]["last_check_status"] == "PARTIAL"
    assert entries[second.id]["last_check_status"] == "OK"


def test_no_watchlist_causes_zero_network_calls_and_scheduler_skips_full_refresh(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    calls = []
    result = refresh_watchlist(repository, live_search_service=lambda *_args: calls.append(True))
    assert result.watched == result.checked == result.offers_updated == 0 and not calls
    cycle = run_cycle(repository, live_search_service=lambda *_args: calls.append(True))
    assert cycle == {"watched": 0, "checked": 0, "partial": 0, "offers_updated": 0, "fresh_offers": 0, "possible_offers": 0, "cached_offers": 0, "alerts": 0, "sent": 0, "failed": 0}
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


def test_cached_offer_is_visible_but_never_creates_watch_alert(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    release = _release(repository, "1", barcode=None, catalog=None, label=None)
    repository.add_watchlist(release.id)
    offer_id = next(offer_id for offer_id, _ in repository.offers_for_release(release.id))

    def search(_repository, query):
        cached = LiveStoreResult("seed", StoreState.DEGRADED, 1, cached=True, status=StoreSearchStatus.CACHED)
        return LiveSearchResult(query, (), (cached,), fresh_offer_ids=(), cached_offer_ids=(offer_id,))

    cycle = run_cycle(repository, live_search_service=search)
    entry = repository.watchlist_entries()[0]
    assert cycle["fresh_offers"] == 0 and cycle["cached_offers"] == 1 and cycle["alerts"] == 0
    assert entry["last_fresh_offer_count"] == 0 and entry["last_cached_offer_count"] == 1


def test_cached_fallback_is_retained_when_broader_live_queries_are_empty(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    release = _release(repository, "1")
    repository.add_watchlist(release.id)
    offer_id = next(offer_id for offer_id, _ in repository.offers_for_release(release.id))

    def search(_repository, query):
        if query.search_text == "04006381333931":
            cached = LiveStoreResult("seed", StoreState.DEGRADED, 1, cached=True, status=StoreSearchStatus.CACHED)
            return LiveSearchResult(query, (), (cached,), cached_offer_ids=(offer_id,))
        return _result(query, offers=0, statuses=(StoreSearchStatus.EMPTY,))

    refreshed = refresh_watchlist(repository, live_search_service=search)
    entry = repository.watchlist_entries()[0]
    assert refreshed.fresh_offers == 0 and refreshed.cached_offers == 1
    assert entry["last_check_status"] == "OK"
    assert entry["last_cached_offer_count"] == 1


def test_each_store_gets_its_own_fallback_chain(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    release = _release(repository, "1")
    repository.add_watchlist(release.id)
    store_a = _candidate(repository, "store_a", "a", barcode="4006381333931")
    store_b = _candidate(repository, "store_b", "b", barcode="4006381333931")
    calls = []

    def search(_repository, query, *, sources=None):
        calls.append((query.text(), sources))
        if sources is None:
            return LiveSearchResult(query, (), (
                LiveStoreResult("store_a", StoreState.ACTIVE, 1, status=StoreSearchStatus.FOUND),
                LiveStoreResult("store_b", StoreState.ACTIVE, 0, status=StoreSearchStatus.EMPTY),
            ), fresh_offer_ids=(store_a,))
        assert sources == ("store_b",)
        return LiveSearchResult(query, (), (
            LiveStoreResult("store_b", StoreState.ACTIVE, 1, status=StoreSearchStatus.FOUND),
        ), fresh_offer_ids=(store_b,))

    refreshed = refresh_watchlist(repository, live_search_service=search)
    assert calls == [("04006381333931", None), ("MOVLP001 Music On Vinyl", ("store_b",))]
    assert set(refreshed.fresh_confirmed_offer_ids) == {store_a, store_b}
    assert not refreshed.possible_offer_ids


def test_broad_fallback_without_pressing_evidence_is_possible_and_never_alerts(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    release = _release(repository, "1")
    repository.add_watchlist(release.id)
    candidate = _candidate(repository, "store_b", "sparse")

    def search(_repository, query, *, sources=None):
        if query.search_text != "Opeth Blackwater Park":
            return LiveSearchResult(query, (), (LiveStoreResult("store_b", StoreState.ACTIVE, 0, status=StoreSearchStatus.EMPTY),))
        return LiveSearchResult(query, (), (LiveStoreResult("store_b", StoreState.ACTIVE, 1, status=StoreSearchStatus.FOUND),), fresh_offer_ids=(candidate,))

    refreshed = refresh_watchlist(repository, live_search_service=search)
    assert refreshed.fresh_confirmed_offer_ids == ()
    assert refreshed.possible_offer_ids == (candidate,)
    assert run_cycle(repository, live_search_service=search)["alerts"] == 0


def test_broad_fallback_with_matching_identifier_is_confirmed(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    release = _release(repository, "1")
    repository.add_watchlist(release.id)
    candidate = _candidate(repository, "store_b", "confirmed", catalog="MOVLP001", label="Music On Vinyl")

    def search(_repository, query, *, sources=None):
        if query.search_text != "Opeth Blackwater Park":
            return LiveSearchResult(query, (), (LiveStoreResult("store_b", StoreState.ACTIVE, 0, status=StoreSearchStatus.EMPTY),))
        return LiveSearchResult(query, (), (LiveStoreResult("store_b", StoreState.ACTIVE, 1, status=StoreSearchStatus.FOUND),), fresh_offer_ids=(candidate,))

    refreshed = refresh_watchlist(repository, live_search_service=search)
    assert refreshed.fresh_confirmed_offer_ids == (candidate,)
    assert refreshed.possible_offer_ids == ()


def test_broad_fallback_with_conflicting_barcode_is_rejected(tmp_path):
    repository = SQLiteRepository(tmp_path / "watch.sqlite3")
    release = _release(repository, "1")
    repository.add_watchlist(release.id)
    candidate = _candidate(repository, "store_b", "wrong", barcode="4006381333948")

    def search(_repository, query, *, sources=None):
        if query.search_text != "Opeth Blackwater Park":
            return LiveSearchResult(query, (), (LiveStoreResult("store_b", StoreState.ACTIVE, 0, status=StoreSearchStatus.EMPTY),))
        return LiveSearchResult(query, (), (LiveStoreResult("store_b", StoreState.ACTIVE, 1, status=StoreSearchStatus.FOUND),), fresh_offer_ids=(candidate,))

    refreshed = refresh_watchlist(repository, live_search_service=search)
    assert refreshed.fresh_confirmed_offer_ids == ()
    assert refreshed.possible_offer_ids == ()


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
