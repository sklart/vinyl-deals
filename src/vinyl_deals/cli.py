from __future__ import annotations
import argparse
from decimal import Decimal
from time import sleep
from pathlib import Path
from vinyl_deals.adapters import CollectomaniaAdapter, ImagineClubAdapter, VinylRuAdapter
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.database.repository import ManualDecisionConflict, ReleaseMergeConflict

def main() -> int:
    parser = argparse.ArgumentParser(prog="vinyl-deals"); commands = parser.add_subparsers(dest="command", required=True)
    scrape = commands.add_parser("scrape", help="Fetch a public store catalogue"); scrape.add_argument("source", choices=["vinyl_ru", "imagine_club", "collectomania"]); scrape.add_argument("--database", type=Path, default=Path("vinyl_deals.sqlite3")); scrape.add_argument("--page-limit", type=int, help="Temporary safe bound for a paginated source"); scrape.add_argument("--enrich", action="store_true", help="Fetch public product details for listed offers")
    match = commands.add_parser("match", help="Show pending possible release matches"); match.add_argument("--database", type=Path, default=Path("vinyl_deals.sqlite3")); match.add_argument("--build", action="store_true", help="Build candidate pairs from persisted offers")
    decide = commands.add_parser("decide-match", help="Save a manual release-match decision"); decide.add_argument("offer_id", type=int); decide.add_argument("candidate_offer_id", type=int); decide.add_argument("decision", choices=["same_release", "different_release", "ignore"]); decide.add_argument("--note"); decide.add_argument("--database", type=Path, default=Path("vinyl_deals.sqlite3"))
    deals = commands.add_parser("deals", help="Evaluate matched release offers"); deals.add_argument("--database", type=Path, default=Path("vinyl_deals.sqlite3")); deals.add_argument("--min-class", default="NORMAL", choices=["NORMAL", "INTERESTING", "GOOD", "HOT", "VERY_HOT"]); deals.add_argument("--include-insufficient", action="store_true", help="Include historical-only signals with insufficient market data"); deals.add_argument("--limit", type=int, default=50); deals.add_argument("--release-id", type=int)
    search = commands.add_parser("search", help="Search matched releases and their current offers"); search.add_argument("--database", type=Path, default=Path("vinyl_deals.sqlite3")); search.add_argument("--artist"); search.add_argument("--title"); search.add_argument("--barcode"); search.add_argument("--catalog"); search.add_argument("--label"); search.add_argument("--year", type=int); search.add_argument("--format")
    commands.add_parser("doctor", help="Check local configuration"); args = parser.parse_args()
    if args.command == "doctor":
        repository = SQLiteRepository()
        runs = repository.latest_scrape_runs()
        adapters = "vinyl_ru, imagine_club, collectomania"
        print(f"OK: SQLite schema v{repository.schema_version()}; adapters: {adapters}.")
        if runs:
            print("Latest scrape runs:")
            for store, status, finished_at in runs:
                print(f"- {store}: {status} ({finished_at or 'running'})")
        diagnostics = repository.integrity_diagnostics()
        print("Integrity: OK." if not diagnostics else "Integrity warnings:\n" + "\n".join(f"- {item}" for item in diagnostics))
        return 0
    if args.command == "match":
        repository = SQLiteRepository(args.database)
        if args.build:
            from vinyl_deals.matching.service import build_match_queue
            print(f"Built {build_match_queue(repository)} candidate matches.")
        rows = repository.possible_matches()
        print("No pending possible matches." if not rows else "\n".join(f"{offer_id} <-> {candidate_id}: {confidence:.0%} ({reasons})" for offer_id, candidate_id, confidence, reasons in rows))
        return 0
    if args.command == "decide-match":
        try:
            SQLiteRepository(args.database).decide_match(args.offer_id, args.candidate_offer_id, args.decision, args.note)
        except (ManualDecisionConflict, ReleaseMergeConflict) as error:
            print(f"Cannot save manual decision: {error}")
            return 2
        print(f"Saved {args.decision} for {args.offer_id} <-> {args.candidate_offer_id}.")
        return 0
    if args.command == "deals":
        from vinyl_deals.pricing import DealClass, evaluate_deals
        rank = {DealClass.NORMAL: 0, DealClass.INTERESTING: 1, DealClass.GOOD: 2, DealClass.HOT: 3, DealClass.VERY_HOT: 4, DealClass.INSUFFICIENT: -1}
        minimum = rank[DealClass(args.min_class)]
        repository = SQLiteRepository(args.database)
        def visible(item):
            if rank[item.deal_class] >= minimum:
                return True
            return args.include_insufficient and item.deal_class == DealClass.INSUFFICIENT and (item.is_historical_low or (item.price_drop_pct is not None and item.price_drop_pct >= 10))
        results = [item for item in evaluate_deals(repository, args.release_id) if visible(item)]
        results.sort(key=lambda item: (rank[item.deal_class], item.discount_pct or Decimal("-1")), reverse=True)
        for item in results[:args.limit]:
            _, offer = repository.offer_by_id(item.offer_id)
            heading = str(item.deal_class) if item.deal_class == DealClass.INSUFFICIENT else f"{item.deal_class} {item.discount_pct or Decimal('0'):.0f}%"
            market = "" if item.deal_class == DealClass.INSUFFICIENT else f"Market median: {item.market_median or '-'}\n"
            print(f"{heading}\n\n{offer.artist_raw or '-'} — {offer.title_raw or '-'}\nRelease: {offer.label or '-'} / {offer.catalog_number_raw or '-'} / {offer.release_year or '-'}\nStore: {offer.source}\nPrice: {item.current_price} RUB\n{market}Comparisons: {item.comparable_count}\nHistorical min: {item.historical_min or '-'}\n90d median: {item.median_90d or '-'}\n90d minimum: {item.minimum_90d or '-'}\nPrevious price: {item.previous_price or '-'}\nPrice drop: {item.price_drop_pct or Decimal('0'):.0f}%\nHistorical low: {'YES' if item.is_historical_low else 'NO'}\nReasons: {', '.join(item.reasons)}\n{offer.url}\n")
        return 0
    if args.command == "search":
        from vinyl_deals.search import search_releases
        results = search_releases(SQLiteRepository(args.database), artist=args.artist, title=args.title, barcode=args.barcode, catalog=args.catalog, label=args.label, year=args.year, format=args.format)
        if not results:
            print("No releases found.")
            return 0
        for result in results:
            metadata = " / ".join(str(value) for value in (result.label, result.catalog_number, result.release_year, result.format) if value)
            print(f"{result.artist} — {result.title}\n{metadata or '-'}\n")
            def best_line(name, offer):
                return f"{name}: {offer.store} — {offer.price} RUB" if offer else f"{name}: unavailable"
            print(best_line("Lowest price", result.lowest_price_offer))
            print(best_line("Best new", result.best_new_offer))
            print(best_line("Best used", result.best_used_offer) + "\n")
            for offer in result.offers:
                price = f"{offer.price} RUB" if offer.price is not None else "-"
                availability = "" if offer.availability.value == "in_stock" else f" ({offer.availability.value})"
                print(f"{offer.store:<18} {price}{availability}")
            if result.discogs_url:
                print(f"\nDiscogs: {result.discogs_url}")
            print()
        return 0
    adapter = {"vinyl_ru": VinylRuAdapter, "imagine_club": ImagineClubAdapter, "collectomania": CollectomaniaAdapter}[args.source]() if args.source == "vinyl_ru" else {"imagine_club": ImagineClubAdapter, "collectomania": CollectomaniaAdapter}[args.source](page_limit=args.page_limit)
    repository = SQLiteRepository(args.database)
    run_id = repository.start_scrape_run(args.source)
    try:
        result = adapter.get_catalog()
        if result.state != "active":
            repository.finish_scrape_run(run_id, result.state, result.pages_processed, len(result.offers), result.warnings, result.errors)
            print("DEGRADED: " + "; ".join(result.warnings)); return 2
        warnings = list(result.warnings)
        enriched = enrichment_errors = 0
        for index, offer in enumerate(result.offers):
            persisted = offer
            if args.enrich:
                try:
                    persisted = adapter.enrich_offer(offer)
                    enriched += 1
                except Exception as error:
                    enrichment_errors += 1
                    warnings.append(f"detail enrichment failed for {offer.source_product_id}: {error}")
                if index + 1 < len(result.offers):
                    sleep(getattr(adapter, "delay_seconds", 0))
            repository.upsert_offer(persisted)
        repository.finish_scrape_run(run_id, "active", result.pages_processed, len(result.offers), tuple(warnings), result.errors)
        report = f"OK: {args.source} — offers: {len(result.offers)}, enriched: {enriched}, enrichment errors: {enrichment_errors}"
        print(f"{report} persisted in {args.database}"); return 0
    except Exception as error:
        repository.finish_scrape_run(run_id, "error", 0, 0, errors=(str(error),))
        raise

if __name__ == "__main__": raise SystemExit(main())
