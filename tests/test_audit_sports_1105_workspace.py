"""Owner full-site audit 2026-11-05 - sports group, workspace side (workspace_sports.py never imports sports, so these
live with the root tests): Underdog one-sided / quarter lines never read "Standard" or sit on the full-game board (S1/S2),
the period names, and ONE team code per team (B8: UTA, never UTAH). Every name / number here is a TEST FIXTURE."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "sports"


def _names_team_abbr():
    """sports/names.py TEAM_ABBR read from its source (CS2-side tests never import the sports package)."""
    tree = ast.parse((ROOT / "sports" / "names.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "TEAM_ABBR")
    return ast.literal_eval(node.value)


def test_workspace_line_type_period_and_full_game_only():
    import workspace_sports as ws
    assert ws.line_type_of({"p": "underdog", "v": "balanced", "o": [1, 1]}) == "standard"
    assert ws.line_type_of({"p": "underdog", "v": "balanced", "o": [1, 0]}) == "over only"     # stored before the fix
    assert ws.line_type_of({"p": "underdog", "v": "one_sided", "o": [1, 0]}) == "over only"
    assert ws.line_type_of({"p": "prizepicks", "v": "demon", "o": [1, 0]}) == "demon"
    assert ws.line_type_of({"p": "prizepicks", "v": "standard", "o": [1, 1]}) == "standard"
    assert ws.PERIOD["q1"] == "First quarter" and ws.PERIOD["first_half"] == "First half"
    assert ws.full_game_key("e|p|points|full_game") and not ws.full_game_key("e|p|points|q1")
    doc = {"events": {"e1": {"home": {"abbr": "UTAH"}, "away": {"abbr": "GS"}, "start_utc": "2099-01-01T00:00:00Z"}},
           "fc": {}, "stats": {"points": "Points"}}
    m = {"k": "e1|nba:player:espn:1|points|full_game", "e": "e1", "pid": "nba:player:espn:1", "n": "P", "tm": "UTAH",
         "st": "points", "q": []}
    rec, x = ws.record_of("nba", doc, m, {"p": "underdog", "l": 21.5, "v": "balanced", "o": [1, 1], "r": "r1"}, {})
    assert rec["event"]["teams"] == ["GSW", "UTA"] and rec["player"]["team"] == "UTA"        # one code: UTA, never UTAH
    assert rec["market"]["line_type"] == "standard" and x["line_type"] == "standard"


def test_one_team_code_table_everywhere():
    import workspace_sports as ws
    table_ = _names_team_abbr()
    assert ws.TEAM_ABBR == table_
    js = (WEB / "labels.js").read_text(encoding="utf-8")
    blk = re.search(r"var TEAM_ABBR = \{(.*?)\n  \};", js, re.S).group(1)
    for lg, table in table_.items():
        for espn, code in table.items():
            assert re.search(rf"{lg}: \{{[^}}]*\b{espn}: \"{code}\"", blk), (lg, espn)
