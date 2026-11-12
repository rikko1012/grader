"""Line correctness of the game-data store games.db (sports/LINE_CORRECTNESS.md rows G1-G9): ESPN-DraftKings prop
lines, Kalshi game markets and context snapshots - revisions, A -> A sightings, delayed responses, reopen, concurrent
writers, existing-database migration, what factors.py reads."""
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sports.games import factors, store

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 23, 20, tzinfo=timezone.utc)


def at(m):
    return NOW + timedelta(minutes=m)


def prop(line=20.5, upd=None, status="open", line_open=19.5):
    return {"source": "espn", "book": "draftkings", "provenance": "espn odds api", "league": "nba", "event_id": "1",
            "prop_key": "p1", "athlete_id": "3134907", "player": "Karl-Anthony Towns", "player_key": "karl anthony towns",
            "market": "points", "period": "game", "kind": "line", "line": line, "line_open": line_open,
            "provider_updated_at": upd, "status": status}


def mkt(bid=0.48, status="active"):
    return {"source": "kalshi", "league": "nba", "event_id": "1", "provider_market_id": "M1", "market": "total_over",
            "strike": 220.5, "bid": bid, "ask": 0.52, "status": status}


@pytest.fixture
def gdb(tmp_path):
    db = store.connect(tmp_path / "games.db", dsn="")
    yield db
    db.close()


def props(db):
    return db.all("SELECT line, late, observed_at, provider_updated_at FROM sports_game_prop_lines ORDER BY id")


def dk(db, m=60):
    return factors._dk(db, "nba", "1", "3134907", "points", at(m))


def test_g1_prop_a_b_a_is_three_rows_and_factors_read_the_final_a(gdb):
    assert store.save_prop_lines(gdb, [prop(20.5)], now=at(0)) == 1
    assert store.save_prop_lines(gdb, [prop(21.5)], now=at(10)) == 1
    assert store.save_prop_lines(gdb, [prop(20.5)], now=at(20)) == 1
    assert [r["line"] for r in props(gdb)] == [20.5, 21.5, 20.5]
    assert dk(gdb)[0] == 20.5 and dk(gdb, 15)[0] == 21.5


def test_g1_kalshi_market_a_b_a_and_closed_then_reopened(gdb):
    assert [store.save_market_lines(gdb, [r], now=at(i * 10)) for i, r in
            enumerate([mkt(0.48), mkt(0.49), mkt(0.48), mkt(0.48, "closed"), mkt(0.48)])] == [1, 1, 1, 1, 1]
    st = gdb.one("SELECT m.bid, m.status FROM sports_game_line_state s JOIN sports_game_market_lines m "
                 "ON m.id = s.last_id WHERE s.key = 'mkt:kalshi:M1'")
    assert (st["bid"], st["status"]) == (0.48, "active")


def test_g2_a_a_with_a_new_source_time_is_freshness_not_a_line_change(gdb):
    store.save_prop_lines(gdb, [prop(20.5, upd="2026-10-23T19:59:00Z")], now=at(0))
    assert store.save_prop_lines(gdb, [prop(20.5, upd="2026-10-23T20:09:00Z")], now=at(10)) == 0
    assert len(props(gdb)) == 1
    st = gdb.one("SELECT last_seen_at, last_provider_updated_at FROM sports_game_line_state WHERE key = 'prop:espn:p1'")
    assert st["last_seen_at"] == "2026-10-23T20:10:00Z" and st["last_provider_updated_at"] == "2026-10-23T20:09:00Z"
    assert store.save_snapshot(gdb, "nba", "1", "pickcenter", "espn", [{"spread": -3}], now=at(0))
    assert not store.save_snapshot(gdb, "nba", "1", "pickcenter", "espn", [{"spread": -3}], now=at(10))


def test_g3_delayed_older_response_is_history_never_current(gdb):
    store.save_prop_lines(gdb, [prop(21.5, upd="2026-10-23T20:09:00Z")], now=at(10))
    assert store.save_prop_lines(gdb, [prop(20.5, upd="2026-10-23T20:04:00Z")], now=at(12)) == 0
    assert [(r["line"], r["late"]) for r in props(gdb)] == [(21.5, 0), (20.5, 1)]
    assert dk(gdb)[0] == 21.5
    store.save_market_lines(gdb, [mkt(0.49)], now=at(10))
    assert store.save_market_lines(gdb, [mkt(0.40)], now=at(5)) == 0               # read :05, stored after :10
    rows = gdb.all("SELECT * FROM sports_game_market_lines m JOIN (SELECT provider_market_id, MAX(id) AS id FROM "
                   "sports_game_market_lines WHERE late = 0 GROUP BY provider_market_id) z ON z.id = m.id")
    assert [r["bid"] for r in rows] == [0.49]
    store.save_snapshot(gdb, "nba", "1", "pickcenter", "espn", [{"spread": -3}], now=at(10))
    assert not store.save_snapshot(gdb, "nba", "1", "pickcenter", "espn", [{"spread": -9}], now=at(5))
    assert factors._snapshot(gdb, "nba", "1", "pickcenter", at(60))[0] == [{"spread": -3}]
    st = gdb.one("SELECT last_seen_at FROM sports_game_line_state WHERE key = 'mkt:kalshi:M1'")
    assert st["last_seen_at"] == "2026-10-23T20:10:00Z"                              # never rewound


def test_g4_prop_withdrawn_and_reopened_at_the_same_line(gdb):
    store.save_prop_lines(gdb, [prop(20.5)], now=at(0))
    store.save_prop_lines(gdb, [prop(20.5, status="closed")], now=at(10))
    assert store.save_prop_lines(gdb, [prop(20.5)], now=at(20)) == 1
    cur = gdb.one("SELECT p.status FROM sports_game_line_state s JOIN sports_game_prop_lines p ON p.id = s.last_id "
                  "WHERE s.key = 'prop:espn:p1'")
    assert cur["status"] == "open"


WRITER = textwrap.dedent("""
    import sys
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    sys.path.insert(0, {root!r})
    from sports.games import store
    from sports.tests.test_line_correctness_games import prop, mkt
    i, n, rounds = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    db = store.connect(Path({path!r}), dsn="")
    t0 = datetime(2026, 10, 23, 20, tzinfo=timezone.utc)
    for k in range(rounds):
        sec = k * n + i
        store.save_prop_lines(db, [prop(20.5 + (k + i) % 3)], now=t0 + timedelta(seconds=sec))
        store.save_market_lines(db, [mkt(0.40 + 0.01 * ((k + i) % 3))], now=t0 + timedelta(seconds=sec))
    db.close()
""")


def test_g7_concurrent_writers_keep_one_consistent_current_row(tmp_path):
    path = tmp_path / "games.db"
    store.connect(path, dsn="").close()
    script = tmp_path / "w.py"
    script.write_text(WRITER.format(root=str(ROOT), path=str(path)), encoding="utf-8")
    n, rounds = 4, 30
    procs = [subprocess.Popen([sys.executable, str(script), str(i), str(n), str(rounds)], stdin=subprocess.DEVNULL)
             for i in range(n)]
    assert all(p.wait(timeout=240) == 0 for p in procs)
    db = store.connect(path, dsn="")
    k, i = divmod(rounds * n - 1, n)
    cur = db.one("SELECT p.line FROM sports_game_line_state s JOIN sports_game_prop_lines p ON p.id = s.last_id "
                 "WHERE s.key = 'prop:espn:p1'")
    assert cur["line"] == 20.5 + (k + i) % 3
    assert factors._dk(db, "nba", "1", "3134907", "points", NOW + timedelta(hours=1))[0] == cur["line"]
    m = db.one("SELECT m.bid FROM sports_game_line_state s JOIN sports_game_market_lines m ON m.id = s.last_id "
               "WHERE s.key = 'mkt:kalshi:M1'")
    assert abs(m["bid"] - (0.40 + 0.01 * ((k + i) % 3))) < 1e-9
    db.close()


def test_g8_existing_games_db_is_migrated_additively_and_old_digests_still_match(tmp_path):
    path = tmp_path / "games.db"
    from sports.books.store import DB
    old = DB(path=path, schema=store.SCHEMA)                  # pre-change shape (no late / provider-time columns)
    assert "late" not in old.columns("sports_game_prop_lines")
    r = prop(20.5, upd="2026-10-23T19:59:00Z")
    legacy_cols = tuple(c for c in store.PROP_COLS if c != "reported_prices")
    rid = old.insert("INSERT INTO sports_game_prop_lines " + store._ins(legacy_cols),
                     [dict(r, observed_at="2026-10-23T20:00:00Z").get(c) for c in legacy_cols])
    legacy = store.digest((r["line"], r["line_open"], r["status"], r["provider_updated_at"]))   # the old change rule
    old.execute("INSERT INTO sports_game_line_state (key, tbl, last_id, digest, first_seen_at, last_seen_at) VALUES "
                "(?,?,?,?,?,?)", ("prop:espn:p1", "sports_game_prop_lines", rid, legacy, "2026-10-23T20:00:00Z",
                                  "2026-10-23T20:00:00Z"))
    old.commit()
    old.close()
    db = store.connect(path, dsn="")
    assert "late" in db.columns("sports_game_prop_lines") and "last_provider_updated_at" in db.columns(
        "sports_game_line_state")
    assert store.save_prop_lines(db, [r], now=at(10)) == 0    # same line under the old digest: a sighting, no row
    assert store.save_prop_lines(db, [prop(20.5, upd="2026-10-23T20:09:00Z")], now=at(20)) == 0
    assert len(props(db)) == 1 and dk(db)[0] == 20.5
    db.close()
    store.connect(path, dsn="").close()


def test_g9_provider_time_and_observation_time_are_separate(gdb):
    store.save_prop_lines(gdb, [prop(20.5, upd="2026-10-23T19:00:00Z")], now=at(3))
    r = props(gdb)[0]
    assert r["observed_at"] == "2026-10-23T20:03:00Z" and r["provider_updated_at"] == "2026-10-23T19:00:00Z"
