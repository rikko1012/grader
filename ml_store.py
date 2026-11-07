#!/usr/bin/env python3
"""
ml_store.py - keeps the moneyline quote store (moneyline.py's ml_snapshots.db) writable without losing evidence.

Why (2026-10-23, mlstore-1): the store reached 627 MB and the old guard (file size > 500 MB -> SnapshotCapReached) paused
every Polymarket / Kalshi quote write. Nothing was deleted, but no new quote was saved.

What this module does (nothing here touches grading, the baseline rule or the shadow models)
  Archive    raw quote rows (ml_quotes) of UTC days older than RETAIN_DAYS move to gzip JSON-lines files next to the
             store (CS2_DATA_DIR/.cache/ml_archive/ml_quotes_<day>.jsonl.gz, one row per line with its original rowid and
             every column). Each file is written to a temp name, fsynced, re-read and checked (row count + sha256 of the
             lines), renamed, re-read AGAIN from its final name (count + sha256 + the rowids it holds), and only then - in
             ONE transaction - its manifest row (ml_archive) is inserted and exactly the rowids listed in that verified
             file leave ml_quotes (the delete trigger also refuses any row whose day has no verified manifest row).
             Crash points (tests kill the process at each): after the temp write / after the rename / after the manifest
             insert / after the delete before COMMIT - every one leaves the rows in the store (a temp file is removed by
             the next pass, a final file without a manifest row is never read and is overwritten when the day is redone,
             the open transaction rolls back) and the next pass completes the day. A day is archived once (idempotent).
  Kept       every row the moneyline evaluation reads stays in the live store (ml_eval.forward_rows and asks_at_cutoff
             give identical answers before and after - tested): per linked match the latest row(s) at or before each
             cutoff, the rows inside the last ASK window, the rows that hold the match's earliest stated start, all rows
             within CONTEXT_H hours before the cutoff, and every row of a match whose start is not SETTLED_DAYS old.
             Unlinked rows (never read by the evaluation) and in-play / long-before rows are archived.
  Read       iter_quotes() / quotes(): ONE historical lookup (match / platform / market, received_ts range) over the
             archive AND the live store - callers cannot tell where a row lives; only manifest-listed files are read, only
             the archive days inside the range are opened. write_copy() / restore_db() build an evaluation / research
             copy from it; match_eval() runs ml_eval.forward_rows + asks_at_cutoff for one match from the live rows and
             through the unified lookup (identical answers - tested, and shown on production by /admin/ml_store.json).
  Verify     verify_archive(): every manifest-listed file re-read against its rows + sha256 (daily in the background).
  Alerts     compute_alerts() / alerts(): writes stopped while the source is ok, live size > 80% of the cap, free disk
             < 1.5x the floor, archive pass error, archive integrity, state not refreshed - thresholds in the alerts
             section below; admin pages and /health ops only (never a member label); each transition logged once.
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
import heapq
import json
import os
import shutil
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# raw quotes stay live this many days. 4 (was 7) since 2026-10-29: at ~110 MB/day the 7-day window filled the live
# store towards its 900 MB cap, where moneyline.write_snapshots pauses every quote write. Nothing is lost by archiving
# sooner: the rows the evaluation reads stay live (keep set), and iter_quotes() reads archive + live as one store.
RETAIN_DAYS = float(os.environ.get("ML_ARCHIVE_RETAIN_DAYS") or 3)  # 3 since 2026-11-02: 846 of 900 MB with Oct 28 empty (outage)
SETTLED_DAYS = 3          # a linked match's rows move only once its (latest) earliest start is this old
CONTEXT_H = 12            # kept: every row of a linked match within 12 h before its cutoff
MAX_DAYS_PER_PASS = 4
ARCHIVE_EVERY = 6 * 3600  # seconds between archive passes (sooner when the store reports it is at its cap)
STATE_EVERY = 300         # seconds between state refreshes
FIRST_DELAY = 120         # after boot, before the first state read / pass
DISK_FLOOR_MB = float(os.environ.get("ML_DISK_FLOOR_MB") or 400)        # never write an archive file below this free disk
ARCHIVE_VERSION = "mlstore-2"      # mlstore-2: re-read of the final file + rowid-listed delete

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
        e_side = min(side_starts) if side_starts else None          # earliest start an a/b row states
        # asks_at_cutoff's earliest = min(a/b rows' starts, forward_rows' earliest) since bug sweep 2026-11-06 #21
        has_side = any(r[3] in ("a", "b") for r in rows)
        e_ask = min((t for t in (e_side, e_fwd) if t is not None), default=None) if has_side else None
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
        if e_side is not None and not any(r[3] in ("a", "b") and _ts(r[9]) == e_side for r in mine):
            keep.add(next(r[0] for r in rows if r[3] in ("a", "b") and _ts(r[9]) == e_side))

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


def _step(name, **kw):
    """Crash-test hook: called at each step of _archive_day (tests make it raise or kill the process). No-op in production."""
    return None


def fname_of(p):
    return Path(p).name


def _read_file_rowids(path):
    """(rows, sha256 of the decompressed lines, [original rowids]) of one archive file - the check right before a delete."""
    h, n, ids = hashlib.sha256(), 0, []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            ids.append(int(json.loads(line)["_rowid"]))
            h.update(line.encode("utf-8"))
            n += 1
    return n, h.hexdigest(), ids


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
    _step("written", day=day, tmp=tmp, final=final)               # crash point 1: verified temp file, not renamed
    os.replace(tmp, final)
    _fsync(adir)
    _step("renamed", day=day, tmp=tmp, final=final)               # crash point 2: final file, no manifest row yet
    # the file that will be listed in the manifest is re-read from disk and checked against what the manifest will say
    # (rows + sha256), and its rowids become the ONLY rows the delete may touch
    n3, d3, ids = _read_file_rowids(final)
    if n3 != n_exp or d3 != digest:
        raise RuntimeError(f"archive of {day}: {fname_of(final)} re-read after the rename does not match "
                           f"({n3} rows, sha {d3[:12]} vs {n_exp}, {digest[:12]}); nothing removed")
    fname, size = final.name, final.stat().st_size
    with ml._DB_LOCK:
        con.execute("BEGIN IMMEDIATE")
        try:
            again = con.execute(f"SELECT COUNT(*), MIN(rowid), MAX(rowid), TOTAL(rowid) FROM ml_quotes WHERE {_WHERE}",
                                (lo, hi)).fetchone()
            if tuple(again) != tuple(stat):
                raise RuntimeError(f"rows of {day} changed during the archive ({stat} -> {again}); nothing removed")
            con.execute("CREATE TEMP TABLE IF NOT EXISTS ml_del (r INTEGER PRIMARY KEY)")
            con.execute("DELETE FROM temp.ml_del")
            con.executemany("INSERT INTO temp.ml_del (r) VALUES (?)", ((i,) for i in ids))
            con.execute("INSERT INTO ml_archive (day, part, file, rows, sha256, bytes, min_rowid, max_rowid, archived_at, "
                        "verified, version) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
                        (day, part, fname, n_exp, digest, size, stat[1], stat[2], _now_iso(now), ARCHIVE_VERSION))
            _step("manifest", day=day, tmp=tmp, final=final)      # crash point 3: manifest row, rows not removed (one txn)
            # only rows listed in the verified file (and still matching the day's archive condition) leave the store
            cur = con.execute(f"DELETE FROM ml_quotes WHERE rowid IN (SELECT r FROM temp.ml_del) AND {_WHERE}", (lo, hi))
            if cur.rowcount != n_exp:
                raise RuntimeError(f"delete of {day} touched {cur.rowcount} rows, expected {n_exp}")
            _step("deleted", day=day, tmp=tmp, final=final)       # crash point 4: before COMMIT (rolls back on a crash)
            con.execute("DELETE FROM temp.ml_del")
            con.execute("COMMIT")
        except BaseException:
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
        out["removed_tmp"] = _remove_stale_tmp()                  # partial files of an interrupted pass (rows are live)
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


def _remove_stale_tmp():
    """Delete '.ml_quotes_*.jsonl.gz.tmp' files left by an interrupted pass (called with _LOCK held). A temp file is never
    listed in the manifest, so its rows are still in the live store; the day is simply written again."""
    adir = archive_dir()
    n = 0
    if adir.exists():
        for f in adir.glob(".ml_quotes_*.jsonl.gz.tmp"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


# ---------------------------------------------------------------- read back (one lookup over the archive AND the live store)

def _paths(db=None, adir=None):
    if db:
        db = Path(db)
    elif adir:
        db = Path(adir).parent / _ml().db_path().name
    else:
        db = _ml().db_path()
    return db, (Path(adir) if adir else db.parent / "ml_archive")


def _ro(db):
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)


def manifest(db=None):
    """Verified manifest rows (dicts: day, part, file, rows, sha256, bytes, min_rowid, max_rowid), oldest first."""
    db, _ = _paths(db)
    if not db.exists():
        return []
    con = _ro(db)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ml_archive'").fetchone():
            return []
        cols = ("day", "part", "file", "rows", "sha256", "bytes", "min_rowid", "max_rowid", "archived_at")
        return [dict(zip(cols, r)) for r in con.execute(
            f"SELECT {', '.join(cols)} FROM ml_archive WHERE verified = 1 ORDER BY day, part")]
    finally:
        con.close()


def _files(db, adir, day_lo=None, day_hi=None):
    """The archive files listed in a verified manifest row, within [day_lo, day_hi] (UTC days, inclusive). A file with no
    manifest row (left by an interrupted pass - its rows are still live) is never read: no row is returned twice."""
    out = []
    for m in manifest(db):
        if (day_lo and m["day"] < day_lo) or (day_hi and m["day"] > day_hi) or not m["rows"]:
            continue
        out.append((m, adir / str(m["file"])))
    return out


def iter_archived(day=None, adir=None, db=None):
    """Archived rows (dicts with every ml_quotes column + '_rowid'), oldest day first; day='YYYY-MM-DD' for one day.
    Reads only manifest-listed files."""
    db, adir = _paths(db, adir)
    for _m, f in _files(db, adir, day, day):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                yield json.loads(line)


def _needle(k, v):
    return json.dumps({k: v}, separators=(",", ":"))[1:-1]          # '"match_id":"hltv:1"' as the archive writes it


def iter_quotes(match_id=None, platform=None, market_id=None, frm=None, to=None, db=None, adir=None, origin=False,
                live=True, archive=True):
    """THE historical quote lookup: every saved ml_quotes row matching the filters, whether it is still in the live store
    or was moved to the compressed archive - the caller cannot tell the difference (dicts with every column + '_rowid',
    the original rowid). frm / to bound received_ts (ISO strings, inclusive); only archive days inside [frm, to] are
    opened. Streams (archive days oldest first, then the live rows): memory stays bounded. origin=True yields
    (origin, row) with origin 'live' or 'archive:<file>'."""
    db, adir = _paths(db, adir)
    flt = {k: v for k, v in (("match_id", match_id), ("platform", platform), ("market_id", market_id)) if v is not None}
    needles = [_needle(k, v) for k, v in flt.items()]

    def want(r):
        rec = str(r.get("received_ts") or "")
        return (all(r.get(k) == v for k, v in flt.items()) and (frm is None or rec >= frm)
                and (to is None or rec <= to))
    if archive:
        for _m, f in _files(db, adir, frm[:10] if frm else None, to[:10] if to else None):
            tag = f"archive:{f.name}"
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if needles and not all(n in line for n in needles):
                        continue
                    r = json.loads(line)
                    if want(r):
                        yield (tag, r) if origin else r
    if live and db.exists():
        where, args = [], []
        for k, v in flt.items():
            where.append(f"{k} = ?")
            args.append(v)
        if frm is not None:
            where.append("received_ts >= ?")
            args.append(frm)
        if to is not None:
            where.append("received_ts <= ?")
            args.append(to)
        con = _ro(db)
        try:
            cur = con.execute("SELECT rowid AS _rowid, * FROM ml_quotes" + (" WHERE " + " AND ".join(where) if where else "")
                              + " ORDER BY rowid", args)
            names = [d[0] for d in cur.description]
            for row in cur:
                r = dict(zip(names, row))
                yield ("live", r) if origin else r
        finally:
            con.close()


def quotes(match_id=None, platform=None, market_id=None, frm=None, to=None, limit=500, db=None, adir=None):
    """iter_quotes as one bounded, ordered answer: the first `limit` rows by (received_ts, rowid) - heap-bounded memory -
    each with '_origin', plus counts per origin (all matching rows are counted, only `limit` are kept)."""
    n, by = 0, Counter()

    def gen():
        nonlocal n
        for org, r in iter_quotes(match_id, platform, market_id, frm, to, db, adir, origin=True):
            n += 1
            by["live" if org == "live" else "archive"] += 1
            yield (str(r.get("received_ts") or ""), int(r["_rowid"]), org, r)
    top = heapq.nsmallest(max(int(limit), 0), gen(), key=lambda t: (t[0], t[1]))
    return {"rows": [dict(r, _origin=org) for _rec, _id, org, r in top], "matched": n, "by_origin": dict(by),
            "truncated": n > len(top)}


def _copy_schema(out, src_db):
    ml = _ml()
    out.executescript(ml._SCHEMA)
    have = {r[1] for r in out.execute("PRAGMA table_info(ml_forecasts)")}
    fcols = []
    if src_db.exists():
        src = _ro(src_db)
        try:
            fcols = [r[1] for r in src.execute("PRAGMA table_info(ml_forecasts)")]
        finally:
            src.close()
    for c in fcols:
        if c not in have:
            out.execute(f"ALTER TABLE ml_forecasts ADD COLUMN {c}")
    return fcols


def write_copy(out_path, match_ids=None, db=None, adir=None, live=True, archive=True, frm=None, to=None):
    """A research / evaluation copy: ml_quotes rows from the unified lookup (original rowids; for match_ids only when
    given) plus the matching ml_forecasts rows. Never touches the live store. Returns the number of quote rows written."""
    db, adir = _paths(db, adir)
    out = sqlite3.connect(str(out_path))
    try:
        fcols = _copy_schema(out, db)
        if fcols:
            src = _ro(db)
            try:
                sql = f"SELECT {','.join(fcols)} FROM ml_forecasts"
                ins = f"INSERT OR IGNORE INTO ml_forecasts ({','.join(fcols)}) VALUES ({','.join('?' * len(fcols))})"
                if match_ids is None:
                    out.executemany(ins, src.execute(sql))
                else:
                    for mid in match_ids:
                        out.executemany(ins, src.execute(sql + " WHERE match_id = ?", (mid,)))
            finally:
                src.close()
        n, batch = 0, []
        streams = ([iter_quotes(frm=frm, to=to, db=db, adir=adir, live=live, archive=archive)] if match_ids is None else
                   [iter_quotes(mid, frm=frm, to=to, db=db, adir=adir, live=live, archive=archive) for mid in match_ids])
        for it in streams:
            for r in it:
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


def restore_db(out_path, db=None, adir=None):
    """A complete research copy: ml_quotes with every live AND archived row (original rowids) plus ml_forecasts.
    Never touches the live store. Returns the number of quote rows written."""
    return write_copy(out_path, None, db, adir)


def iter_all_quotes(db=None, adir=None):
    """Every quote row ever saved, archived rows first then the live ones (dicts, '_rowid' = original rowid)."""
    yield from iter_quotes(db=db, adir=adir)


def _flush(con, batch):
    for cols, vals in batch:
        names = ["rowid" if c == "_rowid" else c for c in cols]
        con.execute(f"INSERT OR IGNORE INTO ml_quotes ({','.join(names)}) VALUES ({','.join('?' * len(names))})", vals)
    return len(batch)


def _file_digest(path):
    """(lines, sha256) of a file's decompressed content - equal to _read_lines_digest (the digest is over the lines in
    order, each ending in a newline), read in 1 MB chunks without parsing (the periodic integrity check)."""
    h, n = hashlib.sha256(), 0
    with gzip.open(path, "rb") as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            h.update(b)
            n += b.count(b"\n")
    return n, h.hexdigest()


def verify_archive(db=None, adir=None):
    """Each manifest row's file exists and matches its row count and sha256. Returns {file: problem} (empty = all good)."""
    db, adir = _paths(db, adir)
    bad = {}
    for m, p in _files(db, adir):
        if not p.exists():
            bad[m["file"]] = "file missing"
            continue
        try:
            n, d = _file_digest(p)
        except Exception as e:      # noqa: BLE001 - OSError, EOFError, BadGzipFile, zlib.error
            bad[m["file"]] = f"unreadable ({type(e).__name__})"
            continue
        if n != m["rows"] or d != m["sha256"]:
            bad[m["file"]] = f"mismatch ({n} rows, sha {d[:12]})"
    return bad


# ---------------------------------------------------------------- per-match evaluation through the unified lookup

def match_eval(match_id, db=None, adir=None, frm=None, to=None):
    """ml_eval.forward_rows + asks_at_cutoff for ONE linked match, computed twice: from the live store's rows of the
    match only, and through the unified lookup (archive + live). Both answers, whether they are identical, and where
    (live / archive file) each quote the evaluation used is stored. Bounded: one match's rows in two temp copies."""
    import tempfile
    import ml_eval
    db, adir = _paths(db, adir)
    res = {"match_id": match_id, "window": [frm, to]}
    e_fwd = None
    with tempfile.TemporaryDirectory(prefix="mlstore_eval_") as td:
        for name, kw in (("live_only", {"archive": False}), ("unified", {})):
            p = Path(td) / f"{name}.db"
            n = write_copy(p, [match_id], db, adir, frm=frm, to=to, **kw)
            ex = Counter()
            fwd, why = ml_eval.forward_rows(str(p), ex)
            res[name] = {"quote_rows": n, "forward": fwd or [], "excluded": dict(ex), "why": why,
                         "asks": ml_eval.asks_at_cutoff(str(p)).get(match_id)}
            if name == "unified":
                e_fwd = ml_eval.earliest_starts(str(p)).get(match_id)
    rows = list(iter_quotes(match_id, frm=frm, to=to, db=db, adir=adir, origin=True))
    res["by_origin"] = dict(Counter("live" if o == "live" else "archive" for o, _ in rows))
    arc = [str(r.get("received_ts")) for o, r in rows if o != "live"]
    liv = [str(r.get("received_ts")) for o, r in rows if o == "live"]
    res["received"] = {"first": min(arc + liv) if rows else None, "last": max(arc + liv) if rows else None,
                       "archive_last": max(arc) if arc else None, "live_first": min(liv) if liv else None}
    _ts = ml_eval._ts
    used = []
    for f in res["unified"]["forward"]:                      # the quote each forward cohort scored, and where it is stored
        hit = [o for o, r in rows if str(r.get("source") or "ml_quotes") == f["source"] and _ts(r.get("captured_at")) == f["at"]
               and str(r.get("team_side") or "").lower() != "b"]
        used.append({"cohort": f["source"], "at": f["at"], "p": f["p"],
                     "stored_in": sorted(set(hit)) or ["ml_forecasts (live)"]})
    starts = [_ts(r.get("start")) for o, r in rows if r.get("team_side") in ("a", "b")]
    starts = [s for s in starts if s is not None]
    if starts and e_fwd is not None:
        starts.append(e_fwd)                                 # the same earliest start as asks_at_cutoff (#21)
    if starts:                                               # asks_at_cutoff's rows: latest per (platform, side) in window
        cut = min(starts) - ml_eval.CUTOFF_MIN * 60
        last = {}
        for o, r in rows:
            t = _ts(r.get("received_ts"))
            if r.get("team_side") not in ("a", "b") or t is None or t > cut or t < cut - ml_eval.MAX_AGE_H * 3600:
                continue
            k = (r.get("platform"), r.get("team_side"))
            if k not in last or t > last[k][0]:
                last[k] = (t, {o})
            elif t == last[k][0]:
                last[k][1].add(o)
        for (plat, side), (t, orgs) in sorted(last.items()):
            used.append({"cohort": f"ask {plat} side {side}", "at": t, "stored_in": sorted(orgs)})
    res["eval_quotes"] = used
    res["identical"] = all(res["live_only"][k] == res["unified"][k] for k in ("forward", "excluded", "asks"))
    return res


def sample_match(db=None, adir=None, files=2, candidates=40):
    """A linked match with rows on BOTH sides of the archive boundary (rows in the newest archive file(s) and in the live
    store) - the production demonstration. Bounded: reads at most `files` archive files."""
    db, adir = _paths(db, adir)
    fl = _files(db, adir)[-files:]
    cnt = Counter()
    for _m, f in fl:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                if '"match_id":null' in line:
                    continue
                r = json.loads(line)
                if r.get("match_id") and r.get("team_side") in ("a", "b"):
                    cnt[r["match_id"]] += 1
    if not cnt or not db.exists():
        return None
    con = _ro(db)
    try:
        best = None
        for mid, n_arc in cnt.most_common(candidates):
            n_live = con.execute("SELECT COUNT(*) FROM ml_quotes WHERE match_id = ?", (mid,)).fetchone()[0]
            if n_live and (best is None or min(n_arc, n_live) > min(best[1], best[2])):
                best = (mid, n_arc, n_live)
        return best and best[0]
    finally:
        con.close()


MATCH_DAYS_BEFORE = 14       # admin per-match lookup: archive days read = [earliest start - 14 d, latest start + 2 d]
MATCH_DAYS_AFTER = 2


def match_window(match_id, db=None):
    """(from, to) received_ts window of the admin per-match lookup, from the match's stated starts in the LIVE store (the
    archive keeps the row holding the earliest start live) and its forecast log. (None, None) when unknown = all days."""
    db, _ = _paths(db)
    if not db.exists():
        return None, None
    con = _ro(db)
    try:
        st = [r for r in con.execute("SELECT MIN(start), MAX(start) FROM ml_quotes WHERE match_id = ?", (match_id,)).fetchone()
              if r]
        st += [r for r in con.execute("SELECT MIN(start), MAX(start) FROM ml_forecasts WHERE match_id = ?",
                                      (match_id,)).fetchone() if r]
    finally:
        con.close()
    ts = [t for t in (_parse_iso(x) for x in st) if t is not None]
    if not ts:
        return None, None
    lo = datetime.fromtimestamp(min(ts) - MATCH_DAYS_BEFORE * 86400, timezone.utc)
    hi = datetime.fromtimestamp(max(ts) + MATCH_DAYS_AFTER * 86400, timezone.utc)
    return lo.strftime("%Y-%m-%dT00:00:00Z"), hi.strftime("%Y-%m-%dT23:59:59.999999Z")


_LOOKUP_LOCK = threading.Lock()
LOOKUP_MAX_ROWS = 500
LOOKUP_MAX_DAYS = 3


def admin_lookup(q):
    """The admin read-only endpoint (/admin/ml_store.json): (http status, body). No query -> the store state + alerts
    (memory). match_id=<id> [from, to, limit<=500] -> the unified lookup for that match; eval=1 adds match_eval;
    sample=1 picks a match whose rows span the archive boundary; verify=1 re-checks every archive file. Without
    match_id, from + to (<= LOOKUP_MAX_DAYS apart) are required. One lookup at a time (busy -> 429); never writes."""
    if not any(q.get(k) for k in ("match_id", "sample", "from", "to", "verify")):
        return 200, dict(state(), alerts=alerts())
    if not _LOOKUP_LOCK.acquire(blocking=False):
        return 429, {"error": "another lookup is running"}
    t0 = time.time()
    try:
        mid = q.get("match_id")
        out = {}
        if q.get("verify") == "1":
            out["verify"] = verify_archive()
            out["verified_files"] = len(_files(*_paths()))
        if q.get("sample") == "1" and not mid:
            mid = sample_match()
            out["sampled"] = mid
            if not mid:
                return 200, dict(out, error="no linked match has rows in both the archive and the live store")
        frm, to = q.get("from"), q.get("to")
        if mid or frm or to:
            if not mid:
                try:
                    a, b = datetime.fromisoformat(frm[:10]), datetime.fromisoformat(to[:10])
                except (TypeError, ValueError):
                    return 400, {"error": "from and to (ISO) are required without match_id"}
                if b < a or (b - a).days > LOOKUP_MAX_DAYS:
                    return 400, {"error": f"from..to must be 0..{LOOKUP_MAX_DAYS} days apart without match_id"}
            try:
                lim = min(max(int(q.get("limit") or 100), 0), LOOKUP_MAX_ROWS)
            except ValueError:
                return 400, {"error": "limit must be an integer"}
            if mid and not (frm or to):
                frm, to = match_window(mid)
                out["window"] = {"from": frm, "to": to, "rule": f"stated start -{MATCH_DAYS_BEFORE} d .. +{MATCH_DAYS_AFTER} d"}
            out["lookup"] = quotes(mid, q.get("platform"), q.get("market_id"), frm, to, limit=lim)
            if mid and (q.get("eval") == "1" or q.get("sample") == "1"):
                out["eval"] = match_eval(mid, frm=frm, to=to)
        out["took_s"] = round(time.time() - t0, 2)
        return 200, out
    finally:
        _LOOKUP_LOCK.release()


# ---------------------------------------------------------------- alerts (admin only; computed by the background thread)
# Thresholds (documented in MODEL_VERSIONS / the module docstring; admin pages and /health ops only, never a member label):
#   writes_stopped:<platform>  the platform's source is current (ok) and has listings, yet no quote of that platform was
#                              saved for > STALL_ALERT_MIN minutes (counted from the later of the last saved quote and this
#                              process's start, so a restart after an outage does not alert before it had the chance to write)
#   live_size                  live pages + WAL > SIZE_ALERT_SHARE x the cap (moneyline.SNAPSHOT_MAX_MB)
#   disk_free                  free disk < DISK_ALERT_FACTOR x the write floor (moneyline.DISK_FLOOR_MB)
#   archive_error              the last archive pass failed (rows stay live; the next pass retries)
#   archive_integrity          a manifest-listed archive file is missing or does not match its rows / sha256 (daily check)
#   state_stale                the background thread has not refreshed the store state for > 3 x STATE_EVERY
# Each flag's transition (on -> off, off -> on) is logged once ("ml_store ALERT ..." / "ml_store CLEARED ...").
STALL_ALERT_MIN = float(os.environ.get("ML_STALL_ALERT_MIN") or 30)
SIZE_ALERT_SHARE = 0.8
DISK_ALERT_FACTOR = 1.5
VERIFY_EVERY = 24 * 3600
_BOOT_TS = time.time()
_ALERT_PREV = {}
_ALERT_LOG = []
_VERIFY = {}


def thresholds():
    ml = _ml()
    return {"stall_alert_min": STALL_ALERT_MIN, "size_alert_share": SIZE_ALERT_SHARE, "cap_mb": ml.SNAPSHOT_MAX_MB,
            "size_alert_mb": round(SIZE_ALERT_SHARE * ml.SNAPSHOT_MAX_MB, 1), "disk_alert_factor": DISK_ALERT_FACTOR,
            "disk_floor_mb": ml.DISK_FLOOR_MB, "disk_alert_mb": round(DISK_ALERT_FACTOR * ml.DISK_FLOOR_MB, 1),
            "verify_every_h": VERIFY_EVERY / 3600, "state_stale_s": 3 * STATE_EVERY}


def _parse_iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def compute_alerts(st, sources, now_ts=None, boot_ts=None, verify=None):
    """{flag: {on, ...detail}} from a refreshed state() and the sources' status() dicts (ok, listings, snapshot_error)."""
    now_ts = time.time() if now_ts is None else now_ts
    boot_ts = _BOOT_TS if boot_ts is None else boot_ts
    th = thresholds()
    flags = {}
    last = st.get("last_received") or {}
    for name, s in sorted((sources or {}).items()):
        lt = _parse_iso(last.get(name))
        since = max(t for t in (lt, boot_ts) if t is not None)
        mins = round((now_ts - since) / 60, 1)
        ok, n = bool(s.get("ok")), int(s.get("listings") or 0)
        flags[f"writes_stopped:{name}"] = {"on": bool(ok and n and mins > STALL_ALERT_MIN), "source_ok": ok, "listings": n,
                                           "last_saved": last.get(name), "minutes_without_save": mins,
                                           "snapshot_error": s.get("snapshot_error")}
    if st.get("live_mb") is not None:
        flags["live_size"] = {"on": st["live_mb"] > th["size_alert_mb"], "live_mb": st["live_mb"],
                              "alert_mb": th["size_alert_mb"], "cap_mb": th["cap_mb"]}
    if st.get("disk_free_mb") is not None:
        flags["disk_free"] = {"on": st["disk_free_mb"] < th["disk_alert_mb"], "disk_free_mb": st["disk_free_mb"],
                              "alert_mb": th["disk_alert_mb"], "floor_mb": th["disk_floor_mb"]}
    lp = st.get("last_pass") or {}
    err = lp.get("error")
    flags["archive_error"] = {"on": bool(err and err != "another pass is running"), "error": err, "at": lp.get("at")}
    if verify:
        flags["archive_integrity"] = {"on": bool(verify.get("problems")), "problems": verify.get("problems"),
                                      "files": verify.get("files"), "at": verify.get("at")}
    return flags


def _log_transitions(flags):
    for k, f in flags.items():
        prev = _ALERT_PREV.get(k)
        on = bool(f.get("on"))
        if prev is None and not on:
            _ALERT_PREV[k] = on                                  # first sight, healthy: nothing to log
            continue
        if prev == on:
            continue
        _ALERT_PREV[k] = on
        detail = {x: y for x, y in f.items() if x != "on"}
        line = f"ml_store {'ALERT' if on else 'CLEARED'} {k}: {json.dumps(detail, default=str, sort_keys=True)[:400]}"
        _ALERT_LOG.append({"at": _now_iso(), "flag": k, "on": on})
        del _ALERT_LOG[:-20]
        print(line, flush=True)


def alerts(now_ts=None):
    """The alert summary (memory only - safe on any request): {state: ok | alert | pending, on: [flags], flags, ...}."""
    now_ts = time.time() if now_ts is None else now_ts
    flags = dict(_STATE.get("alerts") or {})
    chk = _parse_iso(_STATE.get("checked_at")) if _STATE.get("checked_at") else None
    ref = chk if chk is not None else _BOOT_TS + FIRST_DELAY
    age = now_ts - ref
    flags["state_stale"] = {"on": age > 3 * STATE_EVERY, "checked_at": _STATE.get("checked_at"), "age_s": round(age)}
    on = sorted(k for k, f in flags.items() if f.get("on"))
    return {"state": "alert" if on else ("ok" if chk is not None else "pending"), "on": on, "flags": flags,
            "thresholds": thresholds(), "log": list(_ALERT_LOG)}


# ---------------------------------------------------------------- state (memory; refreshed off the request path)

def _sources_status():
    ml = _ml()
    out = {}
    for name, s in getattr(ml, "SOURCES", {}).items():
        try:
            with s.lock:
                out[name] = {"ok": s.is_current(), "listings": len(s.listings), "snapshot_error": s.snapshot_error}
        except Exception:       # noqa: BLE001
            continue
    return out


def refresh_state(sources=None, now_ts=None):
    ml = _ml()
    p = ml.db_path()
    at = datetime.fromtimestamp(now_ts, timezone.utc) if now_ts is not None else None
    st = {"checked_at": _now_iso(at), "path": str(p), "exists": p.exists(), "cap_mb": ml.SNAPSHOT_MAX_MB,
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
                    f"SELECT {', '.join(cols)} FROM ml_gaps ORDER BY first_saved_after DESC LIMIT 50")]
                st["gaps_total"] = con.execute("SELECT COUNT(*) FROM ml_gaps").fetchone()[0]
        finally:
            con.close()
    st["disk_free_mb"] = round(_disk_free_mb(p.parent if p.parent.exists() else ".") or 0)
    st["last_pass"] = _STATE.get("last_pass")
    st["verify"] = dict(_VERIFY) or None
    flags = compute_alerts(st, _sources_status() if sources is None else sources, now_ts=now_ts, verify=_VERIFY or None)
    _log_transitions(flags)
    st["alerts"] = flags
    _STATE.clear()
    _STATE.update(st)
    return st


def verify_now():
    """Re-check every manifest-listed archive file (row count + sha256); the result feeds archive_integrity."""
    t0 = time.time()
    try:
        bad = verify_archive()
        n = len(_files(*_paths()))
    except Exception as e:      # noqa: BLE001
        bad, n = {"_": f"{type(e).__name__}: {str(e)[:200]}"}, None
    _VERIFY.clear()
    _VERIFY.update(at=_now_iso(), files=n, problems=bad, took_s=round(time.time() - t0, 2))
    return dict(_VERIFY)


def state():
    """The last refreshed store state (memory only - safe on any request)."""
    return dict(_STATE)


def kick():
    """Ask the background thread for an archive pass soon (the writer calls this at its cap)."""
    _KICK.set()


def _loop():
    if _STOP.wait(FIRST_DELAY):
        return
    last_pass = last_verify = 0.0
    while not _STOP.is_set():
        try:
            refresh_state()
        except Exception as e:      # noqa: BLE001
            _STATE["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        if _KICK.is_set() or time.time() - last_pass >= ARCHIVE_EVERY:
            _KICK.clear()
            archive_pass()
            last_pass = time.time()
            if time.time() - last_verify >= VERIFY_EVERY:
                verify_now()
                last_verify = time.time()
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
