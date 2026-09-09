from vinyl_deals.domain import RawOffer
from vinyl_deals.matching import MatchKind, match_offers

def offer(**values): return RawOffer.now(source="test", source_product_id=values.pop("source_product_id", "1"), url="https://example.test", artist_raw="Pink Floyd", title_raw="Animals", **values)
def test_identical_barcodes_are_exact_unless_metadata_conflicts(): assert match_offers(offer(barcode="4006381333931"), offer(source_product_id="2", barcode="4006381333931")).kind == MatchKind.EXACT_BARCODE; assert match_offers(offer(barcode="4006381333931", disc_count=1), offer(source_product_id="2", barcode="4006381333931", disc_count=2)).kind == MatchKind.DIFFERENT
def test_artist_and_title_alone_stays_possible(): assert match_offers(offer(), offer(source_product_id="2")).kind == MatchKind.POSSIBLE
def test_catalog_and_label_match_is_high_confidence(): assert match_offers(offer(catalog_number_raw="PFR-LP10", label="Pink Floyd Records"), offer(source_product_id="2", catalog_number_raw="PFRLP10", label="Pink Floyd Records")).kind == MatchKind.CATALOG_AND_LABEL
