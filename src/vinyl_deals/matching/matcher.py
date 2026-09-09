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

class Comparison(StrEnum):
    MATCH = "match"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"

@dataclass(frozen=True, slots=True)
class MatchResult:
    kind: MatchKind
    confidence: float
    matched_features: tuple[str, ...] = ()
    blocking_conflicts: tuple[str, ...] = ()
    soft_conflicts: tuple[str, ...] = ()
    unknown_features: tuple[str, ...] = ()

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.matched_features + self.blocking_conflicts + self.soft_conflicts

def compare(left: object, right: object) -> Comparison:
    if left in (None, "", (), frozenset()) or right in (None, "", (), frozenset()):
        return Comparison.UNKNOWN
    return Comparison.MATCH if normalize.text(str(left)) == normalize.text(str(right)) else Comparison.CONFLICT

def match_offers(left: RawOffer, right: RawOffer) -> MatchResult:
    matches, blocking, soft, unknown = [], [], [], []
    artist = compare(left.artist_raw, right.artist_raw)
    title = compare(left.title_raw, right.title_raw)
    left_barcode, right_barcode = normalize.normalize_barcode(left.barcode), normalize.normalize_barcode(right.barcode)
    if left_barcode and right_barcode:
        (matches if left_barcode == right_barcode else blocking).append("barcode matches" if left_barcode == right_barcode else "barcode differs")
    elif left.barcode or right.barcode:
        unknown.append("barcode is absent or invalid")
    for attr in ("artist", "title", "format", "disc_count", "vinyl_size", "rpm", "vinyl_color"):
        a = getattr(left, f"{attr}_raw") if attr in {"artist", "title"} else getattr(left, attr)
        b = getattr(right, f"{attr}_raw") if attr in {"artist", "title"} else getattr(right, attr)
        state = compare(a, b)
        if state == Comparison.MATCH: matches.append(f"{attr} matches")
        elif state == Comparison.CONFLICT: blocking.append(f"{attr} differs")
        else: unknown.append(attr)
    tag_state = _compare_tags(left, right)
    if tag_state == Comparison.CONFLICT: blocking.append("edition type differs")
    elif tag_state == Comparison.MATCH: matches.append("edition tags match")
    else: unknown.append("edition tags")
    for attr in ("country", "release_year"):
        state = compare(getattr(left, attr), getattr(right, attr))
        if state == Comparison.MATCH: matches.append(f"{attr} matches")
        elif state == Comparison.CONFLICT: soft.append(f"{attr} differs")
        else: unknown.append(attr)
    if blocking:
        return MatchResult(MatchKind.DIFFERENT, 0.0, tuple(matches), tuple(blocking), tuple(soft), tuple(unknown))
    same_artist_title = artist == title == Comparison.MATCH
    if left_barcode and left_barcode == right_barcode and same_artist_title:
        return MatchResult(MatchKind.EXACT_BARCODE, 0.99, tuple(matches), (), tuple(soft), tuple(unknown))
    left_catalog, right_catalog = normalize.catalog_number(left.catalog_number_raw), normalize.catalog_number(right.catalog_number_raw)
    same_label = normalize.text(left.label) and normalize.text(left.label) == normalize.text(right.label)
    if left_catalog and left_catalog == right_catalog and same_label and same_artist_title:
        return MatchResult(MatchKind.CATALOG_AND_LABEL, 0.95, tuple(matches), (), tuple(soft), tuple(unknown))
    if not same_artist_title:
        return MatchResult(MatchKind.DIFFERENT, 0.0, tuple(matches), ("artist/title are not both confirmed",), tuple(soft), tuple(unknown))
    weights = {"format matches": .08, "disc_count matches": .08, "vinyl_size matches": .04, "rpm matches": .05, "vinyl_color matches": .04, "edition tags match": .04, "country matches": .04, "release_year matches": .05}
    score = .60 + sum(weights.get(item, 0) for item in matches) - .12 * len(soft)
    if score >= .90 and not soft:
        return MatchResult(MatchKind.WEIGHTED, min(score, .98), tuple(matches), (), (), tuple(unknown))
    return MatchResult(MatchKind.POSSIBLE, max(score, .0), tuple(matches), (), tuple(soft), tuple(unknown))

def _compare_tags(left: RawOffer, right: RawOffer) -> Comparison:
    a, b = normalize.tags(left.edition_tags, left.edition_raw), normalize.tags(right.edition_tags, right.edition_raw)
    if not a or not b:
        return Comparison.UNKNOWN
    groups = ({"mono", "stereo"}, {"picture_disc", "black"}, {"box_set", "single"}, {"colored", "black"})
    if any(a & group and b & group and (a & group) != (b & group) for group in groups):
        return Comparison.CONFLICT
    return Comparison.MATCH if a == b else Comparison.UNKNOWN
