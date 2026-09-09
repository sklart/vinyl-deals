from __future__ import annotations
from vinyl_deals.database.repository import SQLiteRepository
from .candidates import CandidateIndex
from .matcher import MatchKind, match_offers

def build_match_queue(repository: SQLiteRepository) -> int:
    indexed = repository.offers_for_matching(); recorded, seen = 0, set(); candidates = CandidateIndex(indexed)
    for offer_id, offer in indexed:
        for candidate_id, candidate in candidates.candidates(offer_id, offer):
            pair = tuple(sorted((offer_id, candidate_id)))
            if pair in seen: continue
            seen.add(pair)
            if repository.manual_decision(*pair) in {"different_release", "ignore"}:
                continue
            result = match_offers(offer, candidate)
            if result.kind != MatchKind.DIFFERENT:
                repository.record_match(pair[0], pair[1], result.kind, result.confidence, result.reasons); recorded += 1
                if result.kind in {MatchKind.EXACT_BARCODE, MatchKind.CATALOG_AND_LABEL, MatchKind.WEIGHTED} and result.confidence >= .90:
                    repository.create_release_for_pair(pair[0], pair[1], offer, candidate)
    return recorded
