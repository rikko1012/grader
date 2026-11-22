"""bp-1 (NBA_BEST_PROP, display only) in the workspace record: a compact-board quote Sports marked q.x.bp = 1 carries
scout.featured = True on a lettered line; with no mark (flag off) or on a Lean the key is absent, so the record is main's
exactly. The board side (snapshots writes x.bp = 1 and grade_marks.json) is asserted in
sports/tests/test_grade_rules_1121.py; this module may not import sports (test_sports_isolation). Every name, id, line and
chance is a TEST FIXTURE."""
import workspace_sports as ws


def _rec(x, po=0.62, pu=0.38, line=19.5):
    q = {"p": "underdog", "r": "offer:1", "l": line, "o": [1, 1], "v": "balanced", "f": "f1", "x": x}
    m = {"k": "nba:e1|p1|points|full_game", "e": "nba:e1", "pid": "p1", "n": "Fixture Forward", "st": "points"}
    doc = {"fc": {"f1": {"m": "sm-1", "po": po, "pu": pu, "pp": 0, "mu": 23.0}}}
    return ws.record_of("nba", doc, m, q, {})[0]["scout"]


def test_featured_mark_reaches_the_record_only_when_set():
    graded = {"v": "cf-2", "ln": "over", "gr": "A", "gp": "sports-gp-1"}
    s = _rec(dict(graded, bp=1))
    assert s["grade"] == "A" and s["featured"] is True
    plain = _rec(graded)
    assert plain["grade"] == "A" and "featured" not in plain                 # flag off: main's record exactly
    lean = _rec({"v": "cf-2", "ln": "over", "gr": "Lean", "gp": "sports-gp-1", "bp": 1}, po=0.52, pu=0.48)
    assert lean["grade"] == "Lean" and "featured" not in lean               # a Lean is never the featured best prop
