"""Soccer / tennis result compaction (drain-1119): COLD stat and context rows of a competition store move into verified
archive files with the existing sports/archive.py machinery - nothing is deleted, every row stays readable.

Why: a stored stat row costs ~1.4-1.85 KB in these stores (shared NBA / NFL schema: ~113-char record id, ~84-char
source key, ISO times, 6+ indexes repeating the text ids) for ~250 B of data, so a summarised soccer match costs
0.75-1.0 MB and the 90-day backlog alone (~2,000 finals) would be ~1.7 GB against a 512 MB budget.

What is cold: every stat / context row of an EVENT that started more than SCOUT_EXPANSION_COMPACT_DAYS (7) days ago
(start from the event's own stored record) and none of whose revisions was LEARNED in the last 48 h (known_at; the
re-read correction window - an unchanged re-read only moves last_seen_at and does not keep an event hot); only whole identities (all revisions of an identity are in the event, checked per identity).
Events, players and teams stay hot (small; _any_event reads them directly).

Where it goes: <competition>/archive/records/result-<competition>-<event day>-<seq>.jsonl.xz (one JSON line per row,
every column verbatim, lzma), the store's own archive_files / arch_segs / arch_revs / arch_text index - the same
crash-safe protocol as the NBA / NFL offer archive: write -> fsync -> re-read + verify (rows, sha256, every payload
re-derives its content hash) -> rename -> re-read -> ONE transaction (manifest + index + DELETE of exactly those rids).

Reads: the store's meta 'archived_kinds' = 'stat,context' is set BEFORE the first part; store._latest_rows / _span then
read these kinds through records_all (hot + archived, the same rows and order - tested byte for byte), store.get /
row_meta already fall back to the archive. A re-read of an archived identity with unchanged content is a duplicate
(store._put), never a new revision.

Freed pages go back to the file system (auto_vacuum = INCREMENTAL, expansion_store.tidy); the history budget counts
the archive files too.
"""
from __future__ import annotations

import os
import sqlite3
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

KINDS = ("stat", "context")
LABEL = "result"
META_KEY = "archived_kinds"
COLD_DAYS = float(os.environ.get("SCOUT_EXPANSION_COMPACT_DAYS", "7"))
QUIET_H = 48
# lzma preset 2 (2 MiB dictionary, ~17 MiB to compress) instead of the offer archive's 6 (8 MiB, ~94 MiB): a part here
# is one competition-day (<= 5,000 rows, a few MB), so the larger dictionary buys little and the history job's
# 120 MB Starter cap (x1.8 on the 2 GB plan) keeps its headroom
PRESET = 2
TS = "%Y-%m-%dT%H:%M:%SZ"


# the decompressed-file LRU (sports/archive.py, 32 MB of raw lines by default) counts raw bytes, not the parsed rows
# it keeps per line: a tennis history run re-reading archived results reached 143-210 MB peak (cap 216 MB on the 2 GB
# plan; ~70-80 MB before compaction). The soccer / tennis jobs (history, learning) bound it lower.
EXPANSION_LRU_MB = float(os.environ.get("SCOUT_EXPANSION_ARCHIVE_CACHE_MB", "6"))


def bound_cache():
    """Called once by the soccer / tennis jobs (own processes): a smaller archive-file cache."""
    from . import archive
    archive.LRU_BYTES = min(archive.LRU_BYTES, int(EXPANSION_LRU_MB * 1_000_000))
    return archive.LRU_BYTES


def enabled():
    return os.environ.get("SCOUT_EXPANSION_COMPACT", "1") != "0"


def archive_dir(db_path):
    return Path(db_path).parent / "archive" / "records"


def _parse(ts):
    try:
        return datetime.strptime(str(ts)[:19] + "Z", TS).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def candidates(con, now, part_rows=5000):
    """[(day, [rids])]: cold stat / context rows grouped by their event's start day (one file per competition-day),
    whole events per part where an event fits (an event over part_rows is split by identity, never inside one).
    Measured on real stores: one file per EVENT instead made cold player-history reads slower (p95 117 -> 673 ms,
    6,905 files for 128k tennis rows; per-file open cost) and compaction 5x slower."""
    from . import store
    cutoff = now - timedelta(days=COLD_DAYS)
    quiet = (now - timedelta(hours=QUIET_H)).strftime(TS)
    events = [r[0] for r in con.execute("SELECT DISTINCT event_id FROM records INDEXED BY records_event WHERE kind IN "
                                        "('stat', 'context') AND event_id IS NOT NULL")]
    starts = {}
    for eid in events:
        rows = con.execute("SELECT payload FROM records WHERE kind = 'event' AND source_key = ? AND late = 0",
                           (eid,)).fetchall()
        st = set()
        for (p,) in rows:
            try:
                st.add(store._unpack(p).get("start_utc"))
            except (ValueError, TypeError, AttributeError):
                st.add(None)
        if st and None not in st:
            parsed = [_parse(x) for x in st]
            if all(x is not None and x < cutoff for x in parsed):
                starts[eid] = min(parsed)
    groups = OrderedDict()
    for eid in sorted(starts, key=lambda e: (starts[e], e)):
        rows = con.execute("SELECT rid, kind, source, source_key, known_at, COALESCE(last_seen_at, '') FROM records "
                           "WHERE kind IN ('stat', 'context') AND event_id = ?", (eid,)).fetchall()
        # sg-stability 2026-11-20: coldness is judged on CONTENT (known_at = when a revision was learned), not on
        # sightings. A final in a base competition is re-read every 24 h for 100 days; each unchanged re-read moves
        # last_seen_at only, so the old max(known_at, last_seen_at) rule never let those events go cold and almost
        # nothing was ever archived (0.2 MB archived vs ~950 MB/day of growth, live ops 2026-11-20). A re-read of an
        # archived identity with unchanged content stays a duplicate (store._put); a correction is a new revision with
        # a new known_at and stays hot for its own 48 h.
        if not rows or max(r[4] or "" for r in rows) >= quiet:
            continue                                    # a revision learned inside the correction window: stays hot
        ident = OrderedDict()
        for rid, kind, source, sk, _k, _s in rows:
            ident.setdefault((kind, source, sk), []).append(rid)
        keep = []
        for (kind, source, sk), rids in ident.items():
            n = con.execute("SELECT COUNT(*) FROM records WHERE kind = ? AND source = ? AND source_key = ?",
                            (kind, source, sk)).fetchone()[0]
            if n == len(rids):                          # every revision of the identity is in this event
                keep.append(rids)
        day = starts[eid].strftime("%Y-%m-%d")
        g = groups.setdefault(day, [[]])
        n_event = sum(len(r) for r in keep)
        if g[-1] and len(g[-1]) + n_event > part_rows:
            g.append([])
        for rids in keep:
            if len(g[-1]) + len(rids) > part_rows and g[-1]:
                g.append([])
            g[-1].extend(rids)
    return [(day, rids) for day, parts in groups.items() for rids in parts if rids]


def mark(con):
    """Readers of this store read stat / context through records_all from now on (set before any row moves)."""
    con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (META_KEY, ",".join(KINDS)))
    con.commit()


def compact(path, budget_s, now=None, part_rows=5000):
    """Archive this store's cold result rows inside budget_s. -> {"rows", "parts", "seconds", "pending", "error"}."""
    from . import archive, store
    now = now or datetime.now(timezone.utc)
    t0 = time.monotonic()
    out = {"file": Path(path).parent.name, "rows": 0, "parts": 0, "pending_rows": 0, "error": None}
    con = store.connect(path)
    con.isolation_level = None
    adir = archive_dir(path)
    try:
        archive.ensure_schema(con)
        archive._remove_stale_tmp(adir)
        archive._remove_orphans(con, adir, LABEL)
        parts = candidates(con, now, part_rows)
        out["pending_rows"] = sum(len(r) for _d, r in parts)
        if not parts:
            return out
        if store.archived_kinds(con) != set(KINDS):
            mark(con)
        archive.install(con, path)
        slowest, now_s = 0.0, now.strftime(TS)
        for day, rids in parts:
            if time.monotonic() - t0 + slowest > budget_s:
                out["stopped"] = "time budget"
                break
            tp = time.monotonic()
            r = archive._archive_part(con, out["file"], day, rids, now_s, kinds=KINDS, adir=adir, label=LABEL,
                                      preset=PRESET)
            slowest = max(slowest, time.monotonic() - tp)
            out["rows"] += r["rows"]
            out["parts"] += 1
        out["pending_rows"] -= out["rows"]
        try:
            con.execute("PRAGMA busy_timeout=5000")
            if con.execute("PRAGMA auto_vacuum").fetchone()[0] == 2:
                con.execute("PRAGMA incremental_vacuum").fetchall()   # one page per step: run it to the end
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
    except Exception as e:                              # noqa: BLE001 - every row stays hot on any failure
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        con.close()
        out["seconds"] = round(time.monotonic() - t0, 2)
    return out


def compact_all(directory, budget_s, now=None):
    """Every store of one sport, largest first, inside budget_s. -> {"rows", "parts", "files": [...], "seconds"}."""
    t0 = time.monotonic()
    out = {"rows": 0, "parts": 0, "files": [], "errors": 0}
    paths = sorted(Path(directory).glob("*/canonical.db"), key=lambda p: -p.stat().st_size)
    for p in paths:
        left = budget_s - (time.monotonic() - t0)
        if left <= 1:
            break
        r = compact(p, left, now)
        if r["rows"] or r["error"] or r.get("pending_rows"):
            out["files"].append(r)
        out["rows"] += r["rows"]
        out["parts"] += r["parts"]
        out["errors"] += bool(r["error"])
    out["seconds"] = round(time.monotonic() - t0, 2)
    return out
