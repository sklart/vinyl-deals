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

def tags(value: tuple[str, ...], edition_raw: str | None = None) -> frozenset[str]:
    source = " ".join((*value, edition_raw or ""))
    vocabulary = ("remaster", "mono", "stereo", "deluxe", "limited", "anniversary", "colored", "colour", "picture disc", "180g", "45 rpm", "rsd", "box", "audiophile")
    normalized = text(source).replace("180 g", "180g")
    return frozenset(item for item in vocabulary if item in normalized)
