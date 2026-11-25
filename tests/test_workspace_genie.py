"""SCOUT_GENIE: payload-grounded questions in the research drawer. Flag off = the unknown-route answer and no feature key; flag on =
answers from the line payload only, every number checked against it, a per-member daily cap, nothing written outside genie.db."""
import ast
import json
from pathlib import Path

import pytest

import server
import workspace_api
import workspace_genie as genie
from tests.test_workspace_api import env, get, post, call, API  # noqa: F401  (env is the shared HTTP fixture)

ROOT = Path(workspace_api.__file__).parent


@pytest.fixture
def genv(env, monkeypatch):
    monkeypatch.setattr(server.hltv, "recent_series", lambda *a, **k: [])
    genie.reset()
    yield env
    genie.reset()


def body(env, who="vargo", book="underdog", **kw):
    x = env.by(who, book)
    d = {"book": book, "id": str(x["id"]), "side": "over", "question": "What is the projection?"}
    d.update(kw)
    return x, d


# ---- unit: the composer and the number check

DOC = {"record": {"sport": "cs2", "key": "k", "player": {"name": "s1mple"}, "market": {"stat_label": "Kills", "period_label": "Maps 1-2"},
                  "offer": {"platform": "underdog", "platform_label": "Underdog", "offer_id": "7", "line": 38.5, "moved_from": 36.5},
                  "scout": {"side": "over", "p_over": 0.6123, "p_under": None, "projection": 40.26, "grade": "B+", "units": 1.0,
                            "stake_reason": "Cap 2 units on Maps 1-2", "hold": None},
                  "event": {"label": "A vs B", "start_utc": "2026-11-04T18:00:00Z"}, "history": {"l10": {"over": 6, "under": 3, "push": 1, "n": 10}}},
       "history": {"tally": {"over": 6, "under": 3, "push": 1, "n": 10}, "avg": 39.44},
       "h2h": {"opponent": "B", "n": 3, "over": 2, "under": 1, "avg": 41.0},
       "books": [{"platform": "prizepicks", "platform_label": "PrizePicks", "row_id": "9", "line": 37.5, "line_type": "standard"}],
       "market": {"available": True, "p_over": 0.48, "p_under": 0.52, "source": "Underdog prices, margin removed"},
       "context": {"rows": [{"label": "Kills per round", "value": 0.81}, {"label": "Rating", "value": 1.12}]}}


def test_every_topic_answers_from_the_payload_with_checked_numbers():
    asks = {"Should I take it?": "B+ Over", "What is the projection?": "40.3", "What is the chance?": "61.2%",
            "Why this grade and stake?": "1U", "How often did this line hit?": "6 of the last 10", "What is the head-to-head?": "2 of 3",
            "Is there a better line elsewhere?": "PrizePicks lists 37.5", "What does the market say?": "Over 48.0%",
            "Did the line move?": "from 36.5 to 38.5", "What is his kills per round?": "Kills per round: 0.81", "When does it start?": "2026-11-04T18:00:00Z"}
    for q, want in asks.items():
        out = genie.answer(DOC, q, "over")
        assert out["matched"] and out["removed_numbers"] == 0, q
        assert want in " ".join(out["sentences"]), (q, out)


def test_a_number_not_in_the_payload_is_removed():
    allowed = genie.allowed_numbers(DOC)
    kept, removed = genie.verify(["Projection 40.3.", "He will get 55 kills.", "s1mple at 38.5 Over 61.2%."], allowed)
    assert kept == ["Projection 40.3.", "s1mple at 38.5 Over 61.2%."] and removed == 1


def test_wording_rules_lean_held_push_and_no_hedge_lines():
    doc = json.loads(json.dumps(DOC))
    doc["record"]["scout"].update(grade=None, units=0)
    assert "Lean Over" in " ".join(genie.answer(doc, "should I play it", "over")["sentences"])
    doc["record"]["scout"]["hold"] = True
    doc["record"]["sport"] = "nba"
    assert "Research estimate Over" in " ".join(genie.answer(doc, "which side", "over")["sentences"])
    doc["record"]["scout"]["p_push"] = 0.05
    assert "a push is not a win" in " ".join(genie.answer(doc, "chance", "over")["sentences"])
    text = " ".join(" ".join(genie.answer(DOC, q, "over")["sentences"]) for q, _ in genie.TOPICS) + genie.HELP
    for banned in ("financial advice", "not betting", "bet placement", "place a bet", "ROI", "return", "might", "unsure", "No bet"):
        assert banned.lower() not in text.lower()


def test_asking_the_other_side_names_scouts_side_in_one_plain_sentence():
    """Review 2026-11-04: 'Grader's read on Underdog 30.5 is Grader side is Under.' Now one sentence, both topics."""
    doc = json.loads(json.dumps(DOC))
    doc["record"]["scout"].update(side="under", p_under=0.58, p_over=None, grade="B")
    pick = genie.answer(doc, "should I take the over?", "over")["sentences"]
    assert pick[0] == "Grader's side on Underdog 38.5 is Under, not Over."
    grade = genie.answer(doc, "why this grade?", "over")["sentences"]
    assert grade[0] == "Grader's side is Under, not Over."
    text = " ".join(pick + grade)
    assert "is Grader side" not in text and "read is Grader" not in text
    assert genie.answer(doc, "should I take the under?", "under")["sentences"][0] == "Grader's read on Underdog 38.5 is B Under."


def test_nothing_checked_returns_the_help_text_free_never_a_status_line(monkeypatch):
    """Review 2026-11-04: an all-removed answer said 'could not be checked' (a status line) and still used a question."""
    monkeypatch.setattr(genie, "verify", lambda said, allowed: ([], len(said)))
    out = genie.answer(DOC, "What is the projection?", "over")
    assert out["sentences"] == [genie.HELP] and out["matched"] is False and out["removed_numbers"] == 1
    assert "could not be checked" not in " ".join(out["sentences"])


def test_unmatched_question_gets_help_and_no_charge():
    out = genie.answer(DOC, "tell me a joke", "over")
    assert not out["matched"] and out["sentences"] == [genie.HELP]


def test_the_module_imports_no_sport_code():
    tree = ast.parse((ROOT / "workspace_genie.py").read_text(encoding="utf-8"))
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
            {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not [m for m in names if m.split(".")[0] in ("sports", "lol", "picklog", "grader", "gradecore")]


def test_daily_cap_is_atomic_per_member_and_day(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_GENIE_DAILY", "2")
    try:
        assert genie.reserve(tmp_path, "m1", "2026-11-04")[0] and genie.reserve(tmp_path, "m1", "2026-11-04")[0]
        ok, q = genie.reserve(tmp_path, "m1", "2026-11-04")
        assert not ok and q["remaining_questions"] == 0 and q["questions_used"] == 2
        assert genie.reserve(tmp_path, "m2", "2026-11-04")[0] and genie.reserve(tmp_path, "m1", "2026-11-05")[0]
        genie.reserve(tmp_path, "m3", "2026-11-20")                      # a new day prunes days older than a week
        assert genie.quota(tmp_path, "m1", "2026-11-04")["questions_used"] == 0
    finally:
        genie.reset()


# ---- HTTP: flag off

def test_flag_off_is_the_unknown_route_answer(genv, monkeypatch):
    monkeypatch.delenv("SCOUT_GENIE", raising=False)
    _, d = body(genv)
    assert post(genv, "genie", d) == post(genv, "no-such-route", d) == (403, {"error": "forbidden"})
    assert get(genv, "genie") == get(genv, "no-such-route") == (404, {"error": "not found"})
    st, me = get(genv, "me")
    assert st == 200 and "genie" not in me["features"]
    assert not (genv.tmp / "genie.db").exists()


def test_flag_off_drawer_code_is_gated_on_the_feature():
    js = (ROOT / "web" / "upgrade.js").read_text(encoding="utf-8")
    assert "${R.me?.features?.genie ? askHtml(rec) : \"\"}<div class=\"u-genie\">" in js


# ---- HTTP: flag on

def test_flag_on_answers_from_the_line_and_charges_the_cap(genv, monkeypatch):
    monkeypatch.setenv("SCOUT_GENIE", "1")
    monkeypatch.setenv("SCOUT_GENIE_DAILY", "2")
    st, me = get(genv, "me")
    assert st == 200 and me["features"]["genie"] is True
    x, d = body(genv)
    st, line = get(genv, f"line?book=underdog&id={x['id']}")
    assert st == 200
    st, out = post(genv, "genie", d)
    assert st == 200 and out["matched"] and out["removed_numbers"] == 0 and out["quota"]["questions_used"] == 1
    allowed = genie.allowed_numbers(line)
    assert all(genie.verify([s], allowed)[1] == 0 for s in out["sentences"])
    st, out = post(genv, "genie", dict(d, question="what's the weather"))     # unmatched: free
    assert st == 200 and not out["matched"] and out["quota"]["questions_used"] == 1
    assert post(genv, "genie", dict(d, question="How often did this line hit?"))[0] == 200
    st, out = post(genv, "genie", d)
    assert st == 429 and out["error"] == "daily_limit_reached" and out["quota"]["remaining_questions"] == 0
    assert post(genv, "genie", d, user="u2")[0] == 200                         # the cap is per member
    st, q = get(genv, "genie")
    assert st == 200 and q["quota"]["questions_used"] == 2


def test_flag_on_refusals(genv, monkeypatch):
    monkeypatch.setenv("SCOUT_GENIE", "1")
    x, d = body(genv)
    assert post(genv, "genie", dict(d, id="not-on-board"))[0] == 404
    assert post(genv, "genie", dict(d, side="push"))[0] == 400
    assert post(genv, "genie", dict(d, question=""))[0] == 400
    assert post(genv, "genie", dict(d, question="x" * 301))[0] == 413
    assert post(genv, "genie", dict(d, revision="stale-rev"))[0] == 409
    assert post(genv, "genie", d, user=None) == (403, {"error": "forbidden"})      # signed out: the gate's unknown-route answer
    assert call(genv, "POST", API + "genie", b"x" * 5000)[0] == 413


def test_flag_on_per_minute_limit(genv, monkeypatch):
    monkeypatch.setenv("SCOUT_GENIE", "1")
    _, d = body(genv)
    codes = [post(genv, "genie", dict(d, question="tell me something"))[0] for _ in range(genie.PER_MIN + 1)]
    assert codes[:genie.PER_MIN] == [200] * genie.PER_MIN and codes[-1] == 429


def test_flag_on_writes_no_forecast_or_grade(genv, monkeypatch):
    monkeypatch.setenv("SCOUT_GENIE", "1")
    x, d = body(genv)
    before = get(genv, f"line?book=underdog&id={x['id']}")[1]
    for q in ("projection", "grade", "chance", "hit", "market"):
        assert post(genv, "genie", dict(d, question=q))[0] == 200
    workspace_api._PRIVATE.clear()
    after = get(genv, f"line?book=underdog&id={x['id']}")[1]
    assert before["record"]["scout"] == after["record"]["scout"] and before["forecast"] == after["forecast"]


def test_v3_answers_state_no_bo3_number_and_v3_off_is_unchanged(genv, monkeypatch):
    """v3 HLTV only (owner 2026-11-20; v3_hltv.scrub): on a v3 request the stats answer reads the HLTV rows only, never the saved grader
    inputs (BO3.gg / merged: rating, KAST, ADR...). v3 off: the answer is as before."""
    monkeypatch.setenv("SCOUT_GENIE", "1")
    monkeypatch.setenv("SCOUT_GENIE_DAILY", "50")
    x, d = body(genv, question="What are his stats? rating kast adr kpr")
    st, line = get(genv, f"line?book=underdog&id={x['id']}")
    saved = [r for r in line["context"]["rows"] if r.get("group") == "Saved with this forecast"]
    assert st == 200 and saved, line["context"]
    st, off = post(genv, "genie", d)
    assert st == 200 and any(r["label"] in " ".join(off["sentences"]) for r in saved), off
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    st, on = post(genv, "genie", d)
    assert st == 200 and not any(r["label"] + ":" in " ".join(on["sentences"]) for r in saved), on
