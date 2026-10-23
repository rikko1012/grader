"""lm-0 (CHALLENGERS.md, frozen 2026-10-23): the old pipeline regraded with its Over / Under margins at 0 on the SAME saved
inputs, logged as a shadow. The live grade must be byte-identical (owner decision: the live Over margin stays 1.0 / 0.6);
performance-model grades are logged as not eligible (no probability), never as identical copies. Inputs = the labelled
test fixture tests/fixtures/grader_inputs_sample.json (one production forecast's saved grader inputs)."""
import copy
import json
from pathlib import Path

import grader
import learning
import picklog

GI = json.loads((Path(__file__).parent / "fixtures" / "grader_inputs_sample.json").read_text(encoding="utf-8"))["inputs"]


def legacy(prop, line):
    return dict(copy.deepcopy(GI), perf=None, prop=prop, line=line)     # no performance-model input -> old pipeline


def perf(prop, line):
    return dict(copy.deepcopy(GI), prop=prop, line=line)


def dump(g):
    return json.dumps(g, sort_keys=True, default=str)


def test_live_margin_config_unchanged():
    assert grader.CONFIG["over_margin"] == {"kills": 1.0, "hs": 0.6}
    assert grader.CONFIG["under_margin"] == {"kills": 0.0, "hs": 0.0}


def test_live_grades_byte_identical_and_lm0_logged():
    cases = [legacy("kills", 24.5), legacy("kills", 35.5), legacy("hs", 10.5), perf("kills", 29.0), perf("hs", 10.5)]
    for gi in cases:
        before = dump(grader.grade_leg(copy.deepcopy(gi)))
        cfg_before = dump(grader.CONFIG)
        g = grader.grade_leg(gi)
        f = picklog._factors({"grader_inputs": gi, "grade": g})
        assert dump(grader.grade_leg(copy.deepcopy(gi))) == before            # live output unchanged after the shadow ran
        assert dump(g) == before and dump(grader.CONFIG) == cfg_before        # nothing mutated
        assert f["shadow"]["lm0"]["version"] == picklog.LM0["version"]


def test_legacy_over_regraded_without_margin():
    gi = legacy("kills", 24.5)
    g = grader.grade_leg(copy.deepcopy(gi))
    assert g["projection_source"].startswith("legacy") and g["p_more"] > 0.5
    e = picklog.legacy_margin_shadow(gi, g)
    assert e["eligible"] is True and e["source"].startswith("legacy")
    assert e["p_more"] > g["p_more"] and e["changed"] is True                 # the margin no longer lowers the Over
    assert e["raw"] == g["probs_raw"]                                         # raw chances: before caps / margins, same
    assert e["live"]["p_more"] == g["p_more"]
    assert set(e["rec"]) == {"side", "grade", "units"} and e["rec"]["side"] == "Over"
    # exactly the documented config: only the two margin keys at 0
    want = grader.grade_leg(copy.deepcopy(gi), dict(grader.CONFIG, over_margin={"kills": 0.0, "hs": 0.0},
                                                     under_margin={"kills": 0.0, "hs": 0.0}))
    assert e["p_more"] == want["p_more"] and e["lambda"] == want["lambda_final"]


def test_legacy_hs_over_and_under_side():
    gi = legacy("hs", 10.5)
    g = grader.grade_leg(copy.deepcopy(gi))
    assert picklog.legacy_margin_shadow(gi, g)["p_more"] > g["p_more"]
    gi = legacy("kills", 35.5)                                                # Under-favoured: the margin never applied
    g = grader.grade_leg(copy.deepcopy(gi))
    e = picklog.legacy_margin_shadow(gi, g)
    assert g["p_more"] < 0.5 and e["eligible"] is True and e["p_more"] == g["p_more"] and e["changed"] is False


def test_performance_model_grades_not_eligible():
    for gi in (perf("kills", 29.0), perf("hs", 10.5)):
        g = grader.grade_leg(copy.deepcopy(gi))
        assert g["projection_source"].startswith("perf")
        e = picklog.legacy_margin_shadow(gi, g)
        assert e["eligible"] is False and "p_more" not in e and "raw" not in e and e["live_source"] == g["projection_source"]


def _unit(fid, match, p, over, logged="2026-10-24T10:00:00+00:00"):
    return {"forecast_id": fid, "match": match, "p": p, "over": over, "prop": "kills", "scope": "12", "version": "v1",
            "logged": logged, "start": "2026-10-24T12:00:00+00:00", "units": 0.5, "lam": 30.0, "actual": 31, "line": 29.5}


def test_learning_report_counts_and_pairs(tmp_path):
    con = learning.connect(tmp_path / "learning.db")
    rows = {1: {"eligible": True, "p_more": 0.62, "live": {"p_more": 0.58}, "rec": {"side": "Over", "grade": "B", "units": 0.5}},
            2: {"eligible": True, "p_more": 0.45, "live": {"p_more": 0.45}, "rec": {"side": "Under", "grade": "LEAN", "units": 0}},
            3: {"eligible": False, "live_source": "perf-2"},
            5: {"eligible": True, "p_more": 0.6, "live": {"p_more": 0.51}},               # live chance disagrees with P1
            6: {"eligible": True, "p_more": 0.6, "live": {"p_more": 0.55}}}               # logged before the freeze
    for fid in (1, 2, 3, 4, 5, 6):
        con.execute("INSERT INTO forecast_copy (forecast_id, lm0) VALUES (?, ?)",
                    (fid, json.dumps(rows[fid]) if fid in rows else None))
    us = [_unit(1, "m1", 0.58, 1), _unit(2, "m2", 0.45, 0), _unit(3, "m3", 0.6, 1), _unit(4, "m4", 0.6, 1),
          _unit(5, "m5", 0.55, 1), _unit(6, "m6", 0.55, 1, logged="2026-10-22T10:00:00+00:00")]
    rep = learning.legacy_margin_report(con, us)
    assert rep["eligible_units"] == 2 and rep["eligible_matches"] == 2 and rep["review_reached"] is False
    assert rep["counts"] == {"not eligible (live grade on perf-2)": 1, "not logged (forecast before lm-0)": 1,
                             "rejected (stored live chance differs from the P1 chance)": 1,
                             "rejected (logged before the freeze)": 1}
    assert rep["paired"]["changed_units"] == 1 and rep["paired"]["gain"] > 0       # lm-0 nearer on the Over that hit
    assert rep["favoured_side"]["lm-0"]["overs"] == 1 and rep["brier"]["gain"] > 0


def test_lm0_copied_from_the_pick_log(tmp_path, monkeypatch):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    c = picklog._db()
    lm = {"id": "lm-0", "eligible": True, "p_more": 0.6}
    c.execute("INSERT INTO forecasts (pick_id, kind, logged_at, line, pick, p_pick, p_more, grade, units, shadow) "
              "VALUES (1, 'first', '2026-10-24T10:00:00+00:00', 29.5, 'More', 0.58, 0.58, 'B', 0.5, ?)",
              (json.dumps({"legacy": {"p_more": 0.58}, "lm0": lm}),))
    c.commit()
    c.close()
    _, fr = learning.read_picklog(tmp_path / "picks.db")
    assert json.loads(learning._lm0_of(fr[0])) == lm and "shadow" not in fr[0]      # only the lm-0 entry is read
    assert learning._lm0_of({"shadow": json.dumps({"lm0": lm})}) == json.dumps(lm, sort_keys=True)
    assert learning._lm0_of({"shadow": None}) is None
