"""rr-1 / RR-KS shadow candidate (FACTOR_AUDIT round 6). ALL inputs are LABELLED TEST FIXTURES."""
import importlib.util
import os

import grader
import perf
import perf_market as PM
from pick_verdict import verdict

FIXTURE = "TEST FIXTURE"
REF = os.path.join(os.environ.get("TEMP", "/tmp"), "claude", "C--Users-tikic-OneDrive-Documents-cs2-scout",
                   "6847ff60-df65-4b0c-851b-1b79f55ef3bf", "scratchpad", "design", "round-race", "rr_live.py")


def _pf(scope="12", lam_k=30.0, hsr=0.5):
    return {"version": "perf-2", "lambda": lam_k, "lambda_kills": lam_k, "sd": perf.SPREAD[("kills", scope)] * lam_k ** 0.5,
            "rounds_per_map": perf.PARAMS["base_r"], "maps": perf.MAPS[scope], "hs_per_kill": hsr, "spread_c_kills": 1.457,
            "data_quality": "ok", "round_share": {"team": 0.53, "opp": 0.48, "team_maps": 40, "opp_maps": 35}, "label": FIXTURE}


def _gi(**kw):
    d = {"prop": "kills", "line": 29.5, "scope": "12", "bo": 3, "maps_played": 40, "kpr": 0.7, "hs_pct": 48,
         "roster_confirmed": True, "perf": _pf(), "p_team_market": 0.66, "team_ml": -194, "opp_ml": 194,
         "market_quality": "SOFT", "odds_provenance": {"selected": "thunderpick", "retrieved_at": "t"}, "label": FIXTURE}
    d.update(kw)
    return d


def test_identical_to_the_frozen_reference_implementation():
    if not os.path.exists(REF):
        import pytest
        pytest.skip("historical rr reference implementation is not available")
    sp = importlib.util.spec_from_file_location("rr_ref", REF)
    ref = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(ref)
    for scope, bo in (("1", 1), ("1", 3), ("12", 3), ("12", 5), ("123", 3), ("123", 5)):
        for p in (0.2, 0.5, 0.66, 0.9):
            for prop in ("kills", "hs"):
                a = ref.project_rr(_pf(scope), p, bo, scope, prop, 0.53, 0.48, "rr-ks")
                b = PM.project_rr(_pf(scope), p, bo, scope, prop, 0.53, 0.48, "rr-ks")
                assert abs(a["lambda"] - b["lambda"]) < 1e-12 and abs(a["sd"] - b["sd"]) < 1e-12


def test_no_market_bo2_or_unknown_format_is_exactly_perf2():
    for p, bo in ((None, 3), (0.6, 2), (0.6, None), (0.6, 1)):      # BO1 beyond Map 1 has no market term either
        r = PM.project_rr(_pf("12"), p, bo, "12")
        assert not r["market_used"] and abs(r["lambda_kills"] - 30.0) < 1e-9
    e = PM.shadow_entry(_gi(p_team_market=None, team_ml=None, opp_ml=None), None)
    assert e["status"].startswith("no market term") and e["market_used"] is False


def test_market_moves_the_projection_and_headshots_stay_conditional():
    fav = PM.project_rr(_pf(), 0.8, 3, "12", "kills", 0.5, 0.5)
    dog = PM.project_rr(_pf(), 0.2, 3, "12", "kills", 0.5, 0.5)
    assert fav["lambda_kills"] > 30.0 > dog["lambda_kills"]
    hs = PM.project_rr(_pf(), 0.8, 3, "12", "hs", 0.5, 0.5)
    assert abs(hs["lambda"] - fav["lambda_kills"] * 0.5) < 1e-9 and hs["lambda_kills"] == fav["lambda_kills"]
    # de-duplication: a team whose history already wins 65% of rounds gains less from the same price
    strong = PM.project_rr(_pf(), 0.8, 3, "12", "kills", 0.65, 0.4)
    assert strong["lambda_kills"] < fav["lambda_kills"]


def test_unreadable_round_share_gives_no_number_never_a_stand_in():
    e = PM.shadow_entry(_gi(perf=dict(_pf(), round_share={"error": "OperationalError: HLTV down"})), None)
    assert e["status"] == "round share unavailable" and "lambda" not in e


def test_shadow_entry_grades_with_the_same_rules_and_never_touches_the_live_grade():
    gi = _gi()
    live = grader.grade_leg(dict(gi))
    e = PM.shadow_entry(gi, live)
    assert e["status"] == "ok" and e["version"] == "rr-1" and e["market_source"] == "thunderpick"
    assert e["lambda"] != live["lambda_final"] and e["side"] in ("More", "Less", None) and e["units"] <= 1.5
    assert grader.grade_leg(dict(gi))["lambda_final"] == live["lambda_final"]       # live projection unchanged
    assert verdict(grader.grade_leg(dict(gi))) == verdict(live)
    assert PM.shadow_entry(_gi(perf=dict(_pf(), version="perf-2-bo3")), None) is None
