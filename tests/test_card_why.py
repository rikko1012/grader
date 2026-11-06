"""card-why (2026-11-06) server side: extra books' NBA / NFL rows get L5 / L10 / L20 (H2H / Season with SCOUT_BOARD_H2H) at their
own line from the player's saved game log; a same-line row keeps the board's own counts and only fills an empty window; the season
is the NBA / NFL season of the newest game (never a calendar year) and is left out when the saved file may cut it short; an
extra NBA line opens its research card; a sports-board FanDuel row keeps its own doc. No chance, letter or stake changes."""
import pytest

import workspace_api as api
import workspace_books as wb
import workspace_sports as ws


def game(v, season=2026, opp="BOS", day=1):
    return {"start_utc": f"2026-10-{day:02d}T00:00:00Z", "opponent": opp, "played": True, "values": {"assists": v}, "season": season}


@pytest.fixture
def h2h_on(monkeypatch):
    monkeypatch.setenv("SCOUT_BOARD_H2H", "1")


def test_sports_series_reads_the_shared_player_file_then_only_the_cache(monkeypatch):
    calls = []
    doc = {"games": [game(7, day=5), dict(game(None, day=4), played=False), game(4, day=3), game(9, opp="MIA", day=2)]}
    monkeypatch.setattr(ws, "_player", lambda srv, lg, pid, path: calls.append((lg, pid, path)) or doc)
    x = {"pid": "nba:player:espn:304", "st": "assists", "scope": "full_game"}
    ser = wb.sports_series(object(), "nba", x)
    assert [g["value"] for g in ser] == [7, 4, 9] and calls == [("nba", "nba:player:espn:304", "/api/sports/history")]
    # past the build's budget: the cached copy only, never a new upstream read
    monkeypatch.setattr(api, "_peek_shared", lambda srv, key: None)
    assert wb.sports_series(object(), "nba", x, budget=0) is None and len(calls) == 1
    # another period or a malformed player id: nothing (no counts shown, never zeros)
    assert wb.sports_series(object(), "nba", dict(x, scope="1h")) is None
    assert wb.sports_series(object(), "nba", dict(x, pid="bad id")) is None and len(calls) == 1


def test_hist_counts_at_the_line_and_season_is_the_sports_season(h2h_on):
    ser = [{"date": "2026-11-01", "opponent": "BOS", "value": 8, "season": 2027, "_n": 6},
           {"date": "2026-10-28", "opponent": "MIA", "value": 5, "season": 2027, "_n": 6},
           {"date": "2026-10-24", "opponent": "BOS", "value": 7, "season": 2027, "_n": 6},
           {"date": "2026-04-10", "opponent": "BOS", "value": 9, "season": 2026, "_n": 6},
           {"date": "2026-04-08", "opponent": "NYK", "value": 2, "season": 2026, "_n": 6},
           {"date": "2026-04-05", "opponent": "BOS", "value": 6, "season": 2026, "_n": 6}]
    h = wb._hist_of(ser, 6.5, "BOS")
    assert h["l5"] == {"over": 3, "under": 2, "push": 0, "n": 5} and h["l10"]["n"] == 6
    assert h["season"] == {"over": 2, "under": 1, "push": 0, "n": 3, "season": "2027"}    # 2026-27 season, not calendar 2026
    assert h["h2h"]["n"] == 4 and h["h2h"]["over"] == 3
    # a full saved file (RESEARCH_MAX_GAMES games) all from one season may be cut short: no season count rather than a wrong one
    full = [dict(g, season=2027, _n=ws.RESEARCH_MAX_GAMES) for g in ser]
    assert wb._hist_of(full, 6.5, "BOS")["season"] is None


def _rec(line, hist):
    return {"sport": "nba", "key": "k", "event": {"id": "e", "teams": ["CHI", "MIA"], "start_utc": "2026-11-07T00:00:00Z"},
            "player": {"id": "nba:player:espn:304", "key": "reedlean", "name": "Reed Lean", "team": "CHI", "opponent": "MIA"},
            "market": {"stat": "assists", "period": "full_game", "line_type": "standard"},
            "offer": {"platform": "underdog", "platform_label": "Underdog", "offer_id": "s-1", "line": line},
            "scout": {"side": "over", "p_side": 0.58, "p_over": 0.58, "p_under": 0.42, "projection": 7.2, "grade": "B", "units": 0, "validation": {}},
            "history": hist, "state": {"kind": "ok"}}


def test_same_line_sports_row_keeps_board_counts_and_fills_only_the_empty_windows(h2h_on):
    board = {"l5": {"over": 4, "under": 1, "push": 0, "n": 5}, "l10": {"over": 7, "under": 3, "push": 0, "n": 10}, "l20": None, "avg": None, "h2h": None, "season": None}
    log = {"l5": {"over": 0, "under": 5, "push": 0, "n": 5}, "l10": {"over": 0, "under": 10, "push": 0, "n": 10}, "l20": {"over": 12, "under": 8, "push": 0, "n": 20},
           "avg": 6.1, "h2h": {"over": 2, "under": 1, "push": 0, "n": 3, "opponent": "MIA"}, "season": {"over": 5, "under": 4, "push": 0, "n": 9, "season": "2027"}}
    seen = []
    read = lambda base, line: seen.append(line) or (None, log)
    o = {"offered": True, "payout": 1.8, "price": None}
    r = wb.derive("sleeper", "Sleeper", "pickem", "nba", [_rec(6.5, board)], 6.5, o, o, None, 0, ("a",), read=read)
    assert r["history"]["l5"] == board["l5"] and r["history"]["l10"] == board["l10"]           # the board's own counts stay
    assert r["history"]["l20"] == log["l20"] and r["history"]["h2h"] == log["h2h"] and r["history"]["season"] == log["season"]
    assert r["scout"]["grade"] == "B" and r["scout"]["units"] is None                           # same line: same read, no stake (unchanged)
    # nothing missing: no read at all
    full = dict(board, l20=log["l20"], h2h=log["h2h"], season=log["season"])
    seen.clear()
    r = wb.derive("sleeper", "Sleeper", "pickem", "nba", [_rec(6.5, full)], 6.5, o, o, None, 0, ("a",), read=read)
    assert seen == [] and r["history"]["l5"] == board["l5"]
    # another line: every window from the game log at THAT line, no chance / letter / stake
    r = wb.derive("sleeper", "Sleeper", "pickem", "nba", [_rec(6.5, full)], 7.5, o, o, None, 0, ("b",), read=read)
    assert seen == [7.5] and r["history"]["l20"] == log["l20"] and r["scout"]["grade"] is None and r["scout"]["p_side"] is None


def test_extra_nba_line_opens_its_card_and_a_sports_board_book_row_keeps_its_own_doc(monkeypatch):
    import book_providers as bp
    monkeypatch.setenv("SCOUT_BOOK_SLEEPER", "1")
    monkeypatch.setenv("SCOUT_BOOK_FANDUEL", "1")
    hit = []
    monkeypatch.setattr(wb, "line_doc", lambda srv, sport, book, rid: hit.append((sport, book, rid)) or (200, {"ok": 1}))

    class Srv:
        @staticmethod
        def shared(key, fn, ttl=None):
            return fn()
    shared = []
    monkeypatch.setattr(api, "_line_body", lambda *a: (200, {}))

    class Mod:
        @staticmethod
        def snapshot(srv, sport):
            return {"index": {("fanduel", "s-9"): {}}}

        @staticmethod
        def line_doc(srv, sport, book, rid):
            shared.append((sport, book, rid))
            return 200, {"sports": 1}
    monkeypatch.setattr(api, "_adapter", lambda sport: Mod)
    srv = Srv()
    assert "nba" in bp.providers()["sleeper"]["sports"]
    for sport in ("nba", "nfl"):
        got = api._line_for(srv, sport, "sleeper", "x-abc")
        assert got is not None and hit[-1] == (sport, "sleeper", "x-abc")
    got = api._line_for(srv, "nba", "fanduel", "s-9")               # the NBA board's own FanDuel row: its sports research doc
    assert got is not None and shared == [("nba", "fanduel", "s-9")]
