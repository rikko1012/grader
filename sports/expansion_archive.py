"""Verified, lossless archive files for the soccer / tennis side stores (sg-stability, 2026-11-20).

Three side stores grew towards hard caps that stop the board or the grades: quotes.db (quote revisions, 250 MB),
learning.db (shadow predictions, 100 MB) and forecasts.db (published model forecasts, 128 MB). Nothing ever moved
their cold rows anywhere. This module is the ONE helper they share (the competition result stores keep their own
sports/archive.py machinery, expansion_compact.py).

Protocol per part (the same order as sports/archive.py's offer archive):
  rows -> one JSON line each (every column verbatim) -> <dir>/.<name>.tmp (lzma) -> fsync -> re-read: rows + sha256
  must match -> rename -> fsync dir -> re-read again -> ONE transaction: manifest row + the caller's change to exactly
  those rows (DELETE, or a slimming UPDATE that keeps the columns live readers use). A crash anywhere before COMMIT
  leaves every row in the store; a final file without a manifest row is an orphan and is removed on the next run
  (its rows are still in the store). Nothing is ever lost.

Manifest: table exp_archive in the store itself (file, tbl, rows, sha256, bytes, first/last key, created_at,
verified_at). verify() re-reads every listed file; read() yields archived rows back (walk-forward tests, audits).

Budgets: live_bytes() counts the bytes the store's pages really hold (page_count - freelist_count) plus the WAL - a
DELETE frees pages that new inserts reuse, so once cold rows move out the file stops growing without a VACUUM (a
VACUUM of a 200 MB store does not fit a 90 s quotes run, and is never run on the board path).
"""
from __future__ import annotations

import hashlib
import json
import lzma
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PRESET = 2                      # small parts (<= a few MB): 2 MiB dictionary, ~17 MiB to compress
DISK_FLOOR_MB = 2048            # never archive onto an almost full disk (the rows simply stay in the store)
TS = "%Y-%m-%dT%H:%M:%SZ"
SCHEMA = ("CREATE TABLE IF NOT EXISTS exp_archive (id INTEGER PRIMARY KEY, file TEXT UNIQUE NOT NULL, tbl TEXT NOT NULL, "
          "rows INTEGER NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, first_key TEXT, last_key TEXT, "
          "created_at TEXT NOT NULL, verified_at TEXT)")


def now_s():
    return datetime.now(timezone.utc).strftime(TS)


def ensure(con):
    con.execute(SCHEMA)


def live_bytes(path):
    """Bytes held by live pages of the SQLite file at `path` plus its WAL / journal. Falls back to the file sizes when
    the file cannot be opened (never raises)."""
    path = Path(path)
    extra = 0
    for suffix in ("-wal", "-journal"):
        try:
            extra += Path(str(path) + suffix).stat().st_size
        except OSError:
            pass
    if not path.exists():
        return extra
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            pc = con.execute("PRAGMA page_count").fetchone()[0]
            fl = con.execute("PRAGMA freelist_count").fetchone()[0]
            ps = con.execute("PRAGMA page_size").fetchone()[0]
        finally:
            con.close()
        return (pc - fl) * ps + extra
    except sqlite3.Error:
        try:
            return path.stat().st_size + extra
        except OSError:
            return extra


def _fsync(path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass                    # directories cannot be opened on Windows; Linux (Render) fsyncs them


def _line(cols, row):
    return json.dumps({c: row[i] for i, c in enumerate(cols)}, separators=(",", ":"), sort_keys=True,
                      allow_nan=False).encode("utf-8")


def _read(path):
    """-> (rows, sha256 over the lines). Streams the file line by line (bounded memory)."""
    h, n = hashlib.sha256(), 0
    with lzma.open(path, "rb") as f:
        for raw in f:
            if not raw.strip():
                continue
            if not raw.endswith(b"\n"):
                raw += b"\n"
            h.update(raw)
            n += 1
    return n, h.hexdigest()


def remove_leftovers(con, adir, prefix):
    """Stale temp files are removed; a final file with no manifest row (a crash between rename and commit - its rows
    are still in the store and are archived again later) is MOVED to <adir>/unlisted/, never deleted: if the store
    itself were ever recreated, that file could hold the only copy of its rows. -> files cleared from the archive dir."""
    adir = Path(adir)
    if not adir.is_dir():
        return 0
    ensure(con)
    listed = {r[0] for r in con.execute("SELECT file FROM exp_archive")}
    n = 0
    for f in adir.glob(f".{prefix}-*.tmp"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    stamp = now_s().replace(":", "")
    for f in [f for f in adir.glob(f"{prefix}-*.jsonl.xz") if f.name not in listed]:
        try:
            q = adir / "unlisted"
            q.mkdir(exist_ok=True)
            os.replace(f, q / f"{f.name}.{stamp}")
            n += 1
        except OSError:
            pass
    return n


def next_name(con, adir, prefix):
    """The file name the next archive_part(con, adir, prefix, ...) call writes (a slimmed row can name its file)."""
    ensure(con)
    seq = con.execute("SELECT COUNT(*) FROM exp_archive WHERE file LIKE ?", (f"{prefix}-%",)).fetchone()[0]
    while True:
        name = f"{prefix}-{seq:06d}.jsonl.xz"
        if not (Path(adir) / name).exists() and not con.execute("SELECT 1 FROM exp_archive WHERE file=?",
                                                                 (name,)).fetchone():
            return name
        seq += 1


def archive_part(con, adir, prefix, tbl, cols, rows, change_sql, change_params, key_col=None, preset=PRESET):
    """Archive `rows` (sequence of tuples in `cols` order, already read from table `tbl`) into one verified file
    <adir>/<prefix>-<seq>.jsonl.xz, then in ONE transaction add the manifest row and run change_sql (executemany with
    change_params: the DELETE / slimming UPDATE of exactly these rows). The change must touch exactly len(rows) rows,
    else everything is rolled back. -> {"file", "rows", "bytes"}. Raises (store unchanged) on any failure."""
    if not rows:
        return {"file": None, "rows": 0, "bytes": 0}
    adir = Path(adir)
    adir.mkdir(parents=True, exist_ok=True)
    try:
        free = shutil.disk_usage(adir).free / 1e6
    except OSError:
        free = None
    if free is not None and free < DISK_FLOOR_MB:
        raise RuntimeError(f"disk free {free:.0f} MB < floor {DISK_FLOOR_MB} MB - archive postponed")
    name = next_name(con, adir, prefix)
    final, tmp = adir / name, adir / f".{name}.tmp"
    h = hashlib.sha256()
    try:
        with lzma.open(tmp, "wb", preset=preset) as f:
            for r in rows:
                ln = _line(cols, r) + b"\n"
                h.update(ln)
                f.write(ln)
        _fsync(tmp)
        digest = h.hexdigest()
        n, d2 = _read(tmp)
        if n != len(rows) or d2 != digest:
            raise RuntimeError(f"archive {name} failed verification ({len(rows)} expected, {n} re-read)")
        os.replace(tmp, final)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    _fsync(adir)
    n3, d3 = _read(final)
    if n3 != len(rows) or d3 != digest:
        raise RuntimeError(f"archive {name} re-read after the rename does not match; nothing changed")
    size = final.stat().st_size
    ki = cols.index(key_col) if key_col else None
    keys = [str(r[ki]) for r in rows] if ki is not None else [None]
    if con.in_transaction:
        con.commit()                # the archive's change is its own transaction
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO exp_archive (file, tbl, rows, sha256, bytes, first_key, last_key, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)", (name, tbl, len(rows), digest, size, min(keys) if ki is not None else None,
                                                  max(keys) if ki is not None else None, now_s()))
        before = con.total_changes
        con.executemany(change_sql, change_params)
        touched = con.total_changes - before
        if touched != len(rows):
            raise RuntimeError(f"archive {name}: change touched {touched} rows, expected {len(rows)}")
        con.execute("COMMIT")
    except BaseException:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        try:
            final.unlink()      # its rows are all still in the store
        except OSError:
            pass
        raise
    return {"file": name, "rows": len(rows), "bytes": size}


def verify(con, adir, stamp=True):
    """Re-read every manifest-listed file: row count and sha256 must match. Stamps verified_at when all are good.
    -> {"files", "rows", "bad": [...], "mb"}."""
    adir = Path(adir)
    out = {"files": 0, "rows": 0, "bad": [], "mb": 0.0}
    try:
        listed = con.execute("SELECT file, rows, sha256, bytes FROM exp_archive ORDER BY id").fetchall()
    except sqlite3.Error:
        return out
    for name, rows, sha, size in listed:
        try:
            n, d = _read(adir / name)
            if n != rows or d != sha:
                out["bad"].append({"file": name, "rows": [rows, n], "sha_ok": d == sha})
        except (OSError, EOFError, lzma.LZMAError) as e:
            out["bad"].append({"file": name, "error": f"{type(e).__name__}: {str(e)[:120]}"})
        out["files"] += 1
        out["rows"] += rows
        out["mb"] += (size or 0) / 1048576
    out["mb"] = round(out["mb"], 2)
    if stamp and not out["bad"] and listed:
        con.execute("UPDATE exp_archive SET verified_at = ?", (now_s(),))
        con.commit()
    return out


def read(con, adir, tbl=None):
    """Every archived row (dict per JSON line), oldest file first - for audits and walk-forward tests (never a member
    request)."""
    adir = Path(adir)
    q, args = "SELECT file FROM exp_archive", ()
    if tbl:
        q, args = q + " WHERE tbl = ?", (tbl,)
    for (name,) in con.execute(q + " ORDER BY id", args).fetchall():
        with lzma.open(adir / name, "rb") as f:
            for raw in f:
                if raw.strip():
                    yield json.loads(raw)


def stats(con):
    try:
        f, r, b, v = con.execute("SELECT COUNT(*), TOTAL(rows), TOTAL(bytes), MIN(verified_at) FROM exp_archive").fetchone()
    except sqlite3.Error:
        return {"files": 0, "rows": 0, "mb": 0.0, "oldest_verified_at": None}
    return {"files": f, "rows": int(r), "mb": round(b / 1048576, 2), "oldest_verified_at": v}
