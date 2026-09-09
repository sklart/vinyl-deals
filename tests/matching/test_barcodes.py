from vinyl_deals.domain import RawOffer
from vinyl_deals.matching import MatchKind, match_offers
from vinyl_deals.matching.normalize import is_valid_gtin, normalize_barcode

def offer(**values): return RawOffer.now(source="t", source_product_id=values.pop("id", "1"), url="https://t", **{"artist_raw": "Artist", "title_raw": "Album", **values})
def test_gtin_validation_and_cleaning():
    assert is_valid_gtin("4006381333931") and is_valid_gtin("036000291452")
    assert normalize_barcode("4006-3813 33931") == "4006381333931"
    assert not is_valid_gtin("4006381333932") and not is_valid_gtin("123456")
def test_invalid_equal_barcode_is_not_exact():
    assert match_offers(offer(barcode="123"), offer(id="2", barcode="123")).kind != MatchKind.EXACT_BARCODE
def test_valid_barcode_with_different_artist_is_never_exact():
    assert match_offers(offer(barcode="4006381333931"), offer(id="2", barcode="4006381333931", artist_raw="Other")).kind == MatchKind.DIFFERENT
