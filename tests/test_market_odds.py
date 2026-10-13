"""market_odds.select - the ONE source of the grader's market win probability (2026-10-13). ALL odds here are LABELLED TEST
FIXTURES; the grader-input file is one saved production forecast used only to check reproducibility of the swap."""
import json
from pathlib import Path

import grader
import market_odds as M
from pick_verdict import verdict

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "grader_inputs_sample.json").read_text(encoding="utf-8"))

PM = {"found": True, "team": "Alpha", "opponent": "Bravo", "team_prob": 0.62, "opp_prob": 0.38, "team_ml": -163, "opp_ml": 163,
      "maps": {"map1": {"team_prob": 0.6}}, "liquidity": 5000, "source": "midpoint", "url": "https://polymarket.com/event/x"}
TP = {"found": True, "team": "Alpha", "opponent": "Bravo", "team_prob": 0.58, "opp_prob": 0.42, "team_ml": -138, "opp_ml": 138,
      "maps": {}, "book": "thunderpick", "provenance": {"source": "thunderpick", "policy": "tp-first-1", "odds_team": 1.65}}


def test_thunderpick_first_when_available():
    o = M.select("Alpha", "Bravo", "2026-10-14T15:00:00Z", tp=lambda *a: dict(TP), pm=lambda *a: dict(PM))
    assert o["team_prob"] == 0.58 and o["provenance"]["selected"] == "thunderpick" and o["provenance"]["fallback_reason"] is None
    assert o["maps"] == {}                                      # no Polymarket map prices mixed with a Thunderpick series price
    assert M.source_label(o) == "Thunderpick"


def test_explicit_fallback_with_the_reason():
    o = M.select("Alpha", "Bravo", None, tp=lambda *a: {"found": False, "reason": "Thunderpick quote stale"}, pm=lambda *a: dict(PM))
    assert o["team_prob"] == 0.62 and o["provenance"]["selected"] == "polymarket"
    assert o["provenance"]["fallback_reason"] == "Thunderpick quote stale" and M.source_label(o) == "Polymarket"
    assert o["maps"] == PM["maps"]                              # the fallback source keeps its own map prices


def test_thunderpick_error_never_blocks_grading_and_never_invents_50():
    def boom(*a):
        raise RuntimeError("fixture failure")
    o = M.select("Alpha", "Bravo", None, tp=boom, pm=lambda *a: {"found": False, "note": "no market"})
    assert o["found"] is False and "error" in o["provenance"]["fallback_reason"] and "team_prob" not in o
    assert M.source_label(o) is None


def test_grades_unchanged_when_thunderpick_is_not_used():
    """The provenance key added to grader inputs is never read by the grader: identical inputs -> identical grade."""
    gi = dict(SAMPLE["inputs"])
    a = grader.grade_leg(dict(gi))
    b = grader.grade_leg(dict(gi, odds_provenance={"selected": "polymarket", "fallback_reason": "policy inactive"}))
    for k in ("lambda_final", "p_more", "grade", "units", "units_less", "status"):
        assert a.get(k) == b.get(k)


def test_compare_is_reproducible_and_swaps_only_the_market_input():
    rows = [{"forecast_id": SAMPLE["forecast_id"], "inputs": SAMPLE["inputs"], "team": "Alpha", "opponent": "Bravo", "start": None}]
    look = lambda t, o, s: dict(TP, provenance=dict(TP["provenance"], label="TEST FIXTURE"))
    r1, r2 = M.compare(rows, look), M.compare(rows, look)
    assert r1["compared"] == 1 and r1 == r2                     # deterministic
    row = r1["rows"][0]
    old = grader.grade_leg(dict(SAMPLE["inputs"]))
    assert row["old"]["lambda"] == old.get("lambda_final") and row["old"]["grade"] == verdict(old).get("grade")
    assert row["new"]["source"] == "thunderpick" and abs(row["new"]["p_win"] - 0.58) < 0.01
    none = M.compare(rows, lambda *a: {"found": False, "reason": "no Thunderpick event for this match"})
    assert none["compared"] == 0 and none["skipped"] == {"no Thunderpick event for this match": 1}


def test_cs2_scout_routes_full_grades_through_the_selector():
    src = (Path(__file__).parent.parent / "cs2_scout.py").read_text(encoding="utf-8")
    assert "market_odds.select(team, args.opponent" in src and '"odds_provenance": (odds or {}).get("provenance")' in src
