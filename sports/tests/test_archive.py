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
