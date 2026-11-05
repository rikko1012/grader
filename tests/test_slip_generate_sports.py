"""Slip Studio sq-1 / sq-2 across CS2, LoL, NBA and NFL over real HTTP (tests/workspace_other_fixture.py): research estimates stay out
unless asked and are then marked, held lines never enter, Underdog keeps one sport, and the LoL settled record is read from the
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


def test_research_estimates_stay_out_unless_asked_then_marked(env, monkeypatch):
    monkeypatch.setattr(server.Handler, "_sports_owner", lambda self: True)
    st, doc = gen(env)
    assert st == 200, doc
    # sq-2: left out, a research letter is a plain leg at its printed chance - never marked, never a letter
    sp = [l for l in legs(doc) if l["sport"] in ("nba", "nfl")]
    assert all(l["chance"] and not l["research"] and l["record"] == f"Lean {l['side'].title()}" and l["p"] > 0.5 for l in sp)
    st, doc = gen(env, research=1, sports="cs2,nba", slips=8)      # the fixture has one research letter per platform: it pairs with CS2
    assert st == 200
    research = [l for l in legs(doc) if l["research"]]
    assert research and all(l["record"] == "Research estimate" for l in research)
    assert all("research estimate" in s["why"] for s in doc["slips"] if any(l["research"] for l in s["legs"]))


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
        if l["chance"]:                         # sq-2: a leg without a letter only on the side the adapter names as the model's own
            assert l["side"] == (r["scout"]["side"] or r["scout"].get("slip_side")) and l["p"] > 0.5
            assert r["scout"].get("slip_side") == l["side"]
        else:
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
