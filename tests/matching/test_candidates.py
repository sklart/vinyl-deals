from vinyl_deals.domain import RawOffer
from vinyl_deals.matching.candidates import candidates_for

def item(id, **values):
    return RawOffer.now(source="s", source_product_id=id, url="https://x", **{"artist_raw": "Artist", "title_raw": "Album", **values})
def test_candidates_include_only_indexable_signals():
    needle = item("1", barcode="123")
    assert [x.source_product_id for x in candidates_for(needle, [needle, item("2", barcode="123"), item("3", artist_raw="Other", title_raw="Else")])] == ["2"]
