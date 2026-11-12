"""Owner 2026-11-12: "use 2 independent books by default, matching the exact line, period and settlement rules. Two sites using
the same underlying feed shouldn't count twice."

Family counting, exact line, settlement exclusion, default = 2, the "1 book" label, both-sides pairs, and the record's bounds.
Real quotes: tests/fixtures/ev_saved_quotes_2026_11_12.json (book rows saved from production board snapshots)."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import workspace_books as wb
import workspace_ev as we
import workspace_ev_log as wl
import workspace_markets as wm
from gradecore import book_rules, ev_board, fair
from tests.book_rules_fixture import like_the_app
from tests.test_pickem_ev import ALL, book, iso, rec, snap
from tests.test_workspace_api import env  # noqa: F401

SAVED = json.loads((Path(__file__).parent / "fixtures" / "ev_saved_quotes_2026_11_12.json").read_text(encoding="utf-8"))["items"]


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# ------------------------------------------------------------------------------------------------- families

def test_family_map_feeds_and_relays():
    assert book_rules.family_of("betrivers") == "kambi" and book_rules.family_of("oddsapi:betrivers") == "kambi"
    assert book_rules.family_of("oddsapi:fanduel") == book_rules.family_of("fanduel") == book_rules.family_of("oddspapi:fanduel")
    assert len(book_rules.families(["fanduel", "betrivers", "bovada", "kalshi", "polymarket"])) == 5      # each its own source
    # independence not established = one shared family: any number of unlisted books count once
    assert book_rules.families(["oddsapi:somebook", "otherbook", "oddspapi:third"]) == {book_rules.UNLISTED}
    for b in ("novig", "gemini", "dabble", "sleeper", "prizepicks", "underdog"):
        assert book_rules.for_pickem(b, "nba") == (False, 0.0, book_rules.REASON_NEVER)


def test_same_family_books_never_reach_two_on_real_saved_quotes(monkeypatch):
    """Every saved market, priced as the board prices it with each real book also arriving through a relay and through a
    second site on the same feed: the count is the number of families, and a single family never passes the default of 2."""
    like_the_app(monkeypatch, ("fanduel", "betrivers", "bovada", "unibet"))
    monkeypatch.setattr(book_rules, "FAMILY", {**book_rules.FAMILY, "unibet": "kambi"})            # a second Kambi site
    checked = single = 0
    for it in SAVED:
        now = max(ts(b["seen_at"]) for b in it["books"]) + 1
        rows = [b for b in it["books"] if b["line"] == it["line"]]
        doubled = rows + [dict(b, platform="oddsapi:" + b["platform"]) for b in rows if b["platform"] in ("fanduel", "bovada")] \
            + [dict(b, platform="unibet") for b in rows if b["platform"] == "betrivers"]
        for side in ("over", "under"):
            a = fair.fair_chance(we.quotes_of(rows, it["sport"]), side, it["line"], now, exact=True, min_books=1, freshness={"sportsbook": 9e9, "exchange": 9e9})
            b = fair.fair_chance(we.quotes_of(doubled, it["sport"]), side, it["line"], now, exact=True, min_books=1, freshness={"sportsbook": 9e9, "exchange": 9e9})
            assert b["books"] == a["books"]                                              # the copies add no family
            assert set(b["books"]) == {book_rules.family_of(s["book"]) for s in b["sources"]}
            if a["p"] is not None:
                checked += 1
                assert b["p"] == pytest.approx(a["p"], abs=1e-9)                         # ... and do not move the fair chance
            if len(b["books"]) == 1 and len(b["sources"]) > 1:
                single += 1
                two = fair.fair_chance(we.quotes_of(doubled, it["sport"]), side, it["line"], now, exact=True, min_books=2,
                                       freshness={"sportsbook": 9e9, "exchange": 9e9})
                assert two["p"] is None and ev_board.hidden_reason(two) == "books"      # several sites, one feed: not 2 books
    assert checked > 40 and single > 0


def test_real_table_on_real_saved_quotes_uses_only_books_that_settle_like_the_app():
    used, left = set(), {}
    for it in SAVED:
        now = max(ts(b["seen_at"]) for b in it["books"]) + 1
        r = fair.fair_chance(we.quotes_of(it["books"], it["sport"]), "over", it["line"], now, exact=True, min_books=1,
                             freshness={"sportsbook": 9e9, "exchange": 9e9})
        used |= {(s["book"], it["sport"]) for s in r["sources"]}
        for d in r["dropped"]:
            left.setdefault((d["book"], it["sport"]), set()).add(d["reason"])
        assert all(s["line"] == it["line"] and not s["shifted"] for s in r["sources"])
    assert used <= {("fanduel", "nba"), ("fanduel", "nfl"), ("kalshi", "nba"), ("kalshi", "nfl"), ("polymarket", "nba")}
    assert book_rules.REASON_DIFFERENT in left[("bovada", "nfl")] and book_rules.REASON_DIFFERENT in left[("polymarket", "nfl")]
    assert not any(b == "betrivers" for b, _ in left)          # a rule that is not confirmed: not a price here, and not listed


# ------------------------------------------------------------------------------------------------- settlement

def test_settlement_table_decides_and_unknown_is_excluded():
    assert book_rules.for_pickem("fanduel", "nfl") == (True, 0.0, None)
    assert book_rules.for_pickem("kalshi", "nba") == (True, 0.0, None)                 # resolves at the last fair price
    assert book_rules.for_pickem("betrivers", "nba") == (False, 0.0, book_rules.REASON_UNKNOWN)
    assert book_rules.for_pickem("bovada", "nba") == (False, 0.0, book_rules.REASON_UNKNOWN)
    assert book_rules.for_pickem("bovada", "nfl") == (False, 0.0, book_rules.REASON_DIFFERENT)      # active roster = action
    assert book_rules.for_pickem("somebook", "nba") == (False, 0.0, book_rules.REASON_UNKNOWN)
    assert book_rules.for_pickem("fanduel", "cs2")[0] is False                           # no rule read for that sport
    ok, share, _ = book_rules.for_pickem("polymarket", "nba")
    frozen = json.loads((Path(book_rules.__file__).parent.parent / "sports" / "model" / "artifacts" / "market_mc1.json").read_text())
    assert ok and share == frozen["dnp_share"]["nba"] == pytest.approx(0.074857)
    assert book_rules.for_pickem("polymarket", "nfl") == (False, 0.0, book_rules.REASON_DIFFERENT)   # NFL share never measured
    assert book_rules.same_bet("fanduel", "oddsapi:fanduel", "nba") and not book_rules.same_bet("fanduel", "kalshi", "nba")
    assert not book_rules.same_bet("polymarket", "kalshi", "nba") and not book_rules.same_bet("fanduel", "betrivers", "nba")


def test_polymarket_sit_out_reads_the_same_in_fair_and_in_the_registered_market_rule():
    """sports/model/market.py quote_prob: min(0.99, mid / (1 - d)). gradecore.fair applies the same to an exchange quote that
    carries sit_out, so a Polymarket Over at 0.46 is Under 50.3%, not 54%."""
    d = book_rules.sit_out_share("nba")
    q = we.quotes_of([{"platform": "polymarket", "line": 24.5, "kind": "mid", "over": "0.45 / 0.47", "under": None, "seen_at": iso(1000.0)}], "nba")
    assert q[0].sit_out == d and q[0].excluded is None
    r = fair.fair_chance(q, "under", 24.5, 1010.0, exact=True)
    assert r["p"] == pytest.approx(1 - 0.46 / (1 - d), abs=1e-9) and r["p"] == pytest.approx(0.5028, abs=1e-3)
    raw = fair.fair_chance([fair.Quote("polymarket", 24.5, 1000.0, kind="exchange", bid=0.45, ask=0.47)], "under", 24.5, 1010.0)
    assert raw["p"] == pytest.approx(0.54)                                               # what the board read before
    hi = fair.fair_chance([fair.Quote("polymarket", 24.5, 1000.0, kind="exchange", bid=0.97, ask=0.99, sit_out=d)], "over", 24.5, 1010.0)
    assert hi["p"] == 0.99                                                               # the same cap as market.py
    src = (Path(book_rules.__file__).parent.parent / "sports" / "model" / "market.py").read_text(encoding="utf-8")
    assert "return min(0.99, mid / (1 - dnp_share)), None" in src                        # the rule this mirrors has not moved
    nfl = we.quotes_of([{"platform": "polymarket", "line": 3.5, "kind": "mid", "over": "0.45 / 0.47", "under": None, "seen_at": iso(1000.0)}], "nfl")
    assert nfl[0].excluded == book_rules.REASON_DIFFERENT and fair.fair_chance(nfl, "under", 3.5, 1010.0, exact=True)["p"] is None


# ------------------------------------------------------------------------------------------------- the board

@pytest.fixture
def board(monkeypatch):
    wb.reset(); we._FAIR.clear(); we._LEGS.clear()
    items = [
        # two independent books on the exact line
        (rec("a1", 20.5, app="underdog", mult=1.0), [book("fanduel", 20.5, 1.50, 2.80, 60), book("kalshi", 20.5, "0.63 / 0.65", None, 60, kind="mid")]),
        # one book on the exact line; BetRivers (rule not confirmed) and a FanDuel relay add nothing
        (rec("a2", 9.5, app="underdog", mult=1.0), [book("fanduel", 9.5, 1.50, 2.80, 60), book("oddsapi:fanduel", 9.5, 1.51, 2.75, 60),
                                                    book("betrivers", 9.5, 1.50, 2.80, 60)]),
        # books only on another line: nothing is shifted
        (rec("a3", 5.5, app="underdog", mult=1.0), [book("fanduel", 6.5, 2.30, 1.60, 60), book("kalshi", 4.5, "0.70 / 0.72", None, 60, kind="mid")]),
        # only books whose rule is not confirmed / that are never a source: no book price at all
        (rec("a4", 7.5, app="underdog", mult=1.0), [book("betrivers", 7.5, 1.50, 2.80, 60), book("novig", 7.5, 1.50, 2.80, 60)]),
        # only a book that settles a sit-out differently (NFL-style rule on an NBA fixture row is set in the test)
        (rec("a5", 8.5, app="underdog", mult=1.0), [book("bovada", 8.5, 1.50, 2.80, 60)]),
    ]
    s = snap(items)
    monkeypatch.setattr(wb, "records", lambda srv, sport: ([r for r, _ in items], s) if sport == "nba" else (None, None))
    return items


def test_default_is_two_independent_books_on_the_exact_line(board, monkeypatch):
    monkeypatch.setattr(book_rules, "SETTLEMENT", {**book_rules.SETTLEMENT, ("bovada", "nba"): book_rules.SETTLEMENT[("bovada", "nfl")]})
    p = we.params({"app": "underdog"}, [])
    assert p[5] == ev_board.MIN_BOOKS_DEFAULT == 2
    d = we.build(None, p, ALL)
    assert {r["key"].split("|")[2] for r in d["rows"]} == {"un-a1"}                          # only the two-family leg
    assert all(r["books"] == 2 and r["one_book"] is False and r["books_label"] == "2 books" and sorted(r["families"]) == ["fanduel", "kalshi"]
               for r in d["rows"])
    assert d["plays"]["underdog"] == sum(1 for r in d["rows"] if r["ev"] > 0)                # the count is of default-view rows
    assert d["hidden"]["by"] == {"books": 2, "line": 2, "rules": 2}
    assert "fewer independent books than the minimum" in d["hidden"]["text"] and "no book on this exact line" in d["hidden"]["text"]
    assert "settle a sit-out differently from the app" in d["hidden"]["text"]
    assert d["model_only_total"] == 2                                                        # a4: no usable book = no book price
    assert {b["id"] for b in d["options"]["books"]} == {"fanduel", "kalshi"}                 # only books the board can price
    assert d["options"]["min_books_default"] == 2


def test_one_book_rows_only_when_the_member_lowers_the_filter_and_they_say_one_book(board):
    d = we.build(None, we.params({"app": "underdog", "min_books": "1"}, []), ALL)
    by = {r["key"].split("|")[2]: r for r in d["rows"] if r["side"] == "over"}
    assert set(by) == {"un-a1", "un-a2"}
    one = by["un-a2"]
    assert one["books"] == 1 and one["one_book"] is True and one["books_label"] == ev_board.ONE_BOOK_LABEL == "1 book"
    assert {s["label"] for s in one["sources"]} == {"FanDuel"} and one["families"] == ["fanduel"]         # the relay is FanDuel
    assert one["left_out"] == []                                             # BetRivers' rule is not confirmed: not listed at all
    assert "confirmed" not in json.dumps(d).lower()                         # nothing unsure on a member page
    assert by["un-a1"]["one_book"] is False
    # never a shifted line or a book with other rules, whatever the filter
    assert "un-a3" not in by and "un-a4" not in by and not any(s["shifted"] for r in d["rows"] for s in r["sources"])


def test_book_rows_of_another_period_never_price_a_line(monkeypatch):
    wb.reset(); we._FAIR.clear(); we._LEGS.clear()
    r = rec("b1", 20.5, app="underdog", mult=1.0)
    rows = [book("fanduel", 20.5, 1.50, 2.80, 60), book("kalshi", 20.5, "0.63 / 0.65", None, 60, kind="mid")]
    s = snap([(r, rows)])
    s["index"][("underdog", "un-b1")]["scope"] = "q1"                                        # the snapshot says: first-quarter prices
    monkeypatch.setattr(wb, "records", lambda srv, sport: ([r], s) if sport == "nba" else (None, None))
    d = we.build(None, we.params({"app": "underdog", "min_books": "1"}, []), ALL)
    assert d["rows"] == [] and d["model_only_total"] == 2
    s["index"][("underdog", "un-b1")]["scope"] = "full_game"
    we._FAIR.clear(); we._LEGS.clear()
    assert len(we.build(None, we.params({"app": "underdog"}, []), ALL)["rows"]) == 2


def test_payout_shown_with_its_date_and_prizepicks_opens_on_its_lowest_entry(board):
    d = we.build(None, we.params({"app": "underdog"}, []), ALL)
    assert d["payout"]["text"] == "Underdog Standard 2-pick pays 3.5x, read from its help center on 2026-10-23."
    assert d["payout"]["multiple"] == 3.5 and d["payout"]["read_at"].startswith("2026-10-23")
    pp = we.build(None, we.params({}, []), ALL)
    assert (pp["entry"], pp["n"]) == ("flex", 6) and pp["break_even"] == pytest.approx(0.54206, abs=1e-5)
    assert pp["payout"]["text"].startswith("PrizePicks Flex 6-pick pays 25x all correct, 2x with 1 missed, 0.4x with 2 missed")
    assert pp["default_entry"] == {"entry": "flex", "n": 6}
    txt = json.dumps([d, pp]).lower()
    for word in ("guarantee", "profit", "record", "unverified", "may ", "might", "likely", "estimate"):
        assert word not in txt, word


# ------------------------------------------------------------------------------------------------- both-sides pairs

def _sheet(monkeypatch, groups, sport="nba"):
    from tests.test_phase4_markets import snap as msnap
    import book_providers as bp
    bp.reset(); wb.reset()
    now = time.time()
    snaps = {sport: msnap(groups, now)}
    monkeypatch.setattr(wb, "snap", lambda srv, s: snaps.get(s))
    monkeypatch.setattr(wm, "model_released", lambda s, stat: False)
    return wm.sheet(None, sport, now)


def test_pairs_need_two_families_that_settle_alike(monkeypatch):
    like_the_app(monkeypatch, ("fanduel", "draftkings", "unibet"))
    monkeypatch.setattr(book_rules, "FAMILY", {**book_rules.FAMILY, "unibet": "kambi"})
    monkeypatch.setattr(book_rules, "SETTLEMENT", {**book_rules.SETTLEMENT, ("betrivers", "nba"): book_rules.SETTLEMENT[("fanduel", "nba")]})
    s = _sheet(monkeypatch, [
        ("p1", 20.5, [("fanduel", 2.25, 1.70, 20), ("draftkings", 1.70, 1.90, 30)]),           # two families, same rules: a pair
        ("p2", 21.5, [("betrivers", 2.25, 1.70, 20), ("unibet", 1.70, 1.90, 30)]),             # one feed twice: not a pair
        ("p3", 22.5, [("fanduel", 2.25, 1.70, 20), ("polymarket", "0.36 / 0.38", None, 20)]),  # sit-out = No on one side: not a pair
        ("p4", 23.5, [("fanduel", 2.25, 1.70, 20), ("bovada", 1.70, 1.90, 30)]),               # rule not confirmed: not a pair
    ])
    assert [x["line"] for x in s["arbitrage"]] == [20.5] and s["arbitrage"][0]["families"] == ["fanduel", "draftkings"]
    assert s["flagged"] == []                                                                  # the others are listed nowhere
    assert s["excluded"][wm.PAIR_ONE_FEED] == 1 and s["excluded"][wm.PAIR_RULES] == 2
    assert s["lag"]["two_family_lines"] == 2 and s["lag"]["same_rules_lines"] == 1 and s["lag"]["limit_s"] == 300


def test_price_check_consensus_counts_families_and_real_rules(monkeypatch):
    # real table: FanDuel priced by BetRivers + Bovada has no two books with confirmed, equal rules
    s = _sheet(monkeypatch, [("p1", 20.5, [("fanduel", 2.40, 1.60, 20), ("betrivers", 1.90, 1.90, 30), ("bovada", 1.90, 1.90, 30)])])
    assert s["ev"] == [] and s["excluded"][wm.NO_CONSENSUS] >= 1 and s["sections_possible"] == {"ev": False, "arb": False}
    # fixture rules: two sites on Kambi are one book, so FanDuel still has only one other family
    like_the_app(monkeypatch, ("fanduel", "betrivers", "unibet", "draftkings"))
    monkeypatch.setattr(book_rules, "FAMILY", {**book_rules.FAMILY, "unibet": "kambi"})
    s = _sheet(monkeypatch, [("p1", 20.5, [("fanduel", 2.40, 1.60, 20), ("betrivers", 1.90, 1.90, 30), ("unibet", 1.90, 1.90, 30)])])
    assert not [r for r in s["ev"] if r["book"] == "fanduel"]
    s = _sheet(monkeypatch, [("p1", 20.5, [("fanduel", 2.40, 1.60, 20), ("betrivers", 1.90, 1.90, 30), ("unibet", 1.94, 1.86, 30),
                                           ("draftkings", 1.90, 1.90, 30)])])
    r = next(r for r in s["ev"] if r["book"] == "fanduel" and r["side"] == "over")
    assert r["families"] == ["draftkings", "kambi"] and r["leg_id"].startswith("nba|e1|p1|points|game|20.5")
    from gradecore import devig
    kambi = (devig.worst_case([1.90, 1.90], 0)[0] + devig.worst_case([1.94, 1.86], 0)[0]) / 2
    assert r["p_cal"] == pytest.approx((kambi + devig.worst_case([1.90, 1.90], 0)[0]) / 2, abs=1e-4)      # a family counts once


def test_snapshot_lag_is_counted(monkeypatch):
    like_the_app(monkeypatch, ("fanduel", "draftkings"))
    s = _sheet(monkeypatch, [("p1", 20.5, [("fanduel", 1.9, 1.9, 200), ("draftkings", 1.9, 1.9, 280)]),
                             ("p2", 21.5, [("fanduel", 1.9, 1.9, 200), ("draftkings", 1.9, 1.9, 480)]),
                             ("p3", 22.5, [("fanduel", 1.9, 1.9, 700), ("draftkings", 1.9, 1.9, 900)])])
    assert s["lag"] == {"two_family_lines": 3, "both_under_limit": 1, "same_rules_lines": 3, "same_rules_under_limit": 1, "limit_s": 300}
    m = wm.for_member(json.dumps({"as_of": "x", "method": {}, "sports": {"nba": s}}).encode(), {"nba": True})
    assert "lag" not in m["sports"]["nba"]


# ------------------------------------------------------------------------------------------------- the record

@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOUT_EV_LOG", raising=False)
    wl.flush()
    wl._SEEN.clear(); wl._SUMMARY.clear()
    for k in ("written", "dropped_queue", "dropped_size", "errors"):
        wl._STATE[k] = 0
    wl.configure(tmp_path / "ev_log")
    yield tmp_path / "ev_log"
    wl.flush()
    wl.configure(None)


def test_record_writes_once_per_change_with_a_heartbeat_and_pages(log):
    row = {"id": "nba|underdog|x|over", "fair": 0.56, "ev_pct": 4.8, "quotes": [{"book": "fanduel", "age_s": 30}]}
    t = 1794400000.0
    assert wl.record("ev", [row], {"priced": 3}, params="p", now=t) == 2                       # the row and a counts line
    assert wl.record("ev", [dict(row, quotes=[{"book": "fanduel", "age_s": 55}])], {"priced": 3}, params="p", now=t + 30) == 0   # only ages moved
    assert wl.record("ev", [dict(row, fair=0.57)], None, params="p", now=t + 60) == 1          # a number changed
    assert wl.record("ev", [dict(row, fair=0.57)], None, params="p", now=t + 60 + wl.HEARTBEAT_S) == 1     # still there
    assert wl.record("ev", [], {"priced": 0}, params="p", now=t + wl.SUMMARY_S) == 1           # an empty board is on record
    assert wl.record("nope", [row]) == 0
    assert wl.flush()
    day = datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%d")
    pg = wl.page("ev", day, limit=2)
    assert [r["kind"] for r in pg["rows"]] == ["row", "summary"] and pg["more"] and pg["rows"][0]["first"] is True
    rest = wl.page("ev", day, after=pg["next"], limit=500)
    assert [r["kind"] for r in rest["rows"]] == ["row", "row", "summary"] and not rest["more"]
    assert rest["rows"][0]["changed"] is True and rest["rows"][0]["fair"] == 0.57 and rest["rows"][0]["tool"] == "ev"
    assert wl.page("ev", "bad")["error"] and wl.page("ev", "20200101")["rows"] == []
    assert wl.status()["written"] == 5 and wl.status()["files"][0]["tool"] == "ev"


def test_record_is_bounded_by_day_size_total_size_retention_and_queue(log, monkeypatch):
    monkeypatch.setattr(wl, "MAX_DAY_BYTES", 2000)
    t = 1794400000.0
    wl.record("arb", [{"id": i, "pad": "x" * 150} for i in range(40)], now=t)
    assert wl.flush()
    day = datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%d")
    size = (log / f"arb-{day}.jsonl").stat().st_size
    assert 0 < size <= 2000 and wl.status()["dropped_day_full"] > 0                           # the day file never passes its cap
    # retention and the total cap: old days go, oldest first; today stays
    for d in ("20250101", "20261101", "20261102"):
        (log / f"price-{d}.jsonl").write_text("x" * 1000)
    monkeypatch.setattr(wl, "MAX_TOTAL_BYTES", size + 1500)
    assert wl.prune(now=t) == 2
    assert sorted(p.name for p in log.iterdir()) == sorted([f"arb-{day}.jsonl", "price-20261102.jsonl"])
    # a full queue drops and counts; record() returns at once and never raises
    monkeypatch.setattr(wl, "_start", lambda: None)
    monkeypatch.setattr(wl, "_Q", __import__("queue").Queue(maxsize=3))
    t0 = time.time()
    assert wl.record("ev", [{"id": f"q{i}"} for i in range(50)], now=t) == 3
    assert time.time() - t0 < 1 and wl.status()["dropped_queue_full"] == 47
    assert len(wl._SEEN) <= wl.SEEN_MAX


def test_record_never_breaks_a_build_and_can_be_switched_off(log, monkeypatch):
    monkeypatch.setenv("SCOUT_EV_LOG", "0")
    assert wl.record("ev", [{"id": 1}]) == 0
    monkeypatch.delenv("SCOUT_EV_LOG")
    assert wl.record("ev", [{"id": object()}], now=1794400000.0) in (0, 1)                    # anything odd is swallowed


def test_shared_builds_queue_what_they_show_and_admin_reads_it_paged(env, monkeypatch, tmp_path):
    import http.client
    import server
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    wl.flush(); wl._SEEN.clear(); wl._SUMMARY.clear(); wl.configure(None)
    we._FAIR.clear(); we._LEGS.clear()
    from tests.test_workspace_api import get
    assert get(env, "pickem-ev")[0] == 200 and get(env, "pickem-ev")[0] == 200 and get(env, "market-tools")[0] == 200
    assert wl.flush()
    assert wl._dir() == tmp_path / "ev_log"                                                 # on the data disk

    def adm(path, token=None):
        c = http.client.HTTPConnection("127.0.0.1", env.port, timeout=30)
        c.request("GET", path, headers={"X-Admin-Token": token} if token else {})
        r = c.getresponse(); body = r.read(); c.close()
        return r.status, body
    st, _ = adm("/admin/ev_log.json")
    assert st != 200                                                                         # no token: not served
    st, body = adm("/admin/ev_log.json", server.ADMIN_TOKEN)
    idx = json.loads(body)
    assert st == 200 and {f["tool"] for f in idx["files"]} >= {"ev", "price", "arb"} and idx["limits"]["retention_days"] == wl.RETENTION_DAYS
    assert idx["book_rules"]["families"]["betrivers"] == "kambi" and any(r["book"] == "polymarket" for r in idx["book_rules"]["settlement"])
    day = idx["files"][0]["day"]
    st, body = adm(f"/admin/ev_log.json?tool=ev&day={day}&limit=1", server.ADMIN_TOKEN)
    pg = json.loads(body)
    assert st == 200 and len(pg["rows"]) == 1 and pg["rows"][0]["kind"] == "summary" and pg["rows"][0]["tool"] == "ev"
    n_ev = sum(1 for ln in (tmp_path / "ev_log" / f"ev-{day}.jsonl").read_text().splitlines() if '"summary"' in ln)
    assert n_ev == 1                                                                         # two member views, one build, one line
    wl.configure(None)
