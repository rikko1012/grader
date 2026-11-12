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


@pytest.fixture(autouse=True)
def fixture_books_settle_like_the_app(monkeypatch):
    from tests.book_rules_fixture import like_the_app
    like_the_app(monkeypatch)


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
    # owner 2026-11-12: Kalshi settles a sit-out at its last fair price, a sportsbook hands the stake back - not the same bet,
    # so the pair is not an arbitrage and is listed nowhere (only counted)
    assert not any(x["line"] == 5.5 for x in s["flagged"] + s["arbitrage"]) and s["excluded"].get(wm.PAIR_RULES) == 1
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
    assert doc["owner"] is False and "p_cal" not in doc["method"] and "arbitrage" not in doc["method"]   # formulas: owner view only
    assert all(k not in d for d in doc["sports"].values() for k in ("flagged", "excluded", "excluded_lines"))
    assert doc["sports"]["cs2"]["ev"] == [] and doc["sports"]["cs2"]["arbitrage"] == []        # no book prices for CS2 props held
    txt = json.dumps(doc).lower()
    assert "guarantee" not in txt and "not bet placement" not in txt


# ---------------------------------------------------------------------------------------------------------------- Price check (owner 2026-11-05)

def test_plain_context_numbers_equal_the_existing_math(fake):
    s = wm.sheet(None, "nba", fake)
    assert s["ev"]
    for r in s["ev"]:
        assert abs(r["needs"] - (1 - r["p_push"]) / r["odds"]) < 1e-3                   # consensus route: p_push 0, so 1 / odds
        assert r["per_100"] == round(r["ev"] * 100, 2) or abs(r["per_100"] - r["ev"] * 100) < 0.011
        assert r["american"] == wm.american(r["odds"]) and r["chance_from"].startswith("Other books (")
    assert s["arbitrage"]
    for a in s["arbitrage"]:
        stakes = [l["stake_per_100"] for l in a["legs"]]
        assert abs(sum(stakes) - 100) <= 0.011
        backs = [l["stake_per_100"] * l["odds"] for l in a["legs"]]
        assert abs(backs[0] - backs[1]) <= 0.05 and abs(backs[0] - (100 + a["return_per_100"])) <= 0.05
        assert a["oldest_leg_s"] == max(l["age_s"] for l in a["legs"])
        assert all(l["american"] == wm.american(l["odds"]) for l in a["legs"])


def test_needs_is_push_adjusted_on_a_whole_number_line_priced_with_scouts_chance(fake, monkeypatch):
    """Review 2026-11-05: on a whole-number line the Scout route keeps the push chance, so 'needs' is the push-adjusted break-even
    (1 - p_push) / odds. With p 0.48, push 0.06 at 2.10 the row must not read 'fair 48% vs needs 47.6%+' against a loss, nor
    'fair below needs' beside a positive result."""
    monkeypatch.setattr(wm, "_released_p", lambda g, sport, side: (0.48, 0.06, "v1") if float(g["line"]).is_integer() else None)
    s = wm.sheet(None, "nba", fake)
    rows = [r for r in s["ev"] if float(r["line"]).is_integer()]
    assert rows and all(r["p_push"] == 0.06 for r in rows)
    for r in rows:
        assert r["needs"] == round((1 - 0.06) / r["odds"], 4)
        assert (r["per_100"] > 0) == (r["p_cal"] > r["needs"])                         # the shown chances agree with the +$ figure
    r = next(r for r in rows if r["odds"] == 2.1)
    assert r["needs"] == 0.4476 and r["per_100"] == 6.8 and r["p_cal"] == 0.48


def test_american_prices():
    assert [wm.american(x) for x in (2.25, 2.0, 1.9, 1.5, 1.0, None, "x")] == ["+125", "+100", "-111", "-200", None, None, None]


def test_exclusions_per_line_never_exceed_lines_checked(fake):
    s = wm.sheet(None, "nba", fake)
    assert sum(s["excluded_lines"].values()) <= s["markets_checked"]
    assert sum(s["excluded"].values()) >= sum(s["excluded_lines"].values())          # the per-price counts stay for the owner
    assert s["sections_possible"] == {"ev": True, "arb": True}


def test_sections_possible_is_false_without_enough_sportsbooks(monkeypatch):
    bp.reset(); wb.reset()
    now = time.time()
    snaps = {"nba": snap([("p1", 20.5, [("fanduel", 2.10, 1.80, 30)]), ("p2", 9.5, [("kalshi", "0.40 / 0.42", None, 30), ("fanduel", 1.9, 1.9, 30)])], now)}
    monkeypatch.setattr(wb, "snap", lambda srv, sport: snaps.get(sport))
    monkeypatch.setattr(wm, "model_released", lambda sport, stat: False)
    s = wm.sheet(None, "nba", now)
    assert s["sections_possible"] == {"ev": False, "arb": False} and s["ev"] == [] and s["arbitrage"] == []


def _member_body(fake, monkeypatch):
    monkeypatch.setenv("SCOUT_BOOK_SLEEPER", "1")
    st = bp.state("sleeper")
    st.update(at=fake, offers=[{"provider": "sleeper", "sport": "nba", "stat": "points", "period": "game", "player": "Nba p1", "player_norm": "nbap1",
                                "team": "A", "team_norm": "a", "line": 20.5, "subject_id": "1", "raw_stat": "points", "game_id": "g",
                                "over": {"offered": True, "payout": 2.6}, "under": {"offered": True, "payout": 1.2}, "source_at": fake - 30, "observed_at": fake}])
    return json.dumps(wm.build(None, now=fake)).encode()


def test_member_view_has_sportsbook_rows_only_and_the_owner_keeps_everything(fake, monkeypatch):
    body = _member_body(fake, monkeypatch)
    every = {"cs2": True, "lol": True, "nba": True, "nfl": True}
    m = wm.for_member(body, every)
    nba = m["sports"]["nba"]
    assert m["owner"] is False and "flagged" not in nba and "excluded" not in nba and "excluded_lines" not in nba
    assert nba["ev"] and all(r["kind"] in ("sportsbook", "prediction") and "p_source" not in r for r in nba["ev"])
    assert not any(r["book"] == "sleeper" for r in nba["ev"])                        # pick'em multipliers: no single-bet break-even
    txt = json.dumps(m)
    assert "Stale" not in txt and "Prediction market" not in txt                     # no flagged pair or its reasons
    assert all(l["kind"] == "sportsbook" and (l["age_s"] or 0) <= 300 for a in nba["arbitrage"] for l in a["legs"])
    rowbooks = {r["book"] for r in nba["ev"]} | {l["book"] for a in nba["arbitrage"] for l in a["legs"]}
    assert {b["id"] for b in m["books"]} == rowbooks                                 # no book that has no row the member can see
    o = wm.for_member(body, every, owner=True)
    assert o["owner"] is True and o["sports"]["nba"]["flagged"] and o["sports"]["nba"]["excluded"]
    assert any(r["book"] == "sleeper" for r in o["sports"]["nba"]["ev"]) and "p_cal" in o["method"] and "margin" in o["method"]["arbitrage"]
    assert "kalshi" not in {b["id"] for b in o["books"]}                             # its pair is not listed (rules differ), so no row


def test_member_keeps_prediction_market_price_rows_and_drops_only_pickem():
    """Review 2026-11-05: members saw Kalshi / Polymarket price rows before Price check; only pick'em multipliers leave the member view
    (and prediction markets stay out of both-sides pairs, as before)."""
    row = lambda book, kind: {"book": book, "book_label": book.title(), "kind": kind, "p_source": "x", "ev": 0.03}
    body = json.dumps({"as_of": iso(time.time()), "method": {"ev": "f", "p_cal": "f", "arbitrage": "f", "arb_max_age_s": 300},
                       "sports": {"nba": {"ev": [row("fanduel", "sportsbook"), row("kalshi", "prediction"), row("polymarket", "prediction"),
                                                 row("sleeper", "pickem")], "arbitrage": [], "flagged": [], "excluded": {}}}}).encode()
    m = wm.for_member(body, {"nba": True})
    assert [r["book"] for r in m["sports"]["nba"]["ev"]] == ["fanduel", "kalshi", "polymarket"]
    assert {b["id"] for b in m["books"]} == {"fanduel", "kalshi", "polymarket"}
    assert [r["book"] for r in wm.for_member(body, {"nba": True}, owner=True)["sports"]["nba"]["ev"]] == ["fanduel", "kalshi", "polymarket", "sleeper"]


def test_member_books_are_cut_to_the_members_sports(fake, monkeypatch):
    body = _member_body(fake, monkeypatch)
    m = wm.for_member(body, {"cs2": True, "lol": True})
    assert set(m["sports"]) == {"cs2", "lol"} and m["books"] == []                     # no NBA/NFL book on a CS2/LoL-only account


def test_duplicate_quote_uses_latest_whole_revision_not_first_attachment(monkeypatch):
    now = time.time()
    snapshot = snap([("p1", 20.5, [("fanduel", 2.3, 1.7, 900), ("betrivers", 1.7, 2.3, 20)])], now)
    import copy
    second = copy.deepcopy(snapshot["index"][("underdog", "o-p1")])
    second["books"][0][0].update(over=1.7, under=2.3, seen_at=iso(now - 10))
    snapshot["index"][("prizepicks", "other-offer")] = second
    quotes, _ = wb.book_quotes(snapshot)
    fd = [b for k, b, x in quotes if k[0] == "fanduel"]
    assert len(fd) == 1 and (fd[0]["over"], fd[0]["under"]) == (1.7, 2.3)
    # Same answer when attachment order changes; stale profitable-looking Over
    # must not be resurrected or combined with the current Under.
    snapshot["index"] = dict(reversed(list(snapshot["index"].items())))
    assert [(k, b) for k, b, x in wb.book_quotes(snapshot)[0]] == [(k, b) for k, b, x in quotes]
