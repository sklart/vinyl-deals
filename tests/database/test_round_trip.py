from decimal import Decimal
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer

def test_raw_offer_round_trip_preserves_all_meaningful_fields(tmp_path):
    offer = RawOffer.now(source="s", source_product_id="p", url="https://x", store_sku="shop-1", artist_raw="Artist", title_raw="Title", edition_raw="Limited", price=Decimal("12.50"), old_price=Decimal("15"), currency="RUB", availability=Availability.IN_STOCK, stock_quantity=2, stock_text="two", city="Moscow", pickup_available=True, delivery_available=False, condition_media="NM", condition_sleeve="EX", format="LP", vinyl_size='12"', rpm=33, disc_count=2, label="Label", catalog_number_raw="CAT-1", barcode="4006381333931", release_year=2024, country="EU", vinyl_color="red", edition_tags=("limited",), description="notes", image_url="https://image", raw_data={"source": True})
    repository = SQLiteRepository(tmp_path / "round.sqlite3"); repository.upsert_offer(offer)
    _, restored = repository.offers_for_matching()[0]
    assert restored == offer
