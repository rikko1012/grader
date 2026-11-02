"""Member pick tracking pk-1: record / refuse / settle / correction, idempotent ingest, spool bounds, routes.
Every name, line and probability here is a TEST FIXTURE (slip_world)."""
import json
import queue
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from sports import picks, settlement as settlement_mod, store, web
from sports.tests import slip_world as w

LATER = datetime(2026, 10, 24, 0, 1, tzinfo=timezone.utc)     # after the fixture start


@pytest.fixture
def con():
    c = store.connect()
    yield c
    c.close()


@pytest.fixture
def tw(monkeypatch, con):
    monkeypatch.setattr(web, "_CACHE", {})
    monkeypatch.setattr(picks, "clock", lambda: w.NOW)
    legs = w.world(con, [("h1", "T1", 1, "over", 0.61), ("h2", "T2", 2, "under", 0.58)])
    yield con, legs


def _req(path, body=None, role="member", member="m_owner"):
    return web.Request(path=path, method="POST" if body is not None else "GET", query={},
                       body=json.dumps(body).encode() if body is not None else b"", member=member, role=role)


def _call(req):
    code, body, _ = web.dispatch(req)
    return code, json.loads(body)


def _item(rid, f, side="over", **kw):
    return dict({"platform": "underdog", "record_id": rid, "side": side, "forecast_id": f.forecast_id,
                 "shown": {"p_over": f.p_over, "p_under": f.p_under, "p_push": f.p_push}, "surface": "board"}, **kw)


def _track(items, member="m_owner"):
    code, b = _call(_req("/api/sports/picks/track", {"picks": items}, member=member))
    assert code == 200
    picks.drain()
    return b["results"]


def test_a_pick_is_recorded_with_its_exact_offer_revision_and_forecast(tw):
    con, legs = tw
    rid, f = legs["h1"]
    r = _track([_item(rid, f)])
    assert r[0]["ok"] and r[0]["pick_id"].startswith("pk-") and r[0]["stake_units"] == 0
    pc = picks.connect()
    assert picks.ingest(pc)["picks"] == 1
    row = json.loads(pc.execute("SELECT payload FROM picks").fetchone()[0])
    assert row["record_id"] == rid and row["revision"] == 1 and row["forecast_id"] == f.forecast_id
    assert row["model_version"] == f.model_version and row["displayed"]["p_over"] == f.p_over
    assert row["member"] == picks.member_hash("m_owner") and "m_owner" not in json.dumps(row)
    assert row["tracked_at"] < row["start_utc"] and row["stake_units"] == 0 and row["grade_shown"] is None
    assert row["platform"] == "underdog" and row["side"] == "over" and row["line"] == 20.5


def test_ingest_is_idempotent_and_a_repeated_click_is_one_pick(tw):
    con, legs = tw
    rid, f = legs["h1"]
    _track([_item(rid, f)])
    _track([_item(rid, f)])                                       # same member, revision, side
    pc = picks.connect()
    c1 = picks.ingest(pc)
    assert c1["picks"] == 1 and c1["duplicates"] == 1
    assert picks.ingest(pc)["lines"] == 0                          # resumable: nothing re-read
    assert pc.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 1
    _track([_item(rid, f)], member="m_other")                     # another member = another pick
    assert picks.ingest(pc)["picks"] == 1
    with pytest.raises(Exception):
        pc.execute("UPDATE picks SET line = 1")                    # append-only


def test_picks_are_refused_when_not_current_or_started_or_mismatched(tw, monkeypatch):
    con, legs = tw
    rid, f = legs["h1"]

    def code_of(item):
        return _track([item])[0].get("code")
    assert code_of(dict(_item(rid, f), platform="kalshi")) == "not_pickem"
    assert code_of(_item(rid, f, shown={"p_over": f.p_over + 0.01, "p_under": f.p_under, "p_push": 0})) \
        == "display_mismatch"
    assert code_of(_item(rid, f, forecast_id=legs["h2"][1].forecast_id)) == "forecast_mismatch"
    assert code_of(_item(rid, f, forecast_id=None)) == "display_mismatch"   # a chance without a record
    assert code_of(_item(rid, f, forecast_id=None, shown=None))  is None     # no chance shown: recorded
    o = store.get(con, rid)
    moved = replace(o, line=22.5, observed_at="2026-10-23T20:03:00Z", known_at="2026-10-23T20:03:00Z")
    store.put(con, moved)
    assert code_of(_item(rid, f)) == "changed"                     # an older revision is never recorded
    monkeypatch.setattr(picks, "clock", lambda: LATER)
    rid2, f2 = legs["h2"]
    assert code_of(_item(rid2, f2, side="under")) == "started"
    monkeypatch.setattr(picks, "clock", lambda: datetime(2026, 10, 23, 20, 40, tzinfo=timezone.utc))
    assert code_of(_item(rid2, f2, side="under")) == "stale"
    one = w.offer("h9", "T9", 2, sides=("over",))
    rid9 = store.put(con, one)["record_id"]
    monkeypatch.setattr(picks, "clock", lambda: w.NOW)
    assert code_of({"platform": "underdog", "record_id": rid9, "side": "under"}) == "side_not_offered"
    code, b = _call(_req("/api/sports/picks/track", {"picks": [{"platform": "underdog", "record_id": rid9,
                                                                 "side": "sideways"}]}))
    assert code == 400


def test_settled_at_the_picks_own_line_with_revisions_on_corrections(tw):
    con, legs = tw
    rid, f = legs["h1"]
    _track([_item(rid, f)])                                        # Over 20.5 on revision 1
    o = store.get(con, rid)
    moved = replace(o, line=24.5, observed_at="2026-10-23T20:03:00Z", known_at="2026-10-23T20:03:00Z")
    store.put(con, moved)                                          # the offer moved before settlement
    pc = picks.connect()
    picks.ingest(pc)
    assert picks.settle_all(con, pc)["pending"] == 1               # nothing settled yet
    s1 = w.settlement(moved, "over", "loss", 22.0)                 # 22 < 24.5 at the CURRENT line ...
    settlement_mod.record(con, [s1])
    c = picks.settle_all(con, pc)
    assert c["inserted"] == 1
    st = picks.latest_settlement(pc, pc.execute("SELECT pick_id FROM picks").fetchone()[0])
    assert st["status"] == "win" and st["value"] == 22.0           # ... but a win at the pick's own 20.5
    assert picks.settle_all(con, pc)["unchanged"] == 1
    settlement_mod.record(con, [w.settlement(moved, "over", "loss", 19.0, at="2026-10-24T06:00:00Z")])
    assert picks.settle_all(con, pc)["revision"] == 1              # provider correction 22 -> 19
    rows = pc.execute("SELECT revision, status, supersedes FROM settlements ORDER BY revision").fetchall()
    assert [tuple(r) for r in rows] == [(1, "win", None), (2, "loss", 1)]


def test_my_picks_and_the_admin_aggregate(tw):
    con, legs = tw
    (r1, f1), (r2, f2) = legs["h1"], legs["h2"]
    _track([_item(r1, f1), _item(r2, f2, side="under", surface="card")])
    _track([_item(r1, f1)], member="m_other")
    pc = picks.connect()
    picks.ingest(pc)
    settlement_mod.record(con, [w.settlement(store.get(con, r1), "over", "win", 25.0),
                                w.settlement(store.get(con, r2), "under", "loss", 30.0)])
    picks.settle_all(con, pc)
    pc.close()
    code, mine = _call(_req("/api/sports/picks/mine"))
    assert code == 200 and mine["total"] == 2 and mine["stake_units"] == 0
    assert {p["result"]["status"] for p in mine["picks"]} == {"win", "loss"}
    assert all("grade" not in p and p["stake_units"] == 0 and p["validation_text"] for p in mine["picks"])
    assert _call(_req("/api/sports/picks/mine", member="m_nobody"))[1]["total"] == 0
    assert _call(_req("/api/sports/admin/picks"))[0] == 403         # member token
    code, agg = _call(_req("/api/sports/admin/picks", role="admin"))
    a = agg["all"]
    assert code == 200 and a["picks"] == 3 and a["members"] == 2 and a["wins"] == 2 and a["losses"] == 1
    assert a["hit_rate"] == round(2 / 3, 4) and a["with_displayed_chance"] == 3
    exp = ((0.61 - 1) ** 2 * 2 + (0.58 - 0) ** 2) / 3
    assert a["brier"] == round(exp, 4) and a["log_loss"] > 0
    assert agg["by"]["surface"]["card"]["picks"] == 1 and "spool" in agg


def test_my_picks_before_any_ingest_is_empty_not_an_error(tw):
    assert _call(_req("/api/sports/picks/mine")) == (200, {"version": picks.VERSION, "picks": [], "total": 0,
                                                          "shown": 0, "stake_units": 0})


def test_spool_is_bounded_and_never_blocks(monkeypatch, tmp_path):
    monkeypatch.setattr(picks, "_Q", queue.Queue(maxsize=2))
    monkeypatch.setattr(picks, "_ensure_writer", lambda: None)     # no writer: the queue fills
    before = picks.STATS["dropped_queue_full"]
    assert picks.spool({"pick_id": "a"}) and picks.spool({"pick_id": "b"})
    assert picks.spool({"pick_id": "c"}) is False
    assert picks.STATS["dropped_queue_full"] == before + 1
    monkeypatch.setattr(picks, "SPOOL_DAY_MAX_MB", 0.0001)          # 100 bytes per day
    d0 = picks.STATS["dropped_day_cap"]
    picks.flush([{"kind": "pick", "x": "y" * 60}, {"kind": "pick", "x": "z" * 60}])
    assert picks.STATS["dropped_day_cap"] == d0 + 1
    (f,) = list(picks.spool_dir().glob("*.jsonl"))
    assert f.stat().st_size < 100


def test_bad_spool_lines_are_counted_not_loaded(tmp_path):
    d = picks.spool_dir()
    d.mkdir(parents=True)
    (d / "2026-10-24.jsonl").write_text('{"kind":"pick"}\nnot json\n{"kind":"pick","pick_id":"p1"', encoding="utf-8")
    c = picks.ingest(picks.connect())
    assert c == {"lines": 2, "picks": 0, "duplicates": 0, "bad": 2}  # the partial last line waits for its newline


# ---- the check alone (research workspace): the same answer as track, and nothing is recorded
def test_check_route_answers_like_track_and_spools_nothing(tw):
    con, legs = tw
    rid, f = legs["h1"]
    before = picks.stats()["spooled"]
    code, b = _call(_req("/api/sports/picks/check", {"picks": [_item(rid, f)]}))
    assert code == 200 and b["results"][0]["ok"] and b["results"][0]["record_id"] == rid and b["results"][0]["forecast_id"] == f.forecast_id
    assert picks.stats()["spooled"] == before                       # nothing queued, so nothing can reach picks.db
    q = web.Request(path="/api/sports/picks/check", method="GET", query={"picks": json.dumps([_item(rid, f)])}, body=b"", member="m_owner", role="member")
    assert _call(q)[1]["results"][0]["ok"]                          # GET too: the gateway's proxy mode forwards GET only


def test_check_route_gives_tracks_refusals(tw):
    con, legs = tw
    rid, f = legs["h1"]
    _, b = _call(_req("/api/sports/picks/check", {"picks": [_item(rid, f, shown={"p_over": 0.01, "p_under": 0.99, "p_push": 0.0})]}))
    assert b["results"][0] == {"ok": False, "code": "display_mismatch", "text": "The chance shown differs from Scout's record - refresh", "record_id": rid, "side": "over"}
    code, b = _call(_req("/api/sports/picks/check", {"picks": []}))
    assert code == 400
    q = web.Request(path="/api/sports/picks/check", method="GET", query={"picks": "not json"}, body=b"", member="m_owner", role="member")
    assert _call(q)[0] == 400
