"""High-precision candidate retrieval; no fuzzy-only candidates."""
from __future__ import annotations
from collections import defaultdict
from vinyl_deals.domain import RawOffer
from . import normalize

def candidates_for(needle: RawOffer, offers: list[RawOffer]) -> list[RawOffer]:
    barcode, catalog = normalize.barcode(needle.barcode), normalize.catalog_number(needle.catalog_number_raw)
    artist_title = (normalize.text(needle.artist_raw), normalize.text(needle.title_raw))
    selected: list[RawOffer] = []
    for offer in offers:
        if offer.source == needle.source and offer.source_product_id == needle.source_product_id: continue
        same_barcode = barcode and barcode == normalize.barcode(offer.barcode)
        same_catalog = catalog and catalog == normalize.catalog_number(offer.catalog_number_raw) and normalize.text(needle.label) == normalize.text(offer.label)
        same_artist_title = artist_title == (normalize.text(offer.artist_raw), normalize.text(offer.title_raw)) and all(artist_title)
        if same_barcode or same_catalog or same_artist_title: selected.append(offer)
    return selected
