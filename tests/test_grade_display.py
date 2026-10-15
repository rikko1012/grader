"""Display gd-1 (owner 2026-10-15): model grade / stake / availability are separate. A graded pick held at 0 units by a stake
rule keeps the grade its chance earns and names the restriction; the logged verdict (and evaluation) is unchanged."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import grade_display as GD
import grader
from pick_verdict import verdict


def g_for(pm, line=26.5, prop="kills", units=None, units_less=None, **kw):
    """A grade the way grader.grade_leg returns it: letters from the UNROUNDED chance, chances rounded to 3 decimals."""
    g = {"p_more": round(pm, 3), "p_less": round(1 - pm, 3), "grade": grader.grade_from_p(pm), "grade_less": grader.grade_from_p(1 - pm),
         "units": grader.units_for_p(pm) if units is None else units, "units_less": grader.units_for_p(1 - pm) if units_less is None else units_less,
         "line": line, "line_type": "standard", "lambda_final": line + (1.0 if pm > 0.5 else -1.0), "prop": prop, "less_available": True}
    g.update(kw)
    return g


@pytest.mark.parametrize("pm,expect", [(0.5499999, "LEAN"), (0.54996, "LEAN"), (0.55, "B-"), (0.5599999, "B-"), (0.56, "B-"),
                                       (0.5600001, "B-"), (0.5699999, "B-"), (0.57, "B"), (0.63, "B+"), (0.80, "A+")])
def test_model_grade_follows_the_unrounded_chance_at_every_boundary(pm, expect):
    g = g_for(pm)
    v = verdict(g)
    assert GD.model_grade(g, v) == expect
    if pm == 0.54996:
        assert v["p"] == 0.55                       # shown as 55.0% - still a Lean: the letter comes from the unrounded chance


@pytest.mark.parametrize("pm", [0.5600001, 0.591, 0.66])
def test_a_zero_stake_gate_keeps_the_grade_and_names_the_restriction(pm):
    g = g_for(pm, prop="hs", zero_reason="AWPer on a headshot prop")
    v = verdict(g)
    assert v["grade"] == "LEAN" and v["units"] == 0                   # the logged verdict is unchanged (evaluation)
    d = GD.annotate(g, v)
    assert d["model_grade"] == grader.grade_from_p(pm) and d["model_grade"] != "LEAN"
    assert d["stake_reason"] == "AWPer on a headshot prop" and d["available"] is True and d["display_version"] == "gd-1"


def test_form_fight_keeps_the_grade():
    g = g_for(0.61, units=0.0, form_fight=True)
    v = verdict(g)
    d = GD.annotate(g, v)
    assert v["grade"] == "LEAN" and d["model_grade"] == "B" and d["stake_reason"].startswith("FORM FIGHT")


def test_a_lean_under_55_stays_a_lean_with_no_restriction():
    g = g_for(0.53)
    d = GD.annotate(g, verdict(g))
    assert d["model_grade"] == "LEAN" and d["stake_reason"] is None


def test_an_unavailable_side_stays_unavailable_and_unstaked():
    g = g_for(0.402, line=17.5, line_type="demon", less_available=False, grade_less="n/a (Less not offered or not confirmed by the feed for this line)")
    v = verdict(g)
    d = GD.annotate(g, v)
    assert v["side"] == "Less" and v["units"] == 0
    assert d["available"] is False and "not offered" in d["availability_reason"] and d["stake_reason"] is None
    assert d["model_grade"] == "B"                     # 59.8% earns a B; the side still cannot be bet
    gone = GD.annotate(g_for(0.62), verdict(g_for(0.62)), {"unavailable": "Under no longer offered", "units": 1.0})
    assert gone["available"] is False and gone["availability_reason"] == "Under no longer offered"


def test_same_match_cap_keeps_the_model_grade_and_names_the_cap():
    import server
    rows = []
    for i in range(8):
        g = g_for(0.60 + i * 0.005)
        v = verdict(g)
        rows.append(dict({"matchup": "A vs B", "start": "2026-10-16T15:00:00Z", "team": "A", "side": v["side"], "units": v["units"],
                          "p": v["p"], "grade": v["grade"], "nick": f"p{i}", "reason": v["reason"]}, **GD.annotate(g, v)))
    server._cap_same_result(rows)
    zero = [r for r in rows if r["units"] == 0]
    assert zero and all(r["grade"] == "LEAN" for r in zero)                     # legacy field (slips / logs) unchanged
    assert all(r["model_grade"] in GD.LETTERS and r["stake_reason"] == "same-match exposure cap (8 bets on this team)" for r in zero)
    assert sum(r["units"] for r in rows) <= server.SAME_RESULT_CAP + 1e-9


def test_board_and_card_annotate_the_same_pick_identically():
    g = g_for(0.591, zero_reason="AWPer on a headshot prop", prop="hs")
    v = verdict(g)
    assert GD.annotate(g, v) == GD.annotate(g, dict(v), dict(v, unavailable=None))


def test_nearby_line_chips():
    assert GD.annotate_item({"side": "More", "p": 0.591, "grade": "LEAN", "units": 0.0})["model_grade"] == "B"
    assert GD.annotate_item({"side": "More", "p": 0.54, "grade": "LEAN", "units": 0.0})["model_grade"] == "LEAN"
    assert GD.annotate_item({"side": "More", "p": 0.62, "grade": "B", "units": 0.5})["model_grade"] == "B"


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_site_helpers_never_make_a_zero_unit_or_unavailable_pick_a_bet(tmp_path):
    html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    want = {"LETTERS": r"const LETTERS = .*?;", "mGrade": r"const mGrade = .*?;", "isLeanV": r"const isLeanV = .*?;",
            "zeroWhy": r"const zeroWhy = v =>.*?: \"\";", "sideGone": r"const sideGone = .*?;", "isBet": r"const isBet = .*?;"}
    src = ["const esc = s => String(s);"] + [re.search(p, html, re.S).group(0) for p in want.values()]
    src.append("""
const cases = [
  {side: "More", grade: "LEAN", model_grade: "B", units: 0, stake_reason: "same-match exposure cap (8 bets on this team)"},
  {side: "Less", grade: "LEAN", model_grade: "B", units: 0, side_unavailable: "Under not offered"},
  {side: "More", grade: "B", model_grade: "B", units: 0.5},
  {side: "More", grade: "LEAN", model_grade: "LEAN", units: 0},
  {side: "More", grade: "LEAN", units: 0}];
console.log(JSON.stringify(cases.map(v => ({g: mGrade(v), lean: isLeanV(v), bet: isBet(v), why: zeroWhy(v)}))));""")
    f = tmp_path / "h.js"
    f.write_text("\n".join(src), encoding="utf-8")
    out = json.loads(subprocess.run([NODE, str(f)], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    assert out[0] == {"g": "B", "lean": False, "bet": False, "why": " — same-match exposure cap (8 bets on this team)"}
    assert out[1]["bet"] is False and out[1]["why"] == ""          # unavailable: never a bet, no 0U note
    assert out[2]["bet"] is True and out[3]["lean"] is True and out[4]["g"] == "LEAN"   # rows without gd-1 fields: as before


def test_rows_saved_before_gd1_get_the_same_display_until_regraded():
    cap = {"side": "More", "p": 0.562, "grade": "LEAN", "units": 0.0,
           "reason": "0 units: same-match cap (8 bets on this team) - Projection 27.7 vs line 26.5 (+1.2 kills)"}
    d = GD.annotate_saved(cap)
    assert d["model_grade"] == "B-" and d["stake_reason"] == "same-match exposure cap (8 bets on this team)" and d["available"] is True
    awp = {"side": "Less", "p": 0.641, "grade": "LEAN", "units": 0.0,
           "reason": "Leans Under 64.1% (projection 8.4 vs line 9.5) - 0 units: AWPer on a headshot prop"}
    assert GD.annotate_saved(awp)["stake_reason"] == "AWPer on a headshot prop" and GD.annotate_saved(awp)["model_grade"] == "B+"
    ff = {"side": "More", "p": 0.58, "grade": "LEAN", "units": 0.0, "reason": "FORM FIGHT - his last 10 matches at this line disagree with the projection - 0 units"}
    assert GD.annotate_saved(ff)["stake_reason"].startswith("FORM FIGHT") and GD.annotate_saved(ff)["model_grade"] == "B"
    assert GD.annotate_saved({"side": "More", "p": 0.53, "grade": "LEAN", "units": 0.0, "reason": "x"})["model_grade"] == "LEAN"
