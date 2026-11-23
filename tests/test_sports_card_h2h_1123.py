"""sweep 2026-11-23 nbanfl-1: the NBA / NFL card's H2H block counts the same meetings as the board's H2H cell.

The card cut the player's log to the 30-game research window (DNPs included) before matching, so meetings at log positions
past 30 were missing (live: board 3 of 3, card 1 of 1). The board side (sports/board_view.h2h_season_lines) gives these same
EXPECTED counts on this same fixture in sports/tests/test_board_h2h_card_parity_1123.py (CS2 tests never import sports).
Every team, player and number here is a TEST FIXTURE."""
from datetime import datetime, timedelta, timezone

import workspace_sports as ws

OPP = {"team_id": "nba:team:espn:21", "abbr": "PHX", "name": "Phoenix"}


def _games(meet_at=(34, 44, 45, 57), n=60, dnp_every=7):
    """Raw player-file games newest first (snapshots.player_games shape); meetings with OPP at the given log positions."""
    t0 = datetime(2026, 11, 22, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        opp = OPP if i in meet_at else {"team_id": f"nba:team:espn:{100 + i % 9}", "abbr": f"T{i % 9}", "name": f"Team {i % 9}"}
        played = "dnp" if (i % dnp_every == 3 and i not in meet_at) else "played"
        out.append({"start_utc": (t0 - timedelta(days=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "opponent": opp, "home": i % 2 == 0,
                    "participation": played, "values": {"points": 10 + (i * 7) % 11} if played == "played" else {},
                    "season": 2026 if i < 30 else 2025, "season_type": 2})
    return out


# board_view.h2h_season_lines on these fixtures (pinned by sports/tests/test_board_h2h_card_parity_1123.py)
EXPECTED = {"base": {12.5: (3, 1, 0, 4), 14.5: (2, 2, 0, 4), 15.0: (2, 2, 0, 4), 17.5: (0, 4, 0, 4)},
            "ten": {14.5: (6, 4, 0, 10)}, "id": {14.5: (2, 2, 0, 4)}}


def _board(case, line):
    o, u, p, n = EXPECTED[case][line]
    return {"over": o, "under": u, "push": p, "n": n}


def _card(games, line, oid=OPP["team_id"], opponent="PHX"):
    hist = ws.trim({"family": "basketball", "games": games})
    h = ws.card_h2h(hist, "points", opponent, oid, line)
    return {k: h[k] for k in ("over", "under", "push", "n")} if h else None


def test_meetings_past_the_research_window_count_on_the_card_as_on_the_board():
    games = _games()
    for line in (12.5, 14.5, 15.0, 17.5):
        assert _card(games, line) == _board("base", line), line
    # the old card rule (30-game research window) saw none of these meetings
    hist = ws.trim({"family": "basketball", "games": games})
    assert not [s for s in ws.series_of(hist, "points") if s["opponent"] == "PHX"]


def test_newest_ten_meetings_like_the_board():
    games = _games(meet_at=tuple(range(1, 60, 4)))           # 15 meetings: both keep the newest 10
    assert _card(games, 14.5) == _board("ten", 14.5)


def test_meetings_match_on_team_id_not_the_code():
    games = _games()
    games[2]["opponent"] = {"team_id": "nba:team:espn:999", "abbr": "PHX", "name": "Other club, same code"}
    assert _card(games, 14.5) == _board("id", 14.5)


def test_without_team_ids_the_exact_code_still_matches():
    games = _games()
    assert _card(games, 14.5, oid=None)["n"] == 4


def test_record_carries_tonights_opponent_team_id():
    q = {"p": "underdog", "r": "offer:underdog:1", "l": 14.5, "o": [1, 1], "f": None, "x": {"v": "cf-2", "ln": None}}
    m = {"k": "e1|p1|points|full_game", "e": "e1", "pid": "p1", "n": "Player", "st": "points", "mk": None, "tid": "nba:team:espn:5"}
    doc = {"fc": {}, "events": {"e1": {"home": {"team_id": "nba:team:espn:5", "abbr": "NOP"}, "away": dict(OPP)}}}
    rec, x = ws.record_of("nba", doc, m, q, {})
    assert x["oid"] == OPP["team_id"] and rec["player"]["opponent"] == "PHX"
    m["tid"] = None
    assert ws.record_of("nba", doc, m, q, {})[1]["oid"] is None
