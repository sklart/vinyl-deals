from datetime import datetime, timezone
from decimal import Decimal

from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.pricing import evaluate_offer
from vinyl_deals.search import search_offers, search_releases


def test_manual_price_has_explicit_provenance_and_is_not_a_deal_or_alert_input(tmp_path):
    repository = SQLiteRepository(tmp_path / "manual.sqlite3")
    repository.upsert_offer(RawOffer.now(
        source="seed", source_product_id="1", url="https://seed.test/1", artist_raw="Opeth",
        title_raw="Blackwater Park", barcode="4006381333931", price=Decimal("5000"),
        availability=Availability.IN_STOCK, condition_media="NEW",
    ))
    build_match_queue(repository); repository.ensure_releases_for_unmatched_offers()
    release = repository.releases_for_search()[0]
    offer_id = repository.add_manual_offer(
        release.id, source="onlinetrade", url="https://www.onlinetrade.ru/product/1",
        price=Decimal("3990"), availability=Availability.IN_STOCK,
        checked_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    assigned, manual = repository.offer_by_id(offer_id)
    assert assigned == release.id and manual.provenance == "MANUAL"
    assert manual.raw_data["provenance"] == "MANUAL"
    assert evaluate_offer(repository, offer_id) is None
    assert any(item.offer_id == offer_id and item.provenance == "MANUAL" for item in search_offers(repository, release.id))


def test_manual_price_is_visible_but_cannot_change_automatic_market_summary(tmp_path):
    repository = SQLiteRepository(tmp_path / "manual-summary.sqlite3")
    for source, product, price in (("target", "1", "4000"), ("market-a", "2", "5000"), ("market-b", "3", "5200")):
        repository.upsert_offer(RawOffer.now(
            source=source, source_product_id=product, url=f"https://{source}.test/{product}",
            artist_raw="Opeth", title_raw="Blackwater Park", barcode="4006381333931",
            price=Decimal(price), availability=Availability.IN_STOCK, condition_media="NEW",
        ))
    build_match_queue(repository)
    repository.ensure_releases_for_unmatched_offers()
    release = repository.releases_for_search()[0]
    before = search_releases(repository, title="Blackwater Park")[0]
    repository.add_manual_offer(
        release.id, source="manual-store", url="https://manual.example/offer", price=Decimal("100"),
        availability=Availability.IN_STOCK,
    )
    after = search_releases(repository, title="Blackwater Park")[0]

    assert before.lowest_price_offer.price == after.lowest_price_offer.price == Decimal("4000")
    assert before.market_median == after.market_median == Decimal("5100")
    assert before.discount_pct == after.discount_pct
    assert after.lowest_manual_offer and after.lowest_manual_offer.price == Decimal("100")
    assert all(item.provenance == "AUTOMATIC" for item in (after.lowest_price_offer, after.best_new_offer))
