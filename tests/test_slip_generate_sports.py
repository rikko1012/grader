"""Slip Studio sq-1 across CS2, LoL, NBA and NFL over real HTTP (tests/workspace_other_fixture.py): NBA / NFL letters enter by
default (ct-1b released), held lines never enter, Underdog keeps one sport, and the LoL settled record is read from the
worker's real schema. No network and no production data."""
import pytest

import server
import slip_quality as sq
import workspace_api
from tests.test_workspace_all_sports import API, env  # noqa: F401  (env is the fixture)
from tests.test_workspace_api import call


def gen(fx, **q):
    q = dict({"platform": "prizepicks", "type": "power", "slips": 8, "picks": 2, "sports": "cs2,lol,nba,nfl"}, **q)
    return call(fx, "GET", API + "generate?" + "&".join(f"{k}={v}" for k, v in q.items()), user="u1")


def legs(doc):
    return [l for s in doc["slips"] for l in s["legs"]]


def test_nba_nfl_letters_enter_by_default_without_research_label(env, monkeypatch):
    """owner 2026-11-12 ("sports should be able to go in slips now"): NBA / NFL letters are released (ct-1b), so ticking NBA or NFL
    puts their A-C legs in Auto-build with no opt-in; research=0 from an old page changes nothing; no "research estimate" text."""
    monkeypatch.setattr(server.Handler, "_sports_owner", lambda self: True)
    for extra in ({}, {"research": 0}):
        st, doc = gen(env, sports="cs2,nba", slips=8, **extra)
        assert st == 200, doc
        assert doc["pool"]["excluded"].get("research", 0) == 0
        st0, only = gen(env, sports="cs2", slips=8, **extra)
        assert doc["pool"]["legs"] > only["pool"]["legs"]                       # the NBA letters are in the pool
        # sb-1 (2026-11-17): an NBA / NFL leg ranks at its league's settled chance band when the sports Results file is up, so it no
        # longer outranks CS2 on its stated chance alone; whichever legs make a slip carry their own record line
        sports = [l for l in legs(doc) if l["sport"] in ("nba", "nfl")]
        assert all(l["record"].startswith(("NBA picks stated", "NFL picks stated")) or l["record"] == "No settled record for this band yet"
                   for l in sports), legs(doc)
        assert all("research estimate" not in s["why"].lower() for s in doc["slips"])


def test_held_lines_never_enter_even_with_research(env, monkeypatch):
    monkeypatch.setattr(server.Handler, "_sports_owner", lambda self: True)
    st, doc = gen(env, research=1, sports="nba,nfl", slips=12)
    assert st == 200
    board = {}
    for lg in ("nba", "nfl"):
        st, b = call(env, "GET", API + f"board?sport={lg}", user="u1")
        board.update({(r["offer"]["platform"], r["offer"]["offer_id"]): r for r in b["records"]})
    for l in legs(doc):
        r = board[(l["book"], l["row_id"])]
        assert not r["scout"]["hold"] and r["scout"]["grade"] in sq.RESEARCH_GRADES


def test_underdog_slips_keep_one_sport(env, monkeypatch):
    monkeypatch.setattr(server.Handler, "_sports_owner", lambda self: True)
    st, doc = gen(env, platform="underdog", type="standard", research=1)
    assert st == 200
    assert all(len({l["sport"] for l in s["legs"]}) == 1 for s in doc["slips"])


def test_a_sport_the_member_does_not_see_is_left_out(env, monkeypatch):
    monkeypatch.setattr(server, "lol_public", lambda: False)
    st, doc = gen(env, sports="lol")
    assert st == 400 and doc == {"error": "Choose at least one sport."}


def test_lol_settled_record_reads_the_worker_schema(env):
    lol = env.lol
    lol.con.execute("UPDATE lol_forecasts SET side_offered = 1")
    lol.con.commit()
    row = next(r for r in lol.rows if r.get("forecast"))
    lol.settle(row, over_hit=1, actual=9)
    got = workspace_api._lol_settled(server)
    assert len(got) == 1 and got[0]["over_hit"] == 1 and got[0]["stat"] == row["stat"]
    units = sq.units_from_lol(got)
    assert len(units) == 1 and units[0]["sport"] == "lol"
    workspace_api.reset()
    table = workspace_api._slip_table(server)
    assert table is not None and any(k.startswith("lol|") for k in table["cells"])


def test_lol_band_table_leaves_out_one_sided_lines_rp1(env, monkeypatch):
    """rp-1 (LoL round 2): an Underdog line that listed one option only (os-1) never was a graded pick, so the slip band table
    leaves it out like the member record does; a two-sided line stays; LOL_RECORD_PLAYABLE=0 restores the old rows."""
    lol = env.lol
    lol.con.execute("UPDATE lol_forecasts SET side_offered = 1")
    lol.con.commit()
    row = next(r for r in lol.rows if r.get("forecast"))
    lol.con.execute("UPDATE lol_forecasts SET platform = 'underdog', sides = ? WHERE market_key = ?",
                    ('{"higher": {"status": "active"}}', row["market_key"]))
    lol.con.commit()
    lol.settle(dict(row, platform="underdog"), over_hit=1, actual=9)
    monkeypatch.delenv("LOL_RECORD_PLAYABLE", raising=False)
    assert workspace_api._lol_settled(server) == []
    monkeypatch.setenv("LOL_RECORD_PLAYABLE", "0")
    assert len(workspace_api._lol_settled(server)) == 1
    monkeypatch.delenv("LOL_RECORD_PLAYABLE")
    lol.con.execute("UPDATE lol_forecasts SET sides = ?", ('{"higher": {"status": "active"}, "lower": {"status": "active"}}',))
    lol.con.commit()
    assert len(workspace_api._lol_settled(server)) == 1
