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
    return digits if is_valid_gtin(digits) else None

def tags(value: tuple[str, ...], edition_raw: str | None = None) -> frozenset[str]:
    source = " ".join((*value, edition_raw or ""))
    vocabulary = {"remaster": ("remaster",), "mono": ("mono",), "stereo": ("stereo",), "deluxe": ("deluxe",), "limited": ("limited",), "anniversary": ("anniversary",), "colored": ("colored", "colour", "color vinyl", "coloured vinyl"), "black": ("black vinyl", "standard"), "picture_disc": ("picture disc",), "180g": ("180g",), "45rpm": ("45 rpm", "45rpm"), "rsd": ("rsd", "record store day"), "box_set": ("box set", "box"), "single": ("single lp",), "audiophile": ("audiophile",)}
    normalized = text(source).replace("180 g", "180g")
    return frozenset(name for name, aliases in vocabulary.items() if any(alias in normalized for alias in aliases))
