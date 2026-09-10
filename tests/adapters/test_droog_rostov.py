from vinyl_deals.adapters.droog_rostov import DroogRostovAdapter
from vinyl_deals.domain import StoreState


def test_droog_never_imports_unverified_directory_offers() -> None:
    result = DroogRostovAdapter().get_catalog()
    assert result.state == StoreState.DEGRADED
    assert result.offers == ()
    assert "no offers were imported" in result.warnings[0]
