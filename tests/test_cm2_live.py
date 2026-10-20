"""cm-2 live for kills Maps 1-2 (owner 2026-10-20, ahead of the registered review): the HLTV-route perf-2 projection is
replaced by cm-2's projection and spread (version perf-cm2) and perf-2's numbers are kept beside it; any line cm-2 cannot
forecast stays on perf-2 with the reason saved; HS, Map 1, Maps 1-3 and the BO3.gg fallback are untouched; CM2_LIVE=0 is the
rollback; the grader prints cm-2's projection; the fh-1 shadow still compares against perf-2. Every team, player and number
is a TEST FIXTURE."""
import copy
from types import SimpleNamespace

import pytest

import cm2
import cs2_scout
import grader
import perf_shadow
from tests.test_audit13_grader import _base
from tests.test_cm1 import DAY, OPP, PLAYER, TEAM
from tests.test_cm2 import T  # noqa: F401 - pytest fixture
from tests.test_fh1_shadow import fake  # noqa: F401 - pytest fixture

PB = {"player": {"nick": "tester"}, "playing_for": "Team", "_env": {"tonight": "online"}}


def _out():
    return {"version": "perf-2", "lambda": 30.0, "lambda_kills": 30.0, "sd": 7.0, "rate": 0.71, "opp_factor": 1.0,
            "rounds_per_map": 21.0, "spread_c": 1.27, "data_quality": "ok", "hltv_player_ids": [PLAYER], "hltv_opp_team": OPP,
            "player_maps": 140, "hltv_data": {"input_revision": "N0/test"}}


@pytest.fixture
def live(monkeypatch, T):  # noqa: F811
    monkeypatch.delenv("CM2_LIVE", raising=False)
    monkeypatch.setattr(cm2, "current", lambda *a, **k: T)
    monkeypatch.setattr(perf_shadow, "_team_id", lambda name: {"Team": TEAM, "Opp": OPP}.get(name))
    return T


def test_cm2_replaces_the_projection_and_keeps_perf2_beside_it(live):
    pf = cs2_scout._cm2_live(_out(), PB, DAY, "online")
    ref = cm2.projection(live, [PLAYER], TEAM, OPP, DAY, False)
    assert pf["version"] == "perf-cm2" and pf["lambda"] == pf["lambda_kills"] == ref["lambda"] and pf["sd"] == ref["sd"]
    assert pf["perf2"]["version"] == "perf-2" and pf["perf2"]["lambda"] == 30.0 and pf["perf2"]["sd"] == 7.0
    # the same arithmetic as the shadow record at any line
    assert abs(cm2.forecast(ref["features"], 20.5)["lambda"] - ref["lambda"]) < 1e-12
    assert pf["cm2_live"]["features"] == ref["features"] and pf["cm2_live"]["model"] == "cm-2"


def test_a_line_cm2_cannot_forecast_stays_on_perf2_with_the_reason(live, monkeypatch):
    assert cs2_scout._cm2_live(_out(), PB, DAY, None)["cm2_live"] == {"ineligible": "target environment (LAN / online) unknown"}
    no_opp = dict(_out(), hltv_opp_team=None)
    r = cs2_scout._cm2_live(no_opp, PB, DAY, "lan")
    assert r["version"] == "perf-2" and r["lambda"] == 30.0 and "opponent not identified" in r["cm2_live"]["ineligible"]
    monkeypatch.setattr(cm2, "current", lambda *a, **k: None)
    assert cs2_scout._cm2_live(_out(), PB, DAY, "lan")["cm2_live"]["ineligible"] == "cm-2 tables not built yet"
    monkeypatch.setattr(cm2, "current", lambda *a, **k: 1 / 0)
    r = cs2_scout._cm2_live(_out(), PB, DAY, "lan")
    assert r["version"] == "perf-2" and r["cm2_live"]["ineligible"] == "cm-2 unavailable (ZeroDivisionError)"


def test_rollback_flag_keeps_perf2(live, monkeypatch):
    monkeypatch.setenv("CM2_LIVE", "0")
    assert cs2_scout._cm2_live(_out(), PB, DAY, "online") == _out()


@pytest.mark.parametrize("prop,scope,route", [("kills", "12", "cm2"), ("hs", "12", "perf2"), ("kills", "1", "perf2"),
                                              ("kills", "123", "perf2")])
def test_only_kills_maps_1_2_on_the_hltv_route(live, monkeypatch, prop, scope, route):
    import perf_live
    monkeypatch.setattr(perf_live, "projection", lambda *a, **k: _out())
    monkeypatch.setattr(cs2_scout, "match_day", lambda s: DAY)
    pf = cs2_scout._perf_input_once(dict(PB), SimpleNamespace(prop=prop, opponent="Opp", match_start=None), scope)
    assert pf["version"] == ("perf-cm2" if route == "cm2" else "perf-2")


def test_the_grader_grades_on_cm2_and_says_so(live):
    d = _base()
    pf = cs2_scout._cm2_live(dict(d["perf"], hltv_player_ids=[PLAYER], hltv_opp_team=OPP), PB, DAY, "online")
    g = grader.grade_leg(dict(copy.deepcopy(d), perf=pf, prop="kills", scope="12"))
    assert g["projection_source"] == "perf-cm2" and g["lambda_final"] == round(pf["lambda"], 2)
    assert any(f.startswith(f"Projection {pf['lambda']:.1f} from the complete model (perf-cm2") and
               f"perf-2 projected {d['perf']['lambda']:.1f}" in f for f in g["flags"])


def test_the_fh1_shadow_still_compares_against_perf2_on_cm2_lines(fake, monkeypatch):  # noqa: F811
    from tests import test_fh1_shadow as F
    G = grader
    monkeypatch.setenv("CM2_LIVE", "0")
    res, r = F.live_res()                                               # perf-2 as the live grade would have been
    base, _ = perf_shadow.build(copy.deepcopy(res), r)
    gi = res["grader_inputs"]
    pf2 = gi["perf"]
    keep = {k: pf2.get(k) for k in cs2_scout.CM2_KEEP if k in pf2}
    pf = dict(pf2, version="perf-cm2", perf2=keep, cm2_live={"model": "cm-2"}, **{"lambda": pf2["lambda"] + 2.0, "sd": pf2["sd"]})
    gi2 = dict(gi, perf=pf)
    g = G.grade_leg(copy.deepcopy(gi2))
    assert g["projection_source"] == "perf-cm2" and g["p_more"] != res["grade"]["p_more"]
    rec, _ = perf_shadow.build(dict(res, grade=g, grader_inputs=gi2), r)
    assert rec["n0_check"]["published_reproduced"] and rec["n0_check"]["exact"]
    assert rec["published"]["proj_version"] == "perf-cm2" and rec["published"]["p_more"] == g["p_more"]
    assert rec["live"] == base["live"] and rec["fh1"] == base["fh1"]  # both arms still replay perf-2 exactly


def test_an_opponent_outside_n0_is_found_by_the_shadows_rule(live):
    """perf_live gives no opponent id when all his maps sit outside N0; cm-2 (full history) takes the canonical id by name,
    exactly as perf_shadow.build does for the cm-2 record."""
    r = cs2_scout._cm2_live(dict(_out(), hltv_opp_team=None), PB, DAY, "online", "Opp")
    assert r["version"] == "perf-cm2" and r["lambda"] == cm2.projection(live, [PLAYER], TEAM, OPP, DAY, False)["lambda"]
    assert cs2_scout._cm2_live(dict(_out(), hltv_opp_team=None), PB, DAY, "online", "Unknown")["version"] == "perf-2"


def test_an_hltv_blip_in_the_team_lookup_is_transient_never_a_quiet_perf2_grade(live, monkeypatch):
    import psycopg2

    def boom(name):
        raise psycopg2.OperationalError("server closed the connection unexpectedly")
    monkeypatch.setattr(perf_shadow, "_team_id", boom)
    assert cs2_scout._transient_hltv_error(psycopg2.OperationalError("x"))
    with pytest.raises(psycopg2.OperationalError):
        cs2_scout._cm2_live(_out(), PB, DAY, "online", "Opp")
