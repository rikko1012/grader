"""Canonical store: additive migrations, dedupe, revisions, immutability, point-in-time reads, leakage guard."""
import sqlite3
from dataclasses import replace

import pytest

from sports import store
from sports.contracts import ContractError
from sports.tests import helpers as h


@pytest.fixture
def db():
    con = store.connect()
    yield con
    con.close()


def test_migrations_are_additive_only():
    for _, stmts in store.MIGRATIONS:
        for s in stmts:
            low = " ".join(s.lower().split())
            assert low.startswith(("create table if not exists", "create index if not exists",
                                   "create unique index if not exists", "alter table")), s[:60]
            assert "alter table" not in low or " add column " in low
            assert not any(w in low for w in store.FORBIDDEN_IN_MIGRATIONS), s[:60]


def test_migrate_is_idempotent(db):
    assert store.migrate(db) == store.MIGRATIONS[-1][0]
    assert store.migrate(db) == store.MIGRATIONS[-1][0]


def test_current_listing_fetches_selected_payloads_by_primary_key(db):
    a = h.offer()
    first = store.put(db, a)
    last = store.put(db, replace(a, line=a.line + 1, observed_at="2026-10-23T21:00:00Z",
                                 known_at="2026-10-23T21:00:00Z"))
    statements = []
    db.set_trace_callback(statements.append)
    current = store.offers(db, league=a.league, platform=a.platform)
    db.set_trace_callback(None)
    assert [rid for rid, _ in current] == [last["record_id"]]
    query = next(s for s in statements if s.startswith("SELECT r.* FROM (SELECT rid"))
    plan = " ".join(str(tuple(r)) for r in db.execute("EXPLAIN QUERY PLAN " + query))
    assert "records_current_league" in plan
    assert "SEARCH r USING INTEGER PRIMARY KEY" in plan
    assert store.offers(db, league=a.league, platform=a.platform, as_of=a.known_at)[0][0] == first["record_id"]


def test_feed_freshness_uses_small_index_and_preserves_sightings(db):
    a = h.offer()
    store.put(db, a)
    later = "2026-10-23T21:00:00Z"
    store.put(db, replace(a, observed_at=later, known_at=later))
    rows = dict(db.execute(store.FEED_SIGHTINGS_SQL).fetchall())
    assert rows[a.platform] == later
    plan = " ".join(str(tuple(r)) for r in db.execute("EXPLAIN QUERY PLAN " + store.FEED_SIGHTINGS_SQL))
    assert "records_feed_seen" in plan
    assert "TEMP B-TREE" not in plan
    # An old database migration adds only an index; historical payload bytes stay unchanged.
    payloads = db.execute("SELECT record_id, payload FROM records ORDER BY rid").fetchall()
    db.execute("DROP INDEX records_feed_seen")
    db.execute("UPDATE meta SET v='5' WHERE k='schema_level'")
    db.commit()
    store.migrate(db)
    assert db.execute("SELECT record_id, payload FROM records ORDER BY rid").fetchall() == payloads
    assert store.migrate(db) == store.MIGRATIONS[-1][0]


def test_feed_freshness_seeks_books_instead_of_scanning_history(db):
    for i in range(240):
        store.put(db, h.offer(platform=('prizepicks', 'underdog', 'kalshi')[i % 3], oid=f'feed-{i}'))
    # Preserve even an imported legacy row with an absent platform.
    db.execute("UPDATE records SET platform=NULL WHERE rid=(SELECT min(rid) FROM records)")
    db.commit()
    old = "SELECT platform, max(coalesce(last_seen_at, observed_at)) FROM records WHERE kind='offer' GROUP BY platform"

    def measured(sql):
        steps = [0]
        def tick():
            steps[0] += 1
            return 0
        db.set_progress_handler(tick, 1)
        try:
            rows = dict(db.execute(sql).fetchall())
        finally:
            db.set_progress_handler(None, 0)
        return rows, steps[0]

    expected, scan_steps = measured(old)
    actual, seek_steps = measured(store.FEED_SIGHTINGS_SQL)
    assert actual == expected
    assert seek_steps < scan_steps / 2, (seek_steps, scan_steps)


def test_dedupe_by_identity_and_hash_and_sighting_counter(db):
    a = h.offer()
    r1 = store.put(db, a)
    r2 = store.put(db, replace(a, observed_at="2026-10-23T20:05:00Z", known_at="2026-10-23T20:05:00Z"))
    assert r1["status"] == "inserted" and r2["status"] == "duplicate" and r1["record_id"] == r2["record_id"]
    meta = store.row_meta(db, r1["record_id"])
    assert meta["seen_count"] == 2 and meta["last_seen_at"] == "2026-10-23T20:05:00Z"
    assert meta["known_at"] == h.T0                                     # first observation kept


def test_revisions_are_preserved_never_overwritten(db):
    a = h.offer(line=20.5)
    store.put(db, a)
    b = replace(a, line=21.5, observed_at="2026-10-23T21:00:00Z", known_at="2026-10-23T21:00:00Z")
    r = store.put(db, b)
    assert r["status"] == "revision" and r["revision"] == 2
    revs = store.revisions(db, "offer", "prizepicks", "pp:1")
    assert [x[1].line for x in revs] == [20.5, 21.5]
    assert store.latest(db, "offer", "prizepicks", "pp:1").line == 21.5
    assert store.latest(db, "offer", "prizepicks", "pp:1", as_of="2026-10-23T20:30:00Z").line == 20.5
    got = store.offers(db, league="nba", as_of="2026-10-23T20:30:00Z")
    assert [o.line for _, o in got] == [20.5]


def test_a_line_that_moves_back_is_the_latest_revision(db):
    """A -> B -> A: the third sighting is a NEW revision (before, it matched revision 1 as a duplicate and "latest"
    kept reporting B, a line that no longer existed)."""
    a = h.offer(line=20.5)
    store.put(db, a)
    store.put(db, replace(a, line=21.5, observed_at="2026-10-23T21:00:00Z", known_at="2026-10-23T21:00:00Z"))
    r = store.put(db, replace(a, observed_at="2026-10-23T22:00:00Z", known_at="2026-10-23T22:00:00Z"))
    assert r["status"] == "revision" and r["revision"] == 3
    assert store.latest(db, "offer", "prizepicks", "pp:1").line == 20.5
    assert [o.line for _, o in store.offers(db, league="nba")] == [20.5]
    assert [x[1].line for x in store.revisions(db, "offer", "prizepicks", "pp:1")] == [20.5, 21.5, 20.5]
    assert store.latest(db, "offer", "prizepicks", "pp:1", as_of="2026-10-23T21:30:00Z").line == 21.5
    again = store.put(db, replace(a, observed_at="2026-10-23T22:05:00Z", known_at="2026-10-23T22:05:00Z"))
    assert again["status"] == "duplicate" and again["revision"] == 3          # same as the latest: a sighting only


def test_forecasts_are_immutable(db):
    rid = store.put(db, h.offer(line=20.0))["record_id"]
    f = h.forecast(offer_snapshot_ids=(rid,))
    assert store.put(db, f)["status"] == "inserted"
    assert store.put(db, f)["status"] == "duplicate"
    fields = {k: v for k, v in f.to_dict().items() if k != "kind"}
    from sports.contracts import Forecast
    forged = Forecast(**(fields | {"reason_codes": ("x",)}))
    with pytest.raises(ContractError):
        store.put(db, forged)                                          # same id, other content: refused


def test_invalid_records_are_quarantined_not_stored(db):
    bad = h.offer(line=float("nan"))
    res = store.put_many(db, [bad, h.offer(oid="pp:2")])
    assert res["invalid"] == 1 and res["inserted"] == 1
    q = store.quarantine_counts(db)
    assert q and q[0]["reason"].startswith("invalid")
    store.quarantine(db, "offer", "prizepicks", "pp:9", "scope_unknown", {"a": 1})
    store.quarantine(db, "offer", "prizepicks", "pp:9", "scope_unknown", {"a": 1})      # idempotent
    assert sum(x["n"] for x in store.quarantine_counts(db) if x["reason"] == "scope_unknown") == 1


def test_history_enforces_chronology_and_known_at(db):
    old = h.event(eid="nba:espn:0", start="2026-10-21T23:00:00Z", status="final")
    later = h.event(eid="nba:espn:2", start="2026-10-25T23:00:00Z")
    for e in (old, later):
        store.put(db, e)
    store.put(db, h.stat(eid="nba:espn:0", value=30.0, observed_at="2026-10-22T03:00:00Z"))
    store.put(db, h.stat(eid="nba:espn:2", value=11.0, observed_at="2026-10-26T03:00:00Z"))
    got = store.history(db, "nba:player:espn:3134907", "pts", before="2026-10-23T23:00:00Z")
    assert [x[1].value for x in got] == [30.0]                         # the later game never leaks in
    # a correction known only AFTER the forecast time is not used
    store.put(db, h.stat(eid="nba:espn:0", value=31.0, observed_at="2026-10-24T05:00:00Z"))
    got = store.history(db, "nba:player:espn:3134907", "pts", before="2026-10-23T23:00:00Z")
    assert [x[1].value for x in got] == [30.0]
    got = store.history(db, "nba:player:espn:3134907", "pts", before="2026-10-25T00:00:00Z")
    assert [x[1].value for x in got] == [31.0]
    with pytest.raises(store.LeakageError):
        store.history(db, "nba:player:espn:3134907", "pts", before="2026-10-23T00:00:00Z",
                      as_of="2026-10-24T00:00:00Z")


def test_assert_known_before(db):
    rid = store.put(db, h.offer(observed_at="2026-10-23T21:00:00Z"))["record_id"]
    assert store.assert_known_before(db, [rid], "2026-10-23T21:00:00Z")
    with pytest.raises(store.LeakageError):
        store.assert_known_before(db, [rid], "2026-10-23T20:59:59Z")
    with pytest.raises(store.LeakageError):
        store.assert_known_before(db, ["nope"], "2026-10-23T22:00:00Z")


def test_read_only_connection_cannot_write(db):
    store.put(db, h.offer())
    ro = store.connect(read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("DELETE FROM records")
        assert len(store.offers(ro, league="nba")) == 1
    finally:
        ro.close()


def test_read_only_on_missing_store_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        store.connect(tmp_path / "none.db", read_only=True)
