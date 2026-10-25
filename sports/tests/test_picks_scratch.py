"""Test picks (pk-1) stay apart from member measurement: admin-token picks, picks posted with "test": true and every pick
while SPORTS_PICKS_SCRATCH=1 (owner-only preview) are recorded with scratch=true; the admin aggregate counts member
picks only and reports test picks under "test". Every name and line is a TEST FIXTURE (slip_world)."""
import json

import pytest

from sports import picks, store, web
from sports.tests import slip_world as w


@pytest.fixture
def tw(monkeypatch):
    con = store.connect()
    monkeypatch.setattr(web, "_CACHE", {})
    monkeypatch.setattr(picks, "clock", lambda: w.NOW)
    monkeypatch.delenv("SPORTS_PICKS_SCRATCH", raising=False)
    legs = w.world(con, [("h1", "T1", 1, "over", 0.61), ("h2", "T2", 2, "under", 0.58), ("h3", "T3", 3, "over", 0.6)])
    yield legs
    con.close()


def _track(rid, f, role="member", member="m_a", **kw):
    item = dict({"platform": "underdog", "record_id": rid, "side": "over", "forecast_id": f.forecast_id,
                 "shown": {"p_over": f.p_over, "p_under": f.p_under, "p_push": f.p_push}, "surface": "board"}, **kw)
    req = web.Request(path="/api/sports/picks/track", method="POST", body=json.dumps({"picks": [item]}).encode(),
                      member=member, role=role)
    code, body, _ = web.dispatch(req)
    assert code == 200
    picks.drain()
    return json.loads(body)["results"][0]


def test_test_picks_are_tagged_and_kept_out_of_member_measurement(tw, monkeypatch):
    assert _track(*tw["h1"])["test"] is False                                    # a member pick
    assert _track(*tw["h1"], role="admin", member="m_owner")["test"] is True       # admin token
    assert _track(*tw["h2"], member="m_b", test=True)["test"] is True              # marked test
    monkeypatch.setenv("SPORTS_PICKS_SCRATCH", "1")                                # owner-only preview
    assert _track(*tw["h3"], member="m_c")["test"] is True
    pc = picks.connect()
    assert picks.ingest(pc)["picks"] == 4
    whys = sorted(json.loads(r[0]).get("scratch_reason") or "member" for r in pc.execute("SELECT payload FROM picks"))
    assert whys == ["SPORTS_PICKS_SCRATCH", "admin", "marked test", "member"]
    agg = picks.aggregate(pc)
    assert agg["all"]["picks"] == 1 and agg["all"]["members"] == 1 and agg["test"]["picks"] == 3
    assert sum(g["picks"] for g in agg["by"]["platform"].values()) == 1
    assert picks.mine(pc, "m_owner")["picks"] == [] and picks.mine(pc, "m_owner")["hidden_test"] == 1   # hidden by default
    mine = picks.mine(pc, "m_owner", include_test=True)["picks"]
    assert len(mine) == 1 and mine[0]["test"] is True
    pc.close()
