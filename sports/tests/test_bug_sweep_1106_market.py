"""Bug sweep 2026-11-06 (sports market / store / builder): #4 sc-1 withdraws an OddsPapi book that dropped the game,
#8 a book's own quote never enters its own Market chance, #31 archived rows whose source contains ':' are found by
record id, #32 the archive file LRU never counts one file twice, #43 count_combinations honours max_per_event.
Every case reproduces the report's failure case."""
import lzma
import threading
from datetime import datetime, timezone

from sports import archive, store
from sports.books import base, bridge
from sports.slips import builder
from sports.tests import helpers as h
from sports.tests.test_market_view import EV, NOW, book, items
from sports import market_view

T1 = "2026-01-31T20:00:00Z"
T2 = "2026-01-31T20:30:00Z"
START = "2026-02-01T00:00:00Z"


# ------------------------------------------------------------------ #4
def _op(bk, line=20.5, side="over"):
    return base.make_offer(provider="oddspapi", book=bk, jurisdiction="us", league="nba", provider_event_id="F",
                           event_name="x", start=START, player="P00", market="points", period="game", line=line,
                           side=side, price_decimal=1.9, market_type="standard", status="open")


def _state(con):
    return {o.platform: o.availability for _, o in store.offers(con, event_id="nba:espn:123", limit=1000)}


def test_4_oddspapi_book_that_dropped_the_game_is_withdrawn_in_sc1():
    con = store.connect()
    put = bridge.sink(con, lambda _lg: None)
    lg = {"key": "nba"}
    put("oddspapi", lg, "espn:123", [_op("draftkings"), _op("draftkings", side="under"), _op("betmgm"),
                                     _op("betmgm", side="under")], True, T1)
    assert _state(con) == {"oddspapi:draftkings": "open", "oddspapi:betmgm": "open"}
    put("oddspapi", lg, "espn:123", [_op("betmgm"), _op("betmgm", side="under")], True, T2)   # DK stopped listing F
    assert _state(con) == {"oddspapi:draftkings": "removed", "oddspapi:betmgm": "open"}
    assert put.totals["oddspapi"]["removed"] == 1


def test_4_oddspapi_game_with_no_props_left_withdraws_every_book_but_a_partial_read_never_does():
    con = store.connect()
    put = bridge.sink(con, lambda _lg: None)
    lg = {"key": "nba"}
    put("oddspapi", lg, "espn:123", [_op("draftkings"), _op("betmgm")], True, T1)
    put("oddspapi", lg, "espn:123", [], False, T2)                                  # failed / partial: nothing removed
    assert set(_state(con).values()) == {"open"}
    put("oddspapi", lg, "espn:123", [], True, T2)                                   # complete read, no props at all
    assert _state(con) == {"oddspapi:draftkings": "removed", "oddspapi:betmgm": "removed"}


def test_4_oddspapi_complete_read_never_touches_the_direct_feeds_on_the_same_game():
    con = store.connect()
    put = bridge.sink(con, lambda _lg: None)
    lg = {"key": "nba"}
    fd = base.make_offer(provider="fanduel", book="fanduel", jurisdiction="oh", league="nba", provider_event_id="g",
                         event_name="x", start=START, player="P00", market="points", period="game", line=20.5,
                         side="over", price_decimal=1.9, market_type="standard", status="open")
    put("fanduel", lg, "espn:123", [fd], True, T1)
    put("oddspapi", lg, "espn:123", [_op("draftkings")], True, T1)
    put("oddspapi", lg, "espn:123", [], True, T2)
    assert _state(con) == {"fanduel": "open", "oddspapi:draftkings": "removed"}


# ------------------------------------------------------------------ #8
def test_8_a_books_own_quote_is_never_in_its_own_market_chance():
    pp = h.offer()                                                                  # PrizePicks 20.5, the head
    fd_alt = book("fanduel", "fd:alt", line=20.5, over=1.9, under=None, variant="ladder_rung")
    fd_main = book("fanduel", "fd:main", line=21.5, over=1.85, under=1.95)
    br = book("betrivers", "br:1", line=21.5, over=2.2, under=1.7)
    its = items(pp, fd_alt, fd_main, br)
    mv = market_view.row_view([(r, o) for r, o, _ in its], its, EV, NOW, "nba", 0.0)
    fd = mv["at"].get("rid:fanduel:fd:main", mv["c"])
    assert fd is not None and fd["used"] == ["betrivers"] and fd["n"] == 1
    br_c = mv["at"].get("rid:betrivers:br:1", mv["c"])
    assert br_c["used"] == ["fanduel"]                                              # BetRivers judged on FanDuel only


# ------------------------------------------------------------------ #31
def test_31_archived_row_with_colon_in_its_source_is_found_by_record_id():
    con = store.connect()
    ids = {}
    for plat, oid in (("oddsapi:fanduel", "ev1:player_points:karl anthony towns:20.5"), ("prizepicks", "pp:1")):
        store.put(con, h.offer(platform=plat, oid=oid, observed_at="2026-10-10T20:00:00Z"))
        ids[plat] = con.execute("SELECT record_id FROM records WHERE source=?", (plat,)).fetchone()[0]
    assert all(store.get(con, r) is not None for r in ids.values())
    out = archive.run_pass(con, now=datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
    assert out.get("error") is None
    assert con.execute("SELECT COUNT(*) FROM records WHERE kind='offer'").fetchone()[0] == 0     # both archived
    for plat, rid in ids.items():
        got = store.get(con, rid)
        assert got is not None and got.platform == plat, plat
        assert store.row_meta(con, rid) is not None, plat
    assert archive.row_by_record_id(con, ids["oddsapi:fanduel"] + "x") is None     # a wrong hash still finds nothing
    con.close()


def test_31_heads_tries_every_split_point():
    assert archive._heads("offer:oddsapi:fanduel:ev1:x") == [
        ("offer", "oddsapi", "fanduel:ev1:x"), ("offer", "oddsapi:fanduel", "ev1:x"),
        ("offer", "oddsapi:fanduel:ev1", "x")]
    assert archive._heads("offer") == []


# ------------------------------------------------------------------ #32
def test_32_concurrent_misses_on_one_file_are_counted_once(tmp_path):
    d = tmp_path / "archive" / "records"
    d.mkdir(parents=True)
    raw = b"".join(b'{"i": %6d}\n' % i for i in range(1000))
    with lzma.open(d / "f1.xz", "wb") as f:
        f.write(raw)
    files = archive._Files()
    gate = threading.Barrier(4)

    def lookup(_fid):
        gate.wait(timeout=10)                                                       # every thread misses first
        return "f1.xz"
    got = []
    ts = [threading.Thread(target=lambda: got.append(files.rows(str(tmp_path / "db.sqlite"), 1, lookup)))
          for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(files.lru) == 1 and files.size == len(raw)                           # was 4 x len(raw)
    assert len({id(x) for x in got}) == 1 and got[0][999] == {"i": 999}


# ------------------------------------------------------------------ #43
def _leg(i, ev, team, side):
    return {"leg_id": f"l{i}", "event_id": f"E{ev}", "team": team, "side": side, "player_id": f"p{i}",
            "prop_keys": (f"p{i}|pts",), "weight": -0.5, "p_win": 0.6, "p_loss": 0.4, "p_push": 0.0,
            "record_id": f"r{i}", "multiplier": 1.0, "platform": "underdog", "league": "nba"}


def _three_events():
    legs, i = [], 0
    for ev in range(3):
        for team in (f"H{ev}", f"A{ev}"):
            for side in ("over", "under"):
                legs.append(_leg(i, ev, team, side))
                i += 1
    return legs


def test_43_count_combinations_honours_max_per_event():
    legs = _three_events()                                                          # 12 legs, 4 per game
    assert builder.count_combinations(legs, 4, "both", 1) == 0                      # unchanged with one per game
    # two per game: choose how many legs each game gives (sum 4, each <= 2), C(4,k) ways per game
    want = 3 * 6 * 6 + 3 * (6 * 4 * 4)                                              # (2,2,0) x3 + (2,1,1) x3
    assert builder.count_combinations(legs, 4, "both", 2) == want
    # single-side: two over legs per game
    assert builder.count_combinations(legs, 4, "over", 2) == 3 * 1 * 1 + 3 * (1 * 2 * 2)
    # max_per_event 1 is still the old count
    assert builder.count_combinations(legs, 2, "both", 1) == 3 * 4 * 4


def test_43_same_player_or_same_prop_pairs_are_not_counted():
    legs = [_leg(0, 0, "H", "over"), dict(_leg(1, 0, "A", "under"), player_id="p0"),
            dict(_leg(2, 0, "A", "over"), prop_keys=("p0|pts",))]
    assert builder.count_combinations(legs, 2, "both", 2) == 1                      # only legs 1 + 2


def test_43_shortage_reason_is_true_under_max_per_event_2():
    legs = _three_events()
    st = builder.normalize({"sides": "both", "sizes": {4: 5}, "max_per_event": 2}, "underdog")
    slips, _ = builder.search(legs, st)
    assert any(len({legs[i]["event_id"] for i in s}) == 2 for s in slips)           # the search does build 2-game slips
    out = builder.explain_shortage(legs, legs, st, {4: len(slips)}, {})
    (row,) = out
    assert row["valid_combinations"] > 0
    assert not any("needs 4 different games" in w for w in row["reasons"])
    st1 = builder.normalize({"sides": "both", "sizes": {4: 5}}, "underdog")
    (row1,) = builder.explain_shortage(legs, legs, st1, {4: 0}, {})
    assert row1["valid_combinations"] == 0 and "needs 4 different games" in row1["reasons"][0]
