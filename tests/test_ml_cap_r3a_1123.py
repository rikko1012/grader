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
