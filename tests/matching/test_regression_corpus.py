"""A compact, extensible corpus of independently specified matching scenarios."""
from vinyl_deals.domain import RawOffer
from vinyl_deals.matching import MatchKind, match_offers

def offer(identifier, **values): return RawOffer.now(source=f"s{identifier}", source_product_id=str(identifier), url="https://x", **{"artist_raw": "Artist", "title_raw": "Album", **values})

SAME = [
    ({"barcode": "4006381333931"}, {"barcode": "4006381333931"}),
    ({"barcode": "036000291452"}, {"barcode": "036 000-291 452"}),
    ({"barcode": "4006381333931", "artist_raw": "A & B"}, {"barcode": "4006381333931", "artist_raw": "A and B"}),
    ({"barcode": "4006381333931", "title_raw": "Album — Live"}, {"barcode": "4006381333931", "title_raw": "Album - Live"}),
    ({"catalog_number_raw": "CAT-1", "label": "Label"}, {"catalog_number_raw": "cat 1", "label": "Label"}),
    ({"catalog_number_raw": "ABC/12.34", "label": "Label"}, {"catalog_number_raw": "abc-1234", "label": "Label"}),
    ({"barcode": "4006381333931", "edition_tags": ("limited",)}, {"barcode": "4006381333931", "edition_tags": ("limited",)}),
    ({"barcode": "4006381333931", "disc_count": 2}, {"barcode": "4006381333931", "disc_count": 2}),
]
DIFFERENT = [
    ({"barcode": "4006381333931"}, {"barcode": "5901234123457"}),
    ({"barcode": "4006381333931", "artist_raw": "Other"}, {"barcode": "4006381333931"}),
    ({"artist_raw": "Other"}, {}), ({"title_raw": "Other Album"}, {}),
    ({"disc_count": 1}, {"disc_count": 2}), ({"disc_count": 2}, {"disc_count": 3}),
    ({"format": "LP"}, {"format": '12 inch single'}), ({"format": "LP"}, {"format": '7"'}),
    ({"rpm": 33}, {"rpm": 45}), ({"vinyl_color": "black"}, {"vinyl_color": "red"}),
    ({"vinyl_color": "red"}, {"vinyl_color": "blue"}),
    ({"edition_tags": ("mono",)}, {"edition_tags": ("stereo",)}),
    ({"edition_tags": ("picture disc",)}, {"edition_tags": ("standard",)}),
    ({"edition_tags": ("box set",)}, {"edition_tags": ("single LP",)}),
    ({"barcode": "4006381333931", "format": "LP"}, {"barcode": "4006381333931", "format": "CD"}),
    ({"barcode": "4006381333931", "rpm": 33}, {"barcode": "4006381333931", "rpm": 45}),
    ({"barcode": "4006381333931", "release_year": 2016}, {"barcode": "4006381333931", "release_year": 2023}),
    ({"catalog_number_raw": "CAT-2", "label": "Label", "country": "EU"}, {"catalog_number_raw": "cat 2", "label": "Label", "country": "US"}),
]
UNCERTAIN = [
    ({}, {}),
    ({"barcode": "123"}, {"barcode": "123"}),
    ({"edition_tags": ("180g",)}, {"edition_tags": ()}),
    ({"vinyl_color": "red"}, {}),
    ({"release_year": 2016}, {"release_year": 2023}),
    ({"country": "EU"}, {"country": "US"}),
    ({"edition_tags": ("rsd",)}, {}),
    ({"catalog_number_raw": "A"}, {"catalog_number_raw": "B"}),
    ({"edition_tags": ("deluxe",)}, {"edition_tags": ("standard",)}),
    ({"edition_raw": "Original pressing"}, {"edition_raw": "2023 repress"}),
    ({"edition_tags": ("rsd",)}, {"edition_tags": ("rsd",)}),
    ({"catalog_number_raw": "CAT-1", "label": "Label A"}, {"catalog_number_raw": "CAT-1", "label": "Label B"}),
    ({"catalog_number_raw": "CAT-1", "label": "Label"}, {"catalog_number_raw": "CAT-2", "label": "Label"}),
    ({"catalog_number_raw": "CAT-1"}, {"catalog_number_raw": "CAT-1", "label": "Label"}),
    ({"artist_raw": None}, {}), ({"title_raw": None}, {}),
    ({"edition_tags": ("picture disc",)}, {}),
    ({"edition_tags": ("rsd",)}, {"edition_tags": ("standard",)}),
    ({"edition_tags": ("remaster",)}, {}),
    ({"edition_tags": ("remaster",), "release_year": 2016}, {"edition_raw": "2023 repress", "release_year": 2023}),
    ({"country": "EU"}, {"country": "Germany"}),
    ({"barcode": "123456"}, {"barcode": "123456"}),
    ({"edition_tags": ("deluxe",)}, {"edition_tags": ("limited",)}),
    ({"format": "LP"}, {"format": None}),
]
CASES = [("SAME_RELEASE", pair) for pair in SAME] + [("DIFFERENT_RELEASE", pair) for pair in DIFFERENT] + [("UNCERTAIN", pair) for pair in UNCERTAIN]

def test_corpus_has_at_least_fifty_cases(): assert len(CASES) >= 50

def test_regression_corpus():
    for expected, (left, right) in CASES:
        result = match_offers(offer(1, **left), offer(2, **right))
        if expected == "SAME_RELEASE": assert result.kind in {MatchKind.EXACT_BARCODE, MatchKind.CATALOG_AND_LABEL}
        elif expected == "DIFFERENT_RELEASE": assert result.kind == MatchKind.DIFFERENT
        else: assert result.kind in {MatchKind.POSSIBLE, MatchKind.DIFFERENT}
