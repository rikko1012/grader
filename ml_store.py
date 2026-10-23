#!/usr/bin/env python3
"""
ml_store.py - keeps the moneyline quote store (moneyline.py's ml_snapshots.db) writable without losing evidence.

Why (2026-10-23, mlstore-1): the store reached 627 MB and the old guard (file size > 500 MB -> SnapshotCapReached) paused
every Polymarket / Kalshi quote write. Nothing was deleted, but no new quote was saved.

What this module does (nothing here touches grading, the baseline rule or the shadow models)
  Archive    raw quote rows (ml_quotes) of UTC days older than RETAIN_DAYS move to gzip JSON-lines files next to the
             store (CS2_DATA_DIR/.cache/ml_archive/ml_quotes_<day>.jsonl.gz, one row per line with its original rowid and
             every column). Each file is written to a temp name, fsynced, re-read and checked (row count + sha256 of the
             lines), renamed, and only then - in ONE transaction - its manifest row (ml_archive) is inserted and exactly
             those rows leave ml_quotes (the delete trigger refuses any row whose day has no verified manifest row).
             A crash at any point leaves either the rows in the store (the day is simply redone) or the rows in a
             verified file with its manifest row - never neither. A day is archived once (idempotent).
  Kept       every row the moneyline evaluation reads stays in the live store (ml_eval.forward_rows and asks_at_cutoff
             give identical answers before and after - tested): per linked match the latest row(s) at or before each
             cutoff, the rows inside the last ASK window, the rows that hold the match's earliest stated start, all rows
             within CONTEXT_H hours before the cutoff, and every row of a match whose start is not SETTLED_DAYS old.
             Unlinked rows (never read by the evaluation) and in-play / long-before rows are archived.
  Read       iter_archived() / iter_all_quotes() / restore_db(): archived rows read back exactly (round-trip tested).
  Gaps       moneyline.write_snapshots records every interval > GAP_MIN_S with no saved quote for a platform in ml_gaps
             (immutable rows, shown in admin diagnostics). Missing quotes are never invented or backfilled.
  State      state() - memory only, refreshed by the background thread every STATE_EVERY s (cheap PRAGMA / index reads),
             never by a member request.

Bounded: one background thread, one connection, a pass archives at most MAX_DAYS_PER_PASS days, rows are streamed
(never all in memory - the keep set holds only kept rowids of settled linked matches). Off the request path; the write
lock is held only for the manifest insert + delete of one day.
"""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

RETAIN_DAYS = float(os.environ.get("ML_ARCHIVE_RETAIN_DAYS") or 7)      # raw quotes stay live this many days
SETTLED_DAYS = 3          # a linked match's rows move only once its (latest) earliest start is this old
CONTEXT_H = 12            # kept: every row of a linked match within 12 h before its cutoff
MAX_DAYS_PER_PASS = 4
ARCHIVE_EVERY = 6 * 3600  # seconds between archive passes (sooner when the store reports it is at its cap)
STATE_EVERY = 300         # seconds between state refreshes
FIRST_DELAY = 120         # after boot, before the first state read / pass
DISK_FLOOR_MB = float(os.environ.get("ML_DISK_FLOOR_MB") or 400)        # never write an archive file below this free disk
ARCHIVE_VERSION = "mlstore-1"

_STATE = {"checked_at": None}
_LOCK = threading.Lock()          # one archive pass at a time
_KICK = threading.Event()
_STOP = threading.Event()
_THREAD = {"t": None}


def _ml():
    import moneyline
    return moneyline


def archive_dir():
    return _ml().db_path().parent / "ml_archive"


def _now_iso(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _disk_free_mb(path):
    try:
        return shutil.disk_usage(str(path)).free / 1e6
    except OSError:
        return None


# ---------------------------------------------------------------- which rows the evaluation needs

def _keep_rowids(con, now_ts, before=None):
    """rowids of linked ml_quotes rows that stay live (see the module docstring). Unlinked rows are never kept. Only rowids
    received before `before` (an ISO day) are returned - newer rows are not archive candidates anyway (bounded memory)."""
    import ml_eval
    _ts = ml_eval._ts
    cut_s = ml_eval.CUTOFF_MIN * 60
    age_s = ml_eval.MAX_AGE_H * 3600
    allowed = ("series", "match", "game", "moneyline")
    fc_min = {}
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ml_forecasts'").fetchone():
        fc_min = {m: _ts(s) for m, s in con.execute("SELECT match_id, MIN(start) FROM ml_forecasts "
                                                    "WHERE match_id IS NOT NULL GROUP BY match_id")}
    q_min = dict(con.execute("SELECT match_id, MIN(start) FROM ml_quotes WHERE match_id IS NOT NULL GROUP BY match_id"))
    keep = set()
    settled_before = now_ts - SETTLED_DAYS * 86400

    def one(mid, rows):
        k = set()
        _one(mid, rows, k)
        keep.update(r[0] for r in rows if r[0] in k and (before is None or str(r[11]) < before))

    def _one(mid, rows, keep):
        qmin = q_min.get(mid)
        cands = [t for t in (_ts(qmin), fc_min.get(mid)) if t is not None]
        e_fwd = min(cands) if cands else None                       # forward_rows' earliest (per-table MIN, parsed)
        side_starts = [_ts(r[9]) for r in rows if r[3] in ("a", "b")]
        side_starts = [t for t in side_starts if t is not None]
        e_ask = min(side_starts) if side_starts else None           # asks_at_cutoff's earliest
        starts = [t for t in (e_fwd, e_ask) if t is not None]
        if not starts or max(starts) > settled_before:
            keep.update(r[0] for r in rows)                         # not settled long ago (or no start): all stay
            return
        # forward_rows: per cohort the latest row at or before the row's cutoff (ties all kept: scan order preserved)
        best = {}
        for r in rows:
            rid, _mid, src, side, ta, tb, p, ok, scope, st_raw, at_raw, rec_raw, plat = r
            st, at = _ts(st_raw), _ts(at_raw)
            if None in (ta, tb, st, at):
                continue
            if scope is not None and str(scope).lower() not in allowed:
                continue
            if str(side or "").lower() == "b":
                continue
            if p is None and (ok is None or ok):
                continue
            st2 = min(st, e_fwd) if e_fwd is not None else st
            if at > st2 - cut_s:
                continue
            k = str(src or "ml_quotes")
            b = best.get(k)
            if b is None or at > b[0]:
                best[k] = (at, [rid])
            elif at == b[0]:
                b[1].append(rid)
        for _at, ids in best.values():
            keep.update(ids)
        # asks_at_cutoff: per (platform, side) the latest row inside [cutoff - MAX_AGE_H, cutoff]
        if e_ask is not None:
            cut = e_ask - cut_s
            last = {}
            for r in rows:
                if r[3] not in ("a", "b"):
                    continue
                rec = _ts(r[11])
                if rec is None or rec > cut or rec < cut - age_s:
                    continue
                k = (r[12], r[3])
                b = last.get(k)
                if b is None or rec > b[0]:
                    last[k] = (rec, [r[0]])
                elif rec == b[0]:
                    b[1].append(r[0])
            for _rec, ids in last.values():
                keep.update(ids)
        # context: everything within CONTEXT_H before the earliest cutoff .. the latest cutoff
        lo, hi = min(starts) - cut_s - CONTEXT_H * 3600, max(starts) - cut_s
        for r in rows:
            rec = _ts(r[11])
            if rec is not None and lo <= rec <= hi:
                keep.add(r[0])
        # the match's earliest stated start must stay readable: one row holding the SQL MIN(start) (forward_rows) and one
        # a/b row holding the earliest parsed start (asks_at_cutoff), unless a kept row already does
        mine = [r for r in rows if r[0] in keep]
        if qmin is not None and not any(r[9] == qmin for r in mine):
            keep.add(next(r[0] for r in rows if r[9] == qmin))
        if e_ask is not None and not any(r[3] in ("a", "b") and _ts(r[9]) == e_ask for r in mine):
            keep.add(next(r[0] for r in rows if r[3] in ("a", "b") and _ts(r[9]) == e_ask))

    cur = con.execute("SELECT rowid, match_id, source, team_side, team_a, team_b, prob_a, usable, scope, start, "
                      "captured_at, received_ts, platform FROM ml_quotes WHERE match_id IS NOT NULL ORDER BY match_id")
    mid, buf = None, []
    for r in cur:
        if r[1] != mid:
            if buf:
                one(mid, buf)
            mid, buf = r[1], []
        buf.append(r)
    if buf:
        one(mid, buf)
    return keep


# ---------------------------------------------------------------- archive

def _day_after(day):
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def retain_from(now_ts):
    """The first UTC day (YYYY-MM-DD) that stays live: whole days older than RETAIN_DAYS may be archived."""
    return datetime.fromtimestamp(now_ts - RETAIN_DAYS * 86400, timezone.utc).date().isoformat()


def eligible_days(con, now_ts):
    """[(day, rows)] - UTC days before retain_from() that still hold archive candidates (temp.ml_keep must be filled).
    A day can be archived in several parts: rows of a match that had not started long enough ago stay live until a
    later pass (the next part of that day)."""
    return [(d, n) for d, n in con.execute(
        "SELECT substr(received_ts, 1, 10) AS d, COUNT(*) FROM ml_quotes WHERE received_ts < ? AND "
        "rowid NOT IN (SELECT r FROM temp.ml_keep) GROUP BY d ORDER BY d", (retain_from(now_ts),))
        if d and len(d) == 10 and d[4] == "-" and d[7] == "-"]


_WHERE = "received_ts >= ? AND received_ts < ? AND rowid NOT IN (SELECT r FROM temp.ml_keep)"


def _fsync(path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _read_lines_digest(path):
    h, n = hashlib.sha256(), 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            json.loads(line)
            h.update(line.encode("utf-8"))
            n += 1
    return n, h.hexdigest()


def _archive_day(con, day, now=None):
    """Archive one day's non-kept rows (temp.ml_keep must be filled). Returns the number of rows moved."""
    ml = _ml()
    lo, hi = day, _day_after(day)
    names = ["_rowid"] + [r[1] for r in con.execute("PRAGMA table_info(ml_quotes)")]
    stat = con.execute(f"SELECT COUNT(*), MIN(rowid), MAX(rowid), TOTAL(rowid) FROM ml_quotes WHERE {_WHERE}", (lo, hi)).fetchone()
    n_exp = stat[0]
    adir = archive_dir()
    if not n_exp:
        return 0
    adir.mkdir(parents=True, exist_ok=True)
    free = _disk_free_mb(adir)
    if free is not None and free < DISK_FLOOR_MB:
        raise RuntimeError(f"disk free {free:.0f} MB < floor {DISK_FLOOR_MB:.0f} MB - archive of {day} postponed")
    part = con.execute("SELECT COALESCE(MAX(part) + 1, 0) FROM ml_archive WHERE day = ?", (day,)).fetchone()[0]
    stem = f"ml_quotes_{day}" + (f".p{part}" if part else "")
    final = adir / f"{stem}.jsonl.gz"
    tmp = adir / f".{stem}.jsonl.gz.tmp"
    h, n = hashlib.sha256(), 0
    with gzip.open(tmp, "wt", encoding="utf-8", newline="\n", compresslevel=6) as fh:
        for row in con.execute(f"SELECT rowid, * FROM ml_quotes WHERE {_WHERE} ORDER BY rowid", (lo, hi)):
            line = json.dumps(dict(zip(names, row)), sort_keys=True, separators=(",", ":")) + "\n"
            fh.write(line)
            h.update(line.encode("utf-8"))
            n += 1
    _fsync(tmp)
    digest = h.hexdigest()
    n2, d2 = _read_lines_digest(tmp)
    if n != n_exp or n2 != n or d2 != digest:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"archive of {day} failed verification ({n_exp} expected, {n} written, {n2} re-read)")
    os.replace(tmp, final)
    _fsync(adir)
    fname, size = final.name, final.stat().st_size
    with ml._DB_LOCK:
        con.execute("BEGIN IMMEDIATE")
        try:
            again = con.execute(f"SELECT COUNT(*), MIN(rowid), MAX(rowid), TOTAL(rowid) FROM ml_quotes WHERE {_WHERE}",
                                (lo, hi)).fetchone()
            if tuple(again) != tuple(stat):
                raise RuntimeError(f"rows of {day} changed during the archive ({stat} -> {again}); nothing removed")
            con.execute("INSERT INTO ml_archive (day, part, file, rows, sha256, bytes, min_rowid, max_rowid, archived_at, "
                        "verified, version) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
                        (day, part, fname, n_exp, digest, size, stat[1], stat[2], _now_iso(now), ARCHIVE_VERSION))
            cur = con.execute(f"DELETE FROM ml_quotes WHERE {_WHERE}", (lo, hi))
            if cur.rowcount != n_exp:
                raise RuntimeError(f"delete of {day} touched {cur.rowcount} rows, expected {n_exp}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return n_exp


def archive_pass(now=None, max_days=MAX_DAYS_PER_PASS, time_budget_s=600):
    """One bounded pass: archive up to max_days eligible days. Idempotent (archived days are skipped). Returns a summary."""
    ml = _ml()
    now = now or datetime.now(timezone.utc)
    out = {"at": _now_iso(now), "days": [], "rows": 0, "error": None}
    if not ml.db_path().exists():
        return out
    if not _LOCK.acquire(blocking=False):
        out["error"] = "another pass is running"
        return out
    t0 = time.time()
    try:
        with ml._DB_LOCK:
            con = ml._connect()                                   # schema (tables, trigger) ensured
        con.isolation_level = None
        try:
            frm = retain_from(now.timestamp())
            if not con.execute("SELECT 1 FROM ml_quotes WHERE received_ts < ? LIMIT 1", (frm,)).fetchone():
                out["pending_days"] = 0
                return out
            keep = _keep_rowids(con, now.timestamp(), before=frm)
            out["kept_old_rows"] = len(keep)
            con.execute("CREATE TEMP TABLE IF NOT EXISTS ml_keep (r INTEGER PRIMARY KEY)")
            con.execute("DELETE FROM temp.ml_keep")
            it = iter(sorted(keep))
            while True:
                chunk = [(x,) for _, x in zip(range(5000), it)]
                if not chunk:
                    break
                con.executemany("INSERT INTO temp.ml_keep (r) VALUES (?)", chunk)
            del keep
            days = [d for d, _n in eligible_days(con, now.timestamp())]
            out["pending_days"] = len(days)
            for day in days[:max_days]:
                if time.time() - t0 > time_budget_s:
                    break
                n = _archive_day(con, day, now)
                out["days"].append(day)
                out["rows"] += n
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        finally:
            con.close()
    except Exception as e:          # noqa: BLE001 - reported in state(); the store keeps every row
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        _LOCK.release()
        out["took_s"] = round(time.time() - t0, 2)
    _STATE["last_pass"] = out
    return out


# ---------------------------------------------------------------- read back

def iter_archived(day=None, adir=None):
    """Archived rows (dicts with every ml_quotes column + '_rowid'), oldest day first; day='YYYY-MM-DD' for one day."""
    adir = Path(adir) if adir else archive_dir()
    files = sorted(adir.glob("ml_quotes_*.jsonl.gz")) if adir.exists() else []
    for f in files:
        if day and not f.name.startswith(f"ml_quotes_{day}."):
            continue
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                yield json.loads(line)


def verify_archive(db=None, adir=None):
    """Each manifest row's file exists and matches its row count and sha256. Returns {day: problem} (empty = all good)."""
    db = Path(db) if db else _ml().db_path()
    adir = Path(adir) if adir else db.parent / "ml_archive"
    bad = {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        for fname, rows, sha in con.execute("SELECT file, rows, sha256 FROM ml_archive ORDER BY day, part"):
            if not rows:
                continue
            p = adir / str(fname)
            if not p.exists():
                bad[fname] = "file missing"
                continue
            n, d = _read_lines_digest(p)
            if n != rows or d != sha:
                bad[fname] = f"mismatch ({n} rows, sha {d[:12]})"
    finally:
        con.close()
    return bad


def iter_all_quotes(db=None, adir=None):
    """Every quote row ever saved, archived rows first then the live ones (dicts, '_rowid' = original rowid)."""
    db = Path(db) if db else _ml().db_path()
    yield from iter_archived(adir=adir or db.parent / "ml_archive")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        cur = con.execute("SELECT rowid AS _rowid, * FROM ml_quotes ORDER BY rowid")
        names = [d[0] for d in cur.description]
        for row in cur:
            yield dict(zip(names, row))
    finally:
        con.close()


def restore_db(out_path, db=None, adir=None):
    """A complete research copy: ml_quotes with every live AND archived row (original rowids) plus ml_forecasts.
    Never touches the live store. Returns the number of quote rows written."""
    ml = _ml()
    db = Path(db) if db else ml.db_path()
    out = sqlite3.connect(str(out_path))
    try:
        out.executescript(ml._SCHEMA)
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        try:
            fcols = [r[1] for r in src.execute("PRAGMA table_info(ml_forecasts)")]
            have = {r[1] for r in out.execute("PRAGMA table_info(ml_forecasts)")}
            for c in fcols:
                if c not in have:
                    out.execute(f"ALTER TABLE ml_forecasts ADD COLUMN {c}")
            out.executemany(f"INSERT OR IGNORE INTO ml_forecasts ({','.join(fcols)}) VALUES ({','.join('?' * len(fcols))})",
                            src.execute(f"SELECT {','.join(fcols)} FROM ml_forecasts"))
        finally:
            src.close()
        n = 0
        batch = []
        for r in iter_all_quotes(db, adir):
            cols = sorted(r)
            batch.append((cols, tuple(r[c] for c in cols)))
            if len(batch) >= 2000:
                n += _flush(out, batch)
                batch = []
        n += _flush(out, batch)
        out.commit()
        return n
    finally:
        out.close()


def _flush(con, batch):
    for cols, vals in batch:
        names = ["rowid" if c == "_rowid" else c for c in cols]
        con.execute(f"INSERT OR IGNORE INTO ml_quotes ({','.join(names)}) VALUES ({','.join('?' * len(names))})", vals)
    return len(batch)


# ---------------------------------------------------------------- state (memory; refreshed off the request path)

def refresh_state():
    ml = _ml()
    p = ml.db_path()
    st = {"checked_at": _now_iso(), "path": str(p), "exists": p.exists(), "cap_mb": ml.SNAPSHOT_MAX_MB,
          "cap_measures": "live pages (file minus free pages) + WAL", "disk_floor_mb": ml.DISK_FLOOR_MB,
          "retain_days": RETAIN_DAYS, "archive_dir": str(archive_dir())}
    if p.exists():
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        try:
            st.update(ml.store_sizes(con))
            st["max_rowid"] = con.execute("SELECT MAX(rowid) FROM ml_quotes").fetchone()[0]
            last = {}
            for plat, rec in con.execute("SELECT platform, received_ts FROM ml_quotes ORDER BY received_ts DESC LIMIT 2000"):
                last.setdefault(plat, rec)
            st["last_received"] = last
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "ml_archive" in names:
                a = con.execute("SELECT COUNT(DISTINCT day), COUNT(*), SUM(rows), SUM(bytes), MIN(day), MAX(day) "
                                "FROM ml_archive").fetchone()
                st["archive"] = {"days": a[0], "files": a[1], "rows": int(a[2] or 0),
                                 "files_mb": round((a[3] or 0) / 1e6, 2), "first_day": a[4], "last_day": a[5]}
            if "ml_gaps" in names:
                cols = ("platform", "last_saved_before", "first_saved_after", "gap_s", "reason", "backfill", "recorded_at")
                st["gaps"] = [dict(zip(cols, r)) for r in con.execute(
                    f"SELECT {', '.join(cols)} FROM ml_gaps ORDER BY first_saved_after DESC LIMIT 20")]
        finally:
            con.close()
    st["disk_free_mb"] = round(_disk_free_mb(p.parent if p.parent.exists() else ".") or 0)
    st["last_pass"] = _STATE.get("last_pass")
    _STATE.clear()
    _STATE.update(st)
    return st


def state():
    """The last refreshed store state (memory only - safe on any request)."""
    return dict(_STATE)


def kick():
    """Ask the background thread for an archive pass soon (the writer calls this at its cap)."""
    _KICK.set()


def _loop():
    if _STOP.wait(FIRST_DELAY):
        return
    last_pass = 0.0
    while not _STOP.is_set():
        try:
            refresh_state()
        except Exception as e:      # noqa: BLE001
            _STATE["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        if _KICK.is_set() or time.time() - last_pass >= ARCHIVE_EVERY:
            _KICK.clear()
            archive_pass()
            last_pass = time.time()
            try:
                refresh_state()
            except Exception:       # noqa: BLE001
                pass
        _KICK.wait(STATE_EVERY)


def start():
    """Idempotent: one background archiver/state thread."""
    t = _THREAD["t"]
    if t and t.is_alive():
        return False
    _STOP.clear()
    t = threading.Thread(target=_loop, name="ml-store", daemon=True)
    _THREAD["t"] = t
    t.start()
    return True


def stop():
    _STOP.set()
    _KICK.set()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("cmd", choices=("state", "archive", "verify", "restore"))
    ap.add_argument("--out", help="restore: path of the research copy to write")
    a = ap.parse_args()
    if a.cmd == "state":
        print(json.dumps(refresh_state(), indent=1, default=str))
    elif a.cmd == "archive":
        print(json.dumps(archive_pass(), indent=1))
    elif a.cmd == "verify":
        print(json.dumps(verify_archive(), indent=1) or "ok")
    else:
        print(restore_db(a.out), "quote rows")
