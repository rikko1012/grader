"""The command line end to end, offline: board -> edges -> record -> report, with the saved answers."""
from sports import espn, kalshi, lines, oddsapi, pinnacle, scout
from sports.tests.conftest import fixture


def _offline_sources(monkeypatch):
    monkeypatch.setattr(lines, "_fetch_pp", lambda lg: fixture("pp_nba.json"))
    monkeypatch.setattr(lines, "_fetch_ud", lambda lg: fixture("ud_nba.json"))
    monkeypatch.setattr(pinnacle, "load", lambda lg, refresh=False: ([], {"ok": False, "error": "refused for this "
                                                                           "server's location - not worked around"}))
    monkeypatch.setattr(kalshi, "_events", lambda s: fixture("kalshi_nbapts.json")["events"] if s == "KXNBAPTS" else [])
    monkeypatch.setattr(espn, "injuries", lambda lg, refresh=False: {})


def test_edges_record_report(monkeypatch, capsys):
    import sports.record as record
    from datetime import datetime, timezone
    _offline_sources(monkeypatch)
    real_save = record.save
    monkeypatch.setattr(record, "save", lambda con, rows, baselines=None, now=None:
                        real_save(con, rows, baselines, now=datetime(2026, 10, 23, 12, tzinfo=timezone.utc)))
    scout.main(["edges", "nba", "--all", "--record"])
    out = capsys.readouterr().out
    assert "kalshi: " in out and "not worked around" in out and "Karl-Anthony Towns" in out
    assert "recorded" in out and "recorded 0 new" not in out
    scout.main(["report", "nba"])
    out = capsys.readouterr().out
    assert "0 settled props" in out and "unsettled" in out


def test_leagues_lists_every_league(capsys):
    scout.main(["leagues"])
    out = capsys.readouterr().out
    for k in ("nba", "nfl", "wnba", "cbb", "cfb"):
        assert f"  {k} " in out
    assert "off (set ODDS_API_KEY" in out


def test_odds_api_failure_never_leaks_the_key(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "secret123")

    def boom(url, params=None, headers=None, **kw):
        raise oddsapi.net.HttpError(401, url + "?apiKey=secret123", "bad key secret123")
    monkeypatch.setattr(oddsapi.net, "get_json", boom)
    q, st = oddsapi.load(scout.config.league("nba"), refresh=True)
    assert q == [] and "secret123" not in st["error"]
