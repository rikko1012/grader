"""sports/archive.py - compaction of cold offer rows with no data loss: every archived row reads back identical through
records_all / store functions, the pass is crash-safe at every step (exception AND a killed process), corruption is
detected, a returning identity continues its revision numbers."""
import json
import lzma
import os
import subprocess
import sys
import textwrap
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sports import archive, store
from sports.tests import helpers as h

T_OLD = "2026-10-10T20:00:00Z"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def _world(con):
    """Cold identities (last seen 2026-10-10/11) with revisions incl. a line that moves back (A->B->A, "~r" tag), and
    hot identities seen now. Two platforms, two events."""
    for line, t in ((20.5, "2026-10-10T20:00:00Z"), (21.5, "2026-10-10T21:00:00Z"), (20.5, "2026-10-11T01:00:00Z")):
        store.put(con, h.offer(oid="pp:1", line=line, observed_at=t))
    for i in range(2, 40):
        store.put(con, h.offer(oid=f"pp:{i}", line=10.5 + i, observed_at=T_OLD))
        store.put(con, h.offer(oid=f"pp:{i}", line=11.5 + i, observed_at="2026-10-10T22:00:00Z"))
    for i in range(40, 50):
        store.put(con, h.offer(oid=f"ud:{i}", platform="underdog", line=5.5, event_id="nba:espn:2", observed_at=T_OLD))
    for i in range(100, 110):                           # hot: seen at NOW
        store.put(con, h.offer(oid=f"pp:{i}", line=3.5, observed_at="2026-10-24T11:00:00Z"))
    store.put(con, h.event())


def _all_rows(con, tbl="records"):
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
    before = _all_rows(con)
    before_get = {r["record_id"]: store.get(con, r["record_id"]) for r in before.values()}
    before_hist = store.offers(con, league="nba", as_of="2026-10-10T21:30:00Z", limit=100000)
    before_ev = store.offers(con, event_id="nba:espn:2", limit=100000)
    before_rev = store.revisions(con, "offer", "prizepicks", "pp:1")
    yield con, before, before_get, before_hist, before_ev, before_rev
    con.close()


def test_archived_rows_read_back_identical_everywhere(world):
    con, before, before_get, before_hist, before_ev, before_rev = world
    out = archive.run_pass(con, now=NOW)
    assert out["error"] is None and out["rows"] == len(before) - 10, out       # every cold row archived
    con.close()
    for ro in (False, True):
        c = store.connect(read_only=ro)
        hot = _all_rows(c)
        assert set(hot) == {r for r, d in before.items() if d["source_key"] in {f"pp:{i}" for i in range(100, 110)}}
        assert _all_rows(c, "records_all") == before                            # EVERY column of every row, exact
        for rid, rec in before_get.items():
            assert store.get(c, rid) == rec                                     # record lookups span the archive
        assert store.offers(c, league="nba", as_of="2026-10-10T21:30:00Z", limit=100000) == before_hist
        assert store.offers(c, event_id="nba:espn:2", limit=100000) == before_ev
        assert store.revisions(c, "offer", "prizepicks", "pp:1") == before_rev
        assert [o.line for _, o in store.revisions(c, "offer", "prizepicks", "pp:1")] == [20.5, 21.5, 20.5]
        cur = store.offers(c, league="nba", limit=100000)                      # CURRENT listing = the hot window
        assert {o.offer_id for _, o in cur} == {f"pp:{i}" for i in range(100, 110)}
        c.close()
    st = archive.stats(store.connect(read_only=True))
    assert st["files"] >= 1 and st["rows"] == len(before) - 10
    assert archive.verify()["bad"] == []


def test_a_second_pass_is_idempotent(world):
    con = world[0]
    archive.run_pass(con, now=NOW)
    again = archive.run_pass(con, now=NOW)
    assert again["rows"] == 0 and again["pending_parts"] == 0 and again["error"] is None


@pytest.mark.parametrize("step", ["written", "renamed", "manifest", "deleted"])
def test_an_exception_at_any_step_keeps_every_row_hot_exactly_once(world, monkeypatch, step):
    con, before = world[0], world[1]

    def boom(name, **kw):
        if name == step:
            raise RuntimeError(f"crash at {name}")
    monkeypatch.setattr(archive, "_STEP_HOOK", boom)
    out = archive.run_pass(con, now=NOW)
    assert out["error"] and f"crash at {step}" in out["error"]
    assert _all_rows(con) == before and _all_rows(con, "records_all") == before          # nothing lost, nothing doubled
    assert con.execute("SELECT COUNT(*) FROM archive_files").fetchone()[0] == 0
    monkeypatch.setattr(archive, "_STEP_HOOK", None)
    out = archive.run_pass(con, now=NOW)                                         # the next pass completes
    assert out["error"] is None and _all_rows(con, "records_all") == before
    assert archive.verify()["bad"] == []
    assert not list(archive.archive_dir().glob(".*.tmp"))


_KILL = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, ROOT)
    os.environ["SPORTS_DATA_DIR"] = DATA
    from sports import archive, config, store
    from pathlib import Path
    config.DATA_DIR = Path(DATA)
    from datetime import datetime, timezone
    def hook(name, **kw):
        if name == STEP:
            os._exit(9)                                  # the process dies here (no cleanup, no rollback call)
    archive._STEP_HOOK = hook
    archive.run_pass(store.connect(Path(DATA) / "canonical.db"), now=datetime(2026, 10, 24, 12, tzinfo=timezone.utc))
""")


@pytest.mark.parametrize("step", ["written", "renamed", "manifest", "deleted"])
def test_a_killed_process_at_any_step_loses_nothing(world, step, tmp_path):
    con, before = world[0], world[1]
    con.close()
    from sports import config
    data = str(config.DATA_DIR)
    root = str(Path(__file__).resolve().parents[2])
    r = subprocess.run([sys.executable, "-c", f"ROOT={root!r}\nDATA={data!r}\nSTEP={step!r}\n" + _KILL],
                       capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert r.returncode == 9, r.stderr[-1500:]
    c = store.connect()
    assert _all_rows(c, "records_all") == before                                # after the crash: every row, once
    out = archive.run_pass(c, now=NOW)                                           # recovery pass
    assert out["error"] is None, out
    assert _all_rows(c, "records_all") == before and archive.verify(c)["bad"] == []
    c.close()


def test_a_bad_payload_is_refused_before_anything_is_removed(world, monkeypatch):
    con, before = world[0], world[1]
    real = archive._line

    def corrupt(row):
        d = json.loads(real(row))
        d["payload"] = d["payload"].replace("20.5", "99.5")
        return json.dumps(d, separators=(",", ":"), sort_keys=True).encode()
    monkeypatch.setattr(archive, "_line", corrupt)
    out = archive.run_pass(con, now=NOW)
    assert "do not re-derive their content hash" in out["error"] and _all_rows(con) == before


def test_corruption_is_detected_never_silent(world):
    con = world[0]
    archive.run_pass(con, now=NOW)
    f = next(archive.archive_dir().glob("offer-*.jsonl.xz"))
    raw = lzma.decompress(f.read_bytes()).replace(b"21.5", b"22.5", 1)
    f.write_bytes(lzma.compress(raw))
    bad = archive.verify(con)["bad"]
    assert bad and bad[0]["file"] == f.name and bad[0]["sha_ok"] is False


def test_a_returning_identity_continues_its_revision_numbers(world):
    con = world[0]
    archive.run_pass(con, now=NOW)
    assert con.execute("SELECT COUNT(*) FROM records WHERE source_key='pp:1'").fetchone()[0] == 0   # archived
    res = store.put(con, h.offer(oid="pp:1", line=30.5, observed_at="2026-10-24T11:30:00Z"))
    assert res["status"] == "revision" and res["revision"] == 4                  # r1..r3 are in the archive
    res2 = store.put(con, h.offer(oid="pp:2", line=13.5, observed_at="2026-10-24T11:30:00Z"))   # = archived latest
    assert res2["revision"] == 3
    tag = con.execute("SELECT content_hash FROM records WHERE record_id=?", (res2["record_id"],)).fetchone()[0]
    assert tag.endswith("~r3")
    ids = [r[0] for r in con.execute("SELECT record_id FROM records_all WHERE source_key IN ('pp:1','pp:2')")]
    assert len(ids) == len(set(ids)) == 7                                         # no record id ever collides
    assert [o.line for _, o in store.revisions(con, "offer", "prizepicks", "pp:1")] == [20.5, 21.5, 20.5, 30.5]


def test_without_archive_tables_records_all_is_the_hot_table():
    con = store.connect()
    store.put(con, h.offer())
    assert con.execute("SELECT COUNT(*) FROM records_all").fetchone()[0] == 1
    con.close()
    ro = store.connect(read_only=True)
    assert ro.execute("SELECT COUNT(*) FROM records_all").fetchone()[0] == 1
    assert archive.row_by_record_id(ro, "offer:x:y:r1:abc") is None


def test_compaction_shrinks_what_cold_rows_cost_on_disk(world):
    """Sizes: the archive (file + slim index rows) costs a fraction of the hot rows it replaces."""
    con, before = world[0], world[1]
    hot_payload = sum(len(zlib.compress(d["payload"].encode())) for d in before.values())
    archive.run_pass(con, now=NOW)
    files = sum(f.stat().st_size for f in archive.archive_dir().glob("*.xz"))
    assert files < hot_payload / 3


# ------------------------------------------------------------- bounded selection, hard deadline, stage notes
_OLD_SELECTION = (
    "WITH ident AS (SELECT source, source_key, MAX(COALESCE(last_seen_at, observed_at, known_at)) AS seen "
    "FROM records WHERE kind = 'offer' GROUP BY source, source_key HAVING seen < ?) "
    "SELECT r.rid, r.league, substr(i.seen, 1, 10) AS day FROM records r JOIN ident i "
    "ON r.source = i.source AND r.source_key = i.source_key WHERE r.kind = 'offer' ORDER BY r.league, day, "
    "r.source, r.source_key, r.revision")


def _old_parts(con, now=NOW):
    cutoff = (now - timedelta(hours=archive.AFTER_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = {}
    for rid, league, day in con.execute(_OLD_SELECTION, (cutoff,)):
        parts.setdefault((league or "-", day), []).append(rid)
    return [(lg, day, rids[i:i + archive.PART_ROWS]) for (lg, day), rids in parts.items()
            for i in range(0, len(rids), archive.PART_ROWS)]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_bounded_selection_equals_the_old_full_query(world, monkeypatch):
    con = world[0]
    # an identity listed under two leagues: cold in one, fresh in the other -> hot as a whole (the old rule)
    store.put(con, h.offer(oid="pp:x", league="nba", line=1.5, observed_at=T_OLD))
    store.put(con, h.offer(oid="pp:x", league="nfl", line=2.5, observed_at="2026-10-24T11:00:00Z"))
    # ... and one that is cold in both: every row goes, each under its own league
    store.put(con, h.offer(oid="pp:y", league="nba", line=1.5, observed_at=T_OLD))
    store.put(con, h.offer(oid="pp:y", league="nfl", line=2.5, observed_at="2026-10-10T23:00:00Z"))
    old = _old_parts(con)
    assert archive.candidates(con, NOW) == old and len(old) >= 3
    monkeypatch.setattr(archive, "PART_ROWS", 7)
    assert archive.candidates(con, NOW) == _old_parts(con)
    chosen = {r for _, _, rids in old for r in rids}
    x = {r[0] for r in con.execute("SELECT rid FROM records WHERE source_key = 'pp:x'")}
    y = {r[0] for r in con.execute("SELECT rid FROM records WHERE source_key = 'pp:y'")}
    assert not x & chosen and y <= chosen and len(y) == 2


def test_selection_scan_reads_the_covering_index_without_a_sort(world):
    con = world[0]
    for where, args in (("", ()), (" AND (source, league, source_key) > (?, ?, ?)", ("prizepicks", "nba", "pp:2"))):
        plan = " | ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + archive._SCAN % where, args).fetchall())
        assert "COVERING INDEX records_offer_seen" in plan and "TEMP B-TREE" not in plan, plan


def test_small_passes_resume_wrap_and_archive_everything(world, monkeypatch):
    con, before = world[0], world[1]
    monkeypatch.setattr(archive, "PART_ROWS", 9)
    cursors, rows = [], 0
    for _ in range(40):
        out = archive.run_pass(con, now=NOW, max_parts=1)
        assert out["error"] is None and out["rows"] <= 9
        rows += out["rows"]
        cursors.append(json.loads((archive.archive_dir().parent / "scan_cursor.json").read_text())["cursor"])
        if out["stopped"] == "done" and not out["rows"]:
            break
    assert rows == len(before) - 10 and out["stopped"] == "done"
    moved = [c for c in cursors if c]
    assert len({tuple(c) for c in moved}) > 1 and cursors[-1] is None           # it advanced, then wrapped
    assert _all_rows(con, "records_all") == before and archive.verify(con)["bad"] == []


def test_slow_setup_does_not_eat_the_parts_budget_and_checkpoint_is_skipped_near_the_deadline(world, monkeypatch):
    con, before = world[0], world[1]
    clock = Clock()
    real = archive.select_cold

    def slow_scan(*a, **kw):                           # setup + scan take 730 s: longer than the parts budget
        got = real(*a, **kw)
        clock.t += 730
        return got
    monkeypatch.setattr(archive, "select_cold", slow_scan)
    monkeypatch.setattr(archive, "PART_ROWS", 20)
    out = archive.run_pass(con, now=NOW, time_budget_s=600, deadline_s=780, clock=clock)
    assert out["error"] is None and len(out["parts"]) >= 1 and out["rows"] == len(before) - 10
    assert out["checkpoint"] == "skipped"                                        # under 60 s were left
    assert _all_rows(con, "records_all") == before


def test_deadline_during_the_scan_is_a_bounded_stop_not_an_error(world):
    con, before = world[0], world[1]
    clock = Clock()
    seen = []

    def ticking():                                     # every look at the clock costs 100 s
        clock.t += 100
        seen.append(clock.t)
        return clock.t
    out = archive.run_pass(con, now=NOW, deadline_s=780, clock=ticking)
    assert out["error"] is None and out["stopped"] == "deadline", out
    assert _all_rows(con) == before and _all_rows(con, "records_all") == before  # every row readable exactly once
    assert con.execute("SELECT COUNT(*) FROM archive_files").fetchone()[0] == 0
    assert not list(archive.archive_dir().glob("*")) if archive.archive_dir().exists() else True
    out = archive.run_pass(con, now=NOW, deadline_s=780)                         # the next pass completes
    assert out["error"] is None and out["stopped"] == "done" and out["rows"] == len(before) - 10


@pytest.mark.parametrize("step", ["written", "renamed", "manifest"])
def test_deadline_reached_inside_a_part_keeps_every_row_hot(world, monkeypatch, step):
    con, before = world[0], world[1]
    clock = Clock()

    def late(name, **kw):
        if name == step:
            clock.t = 10_000
    monkeypatch.setattr(archive, "_STEP_HOOK", late)
    out = archive.run_pass(con, now=NOW, deadline_s=780, clock=clock)
    if step == "manifest":      # inside the short commit transaction no check runs: the part completes, then stops
        assert out["error"] is None and out["stopped"] == "deadline" and len(out["parts"]) == 1
    else:
        assert out["error"] is None and out["stopped"] == "deadline" and out["rows"] == 0, out
        assert _all_rows(con) == before and con.execute("SELECT COUNT(*) FROM archive_files").fetchone()[0] == 0
        assert not list(archive.archive_dir().glob(".*.tmp")) and not list(archive.archive_dir().glob("offer-*"))
    assert _all_rows(con, "records_all") == before
    monkeypatch.setattr(archive, "_STEP_HOOK", None)
    out = archive.run_pass(con, now=NOW, deadline_s=780)
    assert out["error"] is None and _all_rows(con, "records_all") == before and archive.verify(con)["bad"] == []
    assert set(_all_rows(con)) == {r for r, d in before.items() if d["source_key"] in {f"pp:{i}" for i in range(100, 110)}}


def test_a_running_statement_is_interrupted_at_the_deadline(world):
    con, before = world[0], world[1]
    clock = Clock()
    con.create_function("tick", 0, lambda: setattr(clock, "t", 10_000) or 0)

    def run(con_, league, day, rids, now_s):           # a statement that runs past the deadline mid-transaction
        con_.execute("BEGIN IMMEDIATE")
        try:
            con_.execute("DELETE FROM records WHERE kind = 'offer'")
            con_.execute("WITH RECURSIVE n(i) AS (SELECT tick() UNION ALL SELECT i + 1 FROM n WHERE i < 5000000) "
                         "SELECT COUNT(*) FROM n").fetchone()
            con_.execute("COMMIT")
        except BaseException:
            if con_.in_transaction:
                con_.execute("ROLLBACK")
            raise
    import unittest.mock as mock
    with mock.patch.object(archive, "_archive_part", run):
        out = archive.run_pass(con, now=NOW, deadline_s=780, clock=clock)
    assert out["error"] is None and out["stopped"] == "deadline", out
    assert _all_rows(con) == before


def test_last_pass_records_stage_timings_and_a_kill_leaves_the_stage(world, monkeypatch):
    con, before = world[0], world[1]
    monkeypatch.setattr(archive, "PART_ROWS", 30)

    class Killed(BaseException):
        pass

    def kill(name, **kw):
        if name == "written":
            raise Killed()
    monkeypatch.setattr(archive, "_STEP_HOOK", kill)
    real = archive._write_json
    notes = []
    monkeypatch.setattr(archive, "_write_json", lambda path, value: (notes.append(dict(value)), real(path, value)))
    with pytest.raises(Killed):
        archive.run_pass(con, now=NOW)
    died = [n for n in notes if n.get("stage") == "part"][-1]                   # what a SIGKILL would leave on disk
    assert "stopped" not in died and died["selected_rows"] == len(before) - 10 and "scan_s" in died
    monkeypatch.setattr(archive, "_STEP_HOOK", None)
    out = archive.run_pass(con, now=NOW)
    note = archive.last_pass()
    assert note["stage"] == "end" and note["stopped"] == "done" == out["stopped"] and note["error"] is None
    assert [p["file"] for p in note["parts"]] == out["parts"] and all("s" in p and p["rows"] for p in note["parts"])
    assert note["rows"] == len(before) - 10 and note["scanned_rows"] >= note["selected_rows"]
    assert archive.stats(con)["last_pass"] == note


def test_cli_exits_zero_on_a_bounded_stop(monkeypatch, capsys):
    monkeypatch.setenv("SPORTS_INGEST", "1")
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"error": None, "stopped": "deadline", "parts": []}
    monkeypatch.setattr(archive, "run_pass", fake)
    assert archive.main(["pass", "--time-budget-s", "540", "--deadline-s", "780"]) == 0
    assert seen == {"max_parts": 40, "time_budget_s": 540, "deadline_s": 780}
    monkeypatch.setattr(archive, "run_pass", lambda **kw: {"error": "RuntimeError: x"})
    assert archive.main(["pass"]) == 1
