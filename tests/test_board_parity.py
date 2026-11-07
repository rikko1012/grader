"""Competitor spec 4.16 board parity fixes (board_parity.py + gradecore/parity.py): the formulas, the flags (each unset = off in a bare
process; all in server.DEFAULT_ON since the owner's 2026-11-05 "Turn everything on"), the board rows they add, and the Report a problem route into data_issues. Fixture data only."""
import json
import math

import pytest

import board_parity as bpar
import server
import workspace_api
import workspace_store
from gradecore import fair, parity, payouts, slip
from gradecore.tests.pickem_ref import reference_tables  # noqa: F401  (fixture: PrizePicks REFERENCE rates, formula tests only)
from tests.test_workspace_api import calibrated_export, env, get, post, records, fresh  # noqa: F401

PP_BE = math.sqrt(1 / 3.0)      # PrizePicks Power 2-pick pays 3x: (1/3)^(1/2)
UD_BE = math.sqrt(1 / 3.5)      # Underdog standard 2-pick pays 3.5x


@pytest.fixture(autouse=True)
def no_flags(monkeypatch):
    for f in bpar.FLAGS.values():
        monkeypatch.delenv(f, raising=False)


def all_on(monkeypatch):
    for f in bpar.FLAGS.values():
        monkeypatch.setenv(f, "1")


# ---------------------------------------------------------------------------------------------------------- formulas
def test_live_table_has_both_books_prizepicks_verified_by_owner():
    t = parity.table()                             # owner sign-off 2026-11-05: the PrizePicks help-center table is verified
    assert set(t) == {"prizepicks", "underdog"} and parity.break_even("prizepicks")["be"] == pytest.approx(PP_BE)
    r = parity.row(0.7, "over", "prizepicks", "standard", None, rated=True)
    assert r["be"] == pytest.approx(PP_BE, abs=1e-6) and r["ev"] == pytest.approx(0.7 / PP_BE - 1, abs=1e-4)
    assert parity.row(0.7, "over", "prizepicks", "goblin", None, rated=True) is None      # goblin / demon: unpublished payout


def test_break_even_is_the_two_pick_entry_of_each_book(reference_tables):
    t = parity.table()
    assert t["prizepicks"]["be"] == pytest.approx(PP_BE) and t["prizepicks"]["entry"] == "PrizePicks Power 2-pick"
    assert t["underdog"]["be"] == pytest.approx(UD_BE) and t["underdog"]["entry"] == "Underdog standard 2-pick"
    assert t["prizepicks"]["american"] == pytest.approx(payouts.american_of(PP_BE)) and round(t["prizepicks"]["american"]) == -137
    assert t["underdog"]["decimal"] == pytest.approx(1 / UD_BE)
    assert t["prizepicks"]["source"].startswith("https://www.prizepicks.com/") and t["underdog"]["source"].startswith("https://help.underdogsports.com/")


def test_unverified_book_line_type_or_modifier_has_no_break_even(monkeypatch, reference_tables):
    assert parity.break_even("sleeper") is None and parity.leg_be("draftkings", "standard", None) is None
    assert parity.leg_be("prizepicks", "goblin", None) is None and parity.leg_be("prizepicks", "demon", None) is None
    assert parity.leg_be("underdog", "standard", 1.5) is None            # modifier combinations are not published
    assert parity.leg_be("underdog", "standard", 1.0)["be"] == pytest.approx(UD_BE)
    # a book whose table is not marked verified is unavailable, never guessed
    monkeypatch.setattr(payouts, "pickem_config", lambda: payouts.with_tables({}, payouts.CODED_CONFIG))
    assert parity.break_even("prizepicks") is None


@pytest.mark.parametrize("p,book,be", [(0.62, "underdog", UD_BE), (0.55, "prizepicks", PP_BE), (0.5774, "prizepicks", PP_BE)])
def test_ev_is_p_times_per_leg_payout_minus_one(p, book, be, reference_tables):
    r = parity.row(p, "over", book, "standard", None, rated=True)
    assert r["ev"] == pytest.approx(p / be - 1, abs=1e-6) == pytest.approx(p * (1 / be) - 1, abs=1e-6)
    assert r["ev"] == pytest.approx(fair.leg_ev(p, be), abs=1e-6)
    plain = parity.row(p, "over", book, "standard", None)                 # not rated: break-even only, no EV / band / size
    assert plain["be"] == r["be"] and not {"ev", "band", "band_label", "sizes_pct"} & set(plain)


def test_whole_line_ev_uses_the_no_push_chance_like_the_ev_board_and_the_slip_header():
    """One definition everywhere (review 2026-11-05): over 0.55, push 0.08, under 0.37 against Underdog standard 2-pick."""
    from gradecore import ev_board
    from gradecore import slip_header as gsh
    be = UD_BE
    want = 0.55 / (0.55 + 0.37) / be - 1
    r = parity.row(0.55, "over", "underdog", "standard", 1.0, p_push=0.08, rated=True)
    board = ev_board.price_leg([], "over", 20.0, 0, be=be, p_model=0.55, p_model_other=0.37)
    head = gsh.entry_header([{"p": 0.55 / 0.92, "match_key": "a"}, {"p": 0.55 / 0.92, "match_key": "b"}], "underdog", "standard")
    assert r["ev"] == pytest.approx(want, abs=1e-6) == pytest.approx(board["ev_model"], abs=1e-6)
    assert head["legs"][0]["ev"] == pytest.approx(want, abs=1e-4)
    # a half-point line has no push: the same chance on all three surfaces
    half = parity.row(0.61, "over", "underdog", "standard", 1.0, p_push=0.0, rated=True)["ev"]
    hb = ev_board.price_leg([], "over", 20.5, 0, be=be, p_model=0.61, p_model_other=0.39)["ev_model"]
    hh = gsh.entry_header([{"p": 0.61, "match_key": "a"}, {"p": 0.61, "match_key": "b"}], "underdog", "standard")["legs"][0]["ev"]
    assert half == pytest.approx(hb, abs=1e-6) == pytest.approx(hh, abs=1e-4) == pytest.approx(0.61 / be - 1, abs=1e-4)
    # and a board record on a whole-number line is never rated (the slip check refuses whole lines)
    rec = {"sport": "cs2", "scout": {"side": "over", "p_side": 0.55, "p_push": 0.08, "units": 1.0},
           "offer": {"platform": "underdog", "line": 20.0, "over": {"payout": 1.0}}, "market": {"line_type": "standard"}}
    assert "ev" not in parity.of_record(rec, rated=True)


@pytest.mark.parametrize("ev,band", [(-0.2, "skip"), (-1e-9, "skip"), (0.0, "marginal"), (0.0299, "marginal"), (0.03, "strong"),
                                     (0.0599, "strong"), (0.06, "premium"), (0.5, "premium")])
def test_ev_band_edges(ev, band):
    assert parity.band(ev)[0] == band
    assert parity.band(None) is None


def test_hit_rate_key_is_hit_rate_minus_break_even_and_flags_junk(reference_tables):
    r = parity.row(0.6, "under", "prizepicks", "standard", None, {"over": 4, "under": 6, "push": 0, "n": 10})
    assert r["hit"] == {"hits": 6, "n": 10, "key": pytest.approx(0.6 - PP_BE, abs=1e-6), "below": False}
    junk = parity.row(0.6, "over", "prizepicks", "standard", None, {"over": 5, "under": 5, "push": 0, "n": 10})
    assert junk["hit"]["below"] is True and junk["hit"]["key"] < 0
    assert parity.row(0.6, "over", "prizepicks", "standard", None, {"over": 0, "under": 0, "n": 0})["hit"] is None
    assert parity.row(0.6, "over", "prizepicks", "standard", None)["hit"] is None


def test_display_sizes_are_the_entry_kelly_capped_as_a_share_of_bankroll():
    """Review 2026-11-05: size the real 2-pick entry (both legs at p, its payout), not one leg at 1 / break-even odds."""
    p = 0.60
    full = (p * p * 3.5 - 1) / (3.5 - 1)                                  # closed-form Kelly of the all-or-nothing 2-pick entry
    s = parity.row(p, "over", "underdog", "standard", 1.0, rated=True, sized=True)["sizes_pct"]
    assert s["quarter"] == pytest.approx(round(full * 0.25 * 100, 2))       # % of bankroll: 1/4 Kelly of the entry
    assert s["flat"] == pytest.approx(slip.UNIT_SHARE * 100)               # flat = 1% of bankroll
    leg_only = (p / UD_BE - 1) / (1 / UD_BE - 1)                           # the old single-leg reading ran high
    assert leg_only > full
    hi = parity.row(0.70, "over", "underdog", "standard", 1.0, rated=True, sized=True)["sizes_pct"]
    cap = parity.SIZE_CAP_UNITS * slip.UNIT_SHARE * 100
    assert hi["quarter"] == pytest.approx(cap) and hi["half"] == pytest.approx(cap)   # capped at the grader's unit cap
    neg = parity.row(0.5, "over", "underdog", "standard", 1.0, rated=True, sized=True)["sizes_pct"]
    assert neg == {"quarter": 0.0, "half": 0.0, "flat": 0.0}                # no edge: no size
    assert "sizes_pct" not in parity.row(0.6, "over", "underdog", "standard", 1.0, rated=True)   # a 0U line has no size


@pytest.mark.parametrize("sport,scout", [
    ("nba", {"letter_held": True, "units": 0}), ("nfl", {"hold": "League containment", "units": 0}),
    ("lol", {"units": 0.0}), ("cs2", {"letter_held": True, "units": 0.0}), ("cs2", {"units": 0.0})])
def test_held_rows_other_sports_and_zero_unit_rows_get_no_band_and_no_size(sport, scout, monkeypatch, reference_tables):
    all_on(monkeypatch)
    rec = {"sport": sport, "scout": dict({"side": "over", "p_side": 0.72}, **scout),
           "offer": {"platform": "underdog", "line": 22.5, "over": {"payout": 1.0}}, "market": {"line_type": "standard"},
           "history": {"l10": {"over": 7, "under": 3, "n": 10}}}
    v = bpar.attach(rec, rated=True)["parity"]
    assert "sizes_pct" not in v and "sizes" not in v
    if sport != "cs2" or scout.get("letter_held"):
        assert not {"ev", "band", "band_label"} & set(v)
    assert v["be"] == pytest.approx(UD_BE) and v["hit"]["hits"] == 7        # break-even and the hit-rate key stay


def test_no_side_or_no_chance_has_no_row():
    assert parity.row(None, "over", "underdog", "standard", 1.0) is None
    assert parity.row(0.6, None, "underdog", "standard", 1.0) is None


# ---------------------------------------------------------------------------------------------------------- flags
def test_every_flag_off_unset_and_turned_on_by_the_running_server():
    """Unset = off (a bare process); the running server turns every one on (owner 2026-11-05, "Turn everything on")."""
    assert bpar.features() == {} and not bpar.rows_on()
    assert set(bpar.FLAGS.values()) <= set(server.DEFAULT_ON)
    rec = {"scout": {"side": "over", "p_side": 0.6}, "offer": {"platform": "underdog", "over": {"payout": 1.0}}, "market": {"line_type": "standard"}}
    assert "parity" not in bpar.attach(dict(rec))


def test_features_name_only_the_flags_that_are_on(monkeypatch):
    monkeypatch.setenv("SCOUT_PARITY_HIT_SAMPLES", "1")
    assert bpar.features() == {"parity": {"hit_samples": True}}           # no row numbers needed for this one
    all_on(monkeypatch)
    f = bpar.features()["parity"]
    assert set(f) >= set(bpar.FLAGS) and f["unit_share"] == slip.UNIT_SHARE
    assert set(f["break_even"]) == {"prizepicks", "underdog"} and dict(f["reasons"]) == bpar.REASONS   # both verified


# ---------------------------------------------------------------------------------------------------------- the board
def test_board_rows_carry_parity_only_with_a_row_flag(env, monkeypatch):
    r = records(env)
    assert all("parity" not in x for x in r.values())
    _, me = get(env, "me")
    assert "parity" not in me["features"]
    monkeypatch.setenv("SCOUT_PARITY_EDGE", "1")
    workspace_api.reset()
    r = records(env)
    v = r[("vargo", "underdog")]
    assert v["parity"]["be"] == pytest.approx(UD_BE, abs=1e-6)
    assert "ev" not in v["parity"]                 # 0 settled matches: the slip check would not rate it, so no EV here either
    assert v["parity"]["hit"]["n"] == v["history"]["l10"]["n"]
    pp = [x for (n, b), x in r.items() if b == "prizepicks" and (x.get("market") or {}).get("line_type", "standard") == "standard"
          and (x.get("scout") or {}).get("side")]
    assert pp and all(x["parity"]["be"] == pytest.approx(PP_BE, abs=1e-6) for x in pp if "parity" in x)   # verified 2026-11-05
    assert any("parity" in x for x in pp)
    # grades, chances and stakes are untouched
    assert v["scout"]["units"] == env.by("vargo")["units"] and v["scout"]["grade"] == "B"
    _, me = get(env, "me")
    assert me["features"]["parity"]["edge"] is True
    # with settled results that pass the slip's check, the same row carries the EV the slip would show
    monkeypatch.setattr(server, "_p1_export", calibrated_export)
    workspace_api.reset()
    v = records(env)[("vargo", "underdog")]
    assert v["parity"]["ev"] == pytest.approx(v["scout"]["p_side"] / UD_BE - 1, abs=1e-5) and v["parity"]["band"]


# ---------------------------------------------------------------------------------------------------------- Report a problem
def _line(env):
    v = records(env)[("vargo", "underdog")]
    return {"sport": "cs2", "book": "underdog", "id": v["offer"]["offer_id"], "revision": v["offer"]["revision"], "reason": "line", "note": " wrong line "}


def test_report_is_refused_while_its_flag_is_off(env):
    # off = origin/main's unknown-route answer for a POST (403 before any sign-in check), not a registered route
    assert "report-problem" not in workspace_api.POST
    assert post(env, "report-problem", _line(env)) == (403, {"error": "forbidden"})
    assert post(env, "report-problem", _line(env), user=None) == (403, {"error": "forbidden"})
    assert post(env, "no-such-route", _line(env), user=None) == (403, {"error": "forbidden"})


def test_report_writes_one_data_issues_row(env, monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_PARITY_REPORT", "1")
    body = _line(env)
    st, doc = post(env, "report-problem", body)
    assert st == 200 and doc == {"ok": True, "duplicate": False}
    assert post(env, "report-problem", body) == (200, {"ok": True, "duplicate": True})     # the same report is one row
    assert post(env, "report-problem", dict(body, reason="stats"))[0] == 200
    assert post(env, "report-problem", dict(body, reason="nope"))[0] == 400
    assert post(env, "report-problem", dict(body, id="999999"))[0] == 404
    assert post(env, "report-problem", dict(body, sport="dota"))[0] == 400
    st = workspace_api.store()
    with st.reader() as con:
        rows = [dict(r) for r in con.execute("SELECT member, sport, platform, offer_id, reason, note, player, line, status FROM data_issues ORDER BY id")]
    assert [(r["reason"], r["note"]) for r in rows] == [("line", "wrong line"), ("stats", "wrong line")]
    assert rows[0]["member"] and rows[0]["player"] == "vargo" and rows[0]["status"] == "open" and rows[0]["offer_id"] == body["id"]


def test_report_is_rate_limited(env, monkeypatch):
    monkeypatch.setenv("SCOUT_PARITY_REPORT", "1")
    body = _line(env)
    got = [post(env, "report-problem", dict(body, note=str(i)))[0] for i in range(bpar.REPORT_PER_MIN + 2)]
    assert 429 in got and got[0] == 200


def test_clean_report_caps_the_note():
    f, err = bpar.clean_report({"sport": "nba", "book": "prizepicks", "id": "x1", "reason": "other", "note": "a" * 900})
    assert err is None and len(f["note"]) == bpar.NOTE_MAX
    assert bpar.clean_report([])[1] and bpar.clean_report({"sport": "nba", "book": "", "id": "x", "reason": "other"})[1]
