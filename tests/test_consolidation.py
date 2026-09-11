from __future__ import annotations

from decimal import Decimal
from dataclasses import replace

from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer, StoreSearchQuery
from vinyl_deals.live_search import _materialize
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.matching.normalize import normalize_barcode
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


def test_consolidated_release_pricing_uses_all_store_offers(tmp_path):
    repository = SQLiteRepository(tmp_path / "offers.sqlite3")
    repository.upsert_offer(offer("one", "1", barcode="4006381333931", price=Decimal("4290"), condition_media="NEW"))
    repository.upsert_offer(offer("two", "2", barcode="4006381333931", price=Decimal("5850"), condition_media="NEW"))
    repository.upsert_offer(offer("three", "3", barcode="4006381333931", price=Decimal("6000"), condition_media="NEW"))
    build_match_queue(repository)
    result = search_releases(repository, artist="pink")[0]
    assert result.offer_count == result.store_count == 3
    assert result.lowest_price_offer.price == Decimal("4290")
    assert result.market_median == Decimal("5925")
    assert result.comparable_count == 2
    assert result.discount_pct == Decimal("27.59493670886075949367088608")
    # Two foreign stores are a small sample: the discount is visible, but the
    # conservative pricing policy caps the class below HOT.
    assert result.deal_class.value == "GOOD"


def test_single_consolidated_offer_has_no_market_discount(tmp_path):
    repository = SQLiteRepository(tmp_path / "offers.sqlite3")
    repository.upsert_offer(offer("one", "1", barcode="4006381333931", condition_media="NEW"))
    repository.ensure_releases_for_unmatched_offers()
    result = search_releases(repository, artist="pink")[0]
    assert result.market_median is None
    assert result.discount_pct is None
    assert result.deal_class.value == "INSUFFICIENT"


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


def test_targeted_live_search_keeps_sparse_same_title_cards_as_possible(tmp_path):
    repository = SQLiteRepository(tmp_path / "offers.sqlite3")
    releases = _materialize(
        repository,
        [offer("one", "1"), offer("two", "2", price=Decimal("4500"))],
        StoreSearchQuery(artist="Pink Floyd", title="Wish You Were Here"),
    )
    assert len(releases) == 2
    assert repository.possible_matches()


def test_sparse_card_cannot_bridge_different_same_title_pressings(tmp_path):
    repository = SQLiteRepository(tmp_path / "offers.sqlite3")
    offers = [
        offer("tishina", "sparse"),
        offer("vinyl_ru", "lp", barcode="4006381333931", catalog_number_raw="PCS-7009", format="LP", disc_count=1),
        offer("vinyl_ru", "box", barcode="0602445599691", catalog_number_raw="REV-5LP", format="5LP", disc_count=5),
    ]
    releases = _materialize(repository, offers, StoreSearchQuery(title="Wish You Were Here"))
    assert len(releases) == 3
    assert all(item.offer_count == 1 for item in releases)


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


def test_upc_ean_and_gtin14_have_one_canonical_identifier():
    assert {normalize_barcode(value) for value in ("602445599691", "0602445599691", "00602445599691")} == {"00602445599691"}
    assert normalize_barcode("1234567890123") is None


def test_lp_and_1lp_normalize_without_hiding_disc_count(tmp_path):
    repository = SQLiteRepository(tmp_path / "formats.sqlite3")
    repository.upsert_offer(offer("one", "1", format="LP"))
    repository.upsert_offer(offer("two", "2", format="1LP", disc_count=1))
    repository.upsert_offer(offer("three", "3", format="5 LP", disc_count=5))
    assert [(item.format, item.disc_count) for _, item in repository.offers_for_matching()] == [("LP", 1), ("LP", 1), ("LP", 5)]


def test_repair_metadata_cleans_legacy_fields_and_rebuilds(tmp_path):
    repository = SQLiteRepository(tmp_path / "repair.sqlite3")
    repository.upsert_offer(offer("one", "1", store_sku="SKU-55"))
    with repository._connect() as connection:
        raw = replace(repository.offers_for_matching()[0][1], catalog_number_raw="SKU-55", label="Описание товара " * 20, format="2 LP", barcode="0602445599691")
        connection.execute("UPDATE offers SET catalog_number_raw='SKU-55', label=?, format='2 LP', barcode='0602445599691' WHERE id=1", ("Описание товара " * 20,))
        # Simulate the old serialized record too, then let repair replace it.
        connection.execute("UPDATE offers SET offer_json=? WHERE id=1", (__import__("vinyl_deals.database.repository", fromlist=["_serialize_offer"])._serialize_offer(raw),))
    assert repository.repair_metadata() == 1
    repaired = repository.offers_for_matching()[0][1]
    assert repaired.catalog_number_raw is None and repaired.label is None
    assert (repaired.format, repaired.disc_count, repaired.barcode) == ("LP", 2, "00602445599691")


def test_repair_metadata_removes_short_description_disguised_as_label(tmp_path):
    repository = SQLiteRepository(tmp_path / "repair-label.sqlite3")
    repository.upsert_offer(offer("one", "1"))
    legacy = replace(
        repository.offers_for_matching()[0][1],
        label="UMC, \u0433\u043e\u0434 \u0438\u0437\u0434\u0430\u043d\u0438\u044f 2022, \u0441\u0442\u0440\u0430\u043d\u0430 \u0412\u0435\u043b\u0438\u043a\u043e\u0431\u0440\u0438\u0442\u0430\u043d\u0438\u044f, \u0444\u043e\u0440\u043c\u0430\u0442 LP",
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE offers SET label=?, offer_json=? WHERE id=1",
            (legacy.label, __import__("vinyl_deals.database.repository", fromlist=["_serialize_offer"])._serialize_offer(legacy)),
        )
    assert repository.repair_metadata() == 1
    assert repository.offers_for_matching()[0][1].label is None
