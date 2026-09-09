from vinyl_deals.cli import main
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import RawOffer

def test_cli_saves_manual_decision(tmp_path, monkeypatch):
    database = tmp_path / "cli.sqlite3"; repo = SQLiteRepository(database)
    repo.upsert_offer(RawOffer.now(source="a", source_product_id="1", url="https://a")); repo.upsert_offer(RawOffer.now(source="b", source_product_id="2", url="https://b")); repo.record_match(1, 2, "possible", .6, ("test",))
    monkeypatch.setattr("sys.argv", ["vinyl-deals", "decide-match", "1", "2", "different_release", "--database", str(database)])
    assert main() == 0; assert repo.possible_matches() == []
