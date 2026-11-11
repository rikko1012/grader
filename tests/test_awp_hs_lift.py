"""awp-hs-1 (candidate, default off): lifting the AWPer-on-HS 0U gate changes the stake only, only when enabled, and is
not wired into the live path (model tag files untouched)."""
import copy
import inspect
import pathlib

import awp_hs_lift
import grader

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _leg(**kw):
    d = {"prop": "hs", "line": 12.5, "line_type": "standard", "scope": "12", "maps_played": 30, "sample_maps": 60,
         "kpr": 0.70, "hs_pct": 35.0, "rating": 1.05, "teammate_ratings": [1.0, 0.98, 1.02, 0.95], "sniping": 100,
         "roster_confirmed": True, "p_team_market": 0.55, "market_quality": "HARD", "bo": 3,
         "recent_avg": 14.0, "recent_median": 14.0, "recent": {"n": 10, "overs": 6},
         "perf": {"version": "perf-2", "lambda": 14.2, "sd": 4.0, "rate": 0.65, "opp_factor": 1.0,
                  "rounds_per_map": 21.5, "data_quality": "ok"}}
    d.update(kw)
    return d


def _cfg(on):
    cfg = copy.deepcopy(grader.CONFIG)
    cfg["awp_hs_lift"] = on
    return cfg


def test_default_is_off_and_identical_to_the_grader(monkeypatch):
    monkeypatch.delenv("CS2_AWP_HS_LIFT", raising=False)
    assert awp_hs_lift.enabled() is False
    d = _leg()
    assert awp_hs_lift.grade(d) == grader.grade_leg(d)
    monkeypatch.setenv("CS2_AWP_HS_LIFT", "0")
    assert awp_hs_lift.grade(d) == grader.grade_leg(d)


def test_on_lifts_only_the_stake():
    d = _leg()
    off, on = grader.grade_leg(d), awp_hs_lift.grade(d, _cfg(True))
    assert off["units"] == 0.0 and off["zero_reason"] == awp_hs_lift.REASON
    assert on["units"] > 0 and on["zero_reason"] is None
    assert on["units"] == on["units_free"]                      # what the chance earns, no more
    for k in ("p_more", "p_less", "grade", "grade_less", "model_grade", "lambda_final", "sd", "probs_raw",
              "projection_source", "line", "prop"):
        assert on[k] == off[k], k
    assert awp_hs_lift.NOTE in on["flags"]
    assert not any(f == "0 units: " + awp_hs_lift.REASON for f in on["flags"])
    assert on["awp_hs_lift"]["version"] == "awp-hs-1" and on["awp_hs_lift"]["units_before"] == 0.0


def test_inputs_are_not_mutated():
    d = _leg()
    before = copy.deepcopy(d)
    awp_hs_lift.grade(d, _cfg(True))
    assert d == before


def test_env_switch(monkeypatch):
    monkeypatch.setenv("CS2_AWP_HS_LIFT", "1")
    assert awp_hs_lift.enabled() is True
    assert awp_hs_lift.grade(_leg())["units"] > 0
    monkeypatch.setenv("CS2_AWP_HS_LIFT", "0")
    assert awp_hs_lift.grade(_leg())["units"] == 0.0


def test_other_zero_unit_rules_still_hold():
    standin = awp_hs_lift.grade(_leg(is_standin=True), _cfg(True))
    assert standin["units"] == 0.0 and "stand-in" in standin["zero_reason"]
    assert awp_hs_lift.REASON not in standin["zero_reason"]
    thin = awp_hs_lift.grade(_leg(maps_played=3), _cfg(True))
    assert thin["units"] == 0.0 and "recent Maps 1-2" in thin["zero_reason"]


def test_caps_still_apply():
    soft = awp_hs_lift.grade(_leg(market_quality="SOFT", perf=dict(_leg()["perf"], **{"lambda": 19.0})), _cfg(True))
    assert soft["units_free"] > 1.5 and soft["units"] == 1.5      # SOFT BOOK cap survives the lift


def test_kills_props_and_non_awpers_are_untouched():
    for d in (_leg(prop="kills", line=27.5, perf=dict(_leg()["perf"], **{"lambda": 29.0, "sd": 6.0})),
              _leg(sniping=5), _leg(sniping=None), _leg(sniping=40)):
        assert awp_hs_lift.lifted_inputs(d) is None
        assert awp_hs_lift.grade(d, _cfg(True)) == grader.grade_leg(d, _cfg(True))


def test_sniping_is_read_by_the_grader_for_gate_a_only():
    """The lift works by passing sniping = 40; that is only safe while grade_leg reads `sniping` for Gate A alone."""
    src = inspect.getsource(grader.grade_leg)
    assert src.count('d.get("sniping")') == 1 and "elif snip > 40:" in src
    assert src.count("snip") == src.count("sniping") + 3        # snip = ..., if snip is None, elif snip > 40
    a, b = grader.grade_leg(_leg(sniping=5)), grader.grade_leg(_leg(sniping=40))
    assert a == b


def test_not_wired_into_the_live_path():
    """A candidate only: none of the files in the model tag (or the server) imports it."""
    for f in ("grader.py", "cs2_scout.py", "roles.py", "bo3.py", "hltv.py", "pick_verdict.py", "picklog.py", "server.py",
              "perf.py", "perf_live.py", "sim.py"):
        assert "awp_hs_lift" not in (ROOT / f).read_text(encoding="utf-8"), f


def test_preview_units_from_logged_flags():
    pu = awp_hs_lift.preview_units
    gate = "0 units: AWPer on a headshot prop"
    assert pu(0.60, [gate], "More") == (0.5, "")
    assert pu(0.66, [gate, "SOFT BOOK: bookmaker price (Thunderpick) - max 1.5U"], "Less")[0] == 1.0
    assert pu(0.70, [gate, "SOFT BOOK: x - max 1.5U", "RUST: last official map 30 days ago - max 1U"], "More") == (1.0, "cap 1U")
    assert pu(0.66, [gate, "TAPE FIGHT: model Less 61% but last 10 went over 6/10 - max 0.5U"], "Less") == (0.5, "cap 0.5U")
    assert pu(0.66, [gate, "TAPE FIGHT: model Less 61% but last 10 went over 6/10 - max 0.5U"], "More")[0] == 1.0
    assert pu(0.60, [gate, "0 units: stand-in"], "More")[0] == 0.0
    assert pu(0.60, [gate, "FORM FIGHT: model says Less but the last 10 matches went over 7/10 - 0 units"], "Less")[0] == 0.0
    assert pu(0.54, [gate], "More")[0] == 0.0
