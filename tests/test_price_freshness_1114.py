"""Owner 2026-11-14 price call: "For an older price, show the source and age beside the number, for example 'DraftKings · checked
42 min ago.' If it's too old to use, exclude it from Best Price, EV, Arbitrage and slip generation. A missing value should never
become a zero."

Every exclusion path with a quote just inside and just outside its kind's limit: gradecore.fair (Best prices / Market edge fair
chance), the +EV board (workspace_ev, its served limits), Market edge best line per side (sportsbook board rows, snapshot book rows,
exchange rows, direct sportsbook readers; pick'em app lines keep their own rules), Price check +EV and arbitrage (workspace_markets),
the price slips (positive_pool, draft). The label words (price_freshness.py and web/upgrade-fresh.js, kept equal) and
missing-not-zero cases. Fixture data only; no grade, chance, letter, stake or settlement is read or changed."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import price_freshness as pf
import workspace_books as wb
import workspace_ev as we
import workspace_market_edge as wme
import workspace_markets as wm
from gradecore import fair, market_tools as mt
from gradecore.tests.pickem_ref import reference_tables  # noqa: F401  (fixture)
from workspace_price_slips import draft, positive_pool

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
SB = fair.FRESHNESS_S["sportsbook"]
EX = fair.FRESHNESS_S["exchange"]


@pytest.fixture(autouse=True)
def fixture_books_settle_like_the_app(monkeypatch):
    from tests.book_rules_fixture import like_the_app
    like_the_app(monkeypatch)


def iso(t):
    return wb._iso(t)


# --------------------------------------------------------------------------------------------- the rules and the label words

def test_limits_are_the_existing_ones_nothing_loosened():
    assert fair.FRESHNESS_S == {"sportsbook": float(mt.EV_STALE_S), "exchange": 300.0}
    assert we.price_limits() == {k: v - 2 * we.TTL for k, v in fair.FRESHNESS_S.items()}


@pytest.mark.parametrize("kind,limit", [("sportsbook", SB), ("exchange", EX)])
def test_fresh_just_inside_and_just_outside(kind, limit):
    now = 1_800_000_000.0
    assert pf.fresh(now - (limit - 1), now, limit)
    assert pf.fresh(now - limit, now, limit)
    assert not pf.fresh(now - (limit + 1), now, limit)
    assert pf.fresh(now + 30, now, limit) and not pf.fresh(now + 61, now, limit)      # a clock a little ahead; a future stamp is not
    assert not pf.fresh(None, now, limit) and not pf.fresh(float("nan"), now, limit)    # no time = not usable (never age 0)


@pytest.mark.parametrize("age,words", [(5, "checked just now"), (59.9, "checked just now"), (60, "checked 1 min ago"),
                                       (42 * 60 + 5, "checked 42 min ago"), (89 * 60 + 59, "checked 89 min ago"),
                                       (90 * 60, "checked 1 h ago"), (3 * 3600 + 600, "checked 3 h ago"), (47 * 3600, "checked 47 h ago"),
                                       (50 * 3600, "checked 2 d ago"), (-30, "checked just now")])
def test_age_text_minutes_hours_days(age, words):
    assert pf.age_text(age) == words


def test_label_is_source_and_age_and_missing_time_is_source_only():
    now = 1_800_000_000.0
    assert pf.checked_label("DraftKings", now - 42 * 60 - 5, now) == "DraftKings · checked 42 min ago"
    assert pf.checked_label("DraftKings", None, now) == "DraftKings"                   # never "checked 0 min ago"
    assert pf.age_text(None) is None and pf.age_text(float("nan")) is None and pf.age_text(-120) is None
    assert pf.age_s(None, now) is None


JS = """
import {readFileSync} from 'node:fs';
import * as F from %s;
const cases = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const now = cases.now;
console.log(JSON.stringify({
  ages: cases.ages.map(a => F.ageText(a)),
  fresh: cases.quotes.map(q => F.isFresh(q, now)),
  labels: cases.quotes.map(q => F.quoteAgeText(q, now)),
  kinds: cases.quotes.map(q => F.quoteKind(q)),
  bk: F.bkQuote(cases.bk),
  bkFresh: F.isFresh(F.bkQuote(cases.bk), now),
  best: (F.bestFresh(cases.quotes.filter(q => q.price), (a, b) => a.price > b.price, now) || {}).label || null,
  none: F.ageText(undefined), nan: F.ageText(NaN), noTime: F.quoteAgeText({book_label: 'DraftKings'}, now),
  limits: F.LIMITS_S,
}));
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_page_helpers_match_the_server_rules(tmp_path):
    now = 1_800_000_000.0
    ms = now * 1000
    q = lambda **k: k
    quotes = [
        q(book="draftkings", book_label="DraftKings", kind="sportsbook", seen_at=iso(now - 42 * 60 - 5)),                 # 42 min: past 30, shown, never used
        q(platform="fanduel", platform_label="FanDuel", seen_at=iso(now - (SB + 5)), price=2.5),                          # stale sportsbook
        q(platform="fanduel", platform_label="FanDuel", seen_at=iso(now - (SB - 5)), price=2.1),                          # fresh sportsbook
        q(book="kalshi", label="Kalshi", kind="exchange", seen_at=iso(now - (EX - 5)), price=2.4),                         # fresh exchange
        q(platform="polymarket", platform_label="Polymarket", kind="mid", seen_at=iso(now - (EX + 5)), price=3.0),        # stale exchange
        {"offer": {"platform": "underdog", "platform_label": "Underdog", "seen_at": iso(now - 10 * 3600)}},                # pick'em: own rules
        q(book="betrivers", label="BetRivers", at=iso(now - 120)),                                                          # Market edge best
        q(book_label="DraftKings", kind="sportsbook"),                                                                      # no time
        q(book="fanduel", label="FanDuel", seen_at=iso(now + 120)),                                                         # future stamp
    ]
    ages = [5, 60, 42 * 60 + 5, 90 * 60, 3 * 3600 + 600, 50 * 3600, -30, -120]
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"now": ms, "quotes": quotes, "ages": ages, "bk": ["Sleeper", 20.5, "sleeper", "1.85x", "", iso(now - 60)]}))
    mod = tmp_path / "run.mjs"
    mod.write_text(encoding="utf-8", data=JS % json.dumps((ROOT / "web" / "upgrade-fresh.js").as_uri()))
    out = subprocess.run([NODE, str(mod), str(inp)], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    got = json.loads(out)
    assert got["ages"] == [pf.age_text(a) for a in ages]
    assert got["fresh"] == [False, False, True, True, False, True, True, False, False]
    assert got["labels"][0] == "DraftKings · checked 42 min ago" == pf.checked_label("DraftKings", now - 42 * 60 - 5, now)
    assert got["labels"][6] == "BetRivers · checked 2 min ago" and got["labels"][7] == "DraftKings"
    assert got["kinds"] == ["sportsbook", "sportsbook", "sportsbook", "exchange", "exchange", "pickem", "sportsbook", "sportsbook", "sportsbook"]
    assert got["bk"]["seen_at"] == iso(now - 60) and got["bk"]["book"] == "sleeper" and got["bkFresh"] is True
    assert got["best"] == "Kalshi"                 # the 2.5 / 3.0 prices are older than their limits: never the best
    assert got["none"] is None and got["nan"] is None and got["noTime"] == "DraftKings"
    assert got["limits"] == {k: int(v) for k, v in fair.FRESHNESS_S.items()}


def test_page_module_is_served_and_the_ev_board_uses_it():
    import server
    assert server.UPGRADE_ROUTES["/upgrade/fresh.js"][0] == "upgrade-fresh.js"
    js = (ROOT / "web" / "upgrade-ev.js").read_text(encoding="utf-8")
    assert "from '/upgrade/fresh.js'" in js and "<th>Checked</th>" in js
    assert "d.plays[a] || 0" not in js                       # a missing count shows a dash, never 0


# --------------------------------------------------------------------------------------------- gradecore.fair (Best prices, Market edge)

@pytest.mark.parametrize("kind,limit", [("sportsbook", SB), ("exchange", EX)])
def test_fair_chance_drops_a_quote_just_past_its_kind_limit(kind, limit):
    now = 1_800_000_000.0
    mk = lambda age: (fair.Quote("fanduel", 20.5, now - age, over=1.9, under=1.9) if kind == "sportsbook"
                      else fair.Quote("kalshi", 20.5, now - age, kind="exchange", bid=0.48, ask=0.50))
    inside = fair.fair_chance([mk(limit - 1)], "over", 20.5, now)
    outside = fair.fair_chance([mk(limit + 1)], "over", 20.5, now)
    assert inside["p"] is not None and not inside["dropped"]
    assert outside["p"] is None and outside["dropped"][0]["reason"] == "stale" and outside["dropped"][0]["age_s"] == pytest.approx(limit + 1)


# --------------------------------------------------------------------------------------------- the +EV board (Best prices)

def _ev_board(monkeypatch, items):
    from tests.test_pickem_ev import snap
    wb.reset(); we._FAIR.clear(); we._LEGS.clear()
    s = snap(items)
    monkeypatch.setattr(wb, "records", lambda srv, sport: ([r for r, _ in items], s) if sport == "nba" else (None, None))


def test_ev_board_uses_its_served_limit_inside_and_outside(monkeypatch, reference_tables):
    from tests.test_pickem_ev import book, rec
    lim = we.price_limits()["sportsbook"]
    _ev_board(monkeypatch, [
        (rec("p1", 20.5), [book("fanduel", 20.5, 1.65, 2.30, lim - 5), book("betrivers", 20.5, 1.68, 2.25, lim - 5)]),
        (rec("p2", 20.5), [book("fanduel", 20.5, 1.65, 2.30, lim + 5), book("betrivers", 20.5, 1.68, 2.25, lim - 5)]),
    ])
    now = time.time()
    d = we.build(None, we.params({"min_books": "1"}, ["fanduel", "betrivers"]), {"nba": True}, now=now)
    row = lambda pk: next(r for r in d["rows"] if r["key"].endswith(pk) and r["side"] == "over")
    p1, p2 = row("pr-p1"), row("pr-p2")
    assert {s["label"] for s in p1["sources"]} == {"FanDuel", "BetRivers"} and not p1["left_out"]
    assert {s["label"] for s in p2["sources"]} == {"BetRivers"}
    assert [(x["label"], x["reason"]) for x in p2["left_out"]] == [("FanDuel", "stale")]
    # every price carries its source and the time it was checked, so the page can say "FanDuel · checked 29 min ago"
    for s in p1["sources"] + p2["sources"]:
        assert s["label"] and wb._ts(s["seen_at"]) == pytest.approx(now - (lim - 5), abs=2)
    lo = p2["left_out"][0]
    assert wb._ts(lo["seen_at"]) == pytest.approx(now - (lim + 5), abs=2) and lo["age_s"] == pytest.approx(lim + 5, abs=2)
    assert pf.checked_label("FanDuel", wb._ts(lo["seen_at"]), now) == f"FanDuel · checked {int((lim + 5) // 60)} min ago"


def test_ev_board_seen_time_is_the_pricing_pass_time_not_the_serve_time(monkeypatch, reference_tables):
    from tests.test_pickem_ev import book, rec
    _ev_board(monkeypatch, [(rec("p1", 20.5), [book("fanduel", 20.5, 1.65, 2.30, 600), book("betrivers", 20.5, 1.68, 2.25, 600)])])
    t0 = time.time()
    p = we.params({"min_books": "1"}, ["fanduel", "betrivers"])
    we.build(None, p, {"nba": True}, now=t0)
    d = we.build(None, p, {"nba": True}, now=t0 + 20)        # the memoised pricing pass is reused 20 s later
    s = next(r for r in d["rows"] if r["key"].endswith("pr-p1"))["sources"][0]
    assert wb._ts(s["seen_at"]) == pytest.approx(t0 - 600, abs=2)    # not 20 s younger than it is


def test_ev_board_missing_time_stays_missing(monkeypatch, reference_tables):
    from tests.test_pickem_ev import book, rec
    _ev_board(monkeypatch, [(rec("p1", 20.5), [book("fanduel", 20.5, 1.65, 2.30, 60), dict(book("betrivers", 20.5, 1.68, 2.25, 60), seen_at=None)])])
    d = we.build(None, we.params({"min_books": "1"}, ["fanduel", "betrivers"]), {"nba": True})
    r = next(r for r in d["rows"] if r["key"].endswith("pr-p1") and r["side"] == "over")
    lo = r["left_out"][0]
    assert lo["reason"] == "no valid quote time" and lo["age_s"] is None and lo["seen_at"] is None


# --------------------------------------------------------------------------------------------- Market edge: best line per side

def _rec(app, line, seen, over=1.0, under=1.0, price=(None, None), pkey="p1"):
    from tests.test_pickem_ev import rec
    r = rec(pkey, line, app=app, mult=None if app != "underdog" else 1.0)
    r["offer"].update(seen_at=seen, over={"offered": True, "payout": over, "price": price[0]},
                      under={"offered": True, "payout": under, "price": price[1]})
    return r


def _best(monkeypatch, recs, index=None, extra=()):
    monkeypatch.setattr(wme, "_extra_records", lambda srv, sport: list(extra))
    s = {"feeds": {}, "index": index or {}}
    return wme.best_lines(None, "nba", recs, s, time.time())["e1|p1|points|full_game"]


@pytest.mark.parametrize("delta,expect", [(-5, "fanduel"), (5, "underdog")])
def test_best_line_sportsbook_board_row_inside_and_outside(monkeypatch, delta, expect):
    lim = we.price_limits()["sportsbook"]
    ud = _rec("underdog", 20.5, iso(time.time() - 60))
    fd = _rec("fanduel", 19.5, iso(time.time() - (lim + delta)), over=1.9, under=1.9, price=(1.9, 1.9))
    fd["offer"]["offer_id"] = "fd-p1"
    best = _best(monkeypatch, [ud, fd])
    assert best["over"]["book"] == expect                       # FanDuel's 19.5 is the best Over only while its price is fresh
    assert ("fanduel" in [x[2] for x in best["bk"]]) is (delta < 0)
    if delta < 0:
        assert wb._ts(best["over"]["at"]) == pytest.approx(time.time() - (lim - 5), abs=3)


@pytest.mark.parametrize("platform,kind,over,under,limit_kind", [("betrivers", "devig", 1.9, 1.9, "sportsbook"),
                                                                  ("kalshi", "mid", "0.48 / 0.50", None, "exchange")])
@pytest.mark.parametrize("delta", [-5, 5])
def test_best_line_snapshot_book_rows_inside_and_outside(monkeypatch, platform, kind, over, under, limit_kind, delta):
    from tests.test_pickem_ev import book
    lim = we.price_limits()[limit_kind]
    ud = _rec("underdog", 20.5, iso(time.time() - 60))
    row = book(platform, 19.5, over, under, lim + delta, kind=kind)
    best = _best(monkeypatch, [ud], index={("underdog", ud["offer"]["offer_id"]): {"books": ([], [row])}})
    assert (best["over"]["book"] == platform) is (delta < 0)
    assert (platform in [x[2] for x in best["bk"]]) is (delta < 0)


@pytest.mark.parametrize("comparable", [True, False])
@pytest.mark.parametrize("delta", [-5, 5])
def test_best_line_direct_sportsbook_reader_inside_and_outside(monkeypatch, delta, comparable):
    """A direct sportsbook reader's quote past the limit is out whether or not its price is comparable: an easier line wins Best line
    on the line alone, and an old quote's line is as old as its price (adversarial check 2026-11-14)."""
    lim = we.price_limits()["sportsbook"]
    ud = _rec("underdog", 20.5, iso(time.time() - 60))
    xb = _rec("unibet_au", 19.5, iso(time.time() - (lim + delta)), over=1.9, under=1.9, price=(1.9, 1.9))
    xb["offer"]["platform_label"] = "Unibet (AU)"
    best = _best(monkeypatch, [ud], extra=[(xb, comparable, "sportsbook", None)])
    assert (best["over"]["book"] == "unibet_au") is (delta < 0)
    assert ("unibet_au" in [x[2] for x in best["bk"]]) is (delta < 0)


def test_pickem_app_lines_keep_their_own_rules(monkeypatch):
    """A PrizePicks / Underdog board line is not a price that goes stale this way: its feed state decides (unchanged)."""
    ud = _rec("underdog", 20.5, iso(time.time() - 10 * 3600))
    pp = _rec("prizepicks", 21.5, None)
    best = _best(monkeypatch, [ud, pp])
    assert best["over"]["book"] == "underdog" and best["under"]["book"] == "prizepicks"
    assert best["under"]["at"] is None                                          # no time sent = no time shown, never "now"
    row = next(x for x in best["bk"] if x[2] == "prizepicks")
    assert len(row) == 6 and row[5] is None
    assert next(x for x in best["bk"] if x[2] == "underdog")[5] == iso(wb._ts(ud["offer"]["seen_at"]))


# --------------------------------------------------------------------------------------------- Price check (+EV and arbitrage)

def _sheet(monkeypatch, groups):
    import book_providers as bp
    from tests.test_phase4_markets import snap
    bp.reset(); wb.reset()
    monkeypatch.delenv("SCOUT_BOOK_SLEEPER", raising=False)
    now = time.time()
    snaps = {"nba": snap(groups, now)}
    monkeypatch.setattr(wb, "snap", lambda srv, sport: snaps.get(sport))
    monkeypatch.setattr(wm, "model_released", lambda sport, stat: False)
    return wm.sheet(None, "nba", now)


@pytest.mark.parametrize("delta", [-5, 5])
def test_price_check_ev_inside_and_outside(monkeypatch, delta):
    s = _sheet(monkeypatch, [("p1", 20.5, [("fanduel", 2.10, 1.80, 30), ("betrivers", 1.95, 1.90, 40),
                                           ("oddsapi:draftkings", 2.25, 1.70, mt.EV_STALE_S + delta)])])
    dk = [r for r in s["ev"] + s["closest"] if r["book"] == "oddsapi:draftkings"]
    if delta < 0:
        assert dk and all(r["seen_at"] and r["book_label"] and r["source"] for r in dk)
    else:
        assert not dk and s["excluded"]["Quote older than 30 minutes"] >= 1


@pytest.mark.parametrize("delta", [-5, 5])
def test_price_check_arbitrage_inside_and_outside(monkeypatch, delta):
    s = _sheet(monkeypatch, [("p1", 20.5, [("fanduel", 2.10, 1.80, 30), ("betrivers", 1.80, 2.05, mt.ARB_MAX_AGE_S + delta)])])
    clean = [r for r in s["arbitrage"] if r["line"] == 20.5]
    flagged = [r for r in s["flagged"] if r["line"] == 20.5]
    if delta < 0:
        assert clean and not flagged and all(l["seen_at"] and l["book_label"] for l in clean[0]["legs"])
    else:
        assert not clean and flagged and "Stale leg" in flagged[0]["flags"]
        # a member never gets a flagged pair (for_member keeps clean pairs only)
        body = json.dumps({"as_of": iso(time.time()), "sports": {"nba": s}, "books": [], "method": {}}).encode()
        assert wm.for_member(body, {"nba": True})["sports"]["nba"]["arbitrage"] == []


# --------------------------------------------------------------------------------------------- price slips

def _ev_doc(age, now):
    return {"as_of": iso(now), "rows": [{"key": "nba|underdog|o1", "side": "over", "basis": "market", "ev": 0.05, "p_market": 0.6, "line": 20.5,
                                         "sources": [{"kind": "sportsbook", "age_s": age, "seen_at": iso(now - age)}]}]}


@pytest.mark.parametrize("delta,kept", [(-5, True), (5, False)])
def test_positive_ev_slip_pool_inside_and_outside(delta, kept):
    now = time.time()
    lim, horizon = we.price_limits()["sportsbook"], 20
    leg = {"sport": "nba", "book": "underdog", "row_id": "o1", "side": "over", "line": 20.5, "line_type": "standard", "multiplier": None}
    out = positive_pool([leg], _ev_doc(lim - horizon + delta, now), horizon=horizon)
    assert bool(out) is kept


def test_positive_ev_slip_pool_missing_age_is_never_zero():
    now = time.time()
    doc = _ev_doc(60, now)
    doc["rows"][0]["sources"][0]["age_s"] = None
    leg = {"sport": "nba", "book": "underdog", "row_id": "o1", "side": "over", "line": 20.5, "line_type": "standard", "multiplier": None}
    assert positive_pool([leg], doc, horizon=20) == []


def _market(mode, age, now):
    from tests.test_price_slips import market
    d, r = market(mode)
    for l in (r["legs"] if mode == "arb" else [r]):
        l["seen_at"] = None if age is None else iso(now - age)
        l["book_label"] = "FanDuel" if l["book"] == "fanduel" else "BetRivers"
    return d, r


@pytest.mark.parametrize("mode,limit", [("arb", mt.ARB_MAX_AGE_S), ("ev", mt.EV_STALE_S)])
def test_draft_inside_and_outside_with_the_source_and_age_named(mode, limit):
    now = time.time()
    books = "fanduel,betrivers" if mode == "arb" else "fanduel:over"
    d, r = _market(mode, limit - 5, now)
    assert draft(d, mode, r["leg_id"], books, 100, now=now)["slips"]
    d, r = _market(mode, limit + 5, now)
    with pytest.raises(ValueError) as e:
        draft(d, mode, r["leg_id"], books, 100, now=now)
    assert str(e.value).startswith(f"FanDuel · checked {int((limit + 5) // 60)} min ago: older than the {limit // 60}-minute limit")


def test_draft_missing_time_is_named_never_zero():
    now = time.time()
    d, r = _market("arb", None, now)
    with pytest.raises(ValueError, match="FanDuel: this price has no check time"):
        draft(d, "arb", r["leg_id"], "fanduel,betrivers", 100, now=now)


def test_no_zero_placeholders_in_member_price_payloads(monkeypatch, reference_tables):
    """Missing stays missing: no source / leg / best entry carries a 0 or '0.0%' where a value is absent."""
    from tests.test_pickem_ev import book, rec
    _ev_board(monkeypatch, [(rec("p1", 20.5), [book("fanduel", 20.5, 1.65, 2.30, 60), book("betrivers", 20.5, 1.68, 2.25, 60)])])
    d = we.build(None, we.params({"min_books": "1"}, ["fanduel", "betrivers"]), {"nba": True})
    for r in d["rows"]:
        for s in r["sources"]:
            assert s["liquidity"] is None and s["bid"] is None and s["ask"] is None and s["sit_out"] is None   # sportsbook: absent, not 0
    assert "0.0%" not in json.dumps(d)
