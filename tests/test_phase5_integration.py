from datetime import datetime, timezone
from decimal import Decimal

from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.search import search_releases


def test_new_store_offers_share_release_and_search_selects_lowest_product_price(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "phase5.sqlite3")
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    for source, product_id, price, delivery in (("audiomania", "AM-100", "5990", "1000"), ("respublica", "1544912", "5500", "300"), ("drhead", "162248", "6200", None)):
        repository.upsert_offer(RawOffer(
            source=source, source_product_id=product_id, url=f"https://example.test/{source}/{product_id}", fetched_at=now,
            artist_raw="Opeth", title_raw="Blackwater Park", barcode="4006381333931", catalog_number_raw="MOVLP001",
            label="Music On Vinyl", release_year=2021, format="2LP", condition_media="NEW", price=Decimal(price), delivery_cost=Decimal(delivery) if delivery else None, availability=Availability.IN_STOCK,
        ))
    build_match_queue(repository)
    results = search_releases(repository, artist="Opeth", title="Blackwater Park", now=now)
    assert len(results) == 1
    result = results[0]
    assert {offer.store for offer in result.offers} == {"audiomania", "respublica", "drhead"}
    assert (result.lowest_price_offer.store, result.lowest_price_offer.price) == ("respublica", Decimal("5500"))
    assert (result.best_new_offer.store, result.best_new_offer.price) == ("respublica", Decimal("5500"))
    assert (result.best_effective_offer.store, result.best_effective_offer.effective_price) == ("respublica", Decimal("5800"))
