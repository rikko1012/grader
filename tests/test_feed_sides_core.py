"""Feed sides reach the grade (2026-10-14 owner): Less follows the platform feed for the exact line when known; unknown
keeps the line-type rule. ALL inputs are LABELLED TEST FIXTURES."""
import inspect

import grader
from pick_verdict import verdict

FIX = "TEST FIXTURE"


def _d(**kw):
    d = {"prop": "kills", "line": 33.5, "line_type": "standard", "scope": "12", "maps_played": 40, "kpr": 0.7, "hs_pct": 48,
         "roster_confirmed": True, "label": FIX,
         "perf": {"version": "perf-2", "lambda": 29.0, "lambda_kills": 29.0, "sd": 7.8, "hs_per_kill": 0.5,
                  "spread_c_kills": 1.457, "data_quality": "ok"}}
    d.update(kw)
    return d


def test_over_only_standard_line_never_stakes_less():
    free = grader.grade_leg(_d())                                     # model leans Under on a standard line
    assert verdict(free)["side"] == "Less" and free["units_less"] > 0
    over_only = grader.grade_leg(_d(less_available=False))            # feed: allowed_wager_types 'over' (live case)
    v = verdict(over_only)
    assert over_only["units_less"] == 0 and v["units"] == 0 and "not confirmed by the feed" in over_only["grade_less"]


def test_goblin_less_only_when_the_feed_confirms_it():
    unknown = grader.grade_leg(_d(line_type="goblin"))
    assert unknown["units_less"] == 0 and unknown["less_available"] is False
    confirmed = grader.grade_leg(_d(line_type="goblin", less_available=True))            # SYNTHETIC feed statement
    assert confirmed["units_less"] > 0 and confirmed["less_available"] is True


def test_board_rows_pass_their_feed_sides_to_the_grade():
    import cs2_scout, server
    assert "less_available" in inspect.signature(cs2_scout.grade).parameters
    src = inspect.getsource(server)
    assert src.count('less_available=r.get("under_available")') == 2
