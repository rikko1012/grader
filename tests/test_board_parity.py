"""Competitor spec 4.16 board parity fixes (board_parity.py + gradecore/parity.py): the formulas, the flags (each OFF by default,
none in server.DEFAULT_ON), the board rows they add, and the Report a problem route into data_issues. Fixture data only."""
import json
import math

import pytest

import board_parity as bpar
import server
import workspace_api
import workspace_store
from gradecore import fair, parity, payouts, slip
from tests.test_workspace_api import env, get, post, records, fresh  # noqa: F401

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
def test_break_even_is_the_verified_two_pick_entry_of_each_book():
    t = parity.table()
    assert t["prizepicks"]["be"] == pytest.approx(PP_BE) and t["prizepicks"]["entry"] == "PrizePicks Power 2-pick"
    assert t["underdog"]["be"] == pytest.approx(UD_BE) and t["underdog"]["entry"] == "Underdog standard 2-pick"
    assert t["prizepicks"]["american"] == pytest.approx(payouts.american_of(PP_BE)) and round(t["prizepicks"]["american"]) == -137
    assert t["underdog"]["decimal"] == pytest.approx(1 / UD_BE)
    assert t["prizepicks"]["source"].startswith("https://www.prizepicks.com/") and t["underdog"]["source"].startswith("https://help.underdogsports.com/")


def test_unverified_book_line_type_or_modifier_has_no_break_even(monkeypatch):
    assert parity.break_even("sleeper") is None and parity.leg_be("draftkings", "standard", None) is None
    assert parity.leg_be("prizepicks", "goblin", None) is None and parity.leg_be("prizepicks", "demon", None) is None
    assert parity.leg_be("underdog", "standard", 1.5) is None            # modifier combinations are not published
    assert parity.leg_be("underdog", "standard", 1.0)["be"] == pytest.approx(UD_BE)
    # a book whose table is not marked verified is unavailable, never guessed
    monkeypatch.setattr(payouts, "pickem_config", lambda: payouts.with_tables({}))
    assert parity.break_even("prizepicks") is None


@pytest.mark.parametrize("p,book,be", [(0.62, "underdog", UD_BE), (0.55, "prizepicks", PP_BE), (0.5774, "prizepicks", PP_BE)])
def test_ev_is_p_times_per_leg_payout_minus_one(p, book, be):
    r = parity.row(p, "over", book, "standard", None)
    assert r["ev"] == pytest.approx(p / be - 1, abs=1e-6) == pytest.approx(p * (1 / be) - 1, abs=1e-6)
    assert r["ev"] == pytest.approx(fair.leg_ev(p, be), abs=1e-6)


@pytest.mark.parametrize("ev,band", [(-0.2, "skip"), (-1e-9, "skip"), (0.0, "marginal"), (0.0299, "marginal"), (0.03, "strong"),
                                     (0.0599, "strong"), (0.06, "premium"), (0.5, "premium")])
def test_ev_band_edges(ev, band):
    assert parity.band(ev)[0] == band
    assert parity.band(None) is None


def test_hit_rate_key_is_hit_rate_minus_break_even_and_flags_junk():
    r = parity.row(0.6, "under", "prizepicks", "standard", None, {"over": 4, "under": 6, "push": 0, "n": 10})
    assert r["hit"] == {"hits": 6, "n": 10, "key": pytest.approx(0.6 - PP_BE, abs=1e-6), "below": False}
    junk = parity.row(0.6, "over", "prizepicks", "standard", None, {"over": 5, "under": 5, "push": 0, "n": 10})
    assert junk["hit"]["below"] is True and junk["hit"]["key"] < 0
    assert parity.row(0.6, "over", "prizepicks", "standard", None, {"over": 0, "under": 0, "n": 0})["hit"] is None
    assert parity.row(0.6, "over", "prizepicks", "standard", None)["hit"] is None


def test_display_sizes_are_kelly_at_break_even_odds_and_flat_one_unit():
    p, be = 0.62, UD_BE
    d = 1 / be
    full = (p * d - 1) / (d - 1)                                          # closed-form Kelly of a two-outcome bet
    assert parity.leg_kelly(p, be) == pytest.approx(full, abs=1e-9)
    s = parity.row(p, "over", "underdog", "standard", 1.0)["sizes"]
    assert s["quarter"] == pytest.approx(round(full * 0.25 / slip.UNIT_SHARE, 2))
    assert s["half"] == pytest.approx(round(full * 0.5 / slip.UNIT_SHARE, 2))
    assert s["flat"] == 1.0
    neg = parity.row(0.5, "over", "underdog", "standard", 1.0)["sizes"]
    assert neg == {"quarter": 0.0, "half": 0.0, "flat": 0.0}                # no edge: no size


def test_no_side_or_no_chance_has_no_row():
    assert parity.row(None, "over", "underdog", "standard", 1.0) is None
    assert parity.row(0.6, None, "underdog", "standard", 1.0) is None


# ---------------------------------------------------------------------------------------------------------- flags
def test_every_flag_off_by_default_and_not_turned_on_by_the_server():
    assert bpar.features() == {} and not bpar.rows_on()
    assert not set(bpar.FLAGS.values()) & set(server.DEFAULT_ON)
    rec = {"scout": {"side": "over", "p_side": 0.6}, "offer": {"platform": "underdog", "over": {"payout": 1.0}}, "market": {"line_type": "standard"}}
    assert "parity" not in bpar.attach(dict(rec))


def test_features_name_only_the_flags_that_are_on(monkeypatch):
    monkeypatch.setenv("SCOUT_PARITY_HIT_SAMPLES", "1")
    assert bpar.features() == {"parity": {"hit_samples": True}}           # no row numbers needed for this one
    all_on(monkeypatch)
    f = bpar.features()["parity"]
    assert set(f) >= set(bpar.FLAGS) and f["unit_share"] == slip.UNIT_SHARE
    assert set(f["break_even"]) == {"prizepicks", "underdog"} and dict(f["reasons"]) == bpar.REASONS


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
    assert v["parity"]["ev"] == pytest.approx(v["scout"]["p_side"] / UD_BE - 1, abs=1e-5)
    assert v["parity"]["hit"]["n"] == v["history"]["l10"]["n"]
    # grades, chances and stakes are untouched
    assert v["scout"]["units"] == env.by("vargo")["units"] and v["scout"]["grade"] == "B"
    _, me = get(env, "me")
    assert me["features"]["parity"]["edge"] is True


# ---------------------------------------------------------------------------------------------------------- Report a problem
def _line(env):
    v = records(env)[("vargo", "underdog")]
    return {"sport": "cs2", "book": "underdog", "id": v["offer"]["offer_id"], "revision": v["offer"]["revision"], "reason": "line", "note": " wrong line "}


def test_report_is_refused_while_its_flag_is_off(env):
    assert post(env, "report-problem", _line(env)) == (403, {"error": "forbidden"})


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
