from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from vinyl_deals.domain import RawOffer
from . import normalize

class MatchKind(StrEnum):
    EXACT_BARCODE = "exact_barcode"
    CATALOG_AND_LABEL = "catalog_and_label"
    WEIGHTED = "weighted"
    POSSIBLE = "possible"
    DIFFERENT = "different"

class ManualDecision(StrEnum):
    SAME_RELEASE = "same_release"
    DIFFERENT_RELEASE = "different_release"
    IGNORE = "ignore"

@dataclass(frozen=True, slots=True)
class MatchResult:
    kind: MatchKind
    confidence: float
    reasons: tuple[str, ...]

def match_offers(left: RawOffer, right: RawOffer) -> MatchResult:
    conflicts = _conflicts(left, right)
    same_artist_title = normalize.text(left.artist_raw) == normalize.text(right.artist_raw) and normalize.text(left.title_raw) == normalize.text(right.title_raw)
    left_barcode, right_barcode = normalize.barcode(left.barcode), normalize.barcode(right.barcode)
    if left_barcode and right_barcode and left_barcode == right_barcode and not conflicts:
        return MatchResult(MatchKind.EXACT_BARCODE, 0.99, ("matching barcode",))
    if conflicts:
        return MatchResult(MatchKind.DIFFERENT, 0.0, tuple(conflicts))
    left_catalog, right_catalog = normalize.catalog_number(left.catalog_number_raw), normalize.catalog_number(right.catalog_number_raw)
    same_label = normalize.text(left.label) and normalize.text(left.label) == normalize.text(right.label)
    if left_catalog and left_catalog == right_catalog and same_label and same_artist_title:
        return MatchResult(MatchKind.CATALOG_AND_LABEL, 0.95, ("catalog number, label, artist and title match",))
    if not same_artist_title:
        return MatchResult(MatchKind.DIFFERENT, 0.0, ("artist or title differs",))
    score, reasons = 0.60, ["artist and title match"]
    for attr, weight in (("country", .08), ("release_year", .08), ("format", .08), ("disc_count", .08), ("vinyl_color", .04)):
        a, b = getattr(left, attr), getattr(right, attr)
        if a and b and normalize.text(str(a)) == normalize.text(str(b)):
            score += weight; reasons.append(f"{attr} matches")
    if normalize.tags(left.edition_tags, left.edition_raw) == normalize.tags(right.edition_tags, right.edition_raw): score += .04
    if score >= .90: return MatchResult(MatchKind.WEIGHTED, min(score, .98), tuple(reasons))
    return MatchResult(MatchKind.POSSIBLE, score, tuple(reasons))

def _conflicts(left: RawOffer, right: RawOffer) -> list[str]:
    failures = []
    a, b = normalize.barcode(left.barcode), normalize.barcode(right.barcode)
    if a and b and a != b: failures.append("barcode differs")
    a, b = normalize.catalog_number(left.catalog_number_raw), normalize.catalog_number(right.catalog_number_raw)
    if a and b and a != b and normalize.text(left.label) == normalize.text(right.label): failures.append("catalog number differs for same label")
    for attr in ("format", "disc_count", "rpm", "vinyl_color"):
        a, b = getattr(left, attr), getattr(right, attr)
        if a and b and normalize.text(str(a)) != normalize.text(str(b)): failures.append(f"{attr} differs")
    if normalize.tags(left.edition_tags, left.edition_raw) != normalize.tags(right.edition_tags, right.edition_raw): failures.append("edition tags differ")
    return failures
