from datetime import datetime, timedelta, timezone
from decimal import Decimal

from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer, Release
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.matching.normalize import text
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


def test_explicit_best_prices_separate_new_used_and_lowest(tmp_path):
    repo = SQLiteRepository(tmp_path / "best-prices.sqlite3")
    add(repo, "new-store", "1", 8000, condition="NEW")
    add(repo, "used-store", "2", 4000, condition="NM")
    add(repo, "unknown-store", "3", 3000, condition="ungraded")
    add(repo, "sold-store", "4", 100, condition="NEW", availability=Availability.OUT_OF_STOCK)
    add(repo, "stale-store", "5", 200, condition="NM", when=NOW - timedelta(days=8))
    build_match_queue(repo)

    result = search_releases(repo, title="Blackwater Park", now=NOW)[0]
    assert result.best_new_offer.price == Decimal("8000")
    assert result.best_used_offer.price == Decimal("4000")
    assert result.lowest_price_offer.price == Decimal("3000")
    assert result.best_offer.price == Decimal("8000")


def test_best_effective_offer_requires_known_delivery_or_pickup(tmp_path):
    repo = SQLiteRepository(tmp_path / "effective-search.sqlite3")
    add(repo, "unknown-delivery", "1", 3000, condition="NEW")
    repo.upsert_offer(RawOffer(
        source="rio_rostov", source_product_id="2", url="https://example.test/rio", fetched_at=NOW,
        artist_raw="Opeth", title_raw="Blackwater Park", barcode="4006381333931", catalog_number_raw="MOVLP001",
        label="Music On Vinyl", release_year=2021, format="2LP", condition_media="NEW", price=Decimal("5000"),
        availability=Availability.IN_STOCK, city="Ростов-на-Дону", local_store=True, pickup_available=True,
    ))
    add(repo, "known-delivery", "3", 6000, condition="NEW")
    stored = repo.offer_by_id(3)[1]
    repo.upsert_offer(RawOffer(**{**{name: getattr(stored, name) for name in stored.__dataclass_fields__}, "delivery_cost": Decimal("200")}))
    build_match_queue(repo)

    result = search_releases(repo, title="Blackwater Park", now=NOW)[0]
    assert result.lowest_price_offer.store == "unknown-delivery"
    assert result.best_effective_offer.store == "rio_rostov"
    unknown = next(offer for offer in result.offers if offer.store == "unknown-delivery")
    known = next(offer for offer in result.offers if offer.store == "known-delivery")
    assert unknown.effective_price is None and not unknown.effective_price_known
    assert known.effective_price == Decimal("6200") and known.effective_price_known


def test_release_uses_nonempty_semantically_equivalent_artist_and_title(tmp_path):
    repo = SQLiteRepository(tmp_path / "release-text.sqlite3")
    add(repo, "one", "1", 100, artist="OPETH", title="Blackwater   Park")
    add(repo, "two", "2", 110, artist="Opeth", title="Blackwater Park")
    build_match_queue(repo)
    release = repo.releases_for_search()[0]
    assert text(release.artist) == "opeth"
    assert text(release.title) == "blackwater park"


def test_discogs_url_prefers_barcode_then_catalog_and_label():
    by_barcode = Release(1, "Opeth", "Blackwater Park", barcode="4006381333931", catalog_number="MOVLP001", label="Music On Vinyl", release_year=2021)
    assert discogs_search_url(by_barcode) == "https://www.discogs.com/search/?q=4006381333931&type=all"
    by_catalog = Release(2, "Opeth", "Damnation", label="Music On Vinyl", catalog_number="MOV LP 002", release_year=2003)
    assert discogs_search_url(by_catalog) == "https://www.discogs.com/search/?q=MOV+LP+002+Music+On+Vinyl&type=all"
