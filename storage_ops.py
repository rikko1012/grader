#!/usr/bin/env python3
"""
storage_ops.py - zero-loss storage tools for the esports /data disk (storage-1123, 2026-11-23). Owner: "Fix this without
losing any data". Nothing here touches a grade, a projection, a stake, picks.db, a saved forecast or a settlement.

Two stores ran the 10 GB disk to 96.5 %:
  comparison_snapshots.jsonl  append-only context log (never a grade input), 1.25 GB, no rotation (sweep SO-2)
  .cache/ml_snapshots.db      moneyline quote store (shadow tab), over its cap since 2026-11-10 (sweep SO-1)

Admin endpoints (all behind the admin token; an unauthenticated call is a 403 before any lock or read):
  E1 GET  /admin/storage/file_digest?name=&end=|keep_tail=   sha256 + line count of bytes [0,end) of ONE whitelisted
                                                             file (exactly `end` bytes are read, 1 MiB at a time);
                                                             keep_tail=K suggests the cut point: the byte after the last
                                                             newline before size-K (the kept tail is >= K bytes)
  E2 GET  /admin/storage/file_range?name=&offset=&length=    raw bytes (<= 8 MiB, never gzipped) + X-Sha256 / X-Offset /
                                                             X-Length / X-File-Size / X-Inode; &inode= refuses a changed file
  E3 POST /admin/storage/comparison_trim                     {archived_bytes, sha256, lines, inode, dry_run}: dry run unless
                                                             dry_run is exactly false. Refuses (409) on any hash / line count /
                                                             inode / size / boundary mismatch; keeps every byte after the cut
  E4 GET  /admin/storage/ml_tables                           pages / payload per table + index (dbstat), rows, schema (10 min cache)
  E5 GET  /admin/storage/ml_rows?table=&after=&upto=&limit=  one page of canonical rows (rowid order, <= 20,000 rows and <= 8 MiB)
  E6 GET  /admin/storage/ml_digest?table=&after=&upto=       count + sha256 of the same canonical lines over a rowid range
  E7 GET  /admin/storage/disk_detail                         each top-level file of /data and /data/.cache with its size
     GET  /admin/storage/status                              live file, manifest, rotation state (memory + one stat)

Whitelist (any other name is a 404; no path is ever joined from the request): comparison_snapshots.jsonl,
comparison_archive/manifest.jsonl, comparison_archive/<a segment file listed in that manifest>, ml_archive/<a file listed in
the verified ml_archive manifest table>.

Bounded: every read endpoint is single-flight (_IO_LOCK, a second call gets 429), reads at most 8 MiB into memory and holds
no lock while the answer is written to the socket. Cuts (E3 and rotation) share _MAINT_LOCK and ONE cut function.

Cut (E3 / rotation) - the only code that shortens the live file:
  1. the prefix [0,N) is hashed from an open handle WITHOUT comparison._SNAP_LOCK (append-only: the prefix of the same
     inode cannot change; hashing 1.2 GB under the writer lock would stall the board workers);
  2. under comparison._SNAP_LOCK (appends wait): the handle and the path are still the same file, size >= N, the tail
     [N,size) is >= the dedupe seed window and free disk >= tail + 64 MiB; the tail is copied to <file>.trim.tmp from the
     same handle, fsynced, re-read and checked against the tail's sha256; the manifest line (old + new inode) is appended
     and fsynced; the handle is closed; os.replace swaps the file in; the directory is fsynced. Every line appended before
     the lock was taken is in the tail; later appends wait for the lock and land in the new file.
  A crash before os.replace leaves the original file (a stale .tmp is removed by the next cut). A crash between the
  manifest line and os.replace leaves a manifest entry whose new_inode is not the live file's: the next cut appends a
  "void" line for it (its bytes are still in the live file; restore skips void entries).

Rotation (rot-1): once the live file is over COMPARISON_LIVE_MAX_MB (MiB, default 128) a background thread (never a
request) gzips everything but a 32 MiB tail into comparison_archive/comparison_seg_<seq>.jsonl.gz (temp name, fsync,
decompressed again and checked: sha256 + lines of the raw range), renames it, re-reads the final file and only then cuts
the live file with the same locked tail copy. The bytes stay on /data as verified gzip - rotation moves data, it never
removes it, and no code here ever deletes a segment. Guards: it runs only after the manual trim (E3) is in the manifest,
and only with free disk >= the comparison floor + estimated gzip + tail; skips are counted. COMPARISON_ROTATION=0 = off.
"""
import gzip
import hashlib
import json
import math
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = "storage-1"
MIB = 1024 * 1024
CHUNK = MIB
RANGE_MAX = 8 * MIB                 # E2: bytes per call
ROWS_MAX = 20000                    # E5: rows per page
ROWS_BYTES_MAX = 8 * MIB            # E5: encoded bytes per page
DIGEST_BATCH = 5000                 # E6: rows per statement (each statement is its own short read transaction)
DIGEST_TIME_S = 120.0               # E6: one call stops after this long and says where it stopped (next_after)
TRIM_FREE_MARGIN = 64 * MIB         # a cut needs free disk >= tail + this
BOUNDARY_SCAN_MAX = 64 * MIB        # the cut-point search looks back at most this far for a newline
TABLES_TTL_S = 600
LIVE_NAME = "comparison_snapshots.jsonl"
ARCHIVE_SUB = "comparison_archive"
MANIFEST_NAME = "manifest.jsonl"
LIVE_MAX_MB = float(os.environ.get("COMPARISON_LIVE_MAX_MB") or 128)   # MiB; rotation starts above this
ROT_KEEP_BYTES = 32 * MIB           # rotation keeps this much (>= the dedupe seed window) in the live file
ROT_EVERY_S = 600
ROT_FIRST_DELAY_S = 300
ROT_GZ_RATIO = 0.2                  # free-disk estimate of the gzip size (jsonl compresses ~8-10x; 5x assumed)
SEGMENT_ALERT_MB = 500
ML_TABLES = ("ml_quotes", "ml_forecasts", "ml_gaps", "ml_archive", "ml_meta")
ML_MUTABLE = ("ml_meta",)           # no update / delete trigger: exported whole, never paged by rowid

_IO_LOCK = threading.Lock()         # one bounded read at a time (E1/E2/E4/E5/E6, and the cuts' prefix hash)
_MAINT_LOCK = threading.Lock()      # one cut at a time (E3 + rotation)
_TABLES = {"at": 0.0, "body": None}
STATS = {"digests": 0, "ranges": 0, "busy": 0, "trims": 0, "trim_refused": 0, "rotations": 0, "rotation_skips": {},
         "rotation_last": None, "rotation_error": None, "voided": 0}
_THREAD = {"t": None}
_STOP = threading.Event()


class Refused(Exception):
    def __init__(self, code, error, **kw):
        super().__init__(error)
        self.code, self.body = code, dict(kw, error=error)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------- paths + whitelist

def live_path():
    import comparison
    return Path(comparison.snapshot_path())


def archive_dir():
    return live_path().parent / ARCHIVE_SUB


def manifest_path():
    return archive_dir() / MANIFEST_NAME


def ml_paths():
    import ml_store
    return ml_store._paths()


def _fsync_dir(d):
    if os.name != "posix":
        return
    try:
        fd = os.open(str(d), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def read_manifest():
    """(entries, problems): manifest lines in order; a line that does not parse is reported, never used."""
    out, bad = [], []
    try:
        with open(manifest_path(), "rb") as f:
            for i, ln in enumerate(f):
                if not ln.strip():
                    continue
                try:
                    e = json.loads(ln)
                    assert isinstance(e, dict) and isinstance(e.get("seq"), int)
                    out.append(e)
                except Exception:
                    bad.append({"line": i + 1, "bytes": len(ln)})
    except FileNotFoundError:
        pass
    return out, bad


def applied_entries(entries=None):
    """Cut entries that took effect (a later "void" line cancels one), in seq order."""
    entries = read_manifest()[0] if entries is None else entries
    void = {e.get("voids") for e in entries if e.get("kind") == "void"}
    return [e for e in entries if e.get("kind") in ("manual_trim", "segment") and e["seq"] not in void]


def _segment_files(entries=None):
    return {e["file"] for e in applied_entries(entries) if e.get("kind") == "segment" and e.get("file")}


def _ml_archive_files():
    import ml_store
    try:
        return {str(m["file"]) for m in ml_store.manifest() if m.get("file")}
    except Exception:
        return set()


def resolve(name):
    """The whitelisted file for `name`, or None. The file name must equal a listed name exactly (no separators)."""
    name = str(name or "")
    if name == LIVE_NAME:
        return live_path()
    sub, sep, base = name.partition("/")
    if not sep or not base or "/" in base or "\\" in base or base in (".", "..") or "\x00" in base:
        return None
    if sub == ARCHIVE_SUB:
        if base == MANIFEST_NAME or base in _segment_files():
            return archive_dir() / base
        return None
    if sub == "ml_archive" and base in _ml_archive_files():
        return ml_paths()[1] / base
    return None


# ---------------------------------------------------------------- byte-exact reads

def _drop_cache(f, off, n):
    if hasattr(os, "posix_fadvise"):
        try:
            os.posix_fadvise(f.fileno(), off, n, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass


def hash_prefix(f, n):
    """(sha256 hex, newline count, last byte or None) of exactly bytes [0,n) of the open binary handle f."""
    h, lines, last, pos = hashlib.sha256(), 0, None, 0
    f.seek(0)
    while pos < n:
        b = f.read(min(CHUNK, n - pos))
        if not b:
            raise Refused(409, "file_shorter_than_range", at=pos, end=n)
        h.update(b)
        lines += b.count(b"\n")
        last = b[-1:]
        _drop_cache(f, pos, len(b))
        pos += len(b)
    return h.hexdigest(), lines, last


def cut_point(f, size, keep):
    """The byte just after the last newline that leaves a tail of at least `keep` bytes (0 = no such newline within
    BOUNDARY_SCAN_MAX)."""
    target = size - int(keep)
    if target <= 0:
        return 0
    end, floor = target, max(0, target - BOUNDARY_SCAN_MAX)
    while end > floor:
        start = max(floor, end - CHUNK)
        f.seek(start)
        b = f.read(end - start)
        i = b.rfind(b"\n")
        if i >= 0:
            return start + i + 1
        end = start
    return 0


def _line_at(f, off, size, limit=MIB):
    """sha256 + the board_at / key fields of the line starting at off (bounded read; None past the end)."""
    if off >= size:
        return None
    f.seek(off)
    b = f.read(min(limit, size - off))
    i = b.find(b"\n")
    ln = b[:i + 1] if i >= 0 else b
    out = {"offset": off, "bytes": len(ln), "sha256": hashlib.sha256(ln).hexdigest(), "complete": i >= 0}
    try:
        rec = json.loads(ln)
        if isinstance(rec, dict):
            out.update(key=rec.get("key"), board_at=rec.get("board_at"))
    except Exception:
        out["parses"] = False
    return out


def _last_line_start(f, end):
    """Offset of the line that ends at `end` (end is just after a newline)."""
    pos = end - 1
    while pos > 0:
        start = max(0, pos - CHUNK)
        f.seek(start)
        b = f.read(pos - start)
        i = b.rfind(b"\n")
        if i >= 0:
            return start + i + 1
        pos = start
    return 0


def _busy():
    STATS["busy"] += 1
    return 429, {"error": "busy: another storage read or cut is running - retry shortly"}


def _int(q, k, default=None, lo=0, hi=None):
    v = q.get(k)
    if v in (None, ""):
        if default is None:
            raise Refused(400, f"{k} is required")
        return default
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise Refused(400, f"{k} must be an integer")
    if v < lo or (hi is not None and v > hi):
        raise Refused(400, f"{k} must be in [{lo}, {hi if hi is not None else 'inf'}]")
    return v


def file_digest(q):
    """E1. (status, body)."""
    p = resolve(q.get("name"))
    if p is None:
        return 404, {"error": "not a whitelisted file"}
    if q.get("end") not in (None, "") and q.get("keep_tail") not in (None, ""):
        return 400, {"error": "end and keep_tail are exclusive"}
    if not _IO_LOCK.acquire(blocking=False):
        return _busy()
    try:
        try:
            f = open(p, "rb")
        except FileNotFoundError:
            return 404, {"error": "file missing"}
        with f:
            st = os.fstat(f.fileno())
            size = st.st_size
            if q.get("keep_tail") not in (None, ""):
                keep = _int(q, "keep_tail", lo=0)
                end = cut_point(f, size, keep)
                if end == 0:
                    return 409, {"error": "no newline leaves that tail", "size": size}
            else:
                end = _int(q, "end", default=size, lo=0)
                if end > size:
                    return 400, {"error": "end is past the file size", "size": size}
            t0 = time.time()
            sha, lines, last = hash_prefix(f, end)
            first = _line_at(f, 0, end) if end else None
            last_line = _line_at(f, _last_line_start(f, end), end) if end else None
            nxt = _line_at(f, end, size)
        STATS["digests"] += 1
        return 200, {"version": VERSION, "name": q.get("name"), "size": size, "inode": st.st_ino, "mtime": st.st_mtime,
                     "end": end, "sha256": sha, "lines": lines, "ends_with_newline": last in (None, b"\n"),
                     "tail_bytes": size - end, "first_line": first, "last_line": last_line, "next_line": nxt,
                     "took_s": round(time.time() - t0, 2)}
    except Refused as r:
        return r.code, r.body
    finally:
        _IO_LOCK.release()


def file_range(q):
    """E2. (status, bytes | dict, headers). The lock is released before the caller writes the answer."""
    p = resolve(q.get("name"))
    if p is None:
        return 404, {"error": "not a whitelisted file"}, {}
    try:
        off = _int(q, "offset", lo=0)
        n = _int(q, "length", lo=1, hi=RANGE_MAX)
        want_ino = _int(q, "inode", default=-1, lo=-1)
    except Refused as r:
        return r.code, r.body, {}
    if not _IO_LOCK.acquire(blocking=False):
        return _busy() + ({"Retry-After": "2"},)
    try:
        try:
            f = open(p, "rb")
        except FileNotFoundError:
            return 404, {"error": "file missing"}, {}
        with f:
            st = os.fstat(f.fileno())
            if want_ino >= 0 and st.st_ino != want_ino:
                return 409, {"error": "inode changed", "inode": st.st_ino}, {}
            if off > st.st_size:
                return 416, {"error": "offset past the end", "size": st.st_size}, {}
            f.seek(off)
            data = f.read(min(n, st.st_size - off))
            _drop_cache(f, off, len(data))
        STATS["ranges"] += 1
        return 200, data, {"X-Sha256": hashlib.sha256(data).hexdigest(), "X-Offset": str(off), "X-Length": str(len(data)),
                           "X-File-Size": str(st.st_size), "X-Inode": str(st.st_ino), "Content-Encoding": "identity"}
    finally:
        _IO_LOCK.release()


# ---------------------------------------------------------------- the one cut (E3 + rotation)

def _append_manifest(entry):
    d = archive_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(manifest_path(), "ab") as f:
        f.write((json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    _fsync_dir(d)


def _remove_stale_tmp():
    p = live_path()
    for t in (p.with_name(p.name + ".trim.tmp"),):
        try:
            t.unlink()
        except FileNotFoundError:
            pass
    d = archive_dir()
    if d.exists():
        for t in d.glob("*.gz.tmp"):
            try:
                t.unlink()
            except OSError:
                pass


def _prefix_is(n, sha):
    """True when the live file still starts with exactly the n bytes of that sha256 (the crash case: swap never ran)."""
    try:
        with open(live_path(), "rb") as f:
            return bool(n and sha) and os.fstat(f.fileno()).st_size >= int(n) and hash_prefix(f, int(n))[0] == sha
    except (OSError, Refused):
        return False


def reconcile():
    """Void an entry whose os.replace never happened (crash between the manifest line and the swap). Returns the
    applied entries; raises Refused when the manifest and the live file disagree in a way no crash explains."""
    entries, bad = read_manifest()
    if bad:
        raise Refused(409, "manifest has unreadable lines", bad=bad)
    applied = applied_entries(entries)
    if not applied:
        return applied
    last = applied[-1]
    try:
        ino = os.stat(live_path()).st_ino
    except FileNotFoundError:
        raise Refused(409, "live file missing")
    rebinds = [e for e in entries if e.get("kind") == "rebind" and e["seq"] > last["seq"]]
    marker = rebinds[-1]["new_inode"] if rebinds else last.get("new_inode")
    tb, want = int(last.get("tail_bytes") or 0), last.get("tail_sha256")

    def _starts_with_kept_tail():
        if not (want and tb):
            return False
        try:
            with open(live_path(), "rb") as f:
                return hash_prefix(f, tb)[0] == want
        except (OSError, Refused):
            return False

    # an inode number alone is not identity: Linux reuses a freed inode number for the next file created, so a live file
    # replaced outside a cut can carry the marker inode (or the old one). Ours always still starts with the last cut's kept
    # tail; an entry without tail fields (every cut writes them) is never trusted on its inode - it falls to the refusal.
    if ino == marker and _starts_with_kept_tail():
        return applied
    # crash between the manifest line and the swap: the live file still starts with exactly the archived range (sha256).
    # Decided on content: even when a reused inode number lands here, a file that holds the exact archived bytes [0,N)
    # makes "the cut never took effect" true - voiding loses nothing (those bytes are in the live file).
    if ino == last.get("old_inode") and not rebinds and _prefix_is(last.get("archived_bytes"), last.get("sha256")):
        _append_manifest({"seq": max(e["seq"] for e in entries) + 1, "kind": "void", "voids": last["seq"],
                          "reason": "swap never happened (its bytes are still in the live file)", "at": _now_iso()})
        STATS["voided"] += 1
        return applied_entries()
    # the inode changed outside a cut (disk migration / restore / resize copy): the file is still ours when it starts with
    # exactly the tail the last cut kept - then the new inode is recorded ("rebind") and nothing else changes
    if ino != marker and _starts_with_kept_tail():
        _append_manifest({"seq": max(e["seq"] for e in entries) + 1, "kind": "rebind", "after": last["seq"],
                          "old_marker": marker, "new_inode": ino, "at": _now_iso(),
                          "reason": "live file starts with the last cut's kept tail (sha256 checked); inode changed outside a cut"})
        return applied
    raise Refused(409, "live file is neither the last cut's old nor new file", inode=ino, last_seq=last["seq"])


def _locked_cut(f, n, entry, dry_run, min_tail):
    """Step 2 of a cut (see the module docstring). f: the open handle whose prefix [0,n) was verified. Closes f on commit."""
    import comparison
    p = live_path()
    with comparison._SNAP_LOCK:
        st_fd, st_p = os.fstat(f.fileno()), os.stat(p)
        if (st_fd.st_ino, st_fd.st_dev) != (st_p.st_ino, st_p.st_dev):
            raise Refused(409, "file replaced since the hash")
        size = st_p.st_size
        if size < n:
            raise Refused(409, "file shorter than the archived range", size=size)
        tail = size - n
        if tail < min_tail:
            raise Refused(409, "kept tail is under the dedupe seed window", tail_bytes=tail, min_tail=min_tail)
        free = shutil.disk_usage(p.parent).free
        if free < tail + TRIM_FREE_MARGIN:
            raise Refused(409, "not enough free disk for the tail copy", free_bytes=free, need=tail + TRIM_FREE_MARGIN)
        info = {"size_now": size, "archived_bytes": n, "tail_bytes": tail, "free_bytes": free, "would_free_bytes": n}
        if dry_run:
            return dict(info, dry_run=True)
        tmp = p.with_name(p.name + ".trim.tmp")
        h = hashlib.sha256()
        try:
            with open(tmp, "wb") as out:
                f.seek(n)
                left = tail
                while left:
                    b = f.read(min(CHUNK, left))
                    if not b:
                        raise Refused(409, "tail read short")
                    out.write(b)
                    h.update(b)
                    left -= len(b)
                out.flush()
                os.fsync(out.fileno())
            h2 = hashlib.sha256()
            with open(tmp, "rb") as chk:
                for b in iter(lambda: chk.read(CHUNK), b""):
                    h2.update(b)
            if h2.hexdigest() != h.hexdigest() or os.path.getsize(tmp) != tail:
                raise Refused(500, "tail copy did not read back")
            entry = dict(entry, archived_bytes=n, old_inode=st_p.st_ino, new_inode=os.stat(tmp).st_ino,
                         live_size_at_cut=size, tail_bytes=tail, tail_sha256=h.hexdigest(), cut_at=_now_iso(),
                         version=VERSION)
            _step("before_manifest")
            _append_manifest(entry)
            _step("before_replace")
            f.close()
            os.replace(tmp, p)
            _fsync_dir(p.parent)
        except BaseException:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise
        return dict(info, dry_run=False, entry=entry)


def _step(name):
    """Crash-test hook (tests raise here). No-op in production."""
    return None


def _history_offset(applied):
    return sum(int(e.get("archived_bytes") or 0) for e in applied)


def trim_comparison(body):
    """E3. (status, dict). Dry run unless body["dry_run"] is exactly False."""
    import comparison
    if not isinstance(body, dict):
        return 400, {"error": "JSON object body required"}
    try:
        n, lines, ino = (int(body[k]) for k in ("archived_bytes", "lines", "inode"))
        sha = str(body["sha256"]).lower()
        assert n > 0 and lines >= 0 and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
    except Exception:
        return 400, {"error": "archived_bytes (>0), sha256 (hex), lines and inode are required"}
    dry_run = body.get("dry_run") is not False
    if not _MAINT_LOCK.acquire(blocking=False):
        return _busy()
    try:
        if not _IO_LOCK.acquire(blocking=False):
            return _busy()
        try:
            _remove_stale_tmp()
            applied = reconcile()
            f = open(live_path(), "rb")
            try:
                st = os.fstat(f.fileno())
                if st.st_ino != ino:
                    raise Refused(409, "inode mismatch", inode=st.st_ino)
                if st.st_size < n:
                    raise Refused(409, "file shorter than the archived range", size=st.st_size)
                got, got_lines, last = hash_prefix(f, n)
                if got != sha or got_lines != lines:
                    raise Refused(409, "sha256 / line count mismatch: nothing changed", sha256=got, lines=got_lines)
                if last != b"\n":
                    raise Refused(409, "the archived range does not end at a line break")
                entry = {"seq": max([e["seq"] for e in read_manifest()[0]] or [0]) + 1, "kind": "manual_trim",
                         "sha256": sha, "lines": lines, "history_offset": _history_offset(applied),
                         "first_line": _line_at(f, 0, n), "last_line": _line_at(f, _last_line_start(f, n), n),
                         "copies": "two local copies verified before the trim: C:/Users/tikic/cs2_scout/exports/ + D:/scout_storage_archive/"}
                res = _locked_cut(f, n, entry, dry_run, comparison.SEED_BYTES)
            finally:
                if not f.closed:
                    f.close()
            STATS["trims"] += 0 if dry_run else 1
            return 200, dict(res, version=VERSION)
        except Refused as r:
            STATS["trim_refused"] += 1
            return r.code, r.body
        finally:
            _IO_LOCK.release()
    finally:
        _MAINT_LOCK.release()


# ---------------------------------------------------------------- rotation (rot-1)

def _skip(why, **kw):
    STATS["rotation_skips"][why] = STATS["rotation_skips"].get(why, 0) + 1
    return dict(kw, rotated=False, skip=why)


def rotate_once(force_checks=True):
    """One rotation check. Returns what happened (never raises for an expected skip)."""
    import comparison
    if os.environ.get("COMPARISON_ROTATION", "1") == "0":
        return _skip("off")
    if not _MAINT_LOCK.acquire(blocking=False):
        return _skip("busy")
    try:
        if not _IO_LOCK.acquire(timeout=60):
            return _skip("busy")
        try:
            _remove_stale_tmp()
            try:
                applied = reconcile()
            except Refused as r:
                STATS["rotation_error"] = dict(r.body, at=_now_iso())      # an alert in status: the file would regrow silently
                return _skip("manifest", detail=r.body)
            if not any(e.get("kind") == "manual_trim" for e in applied):
                return _skip("no_manual_trim")
            p = live_path()
            try:
                size = os.path.getsize(p)
            except FileNotFoundError:
                return _skip("no_live_file")
            if size <= LIVE_MAX_MB * MIB:
                return _skip("under_max", size=size)
            f = open(p, "rb")
            try:
                st = os.fstat(f.fileno())
                size = st.st_size
                cut = cut_point(f, size, ROT_KEEP_BYTES)
                if cut <= 0:
                    return _skip("no_boundary")
                free = shutil.disk_usage(p.parent).free
                need = comparison.DISK_FLOOR_MB * MIB + cut * ROT_GZ_RATIO + (size - cut) + TRIM_FREE_MARGIN
                if free < need:
                    return _skip("low_disk", free_bytes=free, need=int(need))
                entries = read_manifest()[0]
                seq = max([e["seq"] for e in entries] or [0]) + 1
                name = f"comparison_seg_{seq:05d}.jsonl.gz"
                if name in _segment_files(entries):
                    return _skip("name_taken", file=name)
                d = archive_dir()
                d.mkdir(parents=True, exist_ok=True)
                final, tmp = d / name, d / (name + ".tmp")
                h, lines, pos = hashlib.sha256(), 0, 0
                with open(tmp, "wb") as raw:
                    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as gz:
                        f.seek(0)
                        while pos < cut:
                            b = f.read(min(CHUNK, cut - pos))
                            if not b:
                                raise Refused(409, "live file read short")
                            gz.write(b)
                            h.update(b)
                            lines += b.count(b"\n")
                            _drop_cache(f, pos, len(b))
                            pos += len(b)
                    raw.flush()
                    os.fsync(raw.fileno())
                raw_sha = h.hexdigest()

                def check(path):
                    h2, n2 = hashlib.sha256(), 0
                    with gzip.open(path, "rb") as g:
                        for b in iter(lambda: g.read(CHUNK), b""):
                            h2.update(b)
                            n2 += b.count(b"\n")
                    gh = hashlib.sha256()
                    with open(path, "rb") as g:
                        for b in iter(lambda: g.read(CHUNK), b""):
                            gh.update(b)
                    if (h2.hexdigest(), n2) != (raw_sha, lines):
                        raise Refused(500, "segment did not read back", file=path.name)
                    return gh.hexdigest()

                gz_sha = check(tmp)
                _step("before_segment_rename")
                os.replace(tmp, final)
                _fsync_dir(d)
                if check(final) != gz_sha:
                    raise Refused(500, "segment changed after rename", file=name)
                entry = {"seq": seq, "kind": "segment", "file": name, "sha256": raw_sha, "lines": lines,
                         "gz_sha256": gz_sha, "gz_bytes": os.path.getsize(final), "history_offset": _history_offset(applied),
                         "first_line": _line_at(f, 0, cut), "last_line": _line_at(f, _last_line_start(f, cut), cut)}
                res = _locked_cut(f, cut, entry, False, comparison.SEED_BYTES)
            finally:
                if not f.closed:
                    f.close()
            STATS["rotations"] += 1
            STATS["rotation_last"] = {"at": _now_iso(), "file": name, "raw_bytes": cut, "gz_bytes": entry["gz_bytes"]}
            STATS["rotation_error"] = None
            return dict(res, rotated=True, file=name)
        except Refused as r:
            STATS["rotation_error"] = dict(r.body, at=_now_iso())
            return _skip("refused", detail=r.body)
        finally:
            _IO_LOCK.release()
    finally:
        _MAINT_LOCK.release()


def _loop():
    if _STOP.wait(ROT_FIRST_DELAY_S):
        return
    while True:
        try:
            rotate_once()
        except Exception as e:      # noqa: BLE001 - the thread never dies; the error is shown in status
            STATS["rotation_error"] = {"error": f"{type(e).__name__}: {str(e)[:200]}", "at": _now_iso()}
        if _STOP.wait(ROT_EVERY_S):
            return


def start():
    if _THREAD["t"] and _THREAD["t"].is_alive():
        return False
    _STOP.clear()
    _THREAD["t"] = threading.Thread(target=_loop, daemon=True, name="comparison-rotation")
    _THREAD["t"].start()
    return True


def status():
    p = live_path()
    try:
        st = os.stat(p)
        live = {"bytes": st.st_size, "inode": st.st_ino, "mtime": st.st_mtime}
    except FileNotFoundError:
        live = None
    entries, bad = read_manifest()
    applied = applied_entries(entries)
    seg_bytes = sum(int(e.get("gz_bytes") or 0) for e in applied if e.get("kind") == "segment")
    alerts = []
    if seg_bytes > SEGMENT_ALERT_MB * 1e6:
        alerts.append("segments_over_%d_mb" % SEGMENT_ALERT_MB)
    if bad:
        alerts.append("manifest_unreadable_lines")
    if STATS.get("rotation_error"):
        alerts.append("rotation_error")
    return {"version": VERSION, "live": live, "live_max_mib": LIVE_MAX_MB, "rotation_keep_bytes": ROT_KEEP_BYTES,
            "rotation_on": os.environ.get("COMPARISON_ROTATION", "1") != "0",
            "manifest": {"entries": len(entries), "applied": len(applied), "unreadable": bad,
                         "archived_bytes_total": _history_offset(applied), "segment_gz_bytes": seg_bytes,
                         "last": applied[-1] if applied else None},
            "stats": STATS, "alerts": alerts}


def disk_detail():
    """E7: each top-level file of the data dir and of its .cache with size + allocated bytes (bounded: 2 x 500 entries)."""
    base = live_path().parent
    out = {}
    for d in (base, base / ".cache"):
        rows = []
        try:
            with os.scandir(d) as it:
                for i, e in enumerate(it):
                    if i >= 500:
                        break
                    try:
                        st = e.stat(follow_symlinks=False)
                        rows.append({"name": e.name, "dir": e.is_dir(follow_symlinks=False), "bytes": st.st_size,
                                     "on_disk": getattr(st, "st_blocks", 0) * 512})
                    except OSError:
                        pass
        except OSError:
            pass
        out[str(d.relative_to(base)) if d != base else "."] = sorted(rows, key=lambda r: -max(r["bytes"], r["on_disk"]))
    du = shutil.disk_usage(base)
    return {"total": du.total, "used": du.used, "free": du.free, "entries": out}


# ---------------------------------------------------------------- moneyline store (E4-E6, read-only)

def canonical(row):
    """One canonical line: json of [rowid, *columns] with no spaces, UTF-8, newline. BLOB / NaN / inf refuse (never a
    silent change of a value)."""
    for v in row:
        if isinstance(v, (bytes, bytearray, memoryview)):
            raise ValueError(f"BLOB value in rowid {row[0]}")
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError(f"non-finite REAL in rowid {row[0]}")
    return (json.dumps(list(row), separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _ml_ro():
    db, _ = ml_paths()
    if not db.exists():
        raise Refused(404, "ml_snapshots.db missing")
    import ml_store
    return ml_store._ro(db)


def _table(q):
    t = q.get("table")
    if t not in ML_TABLES:
        raise Refused(400, f"table must be one of {', '.join(ML_TABLES)}")
    return t


def ml_rows(q):
    """E5. (status, bytes | dict, headers)."""
    try:
        t = _table(q)
        after = _int(q, "after", default=0, lo=0)
        upto = _int(q, "upto", default=-1, lo=-1)
        limit = _int(q, "limit", default=ROWS_MAX, lo=1, hi=ROWS_MAX)
    except Refused as r:
        return r.code, r.body, {}
    mutable = t in ML_MUTABLE
    if not _IO_LOCK.acquire(blocking=False):
        return _busy() + ({"Retry-After": "2"},)
    try:
        con = _ml_ro()
        try:
            if mutable:                          # whole table, one statement (a snapshot of a mutable table)
                cur = con.execute(f"SELECT rowid, * FROM {t} ORDER BY rowid")
            elif upto >= 0:
                cur = con.execute(f"SELECT rowid, * FROM {t} WHERE rowid > ? AND rowid <= ? ORDER BY rowid LIMIT ?", (after, upto, limit))
            else:
                cur = con.execute(f"SELECT rowid, * FROM {t} WHERE rowid > ? ORDER BY rowid LIMIT ?", (after, limit))
            cols = [c[0] for c in cur.description]
            parts, size, n, last, cut = [], 0, 0, after, False
            for row in cur:
                ln = canonical(row)
                if mutable and size + len(ln) > ROWS_BYTES_MAX:     # whole or nothing: never a truncated "done" page
                    return 413, {"error": "mutable table over the page bound"}, {}
                if n and size + len(ln) > ROWS_BYTES_MAX:
                    cut = True
                    break
                parts.append(ln)
                size += len(ln)
                n += 1
                last = row[0]
            cur.close()
        finally:
            con.close()
    except Refused as r:
        return r.code, r.body, {}
    except (ValueError, sqlite3.Error) as e:
        return 500, {"error": f"{type(e).__name__}: {str(e)[:200]}"}, {}
    finally:
        _IO_LOCK.release()
    data = b"".join(parts)
    done = mutable or (not cut and n < limit)
    return 200, data, {"X-Table": t, "X-Cols": json.dumps(cols), "X-Count": str(n), "X-After": str(after),
                       "X-Next-After": str(last), "X-Done": "1" if done else "0", "X-Upto": str(upto),
                       "X-Sha256": hashlib.sha256(data).hexdigest(), "X-Mutable": "1" if mutable else "0",
                       "Content-Encoding": "identity"}


def ml_digest(q):
    """E6. (status, dict): count, min/max rowid and sha256 over the canonical lines of rowid in (after, upto]. One call
    stops after DIGEST_TIME_S and returns complete=false with next_after (the range it covered is (after, next_after])."""
    try:
        t = _table(q)
        after = _int(q, "after", default=0, lo=0)
        upto = _int(q, "upto", default=-1, lo=-1) if t in ML_MUTABLE else _int(q, "upto", lo=0)
    except Refused as r:
        return r.code, r.body
    if not _IO_LOCK.acquire(blocking=False):
        return _busy()
    try:
        con = _ml_ro()
        h, n, lo, hi, pos, t0, complete = hashlib.sha256(), 0, None, None, after, time.time(), True
        try:
            if t in ML_MUTABLE:
                for row in con.execute(f"SELECT rowid, * FROM {t} ORDER BY rowid"):
                    h.update(canonical(row)); n += 1
                    lo = row[0] if lo is None else lo
                    hi = pos = row[0]
            else:
                while True:
                    rows = con.execute(f"SELECT rowid, * FROM {t} WHERE rowid > ? AND rowid <= ? ORDER BY rowid LIMIT ?",
                                       (pos, upto, DIGEST_BATCH)).fetchall()
                    for row in rows:
                        h.update(canonical(row)); n += 1
                        lo = row[0] if lo is None else lo
                        hi = row[0]
                    if len(rows) < DIGEST_BATCH:
                        pos = upto
                        break
                    pos = rows[-1][0]
                    if time.time() - t0 > DIGEST_TIME_S:
                        complete = False
                        break
        finally:
            con.close()
        return 200, {"table": t, "after": after, "upto": upto, "next_after": pos, "complete": complete, "count": n,
                     "min_rowid": lo, "max_rowid": hi, "sha256": h.hexdigest(), "took_s": round(time.time() - t0, 2)}
    except Refused as r:
        return r.code, r.body
    except (ValueError, sqlite3.Error) as e:
        return 500, {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        _IO_LOCK.release()


def ml_tables(q):
    """E4. (status, dict): page / payload split per table + index (dbstat when compiled in), rows + rowid range per
    table, the freelist, the WAL size and the schema. Cached TABLES_TTL_S (refresh=1 re-reads)."""
    if _TABLES["body"] and time.time() - _TABLES["at"] < TABLES_TTL_S and q.get("refresh") != "1":
        return 200, dict(_TABLES["body"], cached=True)
    if not _IO_LOCK.acquire(blocking=False):
        return _busy()
    try:
        t0 = time.time()
        db, _ = ml_paths()
        con = _ml_ro()
        try:
            out = {"db_bytes": db.stat().st_size, "page_size": con.execute("PRAGMA page_size").fetchone()[0],
                   "page_count": con.execute("PRAGMA page_count").fetchone()[0],
                   "freelist_count": con.execute("PRAGMA freelist_count").fetchone()[0]}
            wal = db.with_name(db.name + "-wal")
            out["wal_bytes"] = wal.stat().st_size if wal.exists() else 0
            out["schema"] = [dict(zip(("type", "name", "tbl_name", "sql"), r)) for r in
                             con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY rowid")]
            names = {r["name"] for r in out["schema"] if r["type"] == "table"}
            out["tables"] = {}
            for t in ML_TABLES:
                if t in names:
                    c, lo, hi = con.execute(f"SELECT count(*), min(rowid), max(rowid) FROM {t}").fetchone()
                    out["tables"][t] = {"rows": c, "min_rowid": lo, "max_rowid": hi}
            try:
                out["dbstat"] = [dict(zip(("name", "pages", "payload", "unused", "bytes"), r)) for r in con.execute(
                    "SELECT name, count(*), sum(payload), sum(unused), sum(pgsize) FROM dbstat GROUP BY name ORDER BY sum(pgsize) DESC")]
            except sqlite3.Error as e:
                out["dbstat"] = None
                out["dbstat_error"] = str(e)[:120]
                if "ml_forecasts" in names:
                    cols = [r[1] for r in con.execute("PRAGMA table_info(ml_forecasts)")]
                    expr = " + ".join(f"coalesce(length({c}), 0)" for c in cols)
                    out["ml_forecasts_payload_bytes"] = con.execute(f"SELECT sum({expr}) FROM ml_forecasts").fetchone()[0]
        finally:
            con.close()
        out["took_s"] = round(time.time() - t0, 2)
        out["checked_at"] = _now_iso()
        _TABLES.update(at=time.time(), body=out)
        return 200, out
    except Refused as r:
        return r.code, r.body
    except sqlite3.Error as e:
        return 500, {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        _IO_LOCK.release()


# ---------------------------------------------------------------- HTTP glue (server.py calls these after the admin check)

GET_ROUTES = {"/admin/storage/file_digest", "/admin/storage/file_range", "/admin/storage/ml_tables", "/admin/storage/ml_rows",
              "/admin/storage/ml_digest", "/admin/storage/disk_detail", "/admin/storage/status"}
POST_TRIM = "/admin/storage/comparison_trim"


def handle_get(h, path, q):
    """h: the request handler (already authenticated). Sends exactly one answer."""
    if path == "/admin/storage/file_digest":
        return h.send(*file_digest(q))
    if path == "/admin/storage/file_range":
        code, body, hdr = (file_range(q) + ({},))[:3]
        if isinstance(body, bytes):
            return h.send(code, body, "application/octet-stream", hdr)
        return h.send(code, body, "application/json", hdr)
    if path == "/admin/storage/ml_rows":
        code, body, hdr = (ml_rows(q) + ({},))[:3]
        if isinstance(body, bytes):
            return h.send(code, body, "application/x-ndjson", hdr)
        return h.send(code, body, "application/json", hdr)
    if path == "/admin/storage/ml_digest":
        return h.send(*ml_digest(q))
    if path == "/admin/storage/ml_tables":
        return h.send(*ml_tables(q))
    if path == "/admin/storage/disk_detail":
        return h.send(200, disk_detail())
    if path == "/admin/storage/status":
        return h.send(200, status())
    return h.send(404, {"error": "unknown storage route"})
