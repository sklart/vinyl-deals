"""High-precision candidate retrieval; no fuzzy-only candidates."""
from __future__ import annotations
from collections import defaultdict
from vinyl_deals.domain import RawOffer
from . import normalize

class CandidateIndex:
    def __init__(self, offers: list[tuple[int, RawOffer]]) -> None:
        self.by_id = dict(offers)
        self.barcode: dict[str, set[int]] = defaultdict(set)
        self.catalog: dict[tuple[str, str], set[int]] = defaultdict(set)
        self.artist_title: dict[tuple[str, str], set[int]] = defaultdict(set)
        for identifier, offer in offers:
            if value := normalize.normalize_barcode(offer.barcode): self.barcode[value].add(identifier)
            catalog, label = normalize.catalog_number(offer.catalog_number_raw), normalize.text(offer.label)
            if catalog and label: self.catalog[(catalog, label)].add(identifier)
            key = (normalize.text(offer.artist_raw), normalize.text(offer.title_raw))
            if all(key): self.artist_title[key].add(identifier)

    def candidates(self, identifier: int, offer: RawOffer) -> list[tuple[int, RawOffer]]:
        ids: set[int] = set()
        if value := normalize.normalize_barcode(offer.barcode): ids |= self.barcode[value]
        catalog, label = normalize.catalog_number(offer.catalog_number_raw), normalize.text(offer.label)
        if catalog and label: ids |= self.catalog[(catalog, label)]
        key = (normalize.text(offer.artist_raw), normalize.text(offer.title_raw))
        if all(key): ids |= self.artist_title[key]
        return [(candidate_id, self.by_id[candidate_id]) for candidate_id in ids if candidate_id != identifier and not (self.by_id[candidate_id].source == offer.source and self.by_id[candidate_id].source_product_id == offer.source_product_id)]

def candidates_for(needle: RawOffer, offers: list[RawOffer]) -> list[RawOffer]:
    indexed = CandidateIndex(list(enumerate(offers)))
    return [offer for _, offer in indexed.candidates(0, needle)]
