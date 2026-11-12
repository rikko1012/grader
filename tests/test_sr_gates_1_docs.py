"""sr-gates-1 (2026-10-28): the stake-rule tests were registered before scoring, nothing passed, and the grader's gates and
caps are unchanged (FORM FIGHT 0U, AWPer-HS 0U, TAPE FIGHT 0.5U, rust 15-28 d flag only; no SR_GATES_1 switch)."""
from pathlib import Path
import grader

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "research" / "cs2_grade_1028"


def test_register_precedes_results_and_states_the_split():
    reg = (R / "REGISTER.md").read_text(encoding="utf-8")
    assert "## sr-gates-1" in reg and "chronological split" in reg and "BOTH halves must agree" in reg
    res = (R / "RESULTS.md").read_text(encoding="utf-8")
    assert "## 7. sr-gates-1" in res and "c22fea1" in res and "nothing passed" in res


def test_gates_and_caps_unchanged():
    c = grader.CONFIG
    assert c["tape_fight_cap"] == 0.5 and c["form_fight_margin"] == 0.14
    assert c["rust_flag_days"] == 15 and c["rust_cap_days"] == 29
    assert not hasattr(grader, "sr_gates_1_enabled")
    leg = {"prop": "hs", "line": 12.5, "line_type": "standard", "scope": "12", "maps_played": 30, "sample_maps": 60,
           "kpr": 0.70, "hs_pct": 48.0, "rating": 1.05, "teammate_ratings": [1.0, 0.98, 1.02, 0.95], "sniping": 45,
           "roster_confirmed": True, "p_team_market": 0.55, "market_quality": "HARD", "bo": 3,
           "recent_avg": 14.0, "recent_median": 14.0, "recent": {"n": 10, "overs": 5}}
    # the AWPer-HS gate stayed until awp-hs-1 (2026-11-12, its forward re-test passed); CS2_AWP_HS_LIFT=0 keeps it
    g = grader.grade_leg(leg, dict(grader.CONFIG, awp_hs_lift=False))
    assert g["units"] == 0.0 and g["units_less"] == 0.0 and "AWPer on a headshot prop" in (g["zero_reason"] or "")


def test_model_versions_records_no_change():
    mv = (ROOT / "MODEL_VERSIONS.md").read_text(encoding="utf-8")
    assert "sr-gates-1" in mv and "no model change" in mv
