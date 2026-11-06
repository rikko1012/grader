"""SCOUT_PICKEM_EV (competitor spec 4.1): the pick'em +EV board (workspace_ev; math in gradecore.ev_board / fair / payouts /
line_shift). Flag off = 404 and no feature bit; market fair beside the printed model chance (never blended); stale / one-sided
quotes hidden and counted; esports and unpriced lines model only, ranked apart; parameters normalised to a short list."""
import json
import time
from datetime import datetime, timezone

import pytest

import workspace_books as wb
import workspace_ev as we
from gradecore import ev_board, fair, payouts
from gradecore.tests.pickem_ref import reference_tables  # noqa: F401  (fixture: PrizePicks REFERENCE rates, formula tests only)
from tests.test_workspace_api import env, get  # noqa: F401


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def rec(pkey, line, app="prizepicks", stat="points", p_over=0.55, p_under=0.45, mult=None, line_type="standard", start="2099-01-01T00:00:00+00:00"):
    return {"sport": "nba", "key": f"nba.e1.{pkey}.{stat}.full_game.{line}",
            "event": {"id": "e1", "label": "A at B", "start_utc": start, "lock_utc": start, "status": "scheduled"},
            "player": {"key": pkey, "name": "Player " + pkey, "team": "A"},
            "market": {"stat": stat, "stat_label": stat.title(), "period": "full_game", "period_label": "Full game", "line_type": line_type},
            "offer": {"platform": app, "platform_label": app.title(), "offer_id": f"{app[:2]}-{pkey}", "revision": "r1", "line": line,
                      "over": {"offered": True, "payout": mult, "price": None}, "under": {"offered": True, "payout": mult, "price": None}},
            "scout": {"side": None, "p_side": None, "p_over": p_over, "p_under": p_under, "p_push": 0.0}, "state": {"kind": "ok"}}


def book(p, line, over, under, age, kind="devig"):
    return {"platform": p, "platform_label": p, "line": line, "kind": kind, "over": over, "under": under, "seen_at": iso(time.time() - age)}


def snap(items):
    """items: [(record, [book rows])] -> a fake NBA snapshot shaped like workspace_sports._SNAP."""
    index, recs = {}, []
    for r, rows in items:
        recs.append(r)
        index[(r["offer"]["platform"], r["offer"]["offer_id"])] = {"books": (rows, []), "st": r["market"]["stat"]}
    return {"body": json.dumps({"records": recs}).encode(), "index": index}


@pytest.fixture
def fake(monkeypatch, reference_tables):
    """Board arithmetic on fixture quotes. PrizePicks rows are priced on its REFERENCE table here (never live: see pickem_ref)."""
    wb.reset()
    we._FAIR.clear(); we._LEGS.clear()
    items = [
        (rec("p1", 20.5, p_over=0.62, p_under=0.38), [book("fanduel", 20.5, 1.65, 2.30, 60), book("oddsapi:fanduel", 20.5, 1.66, 2.28, 60),
                                                     book("betrivers", 19.5, 1.80, 2.00, 90)]),
        (rec("p2", 9.5), [book("fanduel", 9.5, 1.5, 2.8, 4000), book("betrivers", 9.5, 1.5, 2.8, 5000)]),         # every price stale
        (rec("p3", 5.5), [book("fanduel", 1.9, None, None, 60, kind="one_sided") | {"line": 5.5, "over": 1.9}]),   # one-sided only
        (rec("p4", 30.5, p_over=0.70, p_under=0.30), []),                                                     # no book: model only
        (rec("p5", 12.5, app="underdog", mult=1.04), [book("fanduel", 12.5, 1.70, 2.20, 60), book("kalshi", 12.5, "0.58 / 0.60", None, 60, kind="mid")]),
        (rec("p6", 7.5, line_type="goblin"), [book("fanduel", 7.5, 1.5, 2.6, 60)]),                           # goblin: not on this board
        (rec("p7", 8.5, start="2000-01-01T00:00:00+00:00"), [book("fanduel", 8.5, 1.5, 2.6, 60)]),            # started
    ]
    s = snap(items)
    cs2 = [dict(rec("c1", 38.5, p_over=0.64, p_under=0.36), sport="cs2"), dict(rec("c2", 40.5, app="underdog", p_over=0.4, p_under=0.6), sport="cs2")]
    monkeypatch.setattr(wb, "records", lambda srv, sport: (s["body"] and [r for r, _ in items], s) if sport == "nba"
                        else (cs2, {"body": b"cs2", "index": {}}) if sport == "cs2" else (None, None))
    return items


ALL = {"cs2": True, "lol": True, "nba": True, "nfl": True}


def test_market_fair_beside_model_chance_and_ev_by_the_formula(fake):
    d = we.build(None, we.params({}, ["fanduel", "betrivers"]), ALL)
    assert d["app"] == "prizepicks" and d["entry"] == "power" and d["n"] == 2
    be = payouts.entry_break_even("prizepicks", "power", 2)
    assert d["break_even"] == pytest.approx(be, abs=1e-4) and d["american"] == round(payouts.american_of(be))
    r = next(x for x in d["rows"] if x["key"].endswith("pr-p1") and x["side"] == "over")
    q = [fair.Quote("fanduel", 20.5, 0, over=1.65, under=2.30, cluster="fanduel"), fair.Quote("fanduel", 20.5, 0, over=1.66, under=2.28, cluster="fanduel")]
    # the copy feed (oddsapi:fanduel) counts once with FanDuel; BetRivers on 19.5 is read at 20.5 through the points shape
    assert r["books"] == 2 and {s["label"] for s in r["sources"]} == {"FanDuel", "BetRivers"}
    assert any(s["shifted"] for s in r["sources"]) and r["basis"] == "market"
    assert r["ev"] == pytest.approx(r["p_market"] / be - 1, abs=2e-4)
    assert r["p_model"] == 0.62 and r["ev_model"] == pytest.approx(0.62 / be - 1, abs=1e-4)     # model shown beside, never blended
    assert "p_cal" not in json.dumps(d)
    del q


def test_stale_and_one_sided_legs_are_hidden_and_counted(fake):
    d = we.build(None, we.params({}, []), ALL)
    keys = {(r["key"].split("|")[2], r["side"]) for r in d["rows"]}
    assert not any(k[0] in ("pr-p2", "pr-p3") for k in keys)
    assert d["hidden"]["by"] == {"stale": 2, "one-sided": 2} and d["hidden"]["count"] == 4
    assert d["hidden"]["text"].startswith("4 plays hidden:")


def test_model_only_rows_rank_apart(fake):
    d = we.build(None, we.params({}, []), ALL)
    mo = d["model_only"]
    assert {r["sport"] for r in mo} == {"nba", "cs2"} and all(r["basis"] == "model only" and "p_market" not in r for r in mo)
    evs = [r["ev_model"] for r in mo]
    assert evs == sorted(evs, reverse=True)
    assert not any(r["sport"] == "cs2" for r in d["rows"])                    # esports never in the market list
    # goblin and started lines never appear
    assert not any(r["key"].endswith(("p6", "p7")) for r in d["rows"] + mo)


def test_underdog_modifier_and_exchange(fake):
    d = we.build(None, we.params({"app": "underdog"}, []), ALL)
    r = next(x for x in d["rows"] if x["side"] == "over")
    assert r["be"] == pytest.approx(ev_board.pickem_break_even("underdog", "standard", 2, 1.04), abs=1e-4) and r["multiplier"] == 1.04
    assert {s["kind"] for s in r["sources"]} == {"sportsbook", "exchange"}
    assert [x for x in d["rows"] if x["app"] != "underdog"] == []
    d2 = we.build(None, we.params({"app": "underdog", "entry": "flex", "n": "3"}, []), ALL)
    assert d2["entry"] == "flex" and all(x["ev"] is None and x["american"] is None for x in d2["rows"])  # flex at a 1.04x pick: unavailable


def test_filters_change_the_fair(fake):
    one = we.build(None, we.params({"min_books": "3"}, []), ALL)
    assert one["rows"] == [] and one["hidden"]["by"].get("books", 0) >= 2
    req = we.build(None, we.params({"req": "betrivers,nope"}, ["fanduel", "betrivers"]), ALL)
    assert req["params"]["req"] == ["betrivers"]
    w = we.build(None, we.params({"preset": "custom", "w": "betrivers:0,fanduel:2"}, ["fanduel", "betrivers"]), ALL)
    r = next(x for x in w["rows"] if x["key"].endswith("pr-p1") and x["side"] == "over")
    assert {s["label"]: s["weight"] for s in r["sources"]}["BetRivers"] == 0.0


def test_params_are_normalised(reference_tables):
    p = we.params({"app": "x", "entry": "power", "n": "99", "preset": "bad", "method": "bad", "min_books": "50", "min_liq": "123",
                   "max_width": "7", "req": "a,b,c"}, ["a", "b", "c"])
    assert p[:6] == ("prizepicks", "power", 2, "equal", "worst", ev_board.MIN_BOOKS_MAX)
    assert p[6] == ("a", "b") and p[8] == 100.0 and p[9] == fair.MAX_WIDTH


def test_live_tables_offer_both_apps_prizepicks_verified_by_owner():
    """Owner sign-off 2026-11-05: the PrizePicks help-center table is verified, so PrizePicks entries are priced (standard lines)."""
    assert {e["app"] for e in we.entries()} == {"prizepicks", "underdog"}
    assert not any(e["entry"] == "flex" and e["n"] == 2 for e in we.entries())     # no entry type the app does not show
    assert we.params({})[:3] == ("prizepicks", "power", 2)
    assert we.params({"app": "prizepicks", "entry": "flex", "n": "2"})[:3] == ("prizepicks", "power", 2)
    assert we.params({"app": "underdog"})[:3] == ("underdog", "standard", 2)


def test_quotes_near_the_freshness_limit_are_left_out_for_both_cache_windows(fake):
    """Review 2026-11-05: a price is cached up to TTL in _FAIR and TTL in srv.shared, so pricing stops 2 x TTL early."""
    lim = we.price_limits()
    assert lim["sportsbook"] == fair.FRESHNESS_S["sportsbook"] - 2 * we.TTL and lim["exchange"] == fair.FRESHNESS_S["exchange"] - 2 * we.TTL
    edge = fair.FRESHNESS_S["sportsbook"] - we.TTL                       # inside the raw limit, too old once both windows pass
    res = fair.fair_chance([fair.Quote("fanduel", 20.5, 1000.0 - edge, over=1.9, under=1.9)], "over", 20.5, 1000.0, freshness=lim)
    assert res["p"] is None and res["dropped"][0]["reason"] == "stale"
    d = we.build(None, we.params({}, []), ALL)
    assert d["options"]["freshness_s"] == lim
    for r in d["rows"]:
        assert all(s["age_s"] <= lim[s["kind"] if s["kind"] == "exchange" else "sportsbook"] for s in r["sources"])


def test_sports_the_member_cannot_see_are_cut(fake):
    d = we.build(None, we.params({}, []), {"cs2": True, "nba": False})
    assert d["rows"] == [] and {r["sport"] for r in d["model_only"]} == {"cs2"} and d["sports"] == ["cs2"]


def test_http_flag_off_is_404_and_on_answers(env, monkeypatch):
    monkeypatch.delenv("SCOUT_PICKEM_EV", raising=False)
    assert get(env, "pickem-ev") == (404, {"error": "not found"})
    st, me = get(env, "me")
    assert "pickem_ev" not in me["features"]
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    we._FAIR.clear(); we._LEGS.clear()
    st, me = get(env, "me")
    assert me["features"]["pickem_ev"] is True
    st, doc = get(env, "pickem-ev")
    assert st == 200 and doc["app"] == "prizepicks" and doc["apps"] == ["prizepicks", "underdog"] and "rows" in doc and "model_only" in doc and "cs2" in doc["sports"]
    txt = json.dumps(doc).lower()
    for word in ("guarantee", "lock", "research estimate", "provisional", "placed"):
        assert word not in txt, word


def test_page_script_is_served_only_with_the_flag(env, monkeypatch):
    import http.client
    import server
    import whop_auth
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    monkeypatch.delenv("SCOUT_PICKEM_EV", raising=False)

    def fetch():
        c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=30)
        c.request("GET", server.PICKEM_EV_ROUTE, headers={"Cookie": f"{whop_auth.COOKIE}=good-u1"})
        r = c.getresponse(); body = r.read(); c.close()
        return r.status, body
    assert fetch()[0] == 404
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    st, body = fetch()
    assert st == 200 and b"export function paint" in body
