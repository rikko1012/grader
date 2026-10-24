"""Line correctness on the read side (sports/LINE_CORRECTNESS.md rows C5, C6, C10, C11, C12): board, player card,
slip pool, slip validation and new forecasts use the SAME current offer revision after A -> B -> A; earlier
forecasts keep their inputs and replay identically; forecasting is incremental; settlement scores a forecast / leg
at its own line; quote freshness uses the newest source time."""
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from sports import config, snapshots, store
from sports.contracts import Settlement, SideQuote
from sports.model import datasets, forward, market
from sports.slips import pool as slip_pool
from sports.slips import settle as slip_settle
from sports.slips import validate
from sports.tests import helpers as h
from sports.tests.test_integration_books_e2e import _world as _store_world
from sports.tests.test_model import _hist

CFG = {"markets": {mk: {"market": mk, "half_lives": [5, 20], "features": ["nba.home"], "family": fam,
                        "cal": [1.0, 1.05]} for mk, fam in (("points", "negbin"), ("rebounds", "mixture"),
                                                            ("assists", "poisson"), ("threes", "ratio"))}}


def _fit(hcon, art, cal=None):
    cfg = json.loads(json.dumps(CFG))
    if cal:
        for m in cfg["markets"].values():
            m["cal"] = cal
    (art / "model_config_nba.json").write_text(json.dumps(cfg))
    return forward.fit("nba", hcon, as_of="2026-01-31T20:00:00Z")


def _counts(scon):
    n_fc = scon.execute("SELECT COUNT(*) FROM records WHERE kind='forecast'").fetchone()[0]
    p = config.DATA_DIR / "model" / "forecasts.db"
    n_in = sqlite3.connect(p).execute("SELECT COUNT(*) FROM inputs").fetchone()[0] if p.exists() else 0
    return n_fc, n_in


def _run(scon, hcon, at):
    before = _counts(scon)
    out = forward.forecast_league(scon, hcon, "nba", at, log=lambda *x: None)
    after = _counts(scon)
    return out, (after[0] - before[0], after[1] - before[1])


def _seen(o, at):
    return replace(o, observed_at=at, known_at=at)


@pytest.fixture
def world(tmp_path, monkeypatch):
    hcon = _hist(tmp_path)
    monkeypatch.setattr(datasets, "db_path", lambda: tmp_path / "history.db")
    art = tmp_path / "artifacts"
    art.mkdir()
    monkeypatch.setattr(forward, "ART_DIR", art)
    _fit(hcon, art)
    scon = store.connect()
    rid1 = _store_world(scon)
    yield scon, hcon, art, rid1
    scon.close()


def test_c11_forecasting_is_incremental_and_c6_earlier_forecasts_replay_unchanged(world):
    scon, hcon, art, rid1 = world
    a = store.get(scon, rid1)
    out, grow1 = _run(scon, hcon, "2026-01-31T21:00:00Z")
    assert out["written"].get("sm-1") == 1 and grow1 == (1, 1)
    # unchanged offer revision + unchanged inputs, a later run: nothing new in either store
    out, grow = _run(scon, hcon, "2026-01-31T21:30:00Z")
    assert grow == (0, 0) and out["written"].get("sm-1") == 0
    store.put(scon, _seen(a, "2026-01-31T21:35:00Z"))                 # A seen again (A -> A): still nothing
    assert _run(scon, hcon, "2026-01-31T21:36:00Z")[1] == (0, 0)
    # A -> B: exactly one forecast for the new revision
    rid2 = store.put(scon, _seen(replace(a, line=18.5), "2026-01-31T21:40:00Z"))["record_id"]
    out, grow = _run(scon, hcon, "2026-01-31T21:45:00Z")
    assert grow == (1, 1)
    # B -> A: revision 3, exactly one more forecast (it names revision 3, not revision 1)
    r3 = store.put(scon, _seen(a, "2026-01-31T21:50:00Z"))
    assert r3["status"] == "revision" and r3["revision"] == 3
    out, grow = _run(scon, hcon, "2026-01-31T21:55:00Z")
    assert grow == (1, 1)
    assert _run(scon, hcon, "2026-01-31T22:00:00Z")[1] == (0, 0)
    sm = sorted((f for _, f in store.forecasts(scon, model_version="sm-1")), key=lambda f: f.forecast_at)
    assert [f.offer_snapshot_ids for f in sm] == [(rid1,), (rid2,), (r3["record_id"],)]
    assert [f.line for f in sm] == [17.5, 18.5, 17.5]
    # C6: every earlier forecast keeps its original inputs and replays bit-identically
    for f in sm:
        orig, again = forward.replay(f.forecast_id, scon)
        assert again == orig and orig.offer_snapshot_ids == f.offer_snapshot_ids
    # a new artifact version re-forecasts the unchanged current revision (once)
    old_art = forward.art_path("nba").read_text(encoding="utf-8")
    new = _fit(hcon, art, cal=[1.0, 1.07])
    out, grow = _run(scon, hcon, "2026-01-31T22:05:00Z")
    assert grow == (1, 1)
    assert _run(scon, hcon, "2026-01-31T22:10:00Z")[1] == (0, 0)
    latest = max((f for _, f in store.forecasts(scon, model_version="sm-1")), key=lambda f: f.forecast_at)
    assert latest.artifact_version == f"sm-1:{new['sha']}" and latest.offer_snapshot_ids == (r3["record_id"],)
    # the earlier forecasts still replay unchanged under THEIR artifact
    forward.art_path("nba").write_text(old_art, encoding="utf-8")
    for f in sm:
        orig, again = forward.replay(f.forecast_id, scon)
        assert again == orig


def test_c11_existing_forecasts_are_indexed_on_migration_so_the_first_run_after_deploy_adds_nothing(world):
    scon, hcon, art, rid1 = world
    _run(scon, hcon, "2026-01-31T21:00:00Z")
    scon.execute("DELETE FROM forecast_keys")                       # simulate a store from before migration 3
    scon.execute("DELETE FROM meta WHERE k = 'forecast_keys_backfill'")
    scon.commit()
    scon2 = store.connect()                                           # restart: the backfill indexes the old forecasts
    assert json.loads(store._meta(scon2, "forecast_keys_backfill"))["indexed"] >= 1
    assert _run(scon2, hcon, "2026-01-31T21:30:00Z")[1] == (0, 0)
    scon2.close()


def test_c5_board_card_slip_pool_validation_and_new_forecast_use_the_same_revision(world, tmp_path):
    scon, hcon, art, rid1 = world
    a = store.get(scon, rid1)
    _run(scon, hcon, "2026-01-31T21:00:00Z")
    store.put(scon, _seen(replace(a, line=18.5), "2026-01-31T21:40:00Z"))
    _run(scon, hcon, "2026-01-31T21:42:00Z")
    rid3 = store.put(scon, _seen(a, "2026-01-31T21:50:00Z"))["record_id"]
    _run(scon, hcon, "2026-01-31T21:52:00Z")
    now = "2026-01-31T21:55:00Z"
    # the store's one current rule
    (cur_rid, cur), = [(r, o) for r, o in store.offers(scon, league="nba") if o.offer_id == a.offer_id]
    assert cur_rid == rid3 and cur.line == 17.5
    # the newly created forecast is for that revision
    new_fc = max((f for _, f in store.forecasts(scon, model_version="sm-1")), key=lambda f: f.forecast_at)
    assert new_fc.offer_snapshot_ids == (rid3,)
    # board + player card (UI snapshots)
    snap = tmp_path / "snap"
    snapshots.build(scon, "nba", now=now, base=snap, history=False)
    board = json.loads((snap / "nba" / "board.json").read_text(encoding="utf-8"))
    more = json.loads((snap / "nba" / "board_more.json").read_text(encoding="utf-8"))
    bq = [q for b in (board, more) for row in b["markets"] for q in row["q"]]
    assert [q["r"] for q in bq] == [rid3] and bq[0]["f"] == new_fc.forecast_id
    card = json.loads((snap / "nba" / "players" / f"{snapshots.safe_name(a.player_id)}.json").read_text(
        encoding="utf-8"))
    quotes = [q for r in card["markets"] for q in r["quotes"]]
    assert [q["record_id"] for q in quotes] == [rid3]
    assert quotes[0]["forecast_id"] == new_fc.forecast_id
    assert card["forecasts"][new_fc.forecast_id]["offer_snapshot_ids"] == [rid3]
    # slip pool + validation
    pl = slip_pool.build(scon, "nba", "prizepicks", now=datetime(2026, 1, 31, 21, 55, tzinfo=timezone.utc),
                         policy="research")
    st = pl["state"][a.offer_id]
    assert st["record_id"] == rid3 and st["eligible"], st["reason"]
    leg = st["leg"]
    assert leg["record_id"] == rid3 and leg["forecast_id"] == new_fc.forecast_id
    chk = validate.check({"platform": "prizepicks", "entry_type": "power", "legs": [leg]}, pl,
                         now=datetime(2026, 1, 31, 21, 56, tzinfo=timezone.utc))
    assert not [p for p in chk["problems"] if p["code"] in ("changed", "gone", "forecast", "stale", "policy")], chk
    # a slip built on revision 1 (same line, earlier revision) is NOT silently accepted
    old = dict(leg, record_id=rid1)
    chk = validate.check({"platform": "prizepicks", "entry_type": "power", "legs": [old]}, pl,
                         now=datetime(2026, 1, 31, 21, 56, tzinfo=timezone.utc))
    assert any(p["code"] == "changed" for p in chk["problems"])


# ------------------------------------------------------------------------------------------- C10 settlement line
def _st(line, value, status="win", side="over"):
    return Settlement(settlement_id="s", platform="prizepicks", offer_id="pp:1", side=side, line=line,
                      event_id="nba:espn:1", player_id="nba:player:espn:3134907", stat="points", status=status,
                      outcome=None if status in ("void", "unresolved") else ("over" if value > line else "under"),
                      source_value=value, source="espn", source_ref="x", rule_version="t",
                      settled_at="2026-10-24T04:00:00Z")


def test_c10_settlement_is_read_at_the_forecast_or_leg_line():
    from sports.settlement import at_line
    s = _st(21.5, 21.0, status="loss")                       # the offer's line at settlement: 21.5, value 21
    assert at_line(s, 21.5, "over") == ("loss", "under")
    assert at_line(s, 20.5, "over") == ("win", "over")       # a forecast made on revision 1 at 20.5
    assert at_line(s, 21.0, "over") == ("push", "push")
    assert at_line(s, 20.5, "under") == ("loss", "over")
    assert at_line(_st(21.5, None, status="void"), 20.5, "over") == ("void", None)
    assert at_line(None, 20.5) == (None, None)


def test_c10_slip_leg_is_settled_at_its_own_line():
    s = _st(21.5, 21.0, status="loss")
    legs = [{"platform": "prizepicks", "offer_id": "pp:1", "side": "over", "line": 20.5, "team": "A"},
            {"platform": "prizepicks", "offer_id": "pp:2", "side": "over", "line": 5.5, "team": "B"}]
    s2 = replace(_st(5.5, 7.0), offer_id="pp:2")
    res = slip_settle.settle_slip({"platform": "prizepicks", "entry_type": "power", "legs": legs},
                                  {("prizepicks", "pp:1", "over"): s, ("prizepicks", "pp:2", "over"): s2})
    assert [r["status"] for r in res["leg_results"]] == ["win", "win"] and res["status"] == "won"


# ------------------------------------------------------------------------------------------- C12 quote freshness
def test_c12_an_unchanged_book_quote_is_as_fresh_as_its_newest_source_time():
    ev = h.event(start="2026-10-23T23:00:00Z")
    q = h.offer(platform="fanduel", oid="fd:1", line=20.5, observed_at="2026-10-23T22:00:00Z",
                source_updated_at="2026-10-23T22:00:00Z", source_time_unknown=False,
                sides=(SideQuote("over", True, "decimal", price=1.9), SideQuote("under", True, "decimal", price=1.9)))
    fat = "2026-10-23T22:30:00Z"
    assert market.quote_prob(q, ev, fat, "2026-10-23T22:29:00Z")[1] == "older than 15 min"     # first time only
    p, why = market.quote_prob(q, ev, fat, "2026-10-23T22:29:00Z", last_source="2026-10-23T22:28:00Z")
    assert why is None and abs(p - 0.5) < 1e-9
    # a source time after the forecast time is never used (no leakage)
    assert market.quote_prob(q, ev, fat, "2026-10-23T22:29:00Z", last_source="2026-10-23T22:31:00Z")[1] == \
        "older than 15 min"


def test_c11_the_store_rule_covers_every_forecast_writer():
    """sharp-2 (worker forecast), forward, legacy import: one forecast per input key, whoever writes it."""
    con = store.connect()
    f1 = h.forecast(forecast_at="2026-10-23T20:00:00Z")
    f2 = h.forecast(forecast_at="2026-10-23T20:30:00Z")                    # same inputs, later run
    f3 = h.forecast(forecast_at="2026-10-23T20:30:00Z", offer_snapshot_ids=("offer:y",))   # a new revision
    assert store.put(con, f1)["status"] == "inserted"
    r2 = store.put(con, f2)
    assert r2["status"] == "unchanged" and r2["record_id"] == f1.forecast_id
    assert store.put(con, f3)["status"] == "inserted"
    assert con.execute("SELECT COUNT(*) FROM records WHERE kind='forecast'").fetchone()[0] == 2
    con.close()


def test_c11_slip_pool_finds_an_old_forecast_of_an_unchanged_revision():
    """With incremental forecasting an unchanged offer keeps its first forecast for days: the pool selects forecasts
    by the current offers' events, not by a 72 h window."""
    from datetime import timedelta
    from sports.tests import slip_world as sw
    con = store.connect()
    store.put(con, sw.ev(1))
    o = sw.offer("1", "A1", 1)
    rid = store.put(con, o)["record_id"]
    old = sw.forecast(rid, o, forecast_at="2026-10-19T20:00:00Z")        # 4 days before
    store.put(con, replace(old), known_at="2026-10-19T20:00:00Z")
    store.put(con, sw.offer("1", "A1", 1, observed_at="2026-10-23T20:04:00Z"))   # still listed, unchanged
    pl = slip_pool.build(con, "nba", "underdog", now=sw.NOW)
    st = pl["state"][o.offer_id]
    assert st["record_id"] == rid and st["eligible"], st["reason"]
    assert st["leg"]["forecast_id"] == old.forecast_id
    assert sw.NOW - timedelta(hours=72) > datetime(2026, 10, 19, 20, tzinfo=timezone.utc)
    con.close()
