"""drain-1119: the archive pass drains a live-sized store.

- the part statements look rows up by rowid (NOT INDEXED), never through an offer-wide index;
- the streaming candidate scan (records_offer_seen in its own order, bounded batches, resume key) selects exactly the
  rows of the former window query - with NULL leagues, identities whose revisions changed league, late revisions and
  keys that do not follow time - and never cuts an identity, whatever the batch bounds;
- passes cut short by max_parts resume where they stopped and end with the same archive, every row reading back;
- progress is written to archive/records/last_pass.json (ops reads that file; the store only for manifest totals)."""
import hashlib
import random
import zlib
from datetime import datetime, timedelta, timezone

import pytest

from sports import archive, store
from sports.tests import helpers as h

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
CUT = (NOW - timedelta(hours=archive.AFTER_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
LG = ("nba", "nfl", "nhl", None)
PL = ("prizepicks", "underdog", "betr")


def _ts(e):
    return datetime.fromtimestamp(e, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _world(con, n_ident=260, seed=5):
    """Real offer payloads copied onto random-key identities: 1-5 revisions, last sightings spread over 10 days around
    the cutoff, ~10% NULL league, ~8% league-changing identities, ~10% late revisions."""
    for i in range(20):
        store.put(con, h.offer(oid=f"tpl:{i}", line=0.5 + i, observed_at="2026-10-10T20:00:00Z"))
    tpl = [tuple(r) for r in con.execute("SELECT payload, content_hash, schema_version FROM records WHERE kind='offer'")]
    rnd = random.Random(seed)
    now = NOW.timestamp()
    rows = []
    for ident in range(n_ident):
        plat = rnd.choice(PL)
        lg = rnd.choice(LG)
        sk = hashlib.md5(str(ident).encode()).hexdigest()[:12]
        revs = rnd.randint(1, 5)
        last = now - rnd.uniform(0, 10 * 86400)
        changer = revs > 1 and rnd.random() < 0.08
        for r in range(1, revs + 1):
            e = last - (revs - r) * 3600
            p, ch, sv = tpl[(ident + r) % len(tpl)]
            rl = (rnd.choice([x for x in LG if x != lg])) if (changer and r == revs) else lg
            rows.append((f"offer:{plat}:{sk}:r{r}:{ch[:10]}", plat, sk, r, ch, sv, rl, "nba:espn:1", "nba:player:espn:1",
                         "points", plat, 10.5, _ts(e), _ts(e), _ts(e), p, _ts(e + 60) if r == revs else None,
                         1 if (r > 1 and rnd.random() < 0.1) else 0))
    con.executemany("INSERT INTO records (kind, record_id, source, source_key, revision, content_hash, schema_version, "
                    "league, event_id, player_id, stat, platform, line, observed_at, known_at, stored_at, payload, "
                    "last_seen_at, seen_count, late) VALUES ('offer',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)", rows)
    con.commit()


def _reference(con):
    return sorted((rid, lg or "-", day) for rid, lg, day in con.execute(archive.CANDIDATES_SQL, (CUT, -1)))


def _all(con, tbl="records"):
    out = {}
    for r in con.execute(f"SELECT * FROM {tbl} WHERE kind='offer'").fetchall():
        d = dict(r)
        p = d["payload"]
        d["payload"] = zlib.decompress(p).decode() if isinstance(p, (bytes, bytearray)) else p
        out[d["rid"]] = d
    return out


@pytest.fixture
def world():
    con = store.connect()
    _world(con)
    yield con
    con.close()


def test_part_statements_look_up_rowids_only(world):
    world.execute("CREATE TEMP TABLE IF NOT EXISTS arch_ids (r INTEGER PRIMARY KEY)")
    for kinds in (("offer",), ("stat", "context")):
        for name, sql in archive.part_sql(kinds).items():
            assert "NOT INDEXED" in sql
            plan = " ".join(r[3] for r in world.execute("EXPLAIN QUERY PLAN " + sql))
            assert "INTEGER PRIMARY KEY" in plan and "INDEX" not in plan.replace("INTEGER PRIMARY KEY", ""), (name, plan)
    import inspect
    src = inspect.getsource(archive._archive_part)
    assert src.count('sql["stat"]') == 2 and src.count('sql["delete"]') == 1
    with pytest.raises(ValueError):
        archive.part_sql(("event",))


def test_scan_reads_the_index_in_its_own_order_without_a_sort(world):
    for sql, args in archive._scan_sql(None) + archive._scan_sql(("betr", None, "x")) + \
            archive._scan_sql(("betr", "nba", "x")):
        plan = " ".join(r[3] for r in world.execute("EXPLAIN QUERY PLAN " + sql, args))
        assert "COVERING INDEX records_offer_seen" in plan and "TEMP B-TREE" not in plan, plan


def test_unbounded_scan_selects_exactly_the_former_query(world):
    ref = _reference(world)
    assert len(ref) > 100
    assert any(lg == "-" for _r, lg, _d in ref)                                   # NULL league present
    b = archive.scan(world, CUT, None, emit_rows=10 ** 9, max_rows=10 ** 9)
    assert b["lap_done"] and b["cursor"] is None and b["changers"] > 0
    assert sorted(b["rows"]) == ref


@pytest.mark.parametrize("emit,maxrows", [(1, 1), (3, 7), (7, 2), (40, 50)])
def test_bounded_batches_resume_and_never_cut_an_identity(world, emit, maxrows):
    ref = _reference(world)
    ident_of = {r[0]: (r[1], r[2]) for r in world.execute("SELECT rid, source, source_key FROM records WHERE kind='offer'")}
    got, cursor, emitted, batches = [], None, set(), 0
    while True:
        b = archive.scan(world, CUT, cursor, emit_rows=emit, max_rows=maxrows, emitted=emitted)
        batches += 1
        idents = {ident_of[r[0]] for r in b["rows"]}
        for i in idents:                                                          # whole identities per batch
            n = world.execute("SELECT COUNT(*) FROM records WHERE kind='offer' AND source=? AND source_key=?", i).fetchone()[0]
            assert sum(1 for r in b["rows"] if ident_of[r[0]] == i) == n
        got += b["rows"]
        cursor = b["cursor"]
        if b["lap_done"]:
            break
        assert batches < 5000
    assert len(got) == len(set(got)) and sorted(got) == ref
    assert batches > 1


def test_resume_after_a_null_league_segment(world):
    first = world.execute("SELECT source, source_key FROM records WHERE kind='offer' AND league IS NULL "
                          "ORDER BY source, source_key LIMIT 1").fetchone()
    cur = (first[0], None, first[1])
    rest = archive.scan(world, CUT, cur, emit_rows=10 ** 9, max_rows=10 ** 9)
    full = archive.scan(world, CUT, None, emit_rows=10 ** 9, max_rows=10 ** 9)
    keys = [tuple(r) for r in world.execute(
        "SELECT source, league, source_key, rid FROM records WHERE kind='offer' ORDER BY source, league, source_key, "
        "late, revision")]
    after = {k[3] for k in keys if (k[0], k[1] is not None, k[1] or "", k[2]) > (cur[0], False, "", cur[2])}
    # every row emitted after the resume key is one the full scan emitted, and none before it (changers aside)
    full_set = {r[0] for r in full["rows"]}
    assert {r[0] for r in rest["rows"]} <= full_set
    assert {r[0] for r in rest["rows"] if r[0] in after} == {r for r in full_set if r in after} - {
        r[0] for r in rest["rows"] if r[0] not in after}


def test_cut_passes_resume_and_end_with_the_same_archive(world, monkeypatch):
    monkeypatch.setattr(archive, "PART_ROWS", 10)
    monkeypatch.setattr(archive, "SCAN_EMIT_ROWS", 25)
    monkeypatch.setattr(archive, "SCAN_MAX_ROWS", 60)
    monkeypatch.setattr(archive, "PENDING_MAX", 50)
    before = _all(world)
    ref = {r[0] for r in _reference(world)}
    passes = []
    for _ in range(200):
        out = archive.run_pass(world, now=NOW, max_parts=3, time_budget_s=600)
        assert out["error"] is None, out
        passes.append(out)
        lp = archive.last_pass()
        assert lp["finished"] and lp["rows_moved"] == out["rows"] and lp["parts_done"] == len(out["parts"])
        if not out["rows"] and not out["more_pending"]:
            break
    assert len(passes) > 3 and not passes[-1]["more_pending"]
    hot = set(_all(world))
    assert hot == set(before) - ref                                              # exactly the cold rows moved
    assert _all(world, "records_all") == before                                  # every column reads back
    assert archive.verify(world)["bad"] == []
    st = archive._read_json(archive._state_path())
    assert st["last_lap"]["scanned"] > 0


def test_one_pass_writes_full_parts_and_progress(world, monkeypatch):
    monkeypatch.setattr(archive, "PART_ROWS", 10)
    ref = {r[0] for r in _reference(world)}
    out = archive.run_pass(world, now=NOW, time_budget_s=600)
    assert out["error"] is None and out["rows"] == len(ref) and out["stopped"] == "done", out
    lp = archive.last_pass()
    for k in ("rows_moved", "parts_done", "scan_s", "rows_scanned", "cold_rows_found", "part_s_max", "part_s_mean",
              "remaining_estimate_rows", "freelist_pages", "stopped", "finished_at"):
        assert k in lp, k
    assert lp["remaining_estimate_rows"] == 0 and lp["cold_rows_found"] == len(ref)
    assert lp["part_rows_max"] == 10


def test_ops_shows_the_pass_progress_without_a_hot_count(world, monkeypatch):
    from sports import service
    monkeypatch.setattr(archive, "PART_ROWS", 10)
    out = archive.run_pass(world, now=NOW, time_budget_s=600)
    real = archive.stats
    calls = []

    def stats(con, hot=True):
        calls.append(hot)
        return real(con, hot=hot)
    monkeypatch.setattr(archive, "stats", stats)
    traced = []
    real_ro = service.web.store_ro

    def store_ro(**k):
        cm = real_ro(**k)

        class W:
            def __enter__(self):
                self.c = cm.__enter__()
                self.c.set_trace_callback(traced.append)
                return self.c

            def __exit__(self, *a):
                self.c.set_trace_callback(None)
                return cm.__exit__(*a)
        return W()
    monkeypatch.setattr(service.web, "store_ro", store_ro)
    service._OPS.update(t=0.0, v=None)
    fs = service.feed_state()
    assert calls == [False]                                                     # manifest totals only
    assert not any("COUNT(*) FROM records" in q for q in traced), traced         # never the hot offer COUNT
    lp = fs["archive"]["last_pass"]
    assert lp["rows_moved"] == out["rows"] and lp["finished"] and fs["archive"]["rows"] == out["rows"]
    # the sightings query failing (interrupted / busy) never hides the pass's own numbers
    monkeypatch.setattr(service.web, "store_ro", lambda **k: (_ for _ in ()).throw(RuntimeError("busy")))
    service._OPS.update(t=0.0, v=None)
    fs = service.feed_state()
    assert fs.get("error") == "RuntimeError" and fs["archive"]["last_pass"]["rows_moved"] == out["rows"]


def test_identity_lookups_use_the_identity_index(world, monkeypatch):
    """The league-changer lookup and the count probe are identity lookups - left to the planner, the lookup walked the
    source's whole records_offer_seen range per identity (50 GB read in one batch on a live-sized store)."""
    traced = []
    world.set_trace_callback(traced.append)
    archive._ident_rows(world, "betr", "x")
    archive._ident_count(world, "betr", "x")
    world.set_trace_callback(None)
    for sql in traced:
        if "FROM records" in sql:
            plan = " ".join(r[3] for r in world.execute("EXPLAIN QUERY PLAN " + sql))
            assert "records_ident" in plan and "records_offer_seen" not in plan, plan


def test_a_pass_cut_short_before_its_first_full_lap_still_estimates_what_is_left(world, monkeypatch):
    monkeypatch.setattr(archive, "PART_ROWS", 10)
    monkeypatch.setattr(archive, "SCAN_EMIT_ROWS", 20)
    ref = {r[0] for r in _reference(world)}
    out = archive.run_pass(world, now=NOW, time_budget_s=600, max_parts=2)
    assert out["stopped"] == "max parts" and out["rows"] == 20
    lp = archive.last_pass()
    assert lp["last_lap"] is None and lp["remaining_basis"].startswith("current lap")
    hot_offers = world.execute("SELECT COUNT(*) FROM records WHERE kind='offer'").fetchone()[0]
    assert lp["hot_rows_upper_bound"] >= hot_offers                              # an upper bound, never under
    assert 0 < lp["remaining_estimate_rows"] <= lp["hot_rows_upper_bound"]
    assert len(ref) - out["rows"] > 0
