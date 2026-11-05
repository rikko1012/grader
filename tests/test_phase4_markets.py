"""Phase 4 item 2: SCOUT_MARKET_TOOLS +EV feed and arbitrage sheet (workspace_markets; math in gradecore.market_tools).
EV = p_cal x decimal - 1, push-adjusted; p_cal = released Scout chance or the other books' no-vig consensus (never the priced book);
stake share A = impA/(impA+impB); margin % = (1/(impA+impB) - 1) x 100; stale / prediction-market / pick'em legs flagged and left out."""
import json
import time
from datetime import datetime, timezone

import pytest

import book_providers as bp
import workspace_books as wb
import workspace_markets as wm
from gradecore import devig
from tests.test_workspace_api import env, get  # noqa: F401


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def rec(line, name="Nba Wing", pkey="p1", eid="e1"):
    return {"sport": "nba", "key": f"nba.{eid}.{pkey}.points.full_game.{line}", "event": {"id": eid, "label": "A at B", "start_utc": "2099-01-01T00:00:00+00:00"},
            "player": {"key": pkey, "name": name, "team": "A"},
            "market": {"stat": "points", "stat_label": "Points", "period": "full_game", "period_label": "Full game", "line_type": "standard"},
            "offer": {"platform": "underdog", "platform_label": "Underdog", "offer_id": "o-" + pkey, "line": line},
            "scout": {"side": "over", "p_side": 0.9, "p_over": 0.9, "p_under": 0.1, "p_push": 0.0, "grade": "A", "model_version": "sm"}, "state": {"kind": "ok"}}


def snap(groups, now):
    """groups: [(pkey, line, [(platform, over, under, age_s), ...])] -> a fake NBA snapshot shaped like workspace_sports._SNAP."""
    index, props, recs = {}, {}, []
    for pkey, line, books in groups:
        r = rec(line, name="Nba " + pkey, pkey=pkey)
        recs.append(r)
        rows = [{"platform": p, "platform_label": p, "line": line, "kind": "devig", "over": o, "under": u, "seen_at": iso(now - age), "in_consensus": True}
                for p, o, u, age in books]
        x = {"rec": json.dumps(r).encode(), "books": (rows, []), "e": "e1", "pkey": pkey, "st": "points", "scope": "full_game", "l": line, "p": "underdog"}
        index[("underdog", "o-" + pkey)] = x
        props[("e1", pkey, "points", "full_game")] = [("underdog", "o-" + pkey)]
    return {"body": json.dumps({"records": recs}).encode(), "index": index, "props": props}


@pytest.fixture
def fake(monkeypatch):
    bp.reset()
    wb.reset()
    for f in ("SCOUT_BOOK_SLEEPER",):
        monkeypatch.delenv(f, raising=False)
    now = time.time()
    snaps = {"nba": snap([
        ("p1", 20.5, [("fanduel", 2.10, 1.80, 30), ("betrivers", 1.95, 1.90, 40), ("oddsapi:draftkings", 2.25, 1.70, 20)]),
        ("p2", 21.0, [("fanduel", 2.10, 1.80, 30), ("betrivers", 1.95, 1.90, 40), ("oddsapi:draftkings", 2.25, 1.70, 20)]),   # whole line
        ("p3", 9.5, [("fanduel", 2.30, 1.70, 30), ("betrivers", 1.60, 2.40, 900)]),                                       # stale leg
        ("p4", 5.5, [("fanduel", 1.90, 1.90, 30), ("kalshi", "0.40 / 0.42", None, 30)]),                                 # prediction market
        ("p5", 30.5, [("fanduel", 1.70, 2.30, 30)]),                                                                     # one book only
    ], now)}
    monkeypatch.setattr(wb, "snap", lambda srv, sport: snaps.get(sport))
    monkeypatch.setattr(wm, "model_released", lambda sport, stat: False)
    return now


def test_ev_uses_the_other_books_no_vig_consensus_never_the_priced_book(fake):
    s = wm.sheet(None, "nba", fake)
    dk = [r for r in s["ev"] if r["book"] == "oddsapi:draftkings" and r["line"] == 20.5 and r["side"] == "over"]
    assert len(dk) == 1
    r = dk[0]
    p = (devig.worst_case([2.10, 1.80], 0)[0] + devig.worst_case([1.95, 1.90], 0)[0]) / 2
    assert r["p_cal"] == pytest.approx(p, abs=1e-4) and r["ev"] == pytest.approx(p * 2.25 - 1, abs=1e-4)
    assert "FanDuel" in r["p_source"] or "fanduel" in r["p_source"]
    assert "draftkings" not in r["p_source"].lower() and r["width"]["overround"] == pytest.approx(1 / 2.25 + 1 / 1.70 - 1, abs=1e-6) and r["width"]["wide"] is False
    assert all(x["ev"] > 0 for x in s["ev"])
    assert not any(x["line"] == 21.0 for x in s["ev"]) and s["excluded"]["Whole-number line: push chance not priced"] > 0
    assert not any(x["line"] == 30.5 for x in s["ev"])                       # one book only: no consensus, no row


def test_released_scout_chance_is_used_with_its_push(fake, monkeypatch):
    monkeypatch.setattr(wm, "model_released", lambda sport, stat: True)
    s = wm.sheet(None, "nba", fake)
    r = next(x for x in s["ev"] if x["line"] == 20.5 and x["book"] == "fanduel" and x["side"] == "over")
    assert r["p_cal"] == 0.9 and r["ev"] == pytest.approx(0.9 * 2.10 - 1, abs=1e-4) and r["p_source"].startswith("Scout calibrated chance")
    assert any(x["line"] == 21.0 for x in s["ev"])                         # the released chance carries p_push: whole lines priced


def test_arbitrage_formula_and_flags(fake):
    s = wm.sheet(None, "nba", fake)
    a = next(x for x in s["arbitrage"] if x["line"] == 20.5)
    ia, ib = 1 / 2.25, 1 / 1.90
    assert [l["book"] for l in a["legs"]] == ["oddsapi:draftkings", "betrivers"]
    assert a["legs"][0]["stake_share"] == pytest.approx(ia / (ia + ib), abs=1e-4)
    assert a["margin_pct"] == pytest.approx((1 / (ia + ib) - 1) * 100, abs=1e-3)
    assert not any(x["line"] == 9.5 for x in s["arbitrage"])               # the stale leg's pair is left out ...
    st = next(x for x in s["flagged"] if x["line"] == 9.5)
    assert "Stale leg" in st["flags"]                                      # ... and listed with the reason
    k = next(x for x in s["flagged"] if x["line"] == 5.5)
    assert any("Prediction market" in f for f in k["flags"]) and not any(x["line"] == 5.5 for x in s["arbitrage"])
    assert "guarantee" not in json.dumps(s).lower()


def test_sleeper_multipliers_are_priced_but_never_an_arbitrage_leg(fake, monkeypatch):
    monkeypatch.setenv("SCOUT_BOOK_SLEEPER", "1")
    st = bp.state("sleeper")
    st.update(at=fake, offers=[{"provider": "sleeper", "sport": "nba", "stat": "points", "period": "game", "player": "Nba p1", "player_norm": "nbap1",
                                "team": "A", "team_norm": "a", "line": 20.5, "subject_id": "1", "raw_stat": "points", "game_id": "g",
                                "over": {"offered": True, "payout": 2.6}, "under": {"offered": True, "payout": 1.2}, "source_at": fake - 30, "observed_at": fake}])
    s = wm.sheet(None, "nba", fake)
    sl = [x for x in s["ev"] if x["book"] == "sleeper"]
    assert sl and sl[0]["side"] == "over" and "Pick'em multiplier: needs 2+ picks" in sl[0]["flags"]
    assert all(l["book"] != "sleeper" for a in s["arbitrage"] for l in a["legs"])


def test_a_sport_with_no_board_says_so(fake):
    s = wm.sheet(None, "nfl", fake)
    assert s["state"] == "unavailable" and s["ev"] == [] and s["arbitrage"] == []


def test_exchange_prices():
    assert wm.exchange_prices("0.40 / 0.42") == (pytest.approx(1 / .42), pytest.approx(1 / .6))
    assert wm.exchange_prices(None) == (None, None)


def test_http_off_is_404_and_on_is_cut_to_the_member(env, monkeypatch):
    monkeypatch.delenv("SCOUT_MARKET_TOOLS", raising=False)
    assert get(env, "market-tools") == (404, {"error": "not found"})
    st, me = get(env, "me")
    assert me["features"]["market_tools"] is False
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    st, doc = get(env, "market-tools")
    assert st == 200 and set(doc["sports"]) <= {"cs2", "lol", "nba", "nfl"} and "cs2" in doc["sports"]
    assert "p_cal" in doc["method"] and "margin" in doc["method"]["arbitrage"]
    assert doc["sports"]["cs2"]["ev"] == [] and doc["sports"]["cs2"]["arbitrage"] == []        # no book prices for CS2 props held
    txt = json.dumps(doc).lower()
    assert "guarantee" not in txt and "not bet placement" not in txt
