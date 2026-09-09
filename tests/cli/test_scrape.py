from datetime import datetime, timezone

from vinyl_deals import cli
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import RawOffer, ScrapeResult


class _AdapterWithBrokenDetail:
    delay_seconds = 0

    def get_catalog(self):
        return ScrapeResult((
            RawOffer.now(source="imagine_club", source_product_id="1", url="https://x/1"),
            RawOffer.now(source="imagine_club", source_product_id="2", url="https://x/2"),
        ), pages_processed=1)

    def enrich_offer(self, offer):
        if offer.source_product_id == "2":
            raise RuntimeError("broken detail card")
        return offer


def test_enrichment_failure_is_isolated_and_scrape_run_keeps_statistics(tmp_path, monkeypatch, capsys):
    database = tmp_path / "offers.sqlite3"
    monkeypatch.setattr(cli, "ImagineClubAdapter", lambda **_: _AdapterWithBrokenDetail())
    monkeypatch.setattr("sys.argv", ["vinyl-deals", "scrape", "imagine_club", "--enrich", "--database", str(database)])
    assert cli.main() == 0
    output = capsys.readouterr().out
    assert "offers: 2, enriched: 1, enrichment errors: 1" in output
    repository = SQLiteRepository(database)
    assert len(repository.offers_for_matching()) == 2
    with repository._connect() as connection:
        assert connection.execute("SELECT status, pages_processed, offers_found FROM scrape_runs").fetchone() == ("active", 1, 2)
