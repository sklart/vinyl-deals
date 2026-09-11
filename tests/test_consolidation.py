from __future__ import annotations

from decimal import Decimal

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer, StoreSearchQuery
from vinyl_deals.live_search import _materialize
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.search import search_releases


def offer(source: str, identifier: str, **values) -> RawOffer:
    values.setdefault("price", Decimal("5000"))
    return RawOffer.now(source=source, source_product_id=identifier, url=f"https://{source}/{identifier}", artist_raw="Pink Floyd", title_raw="Wish You Were Here", availability=Availability.IN_STOCK, **values)


def test_same_barcode_consolidates_and_search_counts_all_offers(tmp_path):
    repository = SQLiteRepository(tmp_path / "offers.sqlite3")
    repository.upsert_offer(offer("one", "1", barcode="4006381333931"))
    repository.upsert_offer(offer("two", "2", barcode="4006381333931", price=Decimal("4500")))
    build_match_queue(repository)
    rows = search_releases(repository, artist="pink")
    assert len(rows) == 1
    assert rows[0].offer_count == rows[0].store_count == 2
    assert rows[0].lowest_price_offer.price == Decimal("4500")


def test_conflicting_pressings_stay_separate(tmp_path):
    repository = SQLiteRepository(tmp_path / "offers.sqlite3")
    repository.upsert_offer(offer("one", "1", barcode="4006381333931", release_year=1975, format="LP"))
    repository.upsert_offer(offer("two", "2", barcode="5901234123457", release_year=2011, format="2LP"))
    build_match_queue(repository)
    repository.ensure_releases_for_unmatched_offers()
    assert len(search_releases(repository, artist="pink")) == 2


def test_store_sku_and_long_description_are_not_pressing_metadata(tmp_path):
    repository = SQLiteRepository(tmp_path / "offers.sqlite3")
    repository.upsert_offer(offer("one", "1", store_sku="SKU-55", catalog_number_raw="SKU-55", label="Страна: Великобритания. Подробное описание товара и характеристики винилового издания" * 2, format="LP " * 100))
    stored = repository.offers_for_matching()[0][1]
    assert stored.store_sku == "SKU-55"
    assert stored.catalog_number_raw is None
    assert stored.label is None
    assert stored.format is None


def test_targeted_live_search_consolidates_same_artist_title_when_metadata_is_missing(tmp_path):
    repository = SQLiteRepository(tmp_path / "offers.sqlite3")
    releases = _materialize(
        repository,
        [offer("one", "1"), offer("two", "2", price=Decimal("4500"))],
        StoreSearchQuery(artist="Pink Floyd", title="Wish You Were Here"),
    )
    assert len(releases) == 1
    assert releases[0].offer_count == releases[0].store_count == 2


def test_confirmed_discogs_pressing_consolidates_provisional_possible_groups(tmp_path):
    repository = SQLiteRepository(tmp_path / "offers.sqlite3")
    repository.upsert_offer(offer("one", "1"))
    repository.upsert_offer(offer("two", "2"))
    build_match_queue(repository)
    repository.ensure_releases_for_unmatched_offers()
    first, second = [item.id for item in repository.releases_for_search()]
    assert repository.possible_matches()
    for release_id in (first, second):
        repository.save_discogs_match(
            release_id,
            discogs_release_id=123,
            discogs_master_id=None,
            discogs_url="https://www.discogs.com/release/123",
            confidence="EXACT",
            match_kind="barcode",
            status="confirmed",
            metadata={"artist": "Pink Floyd", "title": "Wish You Were Here"},
        )
    assert repository.consolidate_confirmed_discogs_releases() == 1
    assert len(repository.releases_for_search()) == 1
