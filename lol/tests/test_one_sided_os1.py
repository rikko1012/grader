"""os-1 (owner 2026-11-06, scope "Underdog only"): an Underdog LoL line listing one option is shown "Over only" / "Under only"
and never graded, staked, put on a slip or counted in the record. PrizePicks lines (standard, goblin, demon) keep today's
behaviour. The saved forecast row (raw chances) is kept as it was. Every name is a TEST FIXTURE."""
import json

from lol.center import forecast, learning, markets, slips
from lol.tests.test_consolidation import con, _future_world, test_saved_forecast_settles_when_the_series_completes as settle_fixture  # noqa: F401

UD_OVER = {"higher": {"status": "active", "payout_multiplier": "1.1"}}
UD_BOTH_SUSPENDED = {"higher": {"status": "active"}, "lower": {"status": "suspended"}}
PP_GOBLIN = markets.pp_sides([], "goblin", True)
PP_OVER = markets.pp_sides(["over"], "standard", False)
PP_BOTH = markets.pp_sides([], "standard", False)


def test_one_sided_reading_for_both_books():
    assert forecast.one_sided("underdog", UD_OVER) == "over"
    assert forecast.one_sided("underdog", {"lower": {"status": "active"}}) == "under"
    assert forecast.one_sided("underdog", json.dumps(UD_OVER)) == "over"                   # the saved (text) sides read the same
    assert forecast.one_sided("underdog", UD_BOTH_SUSPENDED) is None                       # a suspended side is still listed: graded
    # PrizePicks keeps today's behaviour (owner: "Underdog only"), goblin / demon More-only lines included
    assert forecast.one_sided("prizepicks", PP_GOBLIN) is None
    assert forecast.one_sided("prizepicks", markets.pp_sides([], "demon", True)) is None
    assert forecast.one_sided("prizepicks", PP_OVER) is None
    assert forecast.one_sided("prizepicks", markets.pp_sides(["under"], "standard", False)) is None
    assert forecast.one_sided("prizepicks", PP_BOTH) is None
    assert forecast.one_sided("prizepicks", {}) is None and forecast.one_sided("underdog", None) is None


def test_shown_keeps_the_chance_and_drops_the_letter():
    f = {"platform": "underdog", "sides": UD_OVER, "p_over": 0.71, "p_under": 0.29, "p_push": 0.0, "model_side": "over",
         "grade": "A", "units_policy": 1.0}
    sh = forecast.shown(f)
    assert sh["one_sided"] == "over" and sh["grade"] is None and sh["grade_label"] == "Over only" and sh["units_policy"] == 0.0
    assert sh["p_over"] == 0.71 and sh["side"] == "over"                     # the saved chance itself is unchanged
    two = forecast.shown(dict(f, sides=UD_BOTH_SUSPENDED))
    assert two["one_sided"] is None and two["grade"] == "A"
    pp = forecast.shown(dict(f, platform="prizepicks", sides=PP_GOBLIN))          # PrizePicks goblin: letter kept
    assert pp["one_sided"] is None and pp["grade"] == "A"
    rec = {"platform": "underdog", "sides": UD_OVER, "stat": "kills", "flags": [], "side_offered": 1, "units": 0.0}
    ok, why = forecast.slip_eligibility(rec)
    assert not ok and forecast.ONE_SIDED_REASON in why
    _, why = forecast.slip_eligibility(dict(rec, platform="prizepicks", sides=PP_GOBLIN))
    assert forecast.ONE_SIDED_REASON not in why


def _one_sided_world(con, tmp_path, monkeypatch):
    """The settled TEST series with its saved line read as Underdog Over only (one option listed)."""
    settle_fixture(con, tmp_path, monkeypatch)
    con.execute("UPDATE lol_forecasts SET sides = ?", (json.dumps(UD_OVER),))
    con.commit()


def test_record_leaves_out_one_sided_rows_and_keeps_them_saved(con, tmp_path, monkeypatch):
    settle_fixture(con, tmp_path, monkeypatch)
    con.execute("UPDATE lol_forecasts SET model_side='under', p_over=.3, p_under=.7, cal_version=NULL")
    con.commit()
    before = learning.dashboard(con)
    assert before["offered_side"]["settled_by_status"] == {"graded": 1}
    assert sum(x["wins"] + x["losses"] for x in before["calendar"]["days"]) in (0, 1)
    assert before["offered_side"]["metrics"]["all"]["n"] == 1 and len(before["containment_rows"]) == 1
    saved = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    con.execute("UPDATE lol_forecasts SET sides = ?", (json.dumps({"lower": {"status": "active"}}),))      # Under only
    con.commit()
    d = learning.dashboard(con)
    assert d["offered_side"]["settled_by_status"] == {} and "all" not in d["offered_side"]["metrics"]
    assert d["series_grouped_95ci"]["offered_side_only"] is None or d["series_grouped_95ci"]["offered_side_only"].get("n", 0) == 0
    assert d["one_sided_lines"]["settled_by_status"] == {"graded": 1}
    assert d["metrics"]["one-sided line (not graded)"]["n"] == 1 and "side offered" not in d["metrics"]
    assert sum(x["wins"] + x["losses"] + x["pushes"] for x in d["calendar"]["days"]) == 0
    assert len(d["containment_rows"]) == 1                     # the letter-hold evidence is unchanged (an owner decision apart)
    after = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    assert {k: v for k, v in after.items() if k != "sides"} == {k: v for k, v in saved.items() if k != "sides"}   # row kept
    assert con.execute("SELECT COUNT(*) FROM lol_settlements").fetchone()[0] == 1
    con.execute("UPDATE lol_forecasts SET platform='prizepicks', sides=?", (json.dumps(PP_GOBLIN),))   # PrizePicks: stays in
    con.execute("UPDATE lol_settlements SET unit_key = 'prizepicks' || substr(unit_key, 9)")
    con.commit()
    pp = learning.dashboard(con)
    assert pp["one_sided_lines"]["settled_by_status"] == {}


def test_one_sided_forecast_is_saved_without_a_grade_and_never_a_slip_leg(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    raw["over_under_lines"][0]["options"] = [{"choice": "higher", "status": "active", "payout_multiplier": "1.1"}]
    markets.poll(con, "underdog", fetcher=lambda p: (200, json.dumps(raw).encode()))
    forecast.run(con, db)
    f = dict(con.execute("SELECT * FROM lol_forecasts ORDER BY id DESC LIMIT 1").fetchone())
    assert forecast.one_sided(f["platform"], f["sides"]) == "over"
    assert f["grade"] is None and f["grade_label"] == "Over only" and f["slip_eligible"] == 0
    assert forecast.ONE_SIDED_REASON in json.loads(f["slip_reasons"])
    assert 0 < f["p_over"] < 1 and f["lambda"] > 0                       # the raw forecast is logged as always
    legs, left = slips.candidates(con, "underdog", preview=True)
    assert not legs and forecast.ONE_SIDED_REASON in left[f["id"]]
    chk = forecast.slip_check(con, [f["id"]])
    assert not chk["eligible"] and any(forecast.ONE_SIDED_REASON in r for r in chk["reasons"])
    b = forecast.board(con)
    row = next(r for r in b["rows"] if r["forecast"])
    assert row["forecast"]["one_sided"] == "over" and row["grade"] == "Over only"
    summary = slips.member_summary(con)
    assert summary["platforms"]["underdog"]["recommended"] == []
    assert forecast.ONE_SIDED_REASON in summary["platforms"]["underdog"]["blocked_by"]


def test_record_saved_before_os1_with_a_letter_reads_over_only(con, tmp_path, monkeypatch):
    """A record saved before os-1 kept its letter and slip_eligible = 1: every surface reads os-1 from its saved sides."""
    _one_sided_world(con, tmp_path, monkeypatch)
    con.execute("UPDATE lol_forecasts SET grade='A', grade_label='A', units_policy=1.0, slip_eligible=1, slip_reasons='[]', "
                "model_side='over', p_over=.8, p_under=.2, cal_version=NULL")
    con.commit()
    f = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    sh = forecast.shown(f)
    assert sh["grade"] is None and sh["grade_label"] == "Over only"
    _, left = slips.candidates(con, f["platform"], preview=True)
    assert forecast.ONE_SIDED_REASON in left[f["id"]]
