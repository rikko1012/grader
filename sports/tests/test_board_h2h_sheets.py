"""SCOUT_BOARD_H2H (default off): the sheet view's H2H + Season counts for the research board. Flag off = the view as before."""
from sports import board_view, sheets


# ---------------------------------------------------------------------------------------------------------------- NBA / NFL
GAMES = [{"participation": "played", "season": 2026, "opponent": {"team_id": "t-bos"}, "values": {"points": 25}},
         {"participation": "played", "season": 2026, "opponent": {"team_id": "t-mia"}, "values": {"points": 18}},
         {"participation": "did_not_play", "season": 2026, "opponent": {"team_id": "t-bos"}, "values": {}},
         {"participation": "played", "season": 2025, "opponent": {"team_id": "t-bos"}, "values": {"points": 20.5}},
         {"participation": "played", "season": 2025, "opponent": {"team_id": "t-bos"}, "values": {"points": 10}}]


def test_sports_h2h_season_lines_count_this_opponent_and_the_newest_season():
    out = board_view.h2h_season_lines("points", [20.5], GAMES, "t-bos")
    assert out["season"] == 2026
    assert out["linesH2h"]["20.5"] == [1, 1, 1, 3] and out["linesSeason"]["20.5"] == [1, 1, 0, 2]
    none = board_view.h2h_season_lines("points", [20.5], GAMES, None)
    assert none["linesH2h"]["20.5"] == [0, 0, 0, 0]


def _pf():
    q = {"record_id": "r1", "platform": "underdog", "line": 20.5, "variant": "standard", "promotional": False, "availability": "open",
         "sides": [{"side": "over", "offered": True}, {"side": "under", "offered": True}], "last_seen_at": "2026-10-23T20:59:00Z",
         "source_updated_at": None, "gradable": True, "forecast_id": None}
    return {"forecasts": {}, "forecast_detail": {},
            "markets": [{"key": "e1|p1|points|full_game", "event_id": "e1", "player_id": "p1", "player": "K", "position": "C", "team": "NY",
                         "team_id": "t-ny", "stat": "points", "stat_label": "Points", "primary": True, "capability": "supported", "quotes": [q],
                         "alternates": [], "market": {"l": 20.5, "c": None, "bk": [], "at": {}}}]}


EVENTS = {"e1": {"start_utc": "2099-01-01T00:00:00Z", "home": {"team_id": "t-ny"}, "away": {"team_id": "t-bos"}}}


def test_sheet_view_is_unchanged_off_and_carries_the_columns_on(monkeypatch):
    monkeypatch.delenv("SCOUT_BOARD_H2H", raising=False)
    off = sheets.sheet_markets(_pf(), GAMES, "underdog", EVENTS, "2026-11-04T00:00:00Z")[2]["e1|p1|points|full_game"]
    assert not {"linesH2h", "linesSeason", "season"} & set(off)
    monkeypatch.setenv("SCOUT_BOARD_H2H", "1")
    on = sheets.sheet_markets(_pf(), GAMES, "underdog", EVENTS, "2026-11-04T00:00:00Z")[2]["e1|p1|points|full_game"]
    assert on["linesH2h"]["20.5"] == [1, 1, 1, 3] and on["season"] == 2026
    assert {k: v for k, v in on.items() if k not in ("linesH2h", "linesSeason", "season")} == off
