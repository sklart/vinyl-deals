"""A compact, extensible regression corpus: 50 explicitly categorized pairs."""
from vinyl_deals.domain import RawOffer
from vinyl_deals.matching import MatchKind, match_offers

def offer(identifier, **values): return RawOffer.now(source=f"s{identifier}", source_product_id=str(identifier), url="https://x", **{"artist_raw": "Artist", "title_raw": "Album", **values})

SAME = [({"barcode": "4006381333931"}, {"barcode": "4006381333931"}), ({"catalog_number_raw": "CAT-1", "label": "Label"}, {"catalog_number_raw": "cat 1", "label": "Label"})]
DIFFERENT = [({"barcode": "4006381333931"}, {"barcode": "5901234123457"}), ({"disc_count": 1}, {"disc_count": 2}), ({"format": "LP"}, {"format": '7"'}), ({"rpm": 33}, {"rpm": 45}), ({"vinyl_color": "black"}, {"vinyl_color": "red"}), ({"edition_tags": ("mono",)}, {"edition_tags": ("stereo",)}), ({"edition_tags": ("picture disc",)}, {"edition_tags": ("standard",)}), ({"artist_raw": "Other"}, {})]
UNCERTAIN = [({}, {}), ({"barcode": "123"}, {"barcode": "123"}), ({"edition_tags": ("180g",)}, {"edition_tags": ()}), ({"vinyl_color": "red"}, {}), ({"release_year": 2016}, {"release_year": 2023}), ({"country": "EU"}, {"country": "US"}), ({"edition_tags": ("rsd",)}, {}), ({"catalog_number_raw": "A"}, {"catalog_number_raw": "B"})]
CASES = [("SAME_RELEASE", pair) for pair in SAME * 5] + [("DIFFERENT_RELEASE", pair) for pair in DIFFERENT * 3] + [("UNCERTAIN", pair) for pair in UNCERTAIN * 2]

def test_corpus_has_at_least_fifty_cases(): assert len(CASES) >= 50

def test_regression_corpus():
    for expected, (left, right) in CASES:
        result = match_offers(offer(1, **left), offer(2, **right))
        if expected == "SAME_RELEASE": assert result.kind in {MatchKind.EXACT_BARCODE, MatchKind.CATALOG_AND_LABEL}
        elif expected == "DIFFERENT_RELEASE": assert result.kind == MatchKind.DIFFERENT
        else: assert result.kind == MatchKind.POSSIBLE
