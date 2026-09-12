from datetime import datetime, timezone
from decimal import Decimal

from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.pricing import evaluate_offer
from vinyl_deals.search import search_offers


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
