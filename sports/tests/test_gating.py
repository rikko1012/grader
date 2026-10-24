"""Stakes and letters stay gated (REGISTER.md 7-8): no market is forward-validated, no production code can pass the
stake gate, and reaching gate F's minimum sample (300 settled props from 100 games) opens a registered REVIEW - it is
never a pass and never promotes anything automatically."""
import hashlib
from pathlib import Path

import numpy as np

from sports.model import forward, grade_policy, learning
from sports.tests import helpers as h

ROOT = Path(__file__).resolve().parents[1]


def test_no_committed_market_is_forward_validated():
    reg = forward.registry()
    states = {s for v in reg.get("versions", {}).values() for s in (v.get("validation") or {}).values()}
    assert states <= {"unvalidated", "history-validated"}, states


def test_no_production_code_marks_forward_validated_or_passes_the_stake_gate():
    hits = {}
    for p in ROOT.rglob("*.py"):
        if "tests" in p.parts:
            continue
        src = p.read_text(encoding="utf-8")
        for needle in ("forward-validated", "stake_gate_passed"):
            if needle in src:
                hits.setdefault(needle, set()).add(p.relative_to(ROOT).as_posix())
    # only the policy itself names them (its states tuple, its comparison and its keyword default False)
    assert hits == {"forward-validated": {"model/grade_policy.py"}, "stake_gate_passed": {"model/grade_policy.py"}}


def test_a_forecast_carries_no_letter_and_no_stake_before_gate_f():
    o = h.offer(line=20.5)
    pmf = np.full(41, 1 / 41)
    for state in ("unvalidated", "history-validated", "forward-validated"):
        fc, pol = forward.build_forecast(o, "rid-1", h.T0, "sm-1", "sm-1:test", pmf, 0, 20.0, ("rid-1",), [],
                                         "ds-test", "cal-test", vstate=state)
        assert fc.stake_units == 0.0                    # the stake gate is never passed by forecasting code
        if state != "forward-validated":
            assert fc.grade is None and pol["grade"] is None and "not forward-validated" in pol["stake_reason"]
        else:                                           # even a (hypothetical) forward-validated market: stake 0
            assert pol["stake_reason"] == "stake gate not passed"
    g = grade_policy.apply(0.70, 0.30, ("over", "under"), "history-validated")
    assert g["grade"] is None and g["stake_units"] == 0.0 and g["label"] == grade_policy.TEXT_HISTORY


def test_gate_f_minimum_sample_opens_a_review_and_promotes_nothing(monkeypatch):
    art = ROOT / "model" / "artifacts" / "registry.json"
    before = hashlib.sha256(art.read_bytes()).hexdigest()
    row = {"league": "nba", "stat": "points", "graded": 300, "games": 100, "hit_minus_p": (0.0, -0.01, 0.01),
           "logloss": 0.60}
    rows = [dict(row, model_version=mv) for mv in ("sm-1", "mi-1", "mc-1")]
    rows += [dict(row, model_version="sharp-2", logloss=0.70)]
    monkeypatch.setattr(learning, "scorecard", lambda lcon=None, model_version=None: rows)
    out = learning.forward_gate()
    assert {r["model_version"] for r in out} == {"sm-1", "mi-1", "mc-1"}
    for r in out:                                       # every criterion met at exactly the minimum sample
        assert r["calibration_ok"] is True and r["logloss_vs_sharp2"] < 0
        assert r["state"] == "review window reached - decision by the registered review, never automatic"
    below = [dict(r, graded=299) for r in rows]
    monkeypatch.setattr(learning, "scorecard", lambda lcon=None, model_version=None: below)
    assert all(r["state"].startswith("waiting (299/300 props") for r in learning.forward_gate())
    assert hashlib.sha256(art.read_bytes()).hexdigest() == before   # nothing written, nothing promoted
    assert forward._validation_state("sm-1", "points") == "history-validated"
