"""Reference prices never become actionable EV or arbitrage slips."""
import json
import pytest
import workspace_markets as wm
import workspace_books as wb
from tests.test_phase4_markets import fake, snap, fixture_books_settle_like_the_app


def test_reference_keeps_single_book_and_unpriced_whole_lines(fake):
    s = wm.sheet(None, "nba", fake)
    assert any(r["line"] == 30.5 for r in s["comparisons"])
    assert any(r["line"] == 21 for r in s["comparisons"])
    assert not any(r["line"] == 30.5 for r in s["ev"])
    assert all(r["reference_only"] and "ev" not in r and "p_cal" not in r for r in s["comparisons"])
    assert all(r["book"] != "kalshi" for r in s["comparisons"])


def test_negative_pairs_and_freshness(fake, monkeypatch):
    data = snap([("fresh", 10.5, [("fanduel",1.9,1.9,20),("betrivers",1.91,1.91,20)]),
                 ("old", 11.5, [("fanduel",1.9,1.9,20),("betrivers",1.91,1.91,301)]),
                 ("expired", 12.5, [("fanduel",1.9,1.9,1801)]),
                 ("future", 13.5, [("fanduel",1.9,1.9,-30)])], fake)
    monkeypatch.setattr(wb,"snap",lambda *a:data)
    s=wm.sheet(None,"nba",fake)
    assert len(s["closest_pairs"]) == 1
    r=s["closest_pairs"][0]
    assert r["line"]==10.5 and r["margin_pct"]==pytest.approx((1/(1/1.9+1/1.91)-1)*100,abs=.001)
    assert r["reference_only"] and "stake_per_100" not in json.dumps(r)
    assert not s["arbitrage"]
    assert {r["line"] for r in s["comparisons"]} == {10.5,11.5}


def test_reference_bounded_per_book_and_entitlement(fake,monkeypatch):
    data=snap([(str(i),i+.5,[("fanduel",1.9,1.9,10),("betrivers",1.9,1.9,20)]) for i in range(100)],fake)
    monkeypatch.setattr(wb,"snap",lambda *a:data)
    doc=wm.build(None,fake)
    assert len(doc["sports"]["nba"]["comparisons"]) == 20
    assert len(doc["sports"]["nba"]["closest_pairs"]) == 25
    m=wm.for_member(json.dumps(doc),{"nba":True})
    assert set(m["sports"])=={"nba"}
    assert {b["id"] for b in m["books"]}=={"fanduel","betrivers"}
    assert wm.for_member(json.dumps(doc),{})["books"]==[]
