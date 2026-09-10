from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from vinyl_deals.alerts import AlertEvent, _meets_deal_threshold, evaluate_watchlist
from vinyl_deals import cli
from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.notifications import TelegramNotifier, format_alert


def _repository(tmp_path, *, price="5000", local=False, delivery=None, pickup=False, fetched_at=None):
    repository = SQLiteRepository(tmp_path / "alerts.sqlite3")
    point = fetched_at or datetime.now(timezone.utc)
    values = dict(source="rio_rostov" if local else "store_a", source_product_id="1", url="https://example.test/1", fetched_at=point, artist_raw="Opeth", title_raw="Blackwater Park", barcode="4006381333931", catalog_number_raw="MOVLP001", label="Music On Vinyl", release_year=2021, format="2LP", condition_media="NEW", condition_sleeve="NEW", price=Decimal(price), availability=Availability.IN_STOCK, local_store=local, city="Ростов-на-Дону" if local else None, pickup_available=pickup, delivery_cost=Decimal(delivery) if delivery else None)
    repository.upsert_offer(RawOffer(**values))
    # A second matching offer creates a concrete Release through normal matching.
    values.update(source="store_b", source_product_id="2", url="https://example.test/2", price=Decimal("8000"), local_store=False, city=None, pickup_available=False, delivery_cost=None)
    repository.upsert_offer(RawOffer(**values))
    build_match_queue(repository)
    release_id = repository.offers_for_matching()[0][0]
    release = repository.offer_by_id(release_id)[0]
    return repository, release


def test_watchlist_crud_duplicate_and_disabled_watch(tmp_path):
    repository, release_id = _repository(tmp_path)
    repository.add_watchlist(release_id, max_price=Decimal("6000"), min_deal_class="GOOD")
    repository.add_watchlist(release_id, max_price=Decimal("5500"))
    assert len(repository.watchlist_entries()) == 1
    repository.set_watchlist_enabled(release_id, False)
    assert evaluate_watchlist(repository) == []
    repository.set_watchlist_enabled(release_id, True)
    repository.remove_watchlist(release_id)
    assert repository.watchlist_entries() == []


def test_new_stock_duplicate_suppression_and_real_price_change(tmp_path):
    repository, release_id = _repository(tmp_path)
    repository.add_watchlist(release_id)
    first = evaluate_watchlist(repository)
    assert any(item.event_type == AlertEvent.NEW_STOCK for item in first)
    assert evaluate_watchlist(repository) == []
    _, offer = repository.offers_for_matching()[0]
    repository.upsert_offer(RawOffer(**{**{name: getattr(offer, name) for name in offer.__dataclass_fields__}, "fetched_at": offer.fetched_at + timedelta(minutes=1), "price": Decimal("4500")}))
    later = evaluate_watchlist(repository, now=offer.fetched_at + timedelta(minutes=1))
    assert any(item.event_type == AlertEvent.PRICE_DROP for item in later)


def test_historical_low_good_deal_and_local_filters(tmp_path):
    repository, release_id = _repository(tmp_path, price="5000", local=True, delivery="300", pickup=False)
    repository.add_watchlist(release_id, local_only=True, city="Ростов-на-Дону", max_price=Decimal("5400"))
    _, offer = repository.offers_for_matching()[0]
    repository.upsert_offer(RawOffer(**{**{name: getattr(offer, name) for name in offer.__dataclass_fields__}, "fetched_at": offer.fetched_at + timedelta(minutes=1), "price": Decimal("4000")}))
    events = evaluate_watchlist(repository, now=offer.fetched_at + timedelta(minutes=1))
    kinds = {item.event_type for item in events}
    assert AlertEvent.LOCAL_HISTORICAL_LOW in kinds and AlertEvent.LOCAL_PRICE_DROP in kinds


def test_good_deal_uses_existing_pricing_engine(tmp_path):
    repository, release_id = _repository(tmp_path, price="5000")
    _, base = repository.offers_for_matching()[0]
    for number, price in enumerate(("8000", "8200", "8400"), start=3):
        repository.upsert_offer(RawOffer(**{**{name: getattr(base, name) for name in base.__dataclass_fields__}, "source": f"store_{number}", "source_product_id": str(number), "url": f"https://example.test/{number}", "price": Decimal(price)}))
    build_match_queue(repository)
    repository.add_watchlist(release_id, min_deal_class="GOOD")
    events = evaluate_watchlist(repository)
    assert any(item.event_type == AlertEvent.GOOD_DEAL for item in events)
    persisted = next(row["payload"] for row in repository.alerts() if row["event_type"] == AlertEvent.GOOD_DEAL)
    assert "Выгода:" in format_alert(persisted)


@pytest.mark.parametrize(("selected", "deal", "expected"), [
    (None, "INTERESTING", False), (None, "GOOD", True),
    ("NORMAL", "NORMAL", True), ("INTERESTING", "INTERESTING", True),
    ("GOOD", "INTERESTING", False), ("GOOD", "GOOD", True),
    ("HOT", "GOOD", False), ("HOT", "HOT", True),
    ("VERY_HOT", "HOT", False), ("VERY_HOT", "VERY_HOT", True),
])
def test_min_deal_class_thresholds(selected, deal, expected):
    assert _meets_deal_threshold(deal, selected) is expected


def test_unknown_local_delivery_does_not_pass_max_price(tmp_path):
    repository, release_id = _repository(tmp_path, local=True, delivery=None)
    repository.add_watchlist(release_id, local_only=True, max_price=Decimal("6000"))
    assert evaluate_watchlist(repository) == []


def test_formatter_and_telegram_network_failure_preserve_alert(tmp_path, monkeypatch):
    payload = {"event_type": "GOOD_DEAL", "artist": "Opeth", "title": "Blackwater Park", "label": "Music On Vinyl", "catalog_number": "MOVLP001", "release_year": 2021, "store": "audiomania", "price": Decimal("5490"), "market_median": Decimal("7200"), "discount_pct": Decimal("24"), "url": "https://example.test", "discogs_url": "https://discogs.test", "local_store": True, "city": "Ростов-на-Дону", "effective_price_known": False}
    text = format_alert(payload)
    assert "GOOD DEAL" in text and "📍 РОСТОВ" in text and "Discogs:" in text
    notifier = TelegramNotifier(token="token", chat_id="chat")
    monkeypatch.setattr("vinyl_deals.notifications.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")))
    try:
        notifier.send(text)
    except RuntimeError:
        pass
    else:
        raise AssertionError("network failure must be reported")
    history_dir = tmp_path / "history"; history_dir.mkdir()
    repository, release_id = _repository(history_dir)
    repository.add_watchlist(release_id)
    evaluate_watchlist(repository)
    alert_id = int(repository.alerts(unsent_only=True)[0]["id"])
    repository.mark_alert_error(alert_id, "offline")
    assert repository.alerts(unsent_only=True)[0]["send_error"] == "offline"


def test_formatter_accepts_json_number_strings():
    payload = {"event_type": "GOOD_DEAL", "artist": "Opeth", "title": "Blackwater Park", "store": "store", "price": "5490", "market_median": "7200", "effective_price": "5700", "effective_price_known": True, "discount_pct": "23.75", "url": "https://example.test"}
    text = format_alert(payload)
    assert "Цена: 5490 ₽" in text and "Выгода: 24%" in text


def test_alert_send_isolates_formatter_failure_and_counts_current_run(tmp_path, monkeypatch, capsys):
    repository, release_id = _repository(tmp_path)
    offer_id = repository.offers_for_release(release_id)[0][0]
    with repository._connect() as connection:
        connection.execute("DELETE FROM alerts")
    repository.save_alert(release_id=release_id, offer_id=offer_id, event_type="BAD", event_key="1", payload={})
    repository.save_alert(release_id=release_id, offer_id=offer_id, event_type="GOOD", event_key="2", payload={"event_type": "GOOD_DEAL", "artist": "Opeth", "title": "Blackwater Park", "store": "store", "price": "5000", "url": "https://example.test"})
    class Notifier:
        configured = True
        def send(self, _text): pass
    monkeypatch.setattr("vinyl_deals.notifications.TelegramNotifier", lambda: Notifier())
    monkeypatch.setattr("sys.argv", ["vinyl-deals", "alerts", "--database", str(repository.path), "send"])
    assert cli.main() == 1
    assert "Telegram sent: 1, failed: 1" in capsys.readouterr().out
    rows = repository.alerts()
    assert rows[0]["sent_at"] is None and rows[0]["send_error"] == "delivery failed: KeyError"
    assert rows[1]["sent_at"] is not None


def test_alert_send_continues_after_transport_failure_without_leaking_error(tmp_path, monkeypatch):
    repository, release_id = _repository(tmp_path)
    offer_id = repository.offers_for_release(release_id)[0][0]
    payload = {"event_type": "GOOD_DEAL", "artist": "Opeth", "title": "Blackwater Park", "store": "store", "price": "5000", "url": "https://example.test"}
    repository.save_alert(release_id=release_id, offer_id=offer_id, event_type="GOOD", event_key="one", payload=payload)
    repository.save_alert(release_id=release_id, offer_id=offer_id, event_type="GOOD", event_key="two", payload=payload)
    class Notifier:
        configured = True
        calls = 0
        def send(self, _text):
            self.calls += 1
            if self.calls == 1: raise RuntimeError("token=must-not-be-stored")
    monkeypatch.setattr("vinyl_deals.notifications.TelegramNotifier", lambda: Notifier())
    monkeypatch.setattr("sys.argv", ["vinyl-deals", "alerts", "--database", str(repository.path), "send"])
    assert cli.main() == 1
    rows = repository.alerts()
    assert rows[0]["sent_at"] is None and rows[0]["send_error"] == "delivery failed: RuntimeError"
    assert rows[1]["sent_at"] is not None
