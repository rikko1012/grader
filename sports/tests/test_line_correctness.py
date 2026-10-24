"""Line correctness of the canonical store (sports/LINE_CORRECTNESS.md, matrix rows C1-C9): revisions, duplicates,
delayed responses, withdrawal / reopening, one current revision under concurrent processes, the existing-database
migration + repair of pre-fix rows, restart behaviour, separate source and observation times."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

from sports import config, store
from sports.books import bridge
from sports.contracts import SideQuote
from sports.tests import helpers as h

ROOT = Path(__file__).resolve().parents[2]
PREFIX_STORE = Path(__file__).resolve().parent / "fixtures" / "prefix_store_506d1ae.py"


def t(m, s=0):
    return f"2026-10-23T20:{m:02d}:{s:02d}Z"


def at(rec, m, s=0, src=None):
    kw = dict(observed_at=t(m, s), known_at=t(m, s))
    if src is not None:
        kw.update(source_updated_at=src, source_time_unknown=False)
    return replace(rec, **kw)


@pytest.fixture
def db():
    con = store.connect()
    yield con
    con.close()


def current(con, platform="prizepicks", oid="pp:1"):
    rows = con.execute(f"SELECT * FROM records WHERE kind='offer' AND source=? AND source_key=? AND {store.CURRENT_SQL} "
                       "ORDER BY revision DESC", (platform, oid)).fetchall()
    return rows[0] if rows else None


def one_current_per_identity(con):
    dupes = con.execute("SELECT kind, source, source_key, revision, COUNT(*) n FROM records "
                        "GROUP BY kind, source, source_key, revision HAVING n > 1").fetchall()
    assert not dupes, [tuple(d) for d in dupes]


# ------------------------------------------------------------------------------------------- C1 A -> B -> A
def test_c1_a_b_a_makes_three_revisions_and_final_a_current(db):
    a = h.offer(line=20.5)
    b = replace(a, line=21.5)
    r = [store.put(db, at(a, 0)), store.put(db, at(b, 10)), store.put(db, at(a, 20))]
    assert [x["status"] for x in r] == ["inserted", "revision", "revision"]
    assert [x["revision"] for x in r] == [1, 2, 3]
    revs = store.revisions(db, "offer", "prizepicks", "pp:1")
    assert [m["revision"] for m, _ in revs] == [1, 2, 3] and [o.line for _, o in revs] == [20.5, 21.5, 20.5]
    assert all(m["late"] == 0 for m, _ in revs)
    (rid, o), = store.offers(db, league="nba")
    assert rid == r[2]["record_id"] and o.line == 20.5
    assert store.latest(db, "offer", "prizepicks", "pp:1").line == 20.5
    # point in time: each revision is current exactly in its own window
    assert store.latest(db, "offer", "prizepicks", "pp:1", as_of=t(5)).line == 20.5
    assert store.latest(db, "offer", "prizepicks", "pp:1", as_of=t(15)).line == 21.5
    assert store.offers(db, league="nba", as_of=t(15))[0][0] == r[1]["record_id"]
    # revision 1 was NOT counted again (the pre-fix bug moved its sighting columns)
    m1 = store.row_meta(db, r[0]["record_id"])
    assert m1["seen_count"] == 1 and m1["last_seen_at"] == t(0)


# ------------------------------------------------------------------------------------------- C2 A -> A
def test_c2_a_a_refreshes_freshness_without_a_line_change(db):
    a = h.offer(line=20.5)
    r1 = store.put(db, at(a, 0, src="2026-10-23T19:59:00Z"))
    r2 = store.put(db, at(a, 10, src="2026-10-23T20:09:00Z"))       # PrizePicks touches updated_at, same content
    r3 = store.put(db, at(a, 20, src="2026-10-23T20:09:00Z"))       # seen again, source time unchanged
    assert r1["status"] == "inserted" and r2["status"] == r3["status"] == "duplicate"
    assert r1["record_id"] == r2["record_id"] == r3["record_id"]
    assert len(store.revisions(db, "offer", "prizepicks", "pp:1")) == 1
    m = store.row_meta(db, r1["record_id"])
    assert m["seen_count"] == 3
    assert m["observed_at"] == t(0) and m["last_seen_at"] == t(20)                     # observation times
    assert m["source_updated_at"] == "2026-10-23T19:59:00Z"                            # source times, separately
    assert m["last_source_updated_at"] == "2026-10-23T20:09:00Z"
    assert m["known_at"] == t(0)                                                       # first knowledge kept
    # the payload stays the first observation (replay reads it; freshness reads the columns)
    assert store.get(db, r1["record_id"]).source_updated_at == "2026-10-23T19:59:00Z"


def test_c2_withdrawn_offer_seen_again_unchanged_stays_withdrawn_but_fresh(db):
    a = h.offer(line=20.5)
    store.put(db, at(a, 0))
    gone = store.put(db, store.withdrawn(at(a, 0), t(10)))
    again = store.put(db, store.withdrawn(at(a, 0), t(20)))
    assert gone["status"] == "revision" and again["status"] == "duplicate"
    assert store.row_meta(db, gone["record_id"])["last_seen_at"] == t(20)


# ------------------------------------------------------------------------------------------- C3 delayed response
def test_c3_older_source_time_cannot_overwrite_a_newer_quote(db):
    a = h.offer(line=20.5)
    b = replace(a, line=21.5)
    store.put(db, at(b, 10, src="2026-10-23T20:09:00Z"))
    late = store.put(db, at(a, 12, src="2026-10-23T20:05:00Z"))   # observed later, but the SOURCE says it is older
    assert late["status"] == "late"
    (rid, o), = store.offers(db, league="nba")
    assert o.line == 21.5
    revs = store.revisions(db, "offer", "prizepicks", "pp:1")
    assert [(m["revision"], m["late"]) for m, _ in revs] == [(1, 0), (2, 1)]          # kept as history
    assert store.latest(db, "offer", "prizepicks", "pp:1", as_of=t(30)).line == 21.5    # never current, at any time
    # a newer source time wins even when our observation of it is older (a stale cache answered first)
    c = store.put(db, at(replace(a, line=22.5), 11, src="2026-10-23T20:10:00Z"))
    assert c["status"] == "revision" and store.offers(db, league="nba")[0][1].line == 22.5


def test_c3_without_source_times_the_observation_time_orders(db):
    a = h.offer(line=20.5)
    store.put(db, at(a, 10))
    late = store.put(db, at(replace(a, line=19.5), 5))             # a slow response read at :05 stored after :10
    assert late["status"] == "late" and store.offers(db, league="nba")[0][1].line == 20.5
    # the next on-time read compares against the CURRENT revision (not the late row)
    assert store.put(db, at(a, 15))["status"] == "duplicate"
    nxt = store.put(db, at(replace(a, line=19.5), 20))
    assert nxt["status"] == "revision" and nxt["revision"] == 3
    assert store.offers(db, league="nba")[0][1].line == 19.5


def test_c3_delayed_duplicate_never_moves_freshness_backwards(db):
    a = h.offer(line=20.5)
    r = store.put(db, at(a, 10, src="2026-10-23T20:09:00Z"))
    store.put(db, at(a, 5, src="2026-10-23T20:04:00Z"))
    m = store.row_meta(db, r["record_id"])
    assert m["last_seen_at"] == t(10) and m["last_source_updated_at"] == "2026-10-23T20:09:00Z"


def test_c3_delayed_complete_read_cannot_withdraw_a_newer_offer(db):
    """bridge.store_game: a complete game read observed at :05 but stored after a :10 read that lists the offer must
    not mark it removed."""
    a = h.offer(platform="fanduel", oid="fd:1", line=20.5)
    store.put(db, at(a, 10))
    bridge.store_game(db, "nba", ["fanduel"], "nba:espn:1", type("B", (), {"records": [], "quarantine": []})(), t(5),
                      True)
    (rid, o), = store.offers(db, league="nba", platform="fanduel")
    assert o.availability == "open"
    assert [m["late"] for m, _ in store.revisions(db, "offer", "fanduel", "fd:1")] == [0, 1]


# ------------------------------------------------------------------------------------------- C4 withdraw / reopen
def test_c4_withdrawal_and_reopening_at_the_same_line_is_available_again(db):
    a = h.offer(platform="fanduel", oid="fd:1", line=20.5, sides=(SideQuote("over", True, "decimal", price=1.9),
                                                                  SideQuote("under", True, "decimal", price=1.9)))
    empty = type("B", (), {"records": [], "quarantine": []})()
    store.put(db, at(a, 0))
    res = bridge.store_game(db, "nba", ["fanduel"], "nba:espn:1", empty, t(10), True)     # complete read, not listed
    assert res["removed"] == 1
    assert store.offers(db, league="nba")[0][1].availability == "removed"
    w = store.revisions(db, "offer", "fanduel", "fd:1")[-1][0]
    assert w["source_updated_at"] is None                                                 # our time, not the book's
    back = type("B", (), {"records": [at(a, 20)], "quarantine": []})()
    res = bridge.store_game(db, "nba", ["fanduel"], "nba:espn:1", back, t(20), True)
    assert res["revision"] == 1
    (rid, o), = store.offers(db, league="nba")
    assert o.availability == "open" and o.line == 20.5
    assert [x.availability for _, x in store.revisions(db, "offer", "fanduel", "fd:1")] == ["open", "removed", "open"]
    from sports import capabilities
    assert capabilities.gate(o)[0] or "offer removed" not in capabilities.gate(o)[1]


def test_c4_pickem_board_withdrawal_and_reopen_through_the_collector(db):
    from sports import worker
    a = h.offer(line=20.5)
    worker._store_offers(db, "prizepicks", "nba", _b([at(a, 0)]), t(0), True)
    worker._store_offers(db, "prizepicks", "nba", _b([]), t(10), True)
    assert store.offers(db, league="nba")[0][1].availability == "removed"
    worker._store_offers(db, "prizepicks", "nba", _b([at(a, 20)]), t(20), True)
    assert store.offers(db, league="nba")[0][1].availability == "open"
    worker._store_offers(db, "prizepicks", "nba", _b([]), t(30), False)                  # failed read: nothing removed
    assert store.offers(db, league="nba")[0][1].availability == "open"


def _b(recs):
    from sports import adapters
    b = adapters.Batch()
    for r in recs:
        b.add(r)
    return b


# ------------------------------------------------------------------------------------------- C7 concurrency
WRITER = textwrap.dedent("""
    import sys
    from dataclasses import replace
    sys.path.insert(0, {root!r})
    from sports import config, store
    from pathlib import Path
    config.DATA_DIR = Path({data!r})
    from sports.tests import helpers as h
    i, n, rounds = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    con = store.connect()
    for k in range(rounds):
        sec = k * n + i                                    # a unique observation time per write across processes
        tt = "2026-10-23T%02d:%02d:%02dZ" % (20 + sec // 3600, (sec // 60) % 60, sec % 60)
        recs = []
        for oid in range(4):
            line = 20.5 if (k + i + oid) % 2 == 0 else 21.5
            recs.append(replace(h.offer(oid=f"pp:{{oid}}", line=line), observed_at=tt, known_at=tt))
        if k % 2:
            store.put_many(con, recs)
        else:
            for r in recs:
                store.put(con, r)
    con.close()
""")


def test_c7_concurrent_workers_cannot_create_conflicting_current_revisions(tmp_path):
    script = tmp_path / "writer.py"
    script.write_text(WRITER.format(root=str(ROOT), data=str(config.DATA_DIR)), encoding="utf-8")
    store.connect().close()                                   # created + migrated once, like the service at start
    n, rounds = 4, 40
    procs = [subprocess.Popen([sys.executable, str(script), str(i), str(n), str(rounds)], stdin=subprocess.DEVNULL)
             for i in range(n)]
    assert all(p.wait(timeout=240) == 0 for p in procs)
    con = store.connect()
    one_current_per_identity(con)
    # every identity has exactly one current row, and it is the content of the NEWEST observation of all processes
    last_sec = rounds * n - 1
    k, i = divmod(last_sec, n)
    for oid in range(4):
        cur = con.execute(f"SELECT * FROM records WHERE kind='offer' AND source_key=? AND {store.CURRENT_SQL} "
                          "ORDER BY revision DESC", (f"pp:{oid}",)).fetchall()
        assert cur
        assert cur[0]["line"] == (20.5 if (k + i + oid) % 2 == 0 else 21.5)
        assert len(store.offers(con, league="nba", player_id=h.offer().player_id)) == 4
    tot = con.execute("SELECT SUM(seen_count) FROM records WHERE kind='offer' AND late = 0").fetchone()[0]
    late = con.execute("SELECT COUNT(*) FROM records WHERE kind='offer' AND late = 1").fetchone()[0]
    assert tot + late == n * rounds * 4                       # every write accounted for: sighting, revision or late
    con.close()


# ------------------------------------------------------------------------------------------- C8 migration + restart
def _prefix_store():
    spec = importlib.util.spec_from_file_location("sports._prefix_store_506d1ae", PREFIX_STORE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_c8_existing_prefix_database_is_migrated_and_repaired_additively(tmp_path):
    old = _prefix_store()
    path = tmp_path / "old_canonical.db"
    con = old.connect(path)
    a = h.offer(line=20.5)
    b = replace(a, line=21.5)
    o2 = h.offer(oid="pp:2", line=5.5)
    old.put(con, at(a, 0))
    old.put(con, at(b, 10))
    old_dup = old.put(con, at(a, 20))                          # the bug: matched revision 1, counted it again
    assert old_dup["status"] == "duplicate" and old_dup["revision"] == 1
    old.put(con, at(o2, 0))
    old.put(con, at(replace(o2, availability="removed"), 10))
    old.put(con, at(o2, 20))                                   # reopened at the same line: stuck as removed
    old.put(con, at(replace(o2, line=9.5, offer_id="pp:3"), 0))     # an ordinary identity: untouched
    assert old.latest(con, "offer", "prizepicks", "pp:1").line == 21.5           # the wrong 'latest'
    assert old.latest(con, "offer", "prizepicks", "pp:2").availability == "removed"
    before = [tuple(r) for r in con.execute("SELECT * FROM records ORDER BY rid").fetchall()]
    assert con.execute("SELECT seen_count FROM records WHERE revision=1 AND source_key='pp:1'").fetchone()[0] == 2
    con.close()

    con = store.connect(path)                                  # the NEW code opens the old file: migrate + repair
    assert store._level(con) == store.MIGRATIONS[-1][0]
    rep = json.loads(store._meta(con, "repair_prefix_duplicates"))
    assert rep["identities"] == 2 and rep["repaired"] == 2 and rep["by_kind"] == {"offer": 2}
    assert store._meta(con, "revision_unique") == "on"
    after = con.execute("SELECT * FROM records ORDER BY rid").fetchall()
    width = len(before[0])
    assert [tuple(r)[:width] for r in after[:len(before)]] == before          # every old row kept byte for byte
    assert len(after) == len(before) + 2
    assert store.latest(con, "offer", "prizepicks", "pp:1").line == 20.5
    assert store.latest(con, "offer", "prizepicks", "pp:2").availability == "open"
    assert store.latest(con, "offer", "prizepicks", "pp:3").line == 9.5
    m = store.revisions(con, "offer", "prizepicks", "pp:1")
    assert [x["revision"] for x, _ in m] == [1, 2, 3] and m[2][0]["known_at"] == t(20)
    assert m[2][0]["origin"].startswith(store.REPAIR_ORIGIN)
    assert store.latest(con, "offer", "prizepicks", "pp:1", as_of=t(15)).line == 21.5     # history unchanged
    # after the migration the NEW rules apply to the old file
    assert store.put(con, at(a, 30))["status"] == "duplicate"
    assert store.put(con, at(b, 40))["revision"] == 4
    con.close()
    # restart: a second open does not repair again, nothing changes
    con = store.connect(path)
    assert con.execute("SELECT COUNT(*) FROM records").fetchone()[0] == len(before) + 3
    ro = store.connect(path, read_only=True)
    assert [o.line for _, o in store.offers(ro, league="nba", player_id=a.player_id) if o.offer_id == "pp:1"] == [21.5]
    ro.close()
    con.close()


def test_c8_read_only_reader_of_an_unmigrated_store_still_reads(tmp_path):
    old = _prefix_store()
    path = tmp_path / "old.db"
    con = old.connect(path)
    old.put(con, at(h.offer(line=20.5), 0))
    con.close()
    ro = store.connect(path, read_only=True)                  # the web before the worker migrated
    assert [o.line for _, o in store.offers(ro, league="nba")] == [20.5]
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO main.records (kind) VALUES ('x')")
    ro.close()


def test_c8_two_processes_migrating_at_once_migrate_once(tmp_path):
    old = _prefix_store()
    path = tmp_path / "old.db"
    con = old.connect(path)
    a = h.offer(line=20.5)
    old.put(con, at(a, 0))
    old.put(con, at(replace(a, line=21.5), 10))
    old.put(con, at(a, 20))
    con.close()
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); from sports import store; "
            f"c = store.connect({str(path)!r}); c.close()")
    procs = [subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.DEVNULL) for _ in range(4)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    con = store.connect(path)
    assert con.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 3               # repaired exactly once
    assert store.latest(con, "offer", "prizepicks", "pp:1").line == 20.5
    con.close()


KILLER = textwrap.dedent("""
    import os, sys
    from dataclasses import replace
    sys.path.insert(0, {root!r})
    from sports import config, store
    from pathlib import Path
    config.DATA_DIR = Path({data!r})
    from sports.tests import helpers as h
    con = store.connect()
    real = store._put
    n = [0]
    def dying(con, rec, known_at):
        n[0] += 1
        if n[0] == 30:
            os._exit(9)                                   # the worker is killed mid-batch (deploy / OOM)
        return real(con, rec, known_at)
    store._put = dying
    recs = [replace(h.offer(oid=f"pp:{{i}}", line=30.5), observed_at="2026-10-23T21:00:00Z",
                    known_at="2026-10-23T21:00:00Z") for i in range(50)]
    store.put_many(con, recs)
""")


def test_c8_worker_killed_mid_cycle_leaves_no_partial_batch_and_rerun_is_idempotent(tmp_path):
    con = store.connect()
    for i in range(50):
        store.put(con, at(h.offer(oid=f"pp:{i}", line=20.5), 0))
    con.close()
    script = tmp_path / "killer.py"
    script.write_text(KILLER.format(root=str(ROOT), data=str(config.DATA_DIR)), encoding="utf-8")
    assert subprocess.run([sys.executable, str(script)], stdin=subprocess.DEVNULL, timeout=120).returncode == 9
    con = store.connect()                                      # service restart: reads the last COMMITTED state
    assert {o.line for _, o in store.offers(con, league="nba")} == {20.5}
    assert con.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 50
    recs = [replace(h.offer(oid=f"pp:{i}", line=30.5), observed_at="2026-10-23T21:00:00Z",
                    known_at="2026-10-23T21:00:00Z") for i in range(50)]
    assert store.put_many(con, recs)["revision"] == 50         # the re-run cycle
    assert store.put_many(con, recs)["duplicate"] == 50        # and again: only sightings
    assert {o.line for _, o in store.offers(con, league="nba")} == {30.5}
    one_current_per_identity(con)
    con.close()


# ------------------------------------------------------------------------------------------- C9 times
def test_c9_source_and_observation_times_are_separate_columns(db):
    a = h.offer(line=20.5)
    r = store.put(db, at(a, 3, src="2026-10-23T19:00:00Z"))
    row = db.execute("SELECT observed_at, known_at, last_seen_at, source_updated_at, last_source_updated_at "
                     "FROM records WHERE record_id = ?", (r["record_id"],)).fetchone()
    assert tuple(row) == (t(3), t(3), t(3), "2026-10-23T19:00:00Z", "2026-10-23T19:00:00Z")
    unknown = store.put(db, at(h.offer(oid="pp:9"), 3))
    assert store.row_meta(db, unknown["record_id"])["source_updated_at"] is None    # never invented
