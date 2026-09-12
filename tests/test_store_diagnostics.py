from vinyl_deals.database import SQLiteRepository
from vinyl_deals.store_diagnostics import store_coverage


def test_store_coverage_is_network_free_and_reports_capabilities(tmp_path):
    repository = SQLiteRepository(tmp_path / "coverage.sqlite3")
    rows = {row.source: row for row in store_coverage(repository)}
    assert rows["respublica"].search_capability == "PUBLIC SEARCH"
    assert rows["respublica"].price_capability == "PRODUCT DETAIL"
    assert rows["rio_rostov"].search_capability == "UNSUPPORTED"
    assert rows["onlinetrade"].search_capability == "PUBLIC SEARCH"
    assert rows["onlinetrade"].last_known_status == "RESTRICTED"
    assert rows["pult"].search_capability == "PUBLIC SEARCH"
    assert rows["pult"].last_known_status == "RESTRICTED"
    assert rows["droog_rostov"].search_capability == "UNSUPPORTED"
    assert rows["droog_rostov"].detail_enrichment == "NO"
    assert rows["vinyl_ru"].detail_enrichment == "NO"
    assert all(row.detail_enrichment == "YES" for source, row in rows.items() if source not in {"droog_rostov", "vinyl_ru"})
