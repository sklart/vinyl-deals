from vinyl_deals.domain import RawOffer
from vinyl_deals.matching.candidates import CandidateIndex

def test_candidate_index_handles_thousands_without_full_scan_per_offer():
    offers = [(index, RawOffer.now(source=f"s{index % 3}", source_product_id=str(index), url="https://x", artist_raw=f"Artist {index}", title_raw=f"Album {index}")) for index in range(5_000)]
    offers.extend([(5_001, RawOffer.now(source="a", source_product_id="same-a", url="https://a", artist_raw="Shared", title_raw="Album")), (5_002, RawOffer.now(source="b", source_product_id="same-b", url="https://b", artist_raw="Shared", title_raw="Album"))])
    index = CandidateIndex(offers)
    assert [identifier for identifier, _ in index.candidates(5_001, offers[-2][1])] == [5_002]
