from pathlib import Path
from vinyl_deals.adapters.vinyl_ru import VinylRuAdapter
from vinyl_deals.database import SQLiteRepository

def test_persists_queue_and_manual_decision(tmp_path: Path) -> None:
    database = tmp_path / "matches.sqlite3"; repository = SQLiteRepository(database); first = VinylRuAdapter().parse_catalog("ID;Название;Цена\n1;A - B;100\n")[0]; second = VinylRuAdapter().parse_catalog("ID;Название;Цена\n2;A - B;200\n")[0]
    repository.upsert_offer(first); repository.upsert_offer(second); repository.record_match(1, 2, "possible", .60, ("artist and title match",))
    assert len(repository.possible_matches()) == 1
    repository.decide_match(1, 2, "different_release", "different pressing")
    assert repository.possible_matches() == []
