"""SCOUT_MARKET_EDGE (owner site audit 2026-11-06): the board's Market edge column and the best line and price per side across
books (workspace_market_edge). Display only: the books' fair chance (the +EV board's own devig, 2+ fresh books) minus the app's
verified 2-pick break-even, shown with or without a Scout side; best Over = lowest line then the cheapest payout, best Under =
highest line; standard lines listed for the discrepancy chip. Esports props carry the plain reason. Fixture data only."""
import json
import sys
import time

import pytest

import workspace_books as wb
import workspace_ev as we
import workspace_market_edge as wme
from gradecore import fair, payouts
from tests.test_pickem_ev import book, rec, snap
from tests.test_workspace_api import env, get  # noqa: F401

PP_BE = payouts.entry_break_even("prizepicks", "power", 2)
UD_BE = payouts.entry_break_even("underdog", "standard", 2)


def scout(r, side, p):
    r["scout"].update(side=side, p_side=p)
    return r


@pytest.fixture
def board(monkeypatch):
    wb.reset(); we._FAIR.clear(); we._LEGS.clear()
    for k in ("SCOUT_BOOK_SLEEPER", "SCOUT_BOOK_DABBLE", "SCOUT_BOOK_FANDUEL", "SCOUT_BOOK_BETRIVERS", "SCOUT_BOOK_ODDSAPI"):
        monkeypatch.delenv(k, raising=False)
    fd_row = dict(rec("p1", 6.5, app="fanduel", stat="rebounds"), offer=dict(rec("p1", 6.5, app="fanduel")["offer"], offer_id="fd-p1",
                  seen_at=book("x", 1, 1, 1, 60)["seen_at"], over={"offered": True, "payout": 1.80, "price": 1.80},
                  under={"offered": True, "payout": 2.02, "price": 2.02}))
    items = [
        # Avdija-like: no Scout side, two fresh books -> a market edge for the side the books favour
        (rec("p1", 6.5, app="underdog", stat="rebounds", mult=1.0), [book("fanduel", 6.5, 1.80, 2.02, 60), book("oddsapi:fanduel", 6.5, 1.81, 2.00, 60),
                                                                    book("betrivers", 6.5, 1.78, 2.05, 90)]),
        (rec("p1", 7.5, app="prizepicks", stat="rebounds"), [book("fanduel", 6.5, 1.80, 2.02, 60), book("betrivers", 6.5, 1.78, 2.05, 90)]),
        # Scout leans Under, books favour Over: both edges
        (scout(rec("p2", 20.5, app="prizepicks"), "under", 0.56), [book("fanduel", 20.5, 1.65, 2.30, 60), book("betrivers", 20.5, 1.70, 2.20, 60)]),
        (rec("p3", 9.5, app="prizepicks"), [book("fanduel", 9.5, 1.5, 2.8, 60)]),                                          # one book only
        (rec("p4", 9.5, app="prizepicks"), [book("fanduel", 9.5, 1.5, 2.8, 4000), book("betrivers", 9.5, 1.5, 2.8, 5000)]),  # stale
        (rec("p5", 7.5, app="prizepicks", line_type="goblin"), [book("fanduel", 7.5, 1.5, 2.6, 60)]),                       # goblin
        (rec("p6", 12.5, app="underdog", mult=1.04), [book("fanduel", 12.5, 1.70, 2.20, 60), book("betrivers", 12.5, 1.72, 2.15, 60)]),
        (rec("p7", 30.5, app="prizepicks"), []),                                                                        # no book
    ]
    items.append((fd_row, []))
    s = snap(items)
    s["feeds"] = {}
    cs2 = [dict(rec("c1", 38.5, app="prizepicks"), sport="cs2"), dict(rec("c1", 40.5, app="underdog", mult=1.0), sport="cs2"),
           dict(rec("c1", 37.5, app="underdog", mult=1.0, line_type="goblin"), sport="cs2"),
           dict(rec("c2", 20.5, app="underdog", mult=1.0), sport="cs2")]
    monkeypatch.setattr(wb, "records", lambda srv, sport: ([r for r, _ in items], s) if sport == "nba"
                        else (cs2, {"body": b"cs2", "index": {}, "feeds": {}}) if sport == "cs2" else (None, None))
    return items


def test_market_edge_without_a_scout_side_on_the_favoured_side(board):
    st, d = wme.build(None, "nba")
    row = d["rows"]["underdog|un-p1"]
    fav = row["fav"]
    # FanDuel and its Odds API copy count once: two books (FanDuel, BetRivers), the same fair chance the +EV board reads
    assert fav["n"] == 2 and fav["books"] == ["BetRivers", "FanDuel"] and "scout" not in row
    q = [fair.Quote("fanduel", 6.5, time.time() - 60, over=1.80, under=2.02, cluster="fanduel"),
         fair.Quote("fanduel", 6.5, time.time() - 60, over=1.81, under=2.00, cluster="fanduel"),
         fair.Quote("betrivers", 6.5, time.time() - 90, over=1.78, under=2.05, cluster="betrivers")]
    po = fair.fair_chance(q, "over", 6.5, time.time(), min_books=2)["p"]
    pu = fair.fair_chance(q, "under", 6.5, time.time(), min_books=2)["p"]
    assert fav["side"] == ("over" if po > pu else "under")
    assert fav["p"] == pytest.approx(max(po, pu), abs=1e-4)
    assert fav["be"] == pytest.approx(UD_BE, abs=1e-4) and abs(UD_BE - 0.5345) < 5e-4
    assert fav["edge"] == pytest.approx(fav["p"] - fav["be"], abs=2e-4) and fav["entry"] == "Underdog standard 2-pick"


def test_scout_side_gets_its_own_edge_when_the_books_favour_the_other(board):
    st, d = wme.build(None, "nba")
    row = d["rows"]["prizepicks|pr-p2"]
    assert row["fav"]["side"] == "over" and row["scout"]["side"] == "under"
    assert row["fav"]["be"] == pytest.approx(PP_BE, abs=1e-4) and abs(PP_BE - 0.5774) < 5e-4
    assert row["scout"]["edge"] < 0 < row["fav"]["p"] - 0.5           # shown, never hidden, whatever its sign


def test_two_fresh_books_needed_and_plain_reasons(board):
    st, d = wme.build(None, "nba")
    rows = d["rows"]
    assert rows["prizepicks|pr-p3"] == {"why": wme.WHY["books"]}            # one book: no market chance
    assert rows["prizepicks|pr-p4"] == {"why": wme.WHY["stale"]}            # both stale (the existing +EV freshness limits)
    assert rows["prizepicks|pr-p5"] == {"why": wme.WHY["type"]}             # goblin: no fixed per-pick payout
    assert "prizepicks|pr-p7" not in rows or "fav" not in rows["prizepicks|pr-p7"]
    assert "fanduel|fd-p1" not in rows                                      # a sportsbook's own row is never measured on itself
    # an Underdog payout modifier moves the break-even (the verified table, ev_board.pickem_break_even)
    p6 = rows["underdog|un-p6"]["fav"]
    assert p6["be"] == pytest.approx(payouts.break_even("underdog", "standard", 2, multiplier=1.04), abs=1e-4) and p6["be"] < UD_BE


def test_min_books_and_freshness_are_the_ev_boards_own(board):
    """No new freshness limit: the edge reads workspace_ev.price_limits() and its min-books 2 parameter set."""
    assert wme._params()[5] == 2 == wme.MIN_BOOKS and wme._params()[3:5] == ("equal", "worst")
    d = we.build(None, (*wme._params()[:3], *wme._params()[3:]), {"nba": True})
    ev = next(r for r in d["rows"] if r["key"] == "nba|prizepicks|pr-p2" and r["side"] == "over")
    st, me = wme.build(None, "nba")
    assert me["rows"]["prizepicks|pr-p2"]["fav"]["p"] == pytest.approx(ev["p_market"], abs=1e-4)   # the same number as Best prices


def test_best_line_per_side_across_books_and_standard_lines_listed(board):
    st, d = wme.build(None, "nba")
    pk = "e1|p1|rebounds|full_game"
    best = d["props"][pk]
    # Over: the lowest line (6.5) - Underdog 1.00x needs 53.5%, FanDuel 1.80 needs 55.6%, BetRivers 1.78 needs 56.2% -> Underdog
    assert best["over"]["line"] == 6.5 and best["over"]["book"] == "underdog" and best["over"]["needs"] == pytest.approx(UD_BE, abs=1e-4)
    # Under: the highest line (PrizePicks 7.5)
    assert best["under"]["line"] == 7.5 and best["under"]["book"] == "prizepicks"
    # the FanDuel board row and FanDuel's snapshot row at 6.5 are one line; the Odds API copy too
    assert sorted(x[2] for x in best["ls"]) == ["betrivers", "fanduel", "prizepicks", "underdog"]
    assert best["n"] == 4


def test_esports_rows_carry_the_reason_and_still_get_best_lines(board):
    st, d = wme.build(None, "cs2")
    assert d["rows"] == {} and d["why"] == wme.ESPORTS_WHY
    best = d["props"]["e1|c1|points|full_game"]
    assert best["over"]["line"] == 38.5 and best["under"]["line"] == 40.5       # the goblin 37.5 is never the best Over
    assert [x[1] for x in best["ls"]] == [38.5, 40.5]


def test_flag_off_is_404_on_answers_and_me_carries_the_entries(env, monkeypatch):
    monkeypatch.delenv("SCOUT_MARKET_EDGE", raising=False)
    assert get(env, "market-edge?sport=cs2") == (404, {"error": "not found"})
    st, me = get(env, "me")
    assert "market_edge" not in me["features"]
    monkeypatch.setenv("SCOUT_MARKET_EDGE", "1")
    st, me = get(env, "me")
    f = me["features"]["market_edge"]
    assert f["min_books"] == 2 and f["entries"]["underdog"]["be"] == pytest.approx(UD_BE, abs=1e-5)
    assert f["entries"]["prizepicks"]["label"] == "PrizePicks Power 2-pick"
    st, doc = get(env, "market-edge?sport=cs2")
    assert st == 200 and doc["sport"] == "cs2" and doc["why"] == wme.ESPORTS_WHY and doc["props"]
    txt = json.dumps(doc).lower()
    for word in ("grade", "lock", "guarantee", "no bet", "provisional", "questionable"):
        assert word not in txt, word


def test_imports_no_sports_or_lol_package():
    import subprocess
    code = ("import sys, workspace_market_edge; bad=[m for m in sys.modules if m=='sports' or m.startswith('sports.') or m=='lol' "
            "or m.startswith('lol.')]; print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(wb.__file__).rsplit("workspace_books", 1)[0])
    assert r.returncode == 0, r.stdout + r.stderr
