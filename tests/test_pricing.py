from datetime import datetime, timedelta, timezone
from decimal import Decimal

from vinyl_deals.database import SQLiteRepository
from vinyl_deals.domain import Availability, RawOffer
from vinyl_deals.matching.service import build_match_queue
from vinyl_deals.pricing import DealClass, classify, condition_bucket, evaluate_offer, median_price


def add(repo, source, product, price, *, condition="NEW", when=None, availability=Availability.IN_STOCK, old_price=None):
    repo.upsert_offer(RawOffer(source=source, source_product_id=product, url="https://x", fetched_at=when or datetime.now(timezone.utc), artist_raw="Artist", title_raw="Album", barcode="4006381333931", price=Decimal(str(price)), old_price=old_price, availability=availability, condition_media=condition))


def test_median_and_market_excludes_target_and_out_of_stock(tmp_path):
    repo = SQLiteRepository(tmp_path / "pricing.sqlite3")
    add(repo, "a", "1", 50); add(repo, "b", "2", 100); add(repo, "c", "3", 120); add(repo, "d", "4", 1000); add(repo, "e", "5", 10, availability=Availability.OUT_OF_STOCK)
    build_match_queue(repo)
    result = evaluate_offer(repo, 1)
    assert median_price([Decimal("1"), Decimal("3")]) == Decimal("2")
    assert result.market_median == Decimal("120")
    assert result.comparable_count == 3
    assert result.deal_class == DealClass.VERY_HOT


def test_small_sample_conditions_and_history(tmp_path):
    repo = SQLiteRepository(tmp_path / "history.sqlite3")
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    add(repo, "a", "1", 80, when=now - timedelta(days=100))
    add(repo, "a", "1", 60, when=now - timedelta(days=20))
    add(repo, "a", "1", 50, when=now - timedelta(days=1))
    add(repo, "b", "2", 100, when=now - timedelta(days=1))
    add(repo, "c", "3", 100, when=now - timedelta(days=1), condition="VG+")
    build_match_queue(repo)
    result = evaluate_offer(repo, 1, now=now)
    assert result.comparable_count == 1
    assert result.deal_class == DealClass.INSUFFICIENT
    assert result.historical_min == Decimal("60")
    assert result.median_30d == Decimal("55")
    assert result.median_90d == Decimal("55")
    assert result.minimum_90d == Decimal("50")
    assert result.is_historical_low


def test_thresholds_and_old_price_do_not_affect_market(tmp_path):
    repo = SQLiteRepository(tmp_path / "threshold.sqlite3")
    add(repo, "a", "1", 75, old_price=Decimal("10000")); add(repo, "b", "2", 100); add(repo, "c", "3", 100); add(repo, "d", "4", 100)
    build_match_queue(repo)
    assert evaluate_offer(repo, 1).deal_class == DealClass.HOT


def test_target_eligibility_grades_freshness_and_history_low(tmp_path):
    repo = SQLiteRepository(tmp_path / "hardening.sqlite3")
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    add(repo, "a", "1", 50, condition="NM", when=now - timedelta(days=30))
    add(repo, "a", "1", 40, condition="NM", when=now - timedelta(days=1))
    add(repo, "b", "2", 100, condition="NM", when=now - timedelta(days=1))
    add(repo, "c", "3", 100, condition="VG+", when=now - timedelta(days=1))
    add(repo, "d", "4", 1000, condition="NM", when=now - timedelta(days=10))
    add(repo, "e", "5", 0, condition="NM", when=now)
    add(repo, "f", "6", 10, condition="NM", when=now, availability=Availability.OUT_OF_STOCK)
    build_match_queue(repo)
    result = evaluate_offer(repo, 1, now=now)
    assert result.comparable_count == 1
    assert result.market_median == Decimal("100")
    assert result.price_drop_pct == Decimal("20")
    assert result.is_historical_low
    assert evaluate_offer(repo, 5, now=now) is None
    assert evaluate_offer(repo, 6, now=now) is None


def test_grade_compatibility_and_one_store_deduplication(tmp_path):
    repo = SQLiteRepository(tmp_path / "conditions.sqlite3")
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    add(repo, "target", "1", 70, condition="NM", when=now)
    add(repo, "shop-a", "2", 100, condition="NM", when=now)
    add(repo, "shop-a", "3", 200, condition="NM", when=now)
    add(repo, "shop-b", "4", 100, condition="NM", when=now)
    add(repo, "used", "5", 20, condition="VG+", when=now)
    add(repo, "sealed", "6", 20, condition="NEW", when=now)
    build_match_queue(repo)

    result = evaluate_offer(repo, 1, now=now)
    assert result.comparable_count == 2
    assert result.market_median == Decimal("100")
    assert result.deal_class == DealClass.GOOD
    assert "small market sample" in result.reasons


def test_first_or_equal_price_is_not_a_new_historical_low(tmp_path):
    repo = SQLiteRepository(tmp_path / "equal-history.sqlite3")
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    add(repo, "a", "1", 50, when=now - timedelta(days=2))
    add(repo, "a", "1", 50, when=now - timedelta(days=1))
    add(repo, "b", "2", 100, when=now)
    build_match_queue(repo)

    result = evaluate_offer(repo, 1, now=now)
    assert result.historical_min == Decimal("50")
    assert not result.is_historical_low
    assert result.price_drop_pct is None


def test_classification_boundaries_and_sample_sizes():
    assert classify(Decimal("9.99"), 3) == DealClass.NORMAL
    assert classify(Decimal("10"), 3) == DealClass.INTERESTING
    assert classify(Decimal("15"), 3) == DealClass.GOOD
    assert classify(Decimal("25"), 3) == DealClass.HOT
    assert classify(Decimal("35"), 3) == DealClass.VERY_HOT
    assert classify(Decimal("40"), 2) == DealClass.GOOD
    assert classify(Decimal("40"), 1) == DealClass.INSUFFICIENT


def test_stale_target_is_excluded_but_fresh_target_is_evaluated(tmp_path):
    repo = SQLiteRepository(tmp_path / "target-freshness.sqlite3")
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    add(repo, "a", "1", 50, when=now - timedelta(days=8))
    add(repo, "b", "2", 100, when=now)
    build_match_queue(repo)
    assert evaluate_offer(repo, 1, now=now) is None

    add(repo, "a", "1", 50, when=now)
    assert evaluate_offer(repo, 1, now=now) is not None


def test_target_store_is_excluded_from_its_own_benchmark(tmp_path):
    repo = SQLiteRepository(tmp_path / "target-store.sqlite3")
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    add(repo, "store-a", "target", 3000, when=now)
    add(repo, "store-a", "duplicate", 4500, when=now)
    add(repo, "store-b", "one", 5000, when=now)
    add(repo, "store-b", "duplicate", 8000, when=now)
    add(repo, "store-c", "one", 5200, when=now)
    build_match_queue(repo)

    result = evaluate_offer(repo, 1, now=now)
    assert result.comparable_count == 2
    assert result.market_median == Decimal("5100")


def test_condition_bucket_accepts_common_long_forms():
    def offer(condition: str) -> RawOffer:
        return RawOffer(source="test", source_product_id=condition, url="https://x", fetched_at=datetime.now(timezone.utc), condition_media=condition)

    assert condition_bucket(offer("Mint (M)")) == "nm"
    assert condition_bucket(offer("Near Mint (NM)")) == "nm"
    assert condition_bucket(offer("Excellent (EX)")) == "ex"
    assert condition_bucket(offer("Very Good Plus (VG+)")) == "vg+"
    assert condition_bucket(offer("Very Good (VG)")) == "vg"
    assert condition_bucket(offer("Good (G)")) == "good"
    assert condition_bucket(offer("NEW")) == "new"
    assert condition_bucket(offer("SEALED")) == "new"
