from __future__ import annotations
from vinyl_deals.database.repository import SQLiteRepository
from .candidates import candidates_for
from .matcher import MatchKind, match_offers

def build_match_queue(repository: SQLiteRepository) -> int:
    indexed = repository.offers_for_matching(); recorded, seen = 0, set()
    for offer_id, offer in indexed:
        for candidate in candidates_for(offer, [item for _, item in indexed]):
            candidate_id = next(identifier for identifier, item in indexed if item is candidate)
            pair = tuple(sorted((offer_id, candidate_id)))
            if pair in seen: continue
            seen.add(pair); result = match_offers(offer, candidate)
            if result.kind != MatchKind.DIFFERENT:
                repository.record_match(pair[0], pair[1], result.kind, result.confidence, result.reasons); recorded += 1
                if result.kind in {MatchKind.EXACT_BARCODE, MatchKind.CATALOG_AND_LABEL, MatchKind.WEIGHTED} and result.confidence >= .90:
                    repository.create_release_for_pair(pair[0], pair[1], offer, candidate)
    return recorded
