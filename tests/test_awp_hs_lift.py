"""awp-hs-1 (live 2026-11-12): the AWPer-on-HS 0U gate is lifted - a stake rule only; CS2_AWP_HS_LIFT=0 restores the
exact old gate; the card explainer follows the switch."""
import copy
import pathlib

import awp_hs_lift
import grade_display
import grader
from pick_verdict import verdict

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


def _no_stake(x):
    """The grade with every stake-carrying field removed, at any depth."""
    drop = {"units", "units_less", "zero_reason", "status", "flags", "stake", "stake_without_market_rule"}
    if isinstance(x, dict):
        return {k: _no_stake(v) for k, v in x.items() if k not in drop}
    if isinstance(x, list):
        return [_no_stake(v) for v in x]
    return x


def test_default_is_on_and_env_zero_rolls_back(monkeypatch):
    monkeypatch.delenv("CS2_AWP_HS_LIFT", raising=False)
    assert grader.awp_hs_1_enabled() is True and awp_hs_lift.enabled() is True
    assert grader.grade_leg(_leg())["units"] > 0
    monkeypatch.setenv("CS2_AWP_HS_LIFT", "0")
    assert grader.awp_hs_1_enabled() is False
    g = grader.grade_leg(_leg())
    assert g["units"] == 0.0 and g["zero_reason"] == "AWPer on a headshot prop"
    assert "0 units: AWPer on a headshot prop" in g["flags"]
    monkeypatch.setenv("CS2_AWP_HS_LIFT", "1")
    assert grader.grade_leg(_leg())["units"] > 0


def test_only_the_stake_moves():
    off, on = grader.grade_leg(_leg(), _cfg(False)), grader.grade_leg(_leg(), _cfg(True))
    assert off["units"] == 0.0 and off["zero_reason"] == grader.AWP_HS_GATE_REASON
    assert on["units"] > 0 and on["zero_reason"] is None
    assert on["units"] == on["units_free"]                       # what the chance earns, no more
    assert _no_stake(on) == _no_stake(off)                       # side, chance, letter, projection, spread: identical
    for k in ("p_more", "p_less", "grade", "grade_less", "model_grade", "lambda_final", "sd", "probs_raw"):
        assert on[k] == off[k], k
    assert grader.AWP_HS_1_NOTE in on["flags"] and grader.AWP_HS_1_NOTE not in off["flags"]
    assert ([f for f in on["flags"] if f != grader.AWP_HS_1_NOTE]
            == [f for f in off["flags"] if f != "0 units: AWPer on a headshot prop"])


def test_rollback_is_the_old_gate_exactly():
    g = grader.grade_leg(_leg(), _cfg(False))
    assert g["flags"][-1] == "0 units: AWPer on a headshot prop"
    assert not any("awp-hs-1" in f for f in g["flags"])
    assert g["units"] == 0.0 and g["units_less"] == 0.0


def test_other_zero_unit_rules_still_hold():
    standin = grader.grade_leg(_leg(is_standin=True), _cfg(True))
    assert standin["units"] == 0.0 and standin["zero_reason"] == "stand-in"
    thin = grader.grade_leg(_leg(maps_played=3), _cfg(True))
    assert thin["units"] == 0.0 and "recent Maps 1-2" in thin["zero_reason"]


def test_caps_still_apply():
    soft = grader.grade_leg(_leg(market_quality="SOFT", perf=dict(_leg()["perf"], **{"lambda": 19.0})), _cfg(True))
    assert soft["units_free"] > 1.5 and soft["units"] == 1.5      # SOFT BOOK cap survives the lift


def test_kills_props_and_non_awpers_are_untouched():
    for d in (_leg(prop="kills", line=27.5, perf=dict(_leg()["perf"], **{"lambda": 29.0, "sd": 6.0})),
              _leg(sniping=5), _leg(sniping=None), _leg(sniping=40)):
        assert grader.grade_leg(d, _cfg(True)) == grader.grade_leg(d, _cfg(False))


def test_verdict_and_display_carry_the_stake():
    g = grader.grade_leg(_leg(), _cfg(True))
    v = verdict(g)
    assert v["units"] > 0 and v["grade"] != "LEAN"
    d = grade_display.annotate(g, v)
    assert d["model_grade"] == g["grade"] and not d.get("stake_reason")
    g0 = grader.grade_leg(_leg(), _cfg(False))
    v0 = verdict(g0)
    assert v0["units"] == 0.0 and v0["side"] == v["side"]
    assert grade_display.annotate(g0, v0)["stake_reason"] == "AWPer on a headshot prop"


def test_member_flag_is_hidden_and_card_explainer_follows_the_switch(monkeypatch):
    import server
    assert any(grader.AWP_HS_1_NOTE.startswith(k) and t is None for k, t in server._FLAG_RULES)
    monkeypatch.delenv("CS2_AWP_HS_LIFT", raising=False)
    assert server.stake_caps()["awp_hs_gate"] is False
    monkeypatch.setenv("CS2_AWP_HS_LIFT", "0")
    assert server.stake_caps()["awp_hs_gate"] is True
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'd.caps.awp_hs_gate ? ["AWPer on a headshot line: 0 units"]' in html      # only shown while rolled back
    assert "an AWPer on a headshot line get 0 units" not in html
    assert "AWP use (HS stake)" not in html


def test_rule_is_in_the_model_tag_and_the_frozen_files_are_untouched():
    import picklog
    assert '"grader.py"' in pathlib.Path(picklog.__file__).read_text(encoding="utf-8")
    assert "awp_hs_1_enabled" in (ROOT / "grader.py").read_text(encoding="utf-8")
    for f in ("cs2_scout.py", "roles.py", "bo3.py", "hltv.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert "awp_hs_1" not in src and "awp_hs_lift" not in src and "CS2_AWP_HS_LIFT" not in src, f


def test_awper_hs_pick_counts_in_the_record_whatever_its_stake():
    """Owner 2026-11-11: no new record lines. The record counts every P1 unit at the rule's chance, staked or not, so an
    AWPer headshot pick that carried 0 units before awp-hs-1 is inside the existing totals, letter and market."""
    import results_stated
    from results_view import RULE
    row = lambda fid, units, over, p: {"match": f"m{fid}", "p": p, "over": over, "prop": "hs", "scope": "12", "units": units,
                                       "forecast_id": fid, "start": "2026-11-01T12:00:00+00:00", "logged": "2026-11-01T08:00:00+00:00"}
    gated = row(1, 0.0, 0, 0.38)          # AWPer HS Under at 62%, 0 units under the old gate: a win
    staked = row(2, 0.5, 1, 0.62)         # the same pick shape with a stake (awp-hs-1 forward)
    below = row(3, 0.0, 1, RULE - 0.01)   # under the record's rule: not counted, as for any pick
    picks, pushed, _ = results_stated.rule_picks({"rows": [gated, staked, below], "pushes": []})
    assert len(picks) == 2 and [x["win"] for x in picks] == [1, 1]
    assert all(x["market"] == "Headshots Maps 1–2" and x["letter"] == "B" for x in picks)
    doc = results_stated.stated_observed({"rows": [gated, staked, below], "pushes": [], "at": "2026-11-02T00:00:00+00:00"})
    assert doc["totals"]["pushes"] == 0 and [g["name"] for g in doc["by_market"]] == ["Headshots Maps 1–2"]
    only_gated, _, _ = results_stated.rule_picks({"rows": [gated], "pushes": []})
    assert len(only_gated) == 1                                   # the stake plays no part in the record rule
    src = (ROOT / "results_stated.py").read_text(encoding="utf-8") + (ROOT / "public_record.py").read_text(encoding="utf-8")
    assert "AWP" not in src and "zero_reason" not in src          # no gate-keyed filter, no special group


def test_preview_units_from_logged_flags():
    pu = awp_hs_lift.preview_units
    gate = "0 units: AWPer on a headshot prop"
    assert pu(0.60, [gate], "More") == (0.5, "")
    assert pu(0.66, [gate, "SOFT BOOK: bookmaker price (Thunderpick) - max 1.5U"], "Less")[0] == 1.0
    assert pu(0.70, [gate, "SOFT BOOK: x - max 1.5U", "RUST: last official map 30 days ago - max 1U"], "More") == (1.0, "cap 1U")
    assert pu(0.66, [gate, "TAPE FIGHT: model Less 61% but last 10 went over 6/10 - max 0.5U"], "Less") == (0.5, "cap 0.5U")
    assert pu(0.60, [gate, "0 units: stand-in"], "More")[0] == 0.0
    assert pu(0.60, [gate, "FORM FIGHT: model says Less but the last 10 matches went over 7/10 - 0 units"], "Less")[0] == 0.0
    assert pu(0.54, [gate], "More")[0] == 0.0
