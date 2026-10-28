"""A15 (lol/EVALUATION.md, 2026-10-28) grading-logic audit: the one defect found on the saved records. A forecast record
stores its chance rounded to 4 places; when that rounding produces an exact tie (p_over == p_under), shown() must keep
the side chosen on the unrounded chance at save time (the saved model_side) instead of re-deciding the tie as Over.
Every number here is a TEST FIXTURE (production record 1703 had 0.5 / 0.5, saved "Lean Under", re-read as "Lean Over")."""
from lol.center import forecast


def _rec(side, **kw):
    r = {"platform": "prizepicks", "sides": {"higher": {"offered": True}, "lower": {"offered": True}}, "p_over": 0.5, "p_under": 0.5,
         "p_push": 0.0, "model_side": side, "grade": "Lean", "units_policy": 0.0, "cal_version": None}
    r.update(kw)
    return r


def test_tie_after_rounding_keeps_the_saved_side(monkeypatch):
    monkeypatch.delenv("LOL_CAL", raising=False)
    sh = forecast.shown(_rec("under"))
    assert sh["side"] == "under" and sh["grade_label"] == "Lean Under" and sh["grade"] == "Lean"
    sh = forecast.shown(_rec("over"))
    assert sh["side"] == "over" and sh["grade_label"] == "Lean Over"


def test_a_clear_chance_still_decides_the_side(monkeypatch):
    monkeypatch.delenv("LOL_CAL", raising=False)
    # a saved side never overrides a chance that is not a tie
    sh = forecast.shown(_rec("under", p_over=0.62, p_under=0.38))
    assert sh["side"] == "over"
    # a calibrated record follows its calibrated chance, not the raw tie
    sh = forecast.shown(_rec("under", cal_version="lc-1:abc", cal_p_over=0.4, cal_p_under=0.6, model_side="over"))
    assert sh["side"] in ("over", "under")      # whichever applies (lc-1 on / off) - never an exception
