"""ec-1 (owner 2026-11-05): the printed CS2 chance = the calibrated one, everywhere members see it, default on in the running
server (Render "0" turns it off). The pick log, BOARD and every logged forecast keep the model's own chance, letter and stake;
letters come from the calibrated chance through the existing thresholds; stakes are never raised; a game whose held-out test
failed keeps its calibrated chance but holds its letters (letter_containment). All names and numbers are TEST FIXTURES."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import esports_cal as ec
import grader
import letter_containment as lc
from gradecore import chance_cal as cc

LETTERS = ("A+", "A", "A-", "B+", "B", "B-")


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(cc.FLAG, "1")
    cc.reset_cache()
    yield
    cc.reset_cache()


def _row(side="More", p=0.662, units=1.0, prop="hs", grade="B+", **kw):
    return dict({"id": "r1", "side": side, "p": p, "grade": grade, "model_grade": grade if grade in LETTERS else "LEAN", "units": units,
                 "prop": prop, "state": "GRADE", "reason": f"Projection 20.1 vs line 18.5 (+1.6) - leans Over {p:.1%}",
                 "stake_reason": None, "available": True}, **kw)


# ------------------------------------------------------------------------------------------------ switch
def test_default_on_in_the_running_server_and_render_zero_turns_it_off():
    import server
    assert "SCOUT_CAL_CHANCE" in server.DEFAULT_ON
    env = {}
    server.apply_default_flags(env)
    assert env["SCOUT_CAL_CHANCE"] == "1"
    env = {"SCOUT_CAL_CHANCE": "0"}
    server.apply_default_flags(env)
    assert env["SCOUT_CAL_CHANCE"] == "0"


def test_off_every_surface_is_the_input_itself(monkeypatch):
    monkeypatch.delenv(cc.FLAG, raising=False)
    r = _row()
    rows = [r]
    res = {"grade": {"prop": "hs", "p_more": 0.662, "p_less": 0.338}, "verdict": dict(r)}
    assert ec.cs2_row(r) is r and ec.cs2_rows(rows) is rows and ec.cs2_card(res) is res
    ex = {"rows": [{"p": 0.7, "logged": "2026-11-06T00:00:00+00:00", "prop": "hs"}]}
    assert ec.forward_export(ex) is ex


# ------------------------------------------------------------------------------------------------ the row rule
@pytest.mark.parametrize("prop", ["kills", "hs"])
@pytest.mark.parametrize("side", ["More", "Less"])
def test_row_prints_the_calibrated_chance_letter_and_never_a_bigger_stake(on, prop, side):
    for i in range(50, 96):
        raw = i / 100
        for units in (0.0, 0.5, 1.0, 2.0, 3.0):
            grade = grader.grade_from_p(raw) if units else "LEAN"
            x = ec.cs2_row(_row(side, raw, units, prop, grade))
            c = cc.side_chance("cs2", prop, "over" if side == "More" else "under", raw)
            letter = grader.grade_from_p(c)
            assert x["p"] == round(c, 3) and x["p_raw"] == raw and x["units_raw"] == units and x["grade_raw"] == grade
            assert x["cal_v"] == cc.tag() and x["side"] == side                     # the side is the model's
            assert x["units"] <= units and x["units"] <= grader.units_for_p(c)       # never raised
            if letter in LETTERS:
                assert x["model_grade"] == letter and x["grade"] == (letter if x["units"] > 0 else "LEAN")
            else:
                assert x["model_grade"] == "LEAN" and x["grade"] == "LEAN" and x["units"] == 0.0 and x["stake_reason"] is None
            assert f"{c:.1%}" in x["reason"] and (f"{raw:.1%}" not in x["reason"] or f"{raw:.1%}" == f"{c:.1%}"
                                                  or "(model " in x["reason"])


def test_a_staked_pick_that_calibrates_under_55_reads_lean_and_says_why(on):
    x = ec.cs2_row(_row("More", 0.612, 0.5, "kills", "B", reason="Projection 25.1 vs line 23.5 (+1.6 kills)"))
    assert x["grade"] == "LEAN" and x["model_grade"] == "LEAN" and x["units"] == 0.0
    assert "calibrated to settled results" in x["reason"] and "model 61.2%" in x["reason"]


def test_scout_shadow_and_unsided_rows_keep_their_shape(on):
    s = ec.cs2_row(_row(state="SCOUT", grade="SCOUT", units=0.0))
    assert s["grade"] == "SCOUT" and s["units"] == 0.0 and s["p"] != s["p_raw"]
    for r in ({"side": None, "p": None, "state": "SHADOW", "shadow": True}, {"side": "More", "p": None}):
        assert ec.cs2_row(r) is r
    once = ec.cs2_row(_row())
    assert ec.cs2_row(once) is once                                                # never calibrated twice


def test_card_block_verdict_and_ladder_agree(on):
    res = {"grade": {"prop": "hs", "p_more": 0.662, "p_less": 0.338, "grade": "B+", "grade_less": "C", "units": 1.0,
                     "units_less": 0.0, "units_free": 1.5},
           "verdict": {"side": "More", "p": 0.662, "grade": "B+", "units": 1.0, "state": "GRADE", "confidence": "medium",
                       "reason": "Projection 20.1 vs line 18.5 (+1.6 headshots)"},
           "ladder": [{"line": 18.5, "side": "More", "p": 0.662, "grade": "B+", "units": 1.0, "state": "GRADE", "exact_grade": "B+"},
                      {"line": 19.5, "side": "Less", "p": 0.55, "grade": "LEAN", "units": 0.0, "state": "GRADE", "exact_grade": "B-"}],
           "dist_view": {"over": 0.66, "note": "x"}}
    out = ec.cs2_card(res)
    g, v = out["grade"], out["verdict"]
    assert res["grade"]["p_more"] == 0.662 and res["verdict"]["p"] == 0.662            # the logged dicts are untouched
    assert abs(g["p_more"] + g["p_less"] - 1) < 1e-9 and g["p_more"] == v["p"] and g["p_more_raw"] == 0.662
    assert v["units"] <= 1.0 and g["units"] <= 1.0 and g["units_free"] <= 1.5 and v["grade"] in LETTERS + ("LEAN",)
    letter = grader.grade_from_p(cc.side_chance("cs2", "hs", "over", 0.662))
    assert g["grade"] == (letter if letter in LETTERS else "C") and g["grade_less"] == "C" and g["grade_raw"] == "B+"
    assert out["ladder"][0]["p"] == v["p"] and all(x["cal_v"] for x in out["ladder"])
    assert "before calibration" in out["dist_view"]["note"] and out["cal_v"] == cc.tag()


# ------------------------------------------------------------------------------------------------ held letters
def test_a_failed_held_out_test_holds_letters_but_keeps_the_chance(on, monkeypatch):
    obj = json.loads(cc.PATH.read_text(encoding="utf-8"))
    obj["games"]["cs2"]["letters"] = "held"
    obj["sha"] = cc._sha(obj["games"])
    monkeypatch.setattr(cc, "load", lambda force=False, path=None: obj)
    assert lc.state("cs2") == {"contained": True, "stats": [], "source": "calibration"}
    assert lc.banner("cs2") == "CS2 letters are held until stated chances match results."
    x = lc.cs2_row(ec.cs2_row(_row("Less", 0.70, 1.0, "hs", "A-")), lc.state("cs2"))
    assert x["grade"] == "LEAN" and x["units"] == 0.0 and x["p"] == round(cc.side_chance("cs2", "hs", "under", 0.70, obj), 3)
    monkeypatch.setenv(cc.FLAG, "0")
    assert lc.state("cs2")["source"] == "flag off"


def test_the_committed_verdicts_decide_the_letters(on):
    obj = cc.load(force=True)
    for g in ("cs2", "lol"):
        assert (lc.state(g)["source"] == "calibration") == (obj["games"][g]["letters"] == "held")


# ------------------------------------------------------------------------------------------------ the record, going forward
def test_record_maps_only_forecasts_logged_after_go_live(on, tmp_path):
    ec.mark_live(tmp_path, now=datetime(2026, 11, 6, tzinfo=timezone.utc))
    doc = ec.mark_live(tmp_path, now=datetime(2026, 12, 1, tzinfo=timezone.utc))           # a restart never moves the start
    assert [e["from"] for e in doc["tags"]] == ["2026-11-06T00:00:00+00:00"]
    ex = {"at": "x", "rows": [{"p": 0.70, "over": 1, "prop": "hs", "logged": "2026-11-05T23:59:00+00:00"},
                              {"p": 0.70, "over": 1, "prop": "hs", "logged": "2026-11-06T00:01:00+00:00"},
                              {"p": 0.30, "over": 0, "prop": "kills", "logged": "2026-11-07T00:00:00+00:00"}], "pushes": []}
    out = ec.forward_export(ex)
    assert out["rows"][0] == ex["rows"][0]                                                 # the past stays as logged
    assert out["rows"][1]["p"] == round(cc.side_chance("cs2", "hs", "over", 0.70), 4) and out["rows"][1]["p_raw"] == 0.70
    assert out["rows"][2]["p"] == round(1 - cc.side_chance("cs2", "kills", "under", 0.70), 4)
    assert ex["rows"][1]["p"] == 0.70                                                      # a new dict


# ------------------------------------------------------------------------------------------------ consumers
def test_workspace_forecast_check_compares_the_logged_values(on):
    import workspace_api
    x = ec.cs2_row(_row("More", 0.662, 1.0, "hs", "B+"))
    fc = {"units": 1.0, "grade": "B+", "p_pick": 0.662}
    assert workspace_api._same_stake_and_grade(x, fc)
    assert not workspace_api._same_stake_and_grade(x, dict(fc, grade="B"))


def test_slip_ranking_reads_the_raw_chance(on):
    import slip_quality as sq
    rec = {"sport": "cs2", "scout": {"side": "over", "p_over": 0.53, "p_raw": 0.62, "grade": "B"},
           "offer": {"platform": "underdog", "offer_id": "1", "line": 18.5, "over": {"payout": 1.0}},
           "market": {"stat": "hs", "period": "12"}, "event": {"id": "e"}, "player": {"key": "p"}}
    table = {"k": 10, "lines": {}, "cells": {"cs2|hs|over|2": {"n": 100, "matches": 30, "printed": 0.62, "observed": 0.51}}}
    leg = sq.make_leg(rec, table)
    assert leg["p"] == 0.53 and leg["band"]["band"] == "60–65%"


def test_posts_state_the_calibrated_chance(on, monkeypatch, tmp_path):
    from tests.test_posts_lc1 import add, row
    import picklog
    import posts
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    picklog._db().close()
    db = tmp_path / "picks.db"
    add(db, 1, "tester", "hs", 18.5, "prizepicks", "More", 0.70, "A-", 1.5)
    r = ec.cs2_row(dict(row(1, "tester", "hs", 18.5, "prizepicks", "More", "A-", 1.5), p=0.70, model_grade="A-"))
    out = posts.drafts([r], "prizepicks", db)
    c = cc.side_chance("cs2", "hs", "over", 0.70)
    if r["grade"] in LETTERS and r["units"] > 0:
        d = out["drafts"][0]
        assert d["chance"] == round(c, 3) and d["chance_model"] == 0.70 and d["grade"] == grader.grade_from_p(c)
        assert f"{round(c, 3) * 100:.1f}% model estimate" in d["text"]
    else:
        assert out["drafts"] == []


def test_every_cs2_member_surface_reads_the_layer():
    src = (Path(__file__).resolve().parent.parent / "server.py").read_text(encoding="utf-8")
    body = src.split("def board_payload")[1].split("def board_stake")[0]
    assert body.index("rows = esports_cal.cs2_rows(rows)") < body.index("_cap_same_result(rows)")
    assert src.index("picklog.log_async(res, res[\"verdict\"], test=load_test)") < src.index("res = esports_cal.cs2_card(res)") \
        < src.index("res[\"verdict\"] = board_stake(res, prop, scope, line, lt") < src.index("res = letter_containment.cs2_card(res)")
    assert "esports_cal.mark_live(DATA_DIR)" in src and "results_view.member_results(esports_cal.forward_export(ex))" in src


# ------------------------------------------------------------------------------------------------ the real board and card
from tests.test_audit13_site import site, card, get as hget, _grade_stub  # noqa: E402,F401  (fixtures)
from tests.test_stake_match_id import _board  # noqa: E402


def test_board_payload_on_and_off(site, monkeypatch):
    import server
    _board(site)
    monkeypatch.delenv(cc.FLAG, raising=False)
    off = {r["id"]: r for r in server.board_payload("underdog")["rows"]}
    assert off and not any(r.get("cal_v") for r in off.values())
    monkeypatch.setenv(cc.FLAG, "1")
    cc.reset_cache()
    rows = server.board_payload("underdog")["rows"]
    assert rows and all(r["cal_v"] == cc.tag() for r in rows)
    for r in rows:
        o = off[r["id"]]
        c = cc.side_chance("cs2", "kills", "under", o["p"])
        assert r["p"] == round(c, 3) and r["p_raw"] == o["p"] and r["side"] == o["side"] == "Less"
        assert r["units"] <= o["units"] and r["units_raw"] == server.BOARD[r["id"]]["units"]
        letter = grader.grade_from_p(c)
        assert r["model_grade"] == (letter if letter in LETTERS else "LEAN")
        assert server.BOARD[r["id"]]["p"] == o["p"] and server.BOARD[r["id"]]["grade"] == "B+"        # BOARD keeps the model's own
    assert sum(r["units"] for r in rows) <= server.SAME_RESULT_CAP + 1e-9


def test_card_prints_the_calibrated_chance_and_logs_the_models_own(card, monkeypatch):
    set_grade, logged = card
    monkeypatch.setenv(cc.FLAG, "1")
    cc.reset_cache()
    set_grade(_grade_stub())                                     # Under 64.0%, B+, 1U (the model's own)
    h = hget("/api/grade?player=s0und&line=36.5&prop=kills&maps=12&book=underdog")
    assert h.code == 200
    d = h.body()
    c = cc.side_chance("cs2", "kills", "under", 0.64)
    v, g = d["verdict"], d["grade"]
    assert v["side"] == "Less" and v["p"] == round(c, 3) and v["p_raw"] == 0.64 and v["units"] <= 1.0
    assert g["p_less"] == round(c, 3) and abs(g["p_more"] + g["p_less"] - 1) < 1e-9 and g["p_less_raw"] == 0.64
    letter = grader.grade_from_p(c)
    assert v["model_grade"] == (letter if letter in LETTERS else "LEAN")
    assert logged and logged[-1]["p"] == 0.64 and logged[-1]["units"] == 1.0 and logged[-1]["grade"] == "B+"   # the pick log
