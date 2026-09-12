from vinyl_deals.browser_profiles import browser_profile_path, interactive_challenge_present


def test_browser_profile_stays_inside_portable_data_directory(tmp_path):
    path = browser_profile_path("onlinetrade", data_dir=tmp_path / "data")
    assert path == tmp_path / "data" / "browser_profiles" / "onlinetrade"


def test_visible_challenge_is_distinct_from_script_configuration():
    assert interactive_challenge_present("<main>Servicepipe access-check <js-challenge-loader></js-challenge-loader></main>")
    assert not interactive_challenge_present("<script>window.captchaProvider='hcaptcha'</script><main>Vinyl catalogue</main>")
