"""Board market view mv-1 (sports/market_view.py): every book on the IDENTICAL market with its own chance, and the
Market (N books) consensus by the mc-1 rule - identical markets only, one-sided handling, stale books shown but not
counted, target book excluded, capture-only sources never counted, pick'em boards never sources."""
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from sports import market_view, snapshots, store
from sports.contracts import SideQuote
from sports.tests import helpers as h
from sports.tests.test_ui_snapshots import built, _load  # noqa: F401 - shared fixture

NOW = "2026-10-23T20:05:00Z"
EV = h.event()
WEB = Path(__file__).resolve().parents[2] / "web" / "sports"


def book(platform, oid, line=20.5, over=1.80, under=2.05, observed_at=h.T0, **kw):
    sides = (SideQuote("over", over is not None, "decimal" if over else "none", price=over),
             SideQuote("under", under is not None, "decimal" if under else "none", price=under))
    return h.offer(platform=platform, oid=oid, line=line, variant=kw.pop("variant", "main"), sides=sides,
                   observed_at=observed_at, **kw)


def poly(oid="pm:1", line=20.5, bid=0.50, ask=0.53, liq=500.0):
    return h.offer(platform="polymarket", oid=oid, line=line, variant="main",
                   sides=(SideQuote("over", True, "exchange_yes_no", bid=bid, ask=ask),
                          SideQuote("under", True, "exchange_yes_no", bid=round(1 - ask, 4), ask=round(1 - bid, 4))),
                   liquidity=(("liquidity", liq),))


def items(*offers):
    return [(f"rid:{o.platform}:{o.offer_id}", o, {"last_seen_at": o.observed_at}) for o in offers]


def devig(o, u):
    return (1 / o) / (1 / o + 1 / u)


def logit(p):
    return math.log(p / (1 - p))


def inv(x):
    return 1 / (1 + math.exp(-x))


def test_every_book_on_the_identical_market_with_its_own_chance_and_the_mc1_consensus():
    pp = h.offer()                                                     # PrizePicks 20.5 (target, never a source)
    fd, br = book("fanduel", "fd:1"), book("betrivers", "br:1", over=2.20, under=1.70)
    pm = poly()
    its = items(pp, fd, br, pm)
    mv = market_view.row_view([(its[0][0], pp), (its[1][0], fd), (its[2][0], br)], its, EV, NOW, "nba", 0.0)
    by = {b["p"]: b for b in mv["bk"]}
    assert set(by) == {"fanduel", "betrivers", "polymarket"}           # pick'em board is not a book
    assert by["fanduel"]["kind"] == "devig" and by["fanduel"]["c"] == pytest.approx(devig(1.80, 2.05), abs=1e-4)
    assert by["polymarket"]["kind"] == "mid" and by["polymarket"]["c"] == pytest.approx(0.515, abs=1e-4)
    assert all(b["in"] for b in by.values()) and by["fanduel"]["age"] == 300
    want = inv((logit(devig(1.80, 2.05)) + logit(devig(2.20, 1.70)) + logit(0.515)) / 3)   # equal weight, log-odds
    c = mv["c"]
    assert mv["hp"] == "prizepicks" and mv["l"] == 20.5
    assert c["n"] == 3 and c["used"] == ["betrivers", "fanduel", "polymarket"]
    assert c["po"] == pytest.approx(want, abs=1e-4) and c["pp"] == 0.0 and c["po"] + c["pu"] == pytest.approx(1)
    # target book excluded: FanDuel's own line is judged against the OTHER books only
    fdc = mv["at"][its[1][0]]
    assert fdc["n"] == 2 and "fanduel" not in fdc["used"]
    assert fdc["po"] == pytest.approx(inv((logit(devig(2.20, 1.70)) + logit(0.515)) / 2), abs=1e-4)
    # never best-price normalisation across books
    best = (1 / 2.20) / (1 / 2.20 + 1 / 2.05)              # best Over (BR) vs best Under (FD)
    assert abs(c["po"] - best) > 1e-3


def test_mismatched_scope_variant_promo_and_other_player_are_not_the_same_market():
    pp = h.offer()
    others = [book("fanduel", "fd:h1", scope="first_half"), book("betrivers", "br:alt", variant="goblin"),
              book("fanduel", "fd:promo", promotional=True), book("betrivers", "br:p2", player_id="nba:player:espn:9")]
    its = items(pp, *others)
    mv = market_view.row_view([(its[0][0], pp)], its, EV, NOW, "nba", 0.0)
    assert mv["bk"] == [] and mv["c"] is None


def test_one_sided_price_is_shown_as_price_only_and_never_counted():
    pp = h.offer()
    br = book("betrivers", "br:1", under=None)
    fd = book("fanduel", "fd:1")
    its = items(pp, br, fd)
    mv = market_view.row_view([(its[0][0], pp)], its, EV, NOW, "nba", 0.0)
    b = {x["p"]: x for x in mv["bk"]}["betrivers"]
    assert b["kind"] == "one_sided" and b["c"] is None and b["over"] == 1.80 and b["under"] is None
    assert not b["in"] and b["why"] == "not a two-sided priced pair"
    assert mv["c"]["n"] == 1 and mv["c"]["used"] == ["fanduel"]


def test_stale_book_is_listed_with_its_age_but_not_in_the_consensus():
    pp = h.offer()
    old = book("fanduel", "fd:1", observed_at="2026-10-23T19:30:00Z")
    fresh = book("betrivers", "br:1", over=1.95, under=1.87)
    its = items(pp, old, fresh)
    mv = market_view.row_view([(its[0][0], pp)], its, EV, NOW, "nba", 0.0)
    b = {x["p"]: x for x in mv["bk"]}["fanduel"]
    assert not b["in"] and b["why"] == "older than 15 min" and b["age"] == 35 * 60
    assert b["c"] == pytest.approx(devig(1.80, 2.05), abs=1e-4)     # its own chance is still shown
    assert mv["c"]["used"] == ["betrivers"] and mv["c"]["po"] == pytest.approx(devig(1.95, 1.87), abs=1e-4)


def test_capture_only_sources_are_listed_but_never_counted_and_kalshi_is_a_book():
    pp = h.offer()
    op = book("oddspapi:draftkings", "op:1")
    k = h.kalshi_rung(20.5, 0.48, 0.52, observed_at="2026-10-23T20:01:00Z")
    its = items(pp, op, k)
    mv = market_view.row_view([(its[0][0], pp)], its, EV, NOW, "nba", 0.0)
    by = {x["p"]: x for x in mv["bk"]}
    assert not by["oddspapi:draftkings"]["in"] and "capture-only" in by["oddspapi:draftkings"]["why"]
    assert by["kalshi"]["kind"] == "mid" and by["kalshi"]["c"] == pytest.approx(0.50) and by["kalshi"]["in"]
    assert mv["c"]["used"] == ["kalshi"] and mv["c"]["n"] == 1


def test_different_line_moves_to_the_target_line_and_is_flagged():
    pp = h.offer(line=20.5)
    fd = book("fanduel", "fd:1", line=21.5)
    its = items(pp, fd)
    mv = market_view.row_view([(its[0][0], pp)], its, EV, NOW, "nba", 0.0)
    assert mv["bk"][0]["l"] == 21.5 and mv["l"] == 20.5
    assert mv["c"]["po"] > devig(1.80, 2.05)                          # one point lower line = more likely Over


def test_build_during_ingest_counts_a_just_seen_quote():
    pp = h.offer()
    fd = book("fanduel", "fd:1")
    its = [(f"r{i}", o, {"last_seen_at": "2026-10-23T20:06:00Z"}) for i, o in enumerate((pp, fd))]
    mv = market_view.row_view([("r0", pp)], its, EV, NOW, "nba", 0.0)
    assert mv["bk"][0]["in"]                                          # 60 s after `now`: seen at now
    its = [(f"r{i}", o, {"last_seen_at": "2026-10-23T21:00:00Z"}) for i, o in enumerate((pp, fd))]
    assert not market_view.row_view([("r0", pp)], its, EV, NOW, "nba", 0.0)["bk"][0]["in"]


def test_snapshot_rows_and_manifest_carry_the_market_view(built):
    con = store.connect()
    t = "2026-10-23T20:55:00Z"                                        # the fixture builds at 21:00
    store.put(con, book("fanduel", "fd:kat", observed_at=t))
    store.put(con, book("betrivers", "br:kat", over=1.95, under=1.87, observed_at=t))
    store.put(con, book("betrivers", "br:kat-h1", scope="first_half", line=10.5, observed_at=t))
    snapshots.build(con, "nba", now="2026-10-23T21:00:00Z")
    con.close()
    b = _load(built, "board.json")
    row = [r for r in b["markets"] if r["st"] == "points" and r["pid"] == "nba:player:espn:3134907"
           and any(q["p"] == "prizepicks" for q in r["q"])][0]
    mk = row["mk"]
    assert {x["p"] for x in mk["bk"]} == {"fanduel", "betrivers", "kalshi"}
    assert mk["c"]["used"] == ["betrivers", "fanduel"]                # Kalshi rungs (20:00) are older than 15 min
    assert {x["why"] for x in mk["bk"] if not x["in"]} == {"quote observed 3600s before forecast (> 900s)"}
    pp = [q for q in row["q"] if q["p"] == "prizepicks"][0]
    assert mk["at"][pp["r"]] == mk["c"]
    m = _load(built, "manifest.json")
    assert m["market_view"]["version"] == "mv-1" and m["market_view"]["with_consensus"] >= 1
    s = m["sources"]
    assert s["fanduel"]["offers"] >= 1 and s["fanduel"]["verified"] and s["fanduel"]["last_read_at"] == t
    assert s["thunderpick"]["verified"] is False and s["thunderpick"]["capture"]["nba"]["status"]
    assert s["oddsapi"]["offers"] == 0 and s["prizepicks"]["role"].startswith("pick'em")
    first_half = [r for r in b["markets"] + _load(built, "board_more.json")["markets"] if r["k"].endswith("first_half")]
    assert all(x["p"] != "fanduel" for r in first_half for x in (r["mk"] or {}).get("bk", []))


def test_inventory_capture_is_structural_and_stores_the_raw_answer(tmp_path, monkeypatch):
    from sports.books import inventory
    monkeypatch.setattr(inventory, "root", lambda: tmp_path / "inv")
    body = [{"id": 1, "name": "NY Knicks - Boston Celtics", "markets": [
        {"name": "Player Points", "selections": [{"name": "Over", "handicap": 20.5, "odds": 1.8},
                                                 {"name": "Under", "handicap": 20.5, "odds": 2.0}]}]}]
    raw = json.dumps(body).encode()
    s = inventory.capture("thunderpick", "nba", getter=lambda p: (200, raw), key="k")
    a = s["answers"][0]
    assert a["http_status"] == 200 and len(a["sha256"]) == 64 and Path(a["raw_file"]).exists()
    st = a["structure"]
    assert st["names"]["[].markets[].name"] == ["Player Points"]
    assert st["lines"]["[].markets[].selections[].handicap"] == [20.5]
    assert inventory.read_summary("thunderpick", "nba")["status"] == "captured (format not verified)"
    out = []
    inventory.render(inventory.read_summary("thunderpick", "nba"), out.append)
    assert any("Player Points" in x for x in out) and any("prop format proven only if" in x for x in out)
    assert inventory.coverage_entry("thunderpick", "nba")["answers"][0]["sha256"] == a["sha256"]
    # no key and no getter: nothing is requested, the reason is stored
    assert "not set" in inventory.capture("thunderpick", "nfl", key="")["status"]


def test_app_never_says_kalshi_market_chance():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    for bad in ("Market chance Over", "the Kalshi market chance", "market prices: Kalshi, PrizePicks, Underdog"):
        assert bad not in app
    assert "S.marketLabel" in app and "mkBooks(m.mk)" in app and "sw-mkrow" in app and "mkBooks(m.market, true)" in app


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_market_display_functions_in_node():
    script = Path(__file__).resolve().parent / "ui_market_test.js"
    r = subprocess.run(["node", str(script), str(WEB / "stats.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
