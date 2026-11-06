"""os-1 (owner 2026-11-06), the main server's side: a LoL line the book offers on one side only - PrizePicks one allowed side,
a goblin / demon line with no allowed sides sent (More only), an Underdog line listing one option - on every scope (Map 1,
Maps 1-2, Maps 1-3) is shown with its line, price and Goblin / Demon tag, reads "Over only" / "Under only" and is never graded,
staked, put on a slip (generator, adder, recheck, track), priced for EV or shown with a Scout chance. A two-sided line keeps its
letter. server.py and workspace_*.py import no lol / sports package. Real HTTP over server.Handler; every name is a TEST FIXTURE
(tests/fixtures/workspace_lol_board.json)."""
import json

import pytest

import server
import slip_quality as sq
from tests.test_workspace_all_sports import env, recs, check, track, problems, fresh, get  # noqa: F401
from tests.workspace_other_fixture import leg

NOTE = "Over only: Scout grades lines that offer both sides."
GOBLIN = {"basis": "adjusted-odds line, no allowed sides sent", "higher": {"offered": True}, "lower": {"offered": None}}


def _rewrite(fx, i, **kw):
    fx.lol.rows[i].update(kw)
    fx.lol.write()
    server._LOL_MEMBER.update(mtime=None, body=None)
    fresh()


def _assert_over_only(r, line_type="standard"):
    s = r["scout"]
    assert s["hold"] == {"label": "Over only", "reason": NOTE} and s["validation"]["label"] == "Over only"
    assert s["grade"] is None and s["side"] is None and s["units"] is None and s["p_side"] is None
    assert s["p_over"] is None and s["p_under"] is None and s["projection"] is None and s["forecast_at"] is None
    assert r["market"]["line_type"] == line_type                                    # the Goblin / Demon tag stays
    assert r["offer"]["over"]["offered"] is True and r["offer"]["under"]["offered"] is False
    assert "No bet" not in json.dumps(r)


def test_prizepicks_over_only_standard_line_is_labelled_not_graded(env):
    fx = env
    r = recs(fx, "lol")[("Guigo", "prizepicks")]                  # fixture row 0: allowed_wager_types over only
    _assert_over_only(r)
    assert r["offer"]["line"] == 7.5
    two = recs(fx, "lol")[("Harky", "prizepicks")]               # a line offering both sides keeps its letter
    assert two["scout"]["grade"] == "A-" and two["scout"]["hold"] is None and two["scout"]["side"] == "under"


@pytest.mark.parametrize("scope", ["Map 1", "Maps 1-2", "Maps 1-3"])
def test_goblin_line_on_every_scope_keeps_its_tag_and_loses_its_letter(env, scope):
    fx = env
    _rewrite(fx, 2, odds_type="goblin", sides=GOBLIN, scope=scope)
    r = recs(fx, "lol")[("Harky", "prizepicks")]
    _assert_over_only(r, "goblin")
    assert r["market"]["period_label"] == scope


def test_underdog_one_option_reads_over_only_and_a_suspended_side_keeps_the_grade(env):
    fx = env
    sides = dict(fx.lol.rows[3]["sides"])
    lower = sides.pop("lower")
    _rewrite(fx, 3, sides=sides)
    r = recs(fx, "lol")[("Harky", "underdog")]
    _assert_over_only(r)
    assert r["offer"]["over"]["payout"] is not None or r["offer"]["over"]["offered"]       # the line's price stays
    _rewrite(fx, 3, sides=dict(sides, higher=dict(sides["higher"], status="suspended"), lower=lower))   # Scout's side (Under) open
    r = recs(fx, "lol")[("Harky", "underdog")]
    assert r["scout"]["hold"] is None and r["scout"]["grade"] == "A-"          # two-sided line, other side suspended right now


def test_one_sided_leg_is_refused_by_recheck_and_track_and_left_out_of_the_generator(env):
    fx = env
    r = recs(fx, "lol")[("Guigo", "prizepicks")]
    lg = leg(r, "over")
    st, doc = check(fx, [lg])
    assert st == 200 and "not_gradable" in problems(doc, lg)
    texts = [p["text"] for p in doc["legs"][lg["id"]]["problems"]]
    assert NOTE in texts and not doc["legs"][lg["id"]]["ok"]
    st, out = track(fx, [lg], "entry-os1")
    assert st != 200 or not (out or {}).get("ok")
    st, board = get(fx, "board?sport=lol")
    legs, why = sq.pool(board["records"], research_ok=True)
    assert not any(l["player"] == "Guigo" and l["book"] == "prizepicks" for l in legs) and why.get("held", 0) >= 1


def test_member_board_match_tables_and_slip_reasons(env):
    fx = env
    view = json.loads(server.lol_member_board())
    row = next(x for x in view["rows"] if x["player"] == "Guigo" and x["platform"] == "prizepicks")
    assert row["one_sided"] == "Over only" and row["grade"] is None and row["pick_offered"] is False
    assert row["p_over"] is None and row["projection"] is None and row["stake_reason"] == NOTE
    assert server.lol_one_sided_label("prizepicks", GOBLIN) == "Over only"
    assert server.lol_one_sided_label("underdog", {"lower": {"status": "active"}}) == "Under only"
    assert server.lol_one_sided_label("underdog", {"higher": {"status": "active"}, "lower": {"status": "suspended"}}) is None
    assert server.lol_one_sided_label("prizepicks", {"higher": {"offered": True}, "lower": {"offered": True}}) is None
    assert server.lol_one_sided_label("prizepicks", {}, "under") == "Under only"            # the forecast's own reading
    out = server._lol_member_slips({"platforms": {"prizepicks": {"blocked_by": {
        "the line offers one side only (Scout grades lines that offer both sides)": 3}, "upcoming_series": 2}}})
    assert any("one side only" in x["text"] for x in out["PrizePicks"]["why_none"])


def test_ev_board_never_prices_a_one_sided_line(env, monkeypatch):
    import workspace_ev
    fx = env
    st, board = get(fx, "board?sport=lol")
    monkeypatch.setattr(workspace_ev.wb, "records", lambda srv, sport: (board["records"], {"body": b"x"}) if sport == "lol" else (None, None))
    workspace_ev._LEGS.clear()
    _, legs = workspace_ev._legs(server)
    names = {(l["player"], l["app"]) for l in legs}
    assert ("Guigo", "prizepicks") not in names and ("Harky", "prizepicks") in names
