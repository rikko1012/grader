"""storage-1123 R3a: the moneyline quote store cap default is 1500 MB (was 900) so quote writes resume with no row removed.
The env var still overrides; a store between the old and the new cap writes again and keeps every existing row; above the
new cap (or under the disk floor) writes still pause."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import moneyline
from tests.test_moneyline import data_dir, refreshed  # noqa: F401  (fixture + helper)

ROOT = Path(__file__).resolve().parents[1]


def _cap_in_fresh_process(tmp_path, env_value):
    env = {k: v for k, v in os.environ.items() if k != "ML_SNAPSHOT_MAX_MB"}
    env["CS2_DATA_DIR"] = str(tmp_path)
    if env_value is not None:
        env["ML_SNAPSHOT_MAX_MB"] = env_value
    out = subprocess.run([sys.executable, "-c", "import moneyline; print(moneyline.SNAPSHOT_MAX_MB)"], cwd=str(ROOT),
                         env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return float(out.stdout.strip().splitlines()[-1])


def test_default_cap_is_1500_and_the_env_var_still_overrides(tmp_path):
    assert _cap_in_fresh_process(tmp_path, None) == 1500.0
    assert _cap_in_fresh_process(tmp_path, "") == 1500.0
    assert _cap_in_fresh_process(tmp_path, "900") == 900.0


def _sizes(live_mb, disk_free_mb):
    return lambda con: {"file_mb": live_mb, "live_mb": live_mb, "free_pages_mb": 0.0, "wal_mb": 0.0,
                        "disk_free_mb": disk_free_mb}


def _rows(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT rowid, * FROM ml_quotes ORDER BY rowid").fetchall()
    finally:
        con.close()


def test_a_store_between_the_old_and_new_cap_writes_again_and_keeps_every_row(data_dir, monkeypatch):  # noqa: F811
    monkeypatch.setattr(moneyline, "SNAPSHOT_MAX_MB", 1500.0)
    refreshed()                                                  # a first set of quotes, under any cap
    db = moneyline.db_path()
    before = _rows(db)
    assert before
    monkeypatch.setattr(moneyline, "store_sizes", _sizes(1072.0, 1525.0))   # the live store on 2026-11-23 after the trim
    monkeypatch.setattr(moneyline, "_LAST", {"path": None, "map": {}})
    rows = [dict(r, received_ts="2026-12-01T00:00:00.000000Z") for r in _first_rows(db)]
    n = moneyline.write_snapshots(rows, force=True)
    after = _rows(db)
    assert n == len(rows) > 0
    assert after[:len(before)] == before                         # every existing row, unchanged, at its rowid
    assert len(after) == len(before) + n


def _first_rows(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM ml_quotes ORDER BY rowid LIMIT 3")]
    finally:
        con.close()


@pytest.mark.parametrize("live_mb,free_mb", [(1500.5, 1525.0), (1072.0, 399.0)])
def test_above_the_new_cap_or_under_the_disk_floor_writes_still_pause(data_dir, monkeypatch, live_mb, free_mb):  # noqa: F811
    monkeypatch.setattr(moneyline, "SNAPSHOT_MAX_MB", 1500.0)
    monkeypatch.setattr(moneyline, "DISK_FLOOR_MB", 400.0)
    refreshed()
    db = moneyline.db_path()
    before = _rows(db)
    monkeypatch.setattr(moneyline, "store_sizes", _sizes(live_mb, free_mb))
    monkeypatch.setattr(moneyline, "_kick_archive", lambda: None)
    rows = [dict(r, received_ts="2026-12-01T00:00:00.000000Z") for r in _first_rows(db)]
    with pytest.raises(moneyline.SnapshotCapReached):
        moneyline.write_snapshots(rows, force=True)
    assert _rows(db) == before


# ------------------------------------------------------------------ resumed writes never reuse an archived rowid
# Production on 2026-11-23: writes stopped 2026-11-10; the archive then moved the newest (unlinked) rows, rowids
# 1,446,395..1,446,399, while a linked row of an unsettled match (1,446,394) stayed live. Without a guard the first resumed
# insert gets MAX(live rowid) + 1 = an archived rowid.

from datetime import datetime, timedelta, timezone  # noqa: E402

import ml_store  # noqa: E402
from tests.test_ml_store import COLS, iso, store  # noqa: E402,F401  (fixture)

DAY = datetime(2026, 11, 10, 1, 0, tzinfo=timezone.utc)
LATER = DAY + timedelta(days=13)                     # the resume, 13 days later


def _quote(i, at, linked, start=None):
    r = {c: None for c in COLS}
    r.update(platform="polymarket", market_id=f"m{i}", outcome_id=f"m{i}-a", received_ts=iso(at), team_side="a",
             team_name="A", depth="{}", source="polymarket", scope="series", captured_at=iso(at), usable=0,
             start=iso(start or at + timedelta(days=1)))
    if linked:
        r.update(match_id="hltv:1", link_status="linked", team_a="A", team_b="B")
    else:
        r.update(link_status="unlinked")
    return r


def _stalled_store():
    """Linked rows of a match that starts after LATER (kept live), then newer unlinked rows (archivable) on top."""
    with moneyline._DB_LOCK:
        con = moneyline._connect()
    sql = f"INSERT INTO ml_quotes ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})"
    far = LATER + timedelta(days=2)
    rows = [_quote(i, DAY + timedelta(minutes=i), True, start=far) for i in range(5)]
    rows += [_quote(100 + i, DAY + timedelta(minutes=10 + i), False) for i in range(5)]
    con.executemany(sql, [tuple(r.get(c) for c in COLS) for r in rows])
    con.commit()
    con.close()
    out = ml_store.archive_pass(now=LATER, max_days=100)
    assert out["error"] is None and out["rows"] == 5, out
    con = sqlite3.connect(moneyline.db_path())
    try:
        live_max = con.execute("SELECT MAX(rowid) FROM ml_quotes").fetchone()[0]
        arch_max = con.execute("SELECT MAX(max_rowid) FROM ml_archive").fetchone()[0]
    finally:
        con.close()
    assert (live_max, arch_max) == (5, 10)               # the production shape: newest rowids only in the archive
    return live_max, arch_max


def _all_rowids():
    return [int(r["_rowid"]) for r in ml_store.iter_all_quotes()]


def test_resumed_writes_start_above_the_archived_rowids_and_restore_keeps_every_row(store, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(moneyline, "SNAPSHOT_MAX_MB", 1500.0)
    _live_max, arch_max = _stalled_store()
    before = sorted(_all_rowids())
    archived = {int(r["_rowid"]): r for r in ml_store.iter_archived()}
    new = [_quote(200 + i, LATER + timedelta(minutes=i), False) for i in range(4)]
    assert moneyline.write_snapshots(new, force=True) == 4
    con = sqlite3.connect(moneyline.db_path())
    try:
        new_ids = [r[0] for r in con.execute("SELECT rowid FROM ml_quotes WHERE market_id LIKE 'm2__' ORDER BY rowid")]
    finally:
        con.close()
    assert new_ids == [arch_max + 1, arch_max + 2, arch_max + 3, arch_max + 4]
    ids = _all_rowids()
    assert len(ids) == len(set(ids)) == len(before) + 4                     # one row per rowid, nothing lost
    assert {int(r["_rowid"]): r for r in ml_store.iter_archived()} == archived
    n = ml_store.restore_db(tmp_path / "copy.db")
    con = sqlite3.connect(tmp_path / "copy.db")
    try:
        assert con.execute("SELECT COUNT(*) FROM ml_quotes").fetchone()[0] == n == len(before) + 4
    finally:
        con.close()
    # a second write follows on (no explicit rowid needed any more)
    assert moneyline.write_snapshots([_quote(300, LATER + timedelta(hours=1), False)], force=True) == 1
    ids = _all_rowids()
    assert len(ids) == len(set(ids)) and max(ids) == arch_max + 5


def test_an_ignored_duplicate_does_not_use_up_the_explicit_rowid(store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(moneyline, "SNAPSHOT_MAX_MB", 1500.0)
    _live_max, arch_max = _stalled_store()
    con = sqlite3.connect(moneyline.db_path())
    con.row_factory = sqlite3.Row
    try:
        dup = dict(con.execute("SELECT * FROM ml_quotes ORDER BY rowid LIMIT 1").fetchone())   # already stored: ignored
    finally:
        con.close()
    fresh = _quote(400, LATER, False)
    assert moneyline.write_snapshots([dup, fresh], force=True) == 1
    ids = _all_rowids()
    assert len(ids) == len(set(ids)) and max(ids) == arch_max + 1


def test_no_archive_means_no_change(data_dir, monkeypatch):  # noqa: F811
    monkeypatch.setattr(moneyline, "SNAPSHOT_MAX_MB", 1500.0)
    refreshed()
    con = sqlite3.connect(moneyline.db_path())
    try:
        ids = [r[0] for r in con.execute("SELECT rowid FROM ml_quotes ORDER BY rowid")]
    finally:
        con.close()
    assert ids == list(range(1, len(ids) + 1))
