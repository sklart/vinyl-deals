from datetime import datetime, timedelta, timezone
from decimal import Decimal

from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer, Release
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.search import discogs_search_url, search_releases


NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def add(repo, source, product, price, *, artist="Opeth", title="Blackwater Park", barcode="4006381333931", catalog="MOVLP001", label="Music On Vinyl", year=2021, format="2LP", condition="NEW", availability=Availability.IN_STOCK, when=NOW):
    repo.upsert_offer(RawOffer(source=source, source_product_id=product, url=f"https://example.test/{source}/{product}", fetched_at=when, artist_raw=artist, title_raw=title, barcode=barcode, catalog_number_raw=catalog, label=label, release_year=year, format=format, condition_media=condition, price=Decimal(str(price)), availability=availability))


def populated_repository(tmp_path):
    repo = SQLiteRepository(tmp_path / "search.sqlite3")
    add(repo, "imagine", "1", 5490)
    add(repo, "collectomania", "2", 5990)
    add(repo, "vinyl_ru", "3", 6300)
    add(repo, "sold", "4", 100, availability=Availability.OUT_OF_STOCK)
    add(repo, "stale", "5", 200, when=NOW - timedelta(days=8))
    add(repo, "imagine", "6", 7000, title="Damnation", barcode="4012345678901", catalog="MOVLP002")
    add(repo, "collectomania", "7", 7200, title="Damnation", barcode="4012345678901", catalog="MOVLP002")
    build_match_queue(repo)
    return repo


def test_search_by_artist_title_and_partial_fields_is_normalized(tmp_path):
    repo = populated_repository(tmp_path)
    result = search_releases(repo, artist="OPETH", title="blackwater", now=NOW)
    assert [item.title for item in result] == ["Blackwater Park"]
    assert result[0].label == "Music On Vinyl"

    partial = search_releases(repo, artist="opeth", label="music", year=2021, format="lp", now=NOW)
    assert {item.title for item in partial} == {"Blackwater Park", "Damnation"}


def test_search_by_barcode_catalog_and_empty_result(tmp_path):
    repo = populated_repository(tmp_path)
    assert [item.title for item in search_releases(repo, barcode="4006-3813 33931", now=NOW)] == ["Blackwater Park"]
    assert [item.title for item in search_releases(repo, catalog="mov lp 002", now=NOW)] == ["Damnation"]
    assert search_releases(repo, artist="Nobody", now=NOW) == []


def test_current_offers_and_best_offer_exclude_out_of_stock_and_stale(tmp_path):
    repo = populated_repository(tmp_path)
    result = search_releases(repo, title="Blackwater Park", now=NOW)[0]
    assert [offer.store for offer in result.offers] == ["imagine", "collectomania", "vinyl_ru", "sold"]
    assert result.best_offer is not None
    assert result.best_offer.store == "imagine"
    assert result.best_offer.price == Decimal("5490")


def test_discogs_url_prefers_barcode_then_catalog_and_label():
    by_barcode = Release(1, "Opeth", "Blackwater Park", barcode="4006381333931", catalog_number="MOVLP001", label="Music On Vinyl", release_year=2021)
    assert discogs_search_url(by_barcode) == "https://www.discogs.com/search/?q=4006381333931&type=all"
    by_catalog = Release(2, "Opeth", "Damnation", label="Music On Vinyl", catalog_number="MOV LP 002", release_year=2003)
    assert discogs_search_url(by_catalog) == "https://www.discogs.com/search/?q=MOV+LP+002+Music+On+Vinyl&type=all"
