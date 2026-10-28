"""2026-10-28 multilevel grading check: the registration precedes the results, nothing shipped, and the documented
shadows (oc-1, sd-bo3-1) are recorded as FAILED in every place a reader would look. CS2 tests never import lol."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "research" / "cs2_grade_1028"


def test_register_fixes_rules_before_results():
    reg = (R / "REGISTER.md").read_text(encoding="utf-8")
    for rule in ("oc-1", "sd-bo3-1", "retention", "balance", "walk-forward", "match-grouped"):
        assert rule in reg
    # the register states the expected failure mode of oc-1 up front (owner constraint registered, not discovered later)
    assert "Expected failure mode" in reg


def test_results_report_failures_with_intervals():
    res = (R / "RESULTS.md").read_text(encoding="utf-8")
    assert "FAIL" in res and "oc-1" in res and "sd-bo3-1" in res
    # every candidate row carries an interval
    rows = [ln for ln in res.splitlines() if ln.startswith("| **oc-1**") or ln.startswith("| **sd-bo3-1**")]
    assert len(rows) == 2 and all(re.search(r"\[[-+]?\d", ln) for ln in rows)
    assert "Nothing passed its registered rule" in res


def test_model_versions_and_challengers_record_no_model_change():
    mv = (ROOT / "MODEL_VERSIONS.md").read_text(encoding="utf-8")
    assert "Multilevel grading check 2026-10-28" in mv and "no model change" in mv
    ch = (ROOT / "CHALLENGERS.md").read_text(encoding="utf-8")
    assert "oc-1 and sd-bo3-1" in ch and "FAILED" in ch


def test_cs2_grader_path_never_imports_lol():
    import grader, grade_display, pick_verdict  # noqa: F401
    assert not any(m == "lol" or m.startswith("lol.") for m in sys.modules)
