"""sweep 2026-11-23 nbanfl-1: the board's H2H counts (board_view.h2h_season_lines) on the fixture whose card counts are pinned
in tests/test_sports_card_h2h_1123.py - same games, same EXPECTED numbers, so the board and the card agree (the two test trees
never import each other). Meetings sit at log positions past 30. Every team and number is a TEST FIXTURE."""
from datetime import datetime, timedelta, timezone

from sports import board_view

OPP = {"team_id": "nba:team:espn:21", "abbr": "PHX", "name": "Phoenix"}
EXPECTED = {"base": {12.5: (3, 1, 0, 4), 14.5: (2, 2, 0, 4), 15.0: (2, 2, 0, 4), 17.5: (0, 4, 0, 4)},
            "ten": {14.5: (6, 4, 0, 10)}, "id": {14.5: (2, 2, 0, 4)}}


def _games(meet_at=(34, 44, 45, 57), n=60, dnp_every=7):
    """Identical to tests/test_sports_card_h2h_1123._games."""
    t0 = datetime(2026, 11, 22, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        opp = OPP if i in meet_at else {"team_id": f"nba:team:espn:{100 + i % 9}", "abbr": f"T{i % 9}", "name": f"Team {i % 9}"}
        played = "dnp" if (i % dnp_every == 3 and i not in meet_at) else "played"
        out.append({"start_utc": (t0 - timedelta(days=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "opponent": opp, "home": i % 2 == 0,
                    "participation": played, "values": {"points": 10 + (i * 7) % 11} if played == "played" else {},
                    "season": 2026 if i < 30 else 2025, "season_type": 2})
    return out


def _board(games, line):
    return tuple(board_view.h2h_season_lines("points", [line], games, OPP["team_id"])["linesH2h"][board_view.line_key(line)])


def test_board_h2h_counts_the_pinned_card_fixture():
    games = _games()
    for line, want in EXPECTED["base"].items():
        assert _board(games, line) == want, line
    assert _board(_games(meet_at=tuple(range(1, 60, 4))), 14.5) == EXPECTED["ten"][14.5]
    g = _games()
    g[2]["opponent"] = {"team_id": "nba:team:espn:999", "abbr": "PHX", "name": "Other club, same code"}
    assert _board(g, 14.5) == EXPECTED["id"][14.5]
