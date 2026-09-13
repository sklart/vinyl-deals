from importlib.util import find_spec

from vinyl_deals.adapters.challenge import interactive_challenge_present


def test_challenge_detector_has_no_persistent_browser_profile_module():
    """The safe manual flow must not retain or import browser sessions."""
    assert find_spec("vinyl_deals.browser_profiles") is None
    assert interactive_challenge_present("<main>Servicepipe access-check</main>")
