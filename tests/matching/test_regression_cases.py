from vinyl_deals.domain import RawOffer
from vinyl_deals.matching import MatchKind, match_offers

def item(**values): return RawOffer.now(source="t", source_product_id=values.pop("id", "1"), url="https://x", artist_raw="Artist", title_raw="Album", **values)

def test_critical_pressing_conflicts_never_auto_match():
    cases = [({"disc_count": 1}, {"disc_count": 2}), ({"format": "LP"}, {"format": '7"'}), ({"rpm": 33}, {"rpm": 45}), ({"vinyl_color": "black"}, {"vinyl_color": "red"}), ({"edition_tags": ("mono",)}, {"edition_tags": ("stereo",)}), ({"edition_tags": ("picture disc",)}, {"edition_tags": ("standard",)})]
    for left, right in cases:
        assert match_offers(item(**left), item(id="2", **right)).kind == MatchKind.DIFFERENT
