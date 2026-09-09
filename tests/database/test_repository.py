from pathlib import Path
from vinyl_deals.adapters.vinyl_ru import VinylRuAdapter
from vinyl_deals.database import SQLiteRepository

def test_upsert_preserves_price_history(tmp_path: Path) -> None:
    offer = VinylRuAdapter().parse_catalog("ID;Название;Цена\n1;A - B;1000\n")[0]; database = tmp_path / "offers.sqlite3"; repository = SQLiteRepository(database); repository.upsert_offer(offer); repository.upsert_offer(offer)
    import sqlite3
    with sqlite3.connect(database) as connection: assert connection.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1; assert connection.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 1
