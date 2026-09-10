"""Reusable catalogue refresh orchestration for CLI and desktop clients."""
from __future__ import annotations

from dataclasses import dataclass
from time import sleep
from typing import Callable

from vinyl_deals.adapters import CollectomaniaAdapter, ImagineClubAdapter, RioRostovAdapter, VinylRuAdapter
from vinyl_deals.database.repository import SQLiteRepository
from vinyl_deals.matching.service import build_match_queue


@dataclass(frozen=True, slots=True)
class StoreUpdateResult:
    source: str
    status: str
    offers: int
    enriched: int
    errors: tuple[str, ...] = ()


DEFAULT_ADAPTER_FACTORIES = {
    "imagine_club": ImagineClubAdapter,
    "collectomania": CollectomaniaAdapter,
    "vinyl_ru": VinylRuAdapter,
    "rio_rostov": RioRostovAdapter,
}
STORE_LABELS = {
    "imagine_club": "Imagine Club",
    "collectomania": "Collectomania",
    "vinyl_ru": "Vinyl.ru",
    "rio_rostov": "РИО",
}


def refresh_catalogs(repository: SQLiteRepository, *, progress: Callable[[str], None] | None = None, adapter_factories: dict[str, Callable[[], object]] | None = None, enrich: bool = True) -> tuple[StoreUpdateResult, ...]:
    """Refresh every supported source, preserving successful stores on failures."""
    emit = progress or (lambda _: None)
    results: list[StoreUpdateResult] = []
    for source, factory in (adapter_factories or DEFAULT_ADAPTER_FACTORIES).items():
        emit(f"Обновление: {STORE_LABELS.get(source, source)}...")
        adapter = factory()
        run_id = repository.start_scrape_run(source)
        try:
            scrape = adapter.get_catalog()
            if scrape.state != "active":
                repository.finish_scrape_run(run_id, scrape.state, scrape.pages_processed, len(scrape.offers), scrape.warnings, scrape.errors)
                results.append(StoreUpdateResult(source, scrape.state, len(scrape.offers), 0, scrape.warnings + scrape.errors))
                emit(f"Магазин временно недоступен: {STORE_LABELS.get(source, source)}")
                continue
            warnings = list(scrape.warnings)
            enriched = 0
            for index, offer in enumerate(scrape.offers):
                persisted = offer
                if enrich:
                    try:
                        persisted = adapter.enrich_offer(offer)
                        enriched += 1
                    except Exception as error:  # One public product card must not fail a store refresh.
                        warnings.append(f"detail enrichment failed for {offer.source_product_id}: {error}")
                repository.upsert_offer(persisted)
                if enrich and index + 1 < len(scrape.offers):
                    sleep(getattr(adapter, "delay_seconds", 0))
            repository.finish_scrape_run(run_id, "active", scrape.pages_processed, len(scrape.offers), tuple(warnings), scrape.errors)
            results.append(StoreUpdateResult(source, "active", len(scrape.offers), enriched, tuple(warnings) + scrape.errors))
        except Exception as error:
            repository.finish_scrape_run(run_id, "error", 0, 0, errors=(str(error),))
            results.append(StoreUpdateResult(source, "error", 0, 0, (str(error),)))
            emit(f"Магазин временно недоступен: {STORE_LABELS.get(source, source)}")
    build_match_queue(repository)
    emit("Обновление завершено")
    return tuple(results)
