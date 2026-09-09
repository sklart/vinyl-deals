from pathlib import Path
from dataclasses import replace
from datetime import timedelta
from vinyl_deals.adapters.vinyl_ru import VinylRuAdapter
from vinyl_deals.database import SQLiteRepository

def test_upsert_preserves_price_history(tmp_path: Path) -> None:
    offer = VinylRuAdapter().parse_catalog("ID;Название;Цена\n1;A - B;1000\n")[0]; database = tmp_path / "offers.sqlite3"; repository = SQLiteRepository(database); repository.upsert_offer(offer); repository.upsert_offer(offer)
    import sqlite3
    with sqlite3.connect(database) as connection: assert connection.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 1; assert connection.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 1


def test_upsert_preserves_first_seen_and_updates_last_seen(tmp_path: Path) -> None:
    offer = VinylRuAdapter().parse_catalog("ID;Название;Цена\n1;A - B;1000\n")[0]
    repository = SQLiteRepository(tmp_path / "offers.sqlite3")
    repository.upsert_offer(offer)
    later_offer = replace(offer, fetched_at=offer.fetched_at + timedelta(hours=1))
    repository.upsert_offer(later_offer)

    import sqlite3

    with sqlite3.connect(repository.path) as connection:
        first_seen, last_seen = connection.execute(
            "SELECT first_seen, last_seen FROM offers"
        ).fetchone()
    assert first_seen == offer.fetched_at.isoformat()
    assert last_seen == later_offer.fetched_at.isoformat()
