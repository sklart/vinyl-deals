import sys

from vinyl_deals import cli


def test_doctor_stores_is_network_free_and_lists_search_price_and_detail(tmp_path, monkeypatch, capsys):
    database = tmp_path / "doctor-stores.sqlite3"
    monkeypatch.setattr(cli, "bootstrap_application_data", lambda: database)
    monkeypatch.setattr(sys, "argv", ["vinyl-deals", "doctor-stores"])

    assert cli.main() == 0

    output = capsys.readouterr().out
    assert "Respublica" in output and "LIVE OK" in output and "DETAIL OK" in output
    assert "РИО" in output and "UNSUPPORTED" in output
    assert "OnlineTrade" in output and "RESTRICTED" in output
    assert "Pult.ru" in output and "last:" in output

