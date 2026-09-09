from __future__ import annotations
import argparse
from time import sleep
from pathlib import Path
from vinyl_deals.adapters import CollectomaniaAdapter, ImagineClubAdapter, VinylRuAdapter
from vinyl_deals.database import SQLiteRepository

def main() -> int:
    parser = argparse.ArgumentParser(prog="vinyl-deals"); commands = parser.add_subparsers(dest="command", required=True)
    scrape = commands.add_parser("scrape", help="Fetch a public store catalogue"); scrape.add_argument("source", choices=["vinyl_ru", "imagine_club", "collectomania"]); scrape.add_argument("--database", type=Path, default=Path("vinyl_deals.sqlite3")); scrape.add_argument("--page-limit", type=int, help="Temporary safe bound for a paginated source"); scrape.add_argument("--enrich", action="store_true", help="Fetch public product details for listed offers")
    match = commands.add_parser("match", help="Show pending possible release matches"); match.add_argument("--database", type=Path, default=Path("vinyl_deals.sqlite3")); match.add_argument("--build", action="store_true", help="Build candidate pairs from persisted offers")
    decide = commands.add_parser("decide-match", help="Save a manual release-match decision"); decide.add_argument("offer_id", type=int); decide.add_argument("candidate_offer_id", type=int); decide.add_argument("decision", choices=["same_release", "different_release", "ignore"]); decide.add_argument("--note"); decide.add_argument("--database", type=Path, default=Path("vinyl_deals.sqlite3"))
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
        SQLiteRepository(args.database).decide_match(args.offer_id, args.candidate_offer_id, args.decision, args.note)
        print(f"Saved {args.decision} for {args.offer_id} ↔ {args.candidate_offer_id}.")
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
