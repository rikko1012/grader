"""os-1 (owner 2026-11-06, scope "Underdog only"), the main server's side: an Underdog LoL line listing one option, on every
scope (Map 1, Maps 1-2, Maps 1-3), is shown with its line and price, reads "Over only" / "Under only" and is never graded,
staked, put on a slip (generator, adder, recheck, track), priced for EV or shown with a Grader chance. A two-listed Underdog
line whose other side is suspended right now keeps its grade. Every PrizePicks line (standard, goblin, demon - More only
included) keeps today's behaviour. server.py and workspace_*.py import no lol / sports package. Real HTTP over
server.Handler; every name is a TEST FIXTURE (tests/fixtures/workspace_lol_board.json)."""
import json

import pytest

import server
import slip_quality as sq
from tests.test_workspace_all_sports import env, recs, check, track, problems, fresh, get  # noqa: F401
from tests.workspace_other_fixture import leg

NOTE = "Over only: Grader grades lines that offer both sides."
GOBLIN = {"basis": "adjusted-odds line, no allowed sides sent", "higher": {"offered": True}, "lower": {"offered": None}}


def _rewrite(fx, i, **kw):
    fx.lol.rows[i].update(kw)
    fx.lol.write()
    server._LOL_MEMBER.update(mtime=None, body=None)
    fresh()


def _over_only_ud(fx, i, **kw):
    """Fixture row i (an Underdog line) listing its Over option only."""
    sides = {k: v for k, v in fx.lol.rows[i]["sides"].items() if k != "lower"}
    _rewrite(fx, i, sides=sides, **kw)


def _assert_over_only(r):
    s = r["scout"]
    assert s["hold"] == {"label": "Over only", "reason": NOTE} and s["validation"]["label"] == "Over only"
    assert s["grade"] is None and s["side"] is None and s["units"] is None and s["p_side"] is None
    assert s["p_over"] is None and s["p_under"] is None and s["projection"] is None and s["forecast_at"] is None
    assert r["offer"]["over"]["offered"] is True and r["offer"]["under"]["offered"] is False
    assert r["offer"]["over"]["payout"] is not None                                   # the line's price stays
    assert "No bet" not in json.dumps(r)


@pytest.mark.parametrize("scope", ["Map 1", "Maps 1-2", "Maps 1-3"])
def test_underdog_one_option_on_every_scope_reads_over_only(env, scope):
    fx = env
    _over_only_ud(fx, 3, scope=scope)                              # Harky Underdog (had an A-)
    r = recs(fx, "lol")[("Harky", "underdog")]
    _assert_over_only(r)
    assert r["market"]["period_label"] == scope


def test_suspended_side_on_a_two_listed_underdog_line_keeps_the_grade(env):
    fx = env
    s = fx.lol.rows[3]["sides"]
    _rewrite(fx, 3, sides=dict(s, higher=dict(s["higher"], status="suspended")))   # Grader's side (Under) still open
    r = recs(fx, "lol")[("Harky", "underdog")]
    assert r["scout"]["hold"] is None and r["scout"]["grade"] == "A-"


def test_prizepicks_lines_keep_todays_behaviour(env):
    fx = env
    r = recs(fx, "lol")
    g = r[("Guigo", "prizepicks")]                                 # fixture row 0: allowed_wager_types over only - unchanged
    assert g["scout"]["hold"] is None and g["scout"]["side"] == "over" and g["scout"]["p_over"] is not None
    assert g["scout"]["units"] == 0.0
    f = dict(fx.lol.rows[2]["forecast"], model_side="over", p_over=fx.lol.rows[2]["forecast"]["p_under"],
             p_under=fx.lol.rows[2]["forecast"]["p_over"])          # leaning the offered More side
    _rewrite(fx, 2, odds_type="goblin", sides=GOBLIN, forecast=f)  # Harky PrizePicks goblin, More only: letter kept
    h = recs(fx, "lol")[("Harky", "prizepicks")]
    assert h["scout"]["hold"] is None and h["scout"]["grade"] == "A-" and h["market"]["line_type"] == "goblin"
    row = next(x for x in json.loads(server.lol_member_board())["rows"] if x["player"] == "Harky" and x["platform"] == "prizepicks")
    assert "one_sided" not in row and row["grade"] == "A-"


def test_one_sided_leg_is_refused_by_recheck_and_track_and_left_out_of_the_generator(env):
    fx = env
    _over_only_ud(fx, 3)
    r = recs(fx, "lol")[("Harky", "underdog")]
    lg = leg(r, "over")
    st, doc = check(fx, [lg])
    assert st == 200 and "not_gradable" in problems(doc, lg)
    assert NOTE in [p["text"] for p in doc["legs"][lg["id"]]["problems"]] and not doc["legs"][lg["id"]]["ok"]
    st, out = track(fx, [lg], "entry-os1")
    assert st != 200 or not (out or {}).get("ok")
    st, board = get(fx, "board?sport=lol")
    legs, why = sq.pool(board["records"], research_ok=True)
    assert not any(l["player"] == "Harky" and l["book"] == "underdog" for l in legs) and why.get("held", 0) >= 1


def test_member_board_labels_and_slip_reasons(env):
    fx = env
    _over_only_ud(fx, 3)
    view = json.loads(server.lol_member_board())
    row = next(x for x in view["rows"] if x["player"] == "Harky" and x["platform"] == "underdog")
    assert row["one_sided"] == "Over only" and row["grade"] is None and row["pick_offered"] is False
    assert row["p_over"] is None and row["projection"] is None and row["stake_reason"] == NOTE
    assert server.lol_one_sided_label("underdog", {"higher": {"status": "active"}}) == "Over only"
    assert server.lol_one_sided_label("underdog", {"lower": {"status": "active"}}) == "Under only"
    assert server.lol_one_sided_label("underdog", {"higher": {"status": "active"}, "lower": {"status": "suspended"}}) is None
    assert server.lol_one_sided_label("underdog", {}, "under") == "Under only"                # the forecast's own reading
    assert server.lol_one_sided_label("prizepicks", GOBLIN) is None                          # PrizePicks: unchanged
    assert server.lol_one_sided_label("prizepicks", {"higher": {"offered": True}, "lower": {"offered": False}}, "over") is None
    out = server._lol_member_slips({"platforms": {"underdog": {"blocked_by": {
        "the line offers one side only (Grader grades lines that offer both sides)": 3}, "upcoming_series": 2}}})
    assert any("one side only" in x["text"] for x in out["Underdog"]["why_none"])


def test_ev_board_never_prices_a_one_sided_line(env, monkeypatch):
    import workspace_ev
    fx = env
    _over_only_ud(fx, 3)
    st, board = get(fx, "board?sport=lol")
    monkeypatch.setattr(workspace_ev.wb, "records", lambda srv, sport: (board["records"], {"body": b"x"}) if sport == "lol" else (None, None))
    workspace_ev._LEGS.clear()
    _, legs = workspace_ev._legs(server)
    names = {(l["player"], l["app"]) for l in legs}
    assert ("Harky", "underdog") not in names and ("Harky", "prizepicks") in names and ("Guigo", "prizepicks") in names
