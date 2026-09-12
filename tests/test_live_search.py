from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from time import sleep

from vinyl_deals.adapters.vinyl_ru import VinylRuAdapter
from vinyl_deals.adapters.collectomania import CollectomaniaAdapter
from vinyl_deals.adapters.drhead import DrHeadAdapter
from vinyl_deals.adapters.imagine_club import ImagineClubAdapter
from vinyl_deals.adapters.wave2 import MaximumVinylAdapter
from vinyl_deals.domain import Availability, RawOffer, StoreSearchQuery, StoreSearchResult, StoreSearchStatus, StoreState
from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.live_search import LiveStoreResult, _rank_live_offers, live_search
from vinyl_deals.matching.normalize import normalize_barcode
from vinyl_deals.pricing import DealClass, evaluate_offer, median_price


def offer(source: str, identifier: str, price: int, *, barcode: str | None = "4006381333931", catalog: str | None = None) -> RawOffer:
    return RawOffer.now(
        source=source, source_product_id=identifier, url=f"https://{source}.test/{identifier}",
        artist_raw="Pink Floyd", title_raw="Wish You Were Here", barcode=barcode, catalog_number_raw=catalog,
        price=Decimal(price), availability=Availability.IN_STOCK, condition_media="NEW",
    )


class Adapter:
    def __init__(self, source: str, offers: tuple[RawOffer, ...], delay: float = 0, state: StoreState = StoreState.ACTIVE) -> None:
        self.source, self.offers, self.delay, self.state = source, offers, delay, state

    def search_offers(self, _query):
        sleep(self.delay)
        return StoreSearchResult(self.source, self.offers, self.state, ("blocked",) if self.state != StoreState.ACTIVE else ())

    def enrich_offer(self, item):
        return replace(item, raw_data={"fully_enriched": True})


def test_live_search_runs_stores_in_parallel_persists_and_matches(tmp_path):
    repository = SQLiteRepository(tmp_path / "live.sqlite3")
    reports = []
    result = live_search(
        repository, StoreSearchQuery(artist="Pink Floyd", title="Wish You Were Here"),
        adapter_factories={
            "one": lambda: Adapter("one", (offer("one", "1", 5000),), .08),
            "two": lambda: Adapter("two", (offer("two", "2", 4000),), .08),
        },
        per_store_timeout=1, global_timeout=1, progress=reports.append,
    )
    assert len(reports) == 2
    assert len(result.releases) == 1
    release = result.releases[0]
    assert release.lowest_price_offer and release.lowest_price_offer.price == Decimal("4000")
    assert {item.source for item in result.stores} == {"one", "two"}
    assert len(repository.price_history(1)) == 1


def test_live_statuses_explain_why_a_store_has_no_results():
    assert LiveStoreResult("one", StoreState.ACTIVE, 6).status_kind == "found"
    assert LiveStoreResult("one", StoreState.ACTIVE, 0).status_kind == "empty"
    assert LiveStoreResult("one", StoreState.DEGRADED, 4, status=StoreSearchStatus.CACHED).status_kind == "cached"
    assert LiveStoreResult("one", StoreState.DEGRADED, 0, status=StoreSearchStatus.UNSUPPORTED).status_kind == "unsupported"
    assert LiveStoreResult("one", StoreState.DEGRADED, 0, status=StoreSearchStatus.RESTRICTED).status_kind == "restricted"
    assert LiveStoreResult("one", StoreState.DEGRADED, 0, status=StoreSearchStatus.TIMEOUT).status_kind == "timeout"
    assert LiveStoreResult("one", StoreState.DEGRADED, 0, status=StoreSearchStatus.ERROR).status_kind == "error"


def test_live_ranking_happens_before_detail_enrichment_limit():
    broad = offer("one", "broad", 1000)
    broad = replace(broad, artist_raw="Space", title_raw="Space Revolver")
    exact = offer("one", "exact", 2000)
    exact = replace(exact, artist_raw="The Beatles", title_raw="Revolver")
    ranked = _rank_live_offers((broad, exact), StoreSearchQuery(title="Revolver"))
    assert [item.source_product_id for item in ranked] == ["exact", "broad"]


def test_timeout_and_degraded_store_do_not_hide_partial_results(tmp_path):
    repository = SQLiteRepository(tmp_path / "partial.sqlite3")
    result = live_search(
        repository, StoreSearchQuery(barcode="4006381333931"),
        adapter_factories={
            "fast": lambda: Adapter("fast", (offer("fast", "1", 5000),)),
            "slow": lambda: Adapter("slow", (offer("slow", "1", 3000),), .25),
            "blocked": lambda: Adapter("blocked", (), state=StoreState.DEGRADED),
        },
        per_store_timeout=.05, global_timeout=.2,
    )
    by_source = {item.source: item for item in result.stores}
    assert result.releases and by_source["fast"].offers == 1
    assert by_source["slow"].state == StoreState.DEGRADED
    assert by_source["blocked"].state == StoreState.DEGRADED


def test_live_search_does_not_call_full_catalogue(tmp_path):
    class SearchOnly(Adapter):
        def get_catalog(self):
            raise AssertionError("full catalogue must not run")
    result = live_search(
        SQLiteRepository(tmp_path / "targeted.sqlite3"), StoreSearchQuery(catalog_number="PFRLP9"),
        adapter_factories={"only": lambda: SearchOnly("only", (offer("only", "1", 5000),))},
    )
    assert result.stores[0].offers == 1


def test_live_search_can_target_only_unresolved_store_sources(tmp_path):
    result = live_search(
        SQLiteRepository(tmp_path / "one-store.sqlite3"), StoreSearchQuery(title="Wish You Were Here"),
        adapter_factories={
            "first": lambda: Adapter("first", (offer("first", "1", 5000),)),
            "second": lambda: Adapter("second", (offer("second", "1", 4000),)),
        },
        sources=("second",),
    )
    assert [item.source for item in result.stores] == ["second"]
    assert result.fresh_confirmed_offer_ids == result.fresh_offer_ids


def test_identifier_query_keeps_listing_without_id_when_detail_confirms_barcode(tmp_path):
    listing = offer("detail", "1", 5000, barcode=None)
    class DetailAdapter(Adapter):
        def enrich_offer(self, item):
            return replace(item, barcode="4006381333931", raw_data={"fully_enriched": True})
    result = live_search(
        SQLiteRepository(tmp_path / "barcode-ok.sqlite3"), StoreSearchQuery(barcode="4006381333931"),
        adapter_factories={"detail": lambda: DetailAdapter("detail", (listing,))},
    )
    assert result.stores[0].offers == 1
    assert result.releases and result.releases[0].barcode == normalize_barcode("4006381333931")


def test_identifier_query_rejects_listing_when_detail_disagrees_on_barcode(tmp_path):
    listing = offer("detail", "1", 5000, barcode=None)
    class DetailAdapter(Adapter):
        def enrich_offer(self, item):
            return replace(item, barcode="3770024955316", raw_data={"fully_enriched": True})
    result = live_search(
        SQLiteRepository(tmp_path / "barcode-reject.sqlite3"), StoreSearchQuery(barcode="4006381333931"),
        adapter_factories={"detail": lambda: DetailAdapter("detail", (listing,))},
    )
    assert result.stores[0].offers == 0
    assert result.releases == ()


def test_identifier_query_keeps_listing_without_id_when_detail_confirms_catalog(tmp_path):
    listing = offer("detail", "1", 5000, barcode=None, catalog=None)
    class DetailAdapter(Adapter):
        def enrich_offer(self, item):
            return replace(item, catalog_number_raw="PFRLP-9", raw_data={"fully_enriched": True})
    result = live_search(
        SQLiteRepository(tmp_path / "catalog-ok.sqlite3"), StoreSearchQuery(catalog_number="PFRLP9"),
        adapter_factories={"detail": lambda: DetailAdapter("detail", (listing,))},
    )
    assert result.stores[0].offers == 1
    assert result.releases and result.releases[0].catalog_number == "PFRLP-9"


def test_live_search_groups_pressings_and_exposes_best_price_and_live_deal(tmp_path):
    repository = SQLiteRepository(tmp_path / "end-to-end.sqlite3")
    query = StoreSearchQuery(artist="Pink Floyd", title="Wish You Were Here")
    result = live_search(
        repository, query,
        adapter_factories={
            "target": lambda: Adapter("target", (offer("target", "1", 3000),)),
            "one": lambda: Adapter("one", (offer("one", "1", 5000),)),
            "two": lambda: Adapter("two", (offer("two", "1", 5200),)),
            "three": lambda: Adapter("three", (offer("three", "1", 5400),)),
            # Same album title but a different pressing must remain separate.
            "different": lambda: Adapter("different", (offer("different", "1", 2900, barcode="3770024955316"),)),
        },
    )
    assert len(result.releases) == 2
    primary = next(item for item in result.releases if item.barcode == normalize_barcode("4006381333931"))
    assert primary.lowest_price_offer and primary.lowest_price_offer.price == Decimal("3000")
    target = next(item for item in primary.offers if item.store == "target")
    assert target.deal_class == DealClass.VERY_HOT
    assert target.discount_pct and target.discount_pct > 40


def test_live_search_uses_multiple_verified_production_adapters_without_catalogue(tmp_path):
    vinyl_json = Path("tests/fixtures/vinyl_ru/live_search.json").read_text(encoding="utf-8")
    vinyl_page = Path("tests/fixtures/vinyl_ru/live_search_album.html").read_text(encoding="utf-8")
    maximum_json = Path("tests/fixtures/wave2/maximum_vinyl_live_search.json").read_text(encoding="utf-8")
    imagine_html = Path("tests/fixtures/imagine_club/live_search_communique.html").read_text(encoding="utf-8")
    collectomania_html = Path("tests/fixtures/collectomania/live_search_communique.html").read_text(encoding="utf-8")

    def vinyl_factory():
        adapter = VinylRuAdapter()
        adapter._fetch_text = lambda url: vinyl_json if "smartSearch" in url else vinyl_page
        adapter.enrich_offer = lambda item: replace(item, artist_raw="Dire Straits", title_raw="Communique", barcode="4006381333931", catalog_number_raw="VERTIGO-6360", label="Vertigo", release_year=1979, country="UK", format="LP", disc_count=1, vinyl_size='12"', rpm=None, vinyl_color=None, edition_tags=(), condition_media="NEW", condition_sleeve="NEW", raw_data={"fully_enriched": True})
        adapter.get_catalog = lambda: (_ for _ in ()).throw(AssertionError("catalogue must not run"))
        return adapter

    def maximum_factory():
        adapter = MaximumVinylAdapter()
        adapter._fetch = lambda _url: maximum_json
        adapter.enrich_offer = lambda item: replace(
                item,
                artist_raw="Dire Straits",
                title_raw="Communique" if item.source_product_id == "8618" else item.title_raw,
                barcode="4006381333931" if item.source_product_id == "8618" else "3770024955316",
                catalog_number_raw="VERTIGO-6360" if item.source_product_id == "8618" else "VERTIGO-6361",
                label="Vertigo",
                release_year=1979 if item.source_product_id == "8618" else 1985,
                country="UK", format="LP" if item.source_product_id == "8618" else "2LP",
                disc_count=1 if item.source_product_id == "8618" else 2,
                vinyl_size='12"', rpm=None, vinyl_color=None, edition_tags=(),
                condition_media="NEW",
                condition_sleeve="NEW",
            raw_data={"fully_enriched": True},
        )
        adapter.get_catalog = lambda: (_ for _ in ()).throw(AssertionError("catalogue must not run"))
        return adapter

    def imagine_factory():
        adapter = ImagineClubAdapter()
        adapter._fetch = lambda _url: imagine_html
        original_search = adapter.search_offers
        def search_one(query):
            result = original_search(query)
            return replace(result, offers=result.offers[:1])
        adapter.search_offers = search_one
        adapter.enrich_offer = lambda item: replace(
            item,
            artist_raw="Dire Straits",
            title_raw="Communique",
            barcode="4006381333931",
                catalog_number_raw="VERTIGO-6360", label="Vertigo", release_year=1979, country="UK", format="LP", disc_count=1, vinyl_size='12"', rpm=None, vinyl_color=None, edition_tags=(),
            condition_media="NEW",
            condition_sleeve="NEW",
            raw_data={"fully_enriched": True},
        )
        adapter.get_catalog = lambda: (_ for _ in ()).throw(AssertionError("catalogue must not run"))
        return adapter

    def collectomania_factory():
        adapter = CollectomaniaAdapter()
        adapter._fetch = lambda _url: collectomania_html
        original_search = adapter.search_offers
        def search_one(query):
            result = original_search(query)
            # The public response also contains the band Communic; take the
            # exact Dire Straits product card that the search form returned.
            return replace(result, offers=result.offers[2:3])
        adapter.search_offers = search_one
        adapter.enrich_offer = lambda item: replace(
            item,
            artist_raw="Dire Straits",
            title_raw="Communique",
            barcode="4006381333931",
                catalog_number_raw="VERTIGO-6360", label="Vertigo", release_year=1979, country="UK", format="LP", disc_count=1, vinyl_size='12"', rpm=None, vinyl_color=None, edition_tags=(),
            condition_media="NEW",
            condition_sleeve="NEW",
            raw_data={"fully_enriched": True},
        )
        adapter.get_catalog = lambda: (_ for _ in ()).throw(AssertionError("catalogue must not run"))
        return adapter

    repository = SQLiteRepository(tmp_path / "production-adapters.sqlite3")
    result = live_search(
        repository, StoreSearchQuery(title="Communique"),
        adapter_factories={
            "vinyl_ru": vinyl_factory,
            "maximum_vinyl": maximum_factory,
            "imagine_club": imagine_factory,
            "collectomania": collectomania_factory,
        },
    )
    assert {store.source: store.offers for store in result.stores} == {
        "vinyl_ru": 1,
        "maximum_vinyl": 2,
        "imagine_club": 1,
        "collectomania": 1,
    }
    # The second Maximum Vinyl result is a different pressing and remains a
    # distinct Release even though the live-search responses share a title.
    assert len(repository.releases_for_search()) == 2
    pressing = next(item for item in result.releases if item.barcode == normalize_barcode("4006381333931"))
    assert {offer.store for offer in pressing.offers} == {"vinyl_ru", "maximum_vinyl", "imagine_club", "collectomania"}
    assert pressing.lowest_price_offer is not None
    target = pressing.lowest_price_offer
    deal = evaluate_offer(repository, target.offer_id)
    assert deal is not None
    comparison_prices = [offer.price for offer in pressing.offers if offer.store != target.store and offer.price is not None]
    # The benchmark deliberately excludes all cards from the target store.
    assert deal.comparable_count == 3
    assert deal.market_median == median_price(comparison_prices)
    assert deal.discount_pct == (deal.market_median - target.price) / deal.market_median * 100


def test_live_search_uses_real_product_enrichment_for_production_adapters(tmp_path):
    """Search and detail parsing both run from saved public responses."""
    imagine_search = Path("tests/fixtures/imagine_club/live_search_communique.html").read_text(encoding="utf-8")
    imagine_product = Path("tests/fixtures/imagine_club/live_search_communique_product.html").read_text(encoding="utf-8")
    drhead_search = Path("tests/fixtures/drhead/live_search_communique.html").read_text(encoding="utf-8")
    drhead_product = Path("tests/fixtures/drhead/live_search_communique_product.html").read_text(encoding="utf-8")

    def imagine_factory():
        adapter = ImagineClubAdapter()
        adapter._fetch = lambda url: imagine_search if "/search" in url else imagine_product
        adapter.get_catalog = lambda: (_ for _ in ()).throw(AssertionError("catalogue must not run"))
        return adapter

    def drhead_factory():
        adapter = DrHeadAdapter()
        adapter._fetch = lambda url: drhead_search if "/search/" in url else drhead_product
        adapter.get_catalog = lambda: (_ for _ in ()).throw(AssertionError("catalogue must not run"))
        return adapter

    repository = SQLiteRepository(tmp_path / "detail-enrichment.sqlite3")
    result = live_search(
        repository,
        StoreSearchQuery(title="Communique"),
        adapter_factories={"imagine_club": imagine_factory, "drhead": drhead_factory},
        enrichment_limit=1,
    )

    assert {store.source: store.offers for store in result.stores} == {"imagine_club": 1, "drhead": 1}
    persisted = {offer.source: offer for _, offer in repository.offers_for_matching()}
    assert persisted["imagine_club"].barcode == normalize_barcode("602537529049")
    assert persisted["imagine_club"].raw_data["detail_fields"]["лейбл"] == "Universal"
    assert persisted["drhead"].raw_data["public_card"] is True
