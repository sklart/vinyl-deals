from __future__ import annotations
import re
import unicodedata

def text(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold().replace("&", " and ")
    value = value.replace("–", "-").replace("—", "-").replace("’", "'")
    return re.sub(r"\s+", " ", re.sub(r"[\"'`]+", "", value)).strip()

def catalog_number(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", text(value))

def barcode(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def is_valid_gtin(value: str | None) -> bool:
    """Validate the check digit of EAN-8, UPC-A, EAN-13 or GTIN-14."""
    digits = barcode(value)
    if len(digits) not in {8, 12, 13, 14}:
        return False
    checksum = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(digits[:-1])))
    return (10 - checksum % 10) % 10 == int(digits[-1])


def normalize_barcode(value: str | None) -> str | None:
    digits = barcode(value)
    if not is_valid_gtin(digits):
        return None
    # GTIN-12/UPC and EAN-13 are representations of the same GTIN namespace.
    # Persisting and matching the left-padded GTIN-14 prevents a shop's UPC
    # formatting from splitting an otherwise identical pressing.
    return digits.zfill(14)


def format_and_disc_count(value: str | None, disc_count: int | None = None) -> tuple[str | None, int | None]:
    """Canonicalise record count separately from the physical format label."""
    raw = text(value).upper()
    match = re.fullmatch(r"(\d+)\s*LP", raw)
    if match:
        return "LP", int(match.group(1))
    if raw == "LP":
        return "LP", disc_count or 1
    if raw == "EP":
        return "EP", disc_count
    return (value.strip().upper() if value and len(value.strip()) <= 40 else None), disc_count

def tags(value: tuple[str, ...], edition_raw: str | None = None) -> frozenset[str]:
    source = " ".join((*value, edition_raw or ""))
    vocabulary = {"remaster": ("remaster",), "mono": ("mono",), "stereo": ("stereo",), "deluxe": ("deluxe",), "limited": ("limited",), "anniversary": ("anniversary",), "colored": ("colored", "colour", "color vinyl", "coloured vinyl"), "black": ("black vinyl", "standard"), "picture_disc": ("picture disc",), "180g": ("180g",), "45rpm": ("45 rpm", "45rpm"), "rsd": ("rsd", "record store day"), "box_set": ("box set", "box"), "single": ("single lp",), "audiophile": ("audiophile",)}
    normalized = text(source).replace("180 g", "180g")
    return frozenset(name for name, aliases in vocabulary.items() if any(alias in normalized for alias in aliases))
