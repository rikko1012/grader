"""books-timeout 2026-11-11: the light 'books' job (FanDuel / BetRivers / Bovada player props) timed out at 600 s on every
run. After each complete game read the books sink (sports/books/bridge.store_game) checks for withdrawn offers with
store.offers(league=, event_id=, platform=). Without ANALYZE stats SQLite served that event-scoped read with
records_feed_seen (kind, platform): it walked every stored revision of the platform in every league and game and read each
table row to test event_id - once per game per platform. store.iter_offers now writes league / platform / stat as
'+col = ?' when an event is given, so the event indexes serve it. These tests pin: the event plan on the hot and archive
arms, the same rows as the old predicate, and a cost that no longer grows with other games of the same platform."""
from pathlib import Path

import pytest

from sports import store
from sports.tests import helpers as h

NOW = "2026-10-23T20:00:00Z"


@pytest.fixture
def db(tmp_path):
    con = store.connect(tmp_path / "canonical.db")
    yield con
    con.close()


def _o(platform, oid, event_id, line=20.5, stat="points", league="nba"):
    return h.offer(platform=platform, oid=oid, line=line, stat=stat, league=league, event_id=event_id,
                   observed_at=NOW, known_at=NOW)


def _fill(con, games, per_game=20, platforms=("fanduel", "betrivers", "prizepicks")):
    recs = []
    for g in range(games):
        for p in platforms:
            for i in range(per_game):
                recs.append(_o(p, f"{p}:g{g}:{i}", f"nba:espn:{g}", line=10.5 + i % 5,
                               stat=("points", "rebounds")[i % 2]))
    store.put_many(con, recs)


def _traced(con, fn):
    seen = []
    con.set_trace_callback(seen.append)
    try:
        fn()
    finally:
        con.set_trace_callback(None)
    return seen


def _plan(con, sql):
    return " | ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql).fetchall())


@pytest.mark.parametrize("platform", ["fanduel", None])
def test_event_scoped_offer_read_uses_the_event_index(db, platform):
    """bridge.store_game's withdrawal read (with platform) and the prefix path (league + event only)."""
    _fill(db, 3)
    seen = _traced(db, lambda: store.offers(db, league="nba", event_id="nba:espn:1", platform=platform, limit=100000))
    q = next(s for s in seen if "records_meta_all" in s)
    plan = _plan(db, q)
    assert "records_event" in plan, plan
    for bad in ("records_feed_seen", "records_current_league", "records_current_stat", "records_offer_seen"):
        assert bad not in plan, plan
    assert "SCAN main.records" not in plan and "SCAN records " not in plan + " ", plan
    assert "archive_rows_event" in plan and "arch_segs_event" in plan, plan   # archive arms: event index too


def test_same_rows_as_the_old_predicate(db):
    _fill(db, 4)
    # a new revision, a withdrawal, and another league's game with the same event id
    store.put(db, _o("fanduel", "fanduel:g1:3", "nba:espn:1", line=30.5))
    cur = store.latest(db, "offer", "fanduel", "fanduel:g1:4")
    store.put(db, store.withdrawn(cur, NOW))
    store.put_many(db, [_o("fanduel", f"fanduel:nfl:{i}", "nba:espn:1", league="nfl") for i in range(5)])
    for plat in ("fanduel", "betrivers", None):
        for stat in ("points", None):
            new = store.offers(db, league="nba", event_id="nba:espn:1", platform=plat, stat=stat, limit=100000)
            w, a = ["league = ?", "event_id = ?"], ["nba", "nba:espn:1"]
            if stat:
                w.append("stat = ?")
                a.append(stat)
            if plat:
                w.append("platform = ?")
                a.append(plat)
            old = store._latest_rows(db, "offer", " AND ".join(w), a, None, 100000, scoped=True)
            assert new and [rid for rid, _ in new] == [r["record_id"] for r in old], (plat, stat)


def test_cost_does_not_grow_with_other_games_of_the_platform(db):
    _fill(db, 2)

    def steps():
        n = [0]

        def tick():
            n[0] += 1
            return 0
        db.set_progress_handler(tick, 100)
        try:
            store.offers(db, league="nba", event_id="nba:espn:1", platform="fanduel", limit=100000)
        finally:
            db.set_progress_handler(None, 0)
        return n[0]
    before = steps()
    _fill(db, 60, per_game=40, platforms=("fanduel",))   # 2,400 more FanDuel revisions on OTHER games
    after = steps()
    # games 0/1 got 40 more rows each in that fill; everything else is other games
    assert after <= before * 3 + 10, (before, after)
