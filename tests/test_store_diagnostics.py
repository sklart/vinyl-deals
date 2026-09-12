from vinyl_deals.database import SQLiteRepository
from vinyl_deals.store_diagnostics import store_coverage


def test_store_coverage_is_network_free_and_reports_capabilities(tmp_path):
    repository = SQLiteRepository(tmp_path / "coverage.sqlite3")
    rows = {row.source: row for row in store_coverage(repository)}
    assert rows["respublica"].search == "LIVE OK"
    assert rows["respublica"].price == "DETAIL OK"
    assert rows["rio_rostov"].search == "UNSUPPORTED"
    assert rows["onlinetrade"].search == rows["onlinetrade"].price == "RESTRICTED"
    assert rows["pult"].search == rows["pult"].price == "RESTRICTED"
    assert rows["droog_rostov"].search == rows["droog_rostov"].price == "UNSUPPORTED"
    assert rows["droog_rostov"].detail_enrichment == "NO"
    assert rows["vinyl_ru"].detail_enrichment == "NO"
    assert all(row.detail_enrichment == "YES" for source, row in rows.items() if source not in {"droog_rostov", "vinyl_ru"})
