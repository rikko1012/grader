"""A record of what Best prices (+EV board), Price check and the both-sides list showed (owner 2026-11-12; the 2026-11-12 check
found neither tool had ever written a row, so neither could be audited).

What is written: one JSON line per flagged row, when a shared build first shows it, when its numbers change, and again every
HEARTBEAT_S while it stays (so a later read can tell how long it lasted) - never one per member view. Plus one summary line
per tool and parameter set every SUMMARY_S (counts only), so an empty board is on record too. Each row carries its leg id
(sport | app | offer id | side for a pick'em leg; event | player | stat | period | line for a book line), which is what the
settlement join uses later. Nothing here is shown to a member, and no return or record figure is derived from it.

Where: <data dir>/ev_log/<tool>-<YYYYMMDD>.jsonl on the esports data disk (workspace_api.data_dir()), append-only.
Bounds:  RETENTION_DAYS of files; MAX_TOTAL_BYTES across all files (oldest days go first); MAX_DAY_BYTES per file (rows past
         it are dropped and counted); QUEUE_MAX rows waiting (a full queue drops and counts, it never blocks).
700 members: record() only hashes and enqueues, and it is called from the shared build (one per TTL for everyone), never from
a member request; one daemon thread does every file write. A failed write is counted and dropped - it never reaches a page.
"""
import hashlib
import json
import os
import queue
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

TOOLS = ("ev", "price", "arb")
DIR_NAME = "ev_log"
RETENTION_DAYS = 45
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_DAY_BYTES = 12 * 1024 * 1024
QUEUE_MAX = 4000
SEEN_MAX = 20000
HEARTBEAT_S = 15 * 60
SUMMARY_S = 10 * 60
PRUNE_S = 3600
PAGE_MAX = 500
VOLATILE = ("age_s", "seen_at", "oldest_leg_s", "as_of", "at")          # never part of "did this row change"

_Q = queue.Queue(maxsize=QUEUE_MAX)
_LOCK = threading.Lock()
_SEEN = OrderedDict()                   # (tool, row id) -> (signature, last written)
_SUMMARY = {}                           # (tool, params key) -> last written
_STATE = {"dir": None, "thread": None, "written": 0, "dropped_queue": 0, "dropped_size": 0, "errors": 0, "last_prune": 0.0,
          "last_write": None}
_FILE = re.compile(r"^(ev|price|arb)-(\d{8})\.jsonl$")


def enabled():
    return os.environ.get("SCOUT_EV_LOG", "1").strip() != "0"


def configure(path):
    """Set the folder (tests; the server resolves it from workspace_api.data_dir() on first use)."""
    with _LOCK:
        _STATE["dir"] = None if path is None else Path(path)


def _dir():
    d = _STATE["dir"]
    if d is None:                                      # resolved on every use: the data dir is the server's, never cached here
        import workspace_api
        d = Path(workspace_api.data_dir()) / DIR_NAME
    return d


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in sorted(x.items()) if k not in VOLATILE}
    if isinstance(x, (list, tuple)):
        return [_strip(v) for v in x]
    return x


def _sig(row):
    return hashlib.sha1(json.dumps(_strip(row), sort_keys=True, default=str).encode()).hexdigest()[:16]


def record(tool, rows, summary=None, params="", now=None):
    """Queue what one shared build showed. rows: [{"id": leg id, ...}]. Returns how many rows were queued. Never raises."""
    if tool not in TOOLS or not enabled():
        return 0
    now = time.time() if now is None else now
    out = []
    try:
        with _LOCK:
            for r in rows or ():
                k = (tool, str(r.get("id")), params)
                sig = _sig(r)
                hit = _SEEN.get(k)
                if hit and hit[0] == sig and now - hit[1] < HEARTBEAT_S:
                    _SEEN.move_to_end(k)
                    continue
                _SEEN[k] = (sig, now)
                _SEEN.move_to_end(k)
                out.append(dict(r, kind="row", first=hit is None, changed=bool(hit) and hit[0] != sig))
            while len(_SEEN) > SEEN_MAX:
                _SEEN.popitem(last=False)
            if summary is not None and now - _SUMMARY.get((tool, params), 0.0) >= SUMMARY_S:
                _SUMMARY[(tool, params)] = now
                if len(_SUMMARY) > 500:
                    for kk in sorted(_SUMMARY, key=_SUMMARY.get)[:250]:
                        _SUMMARY.pop(kk, None)
                out.append(dict(summary, kind="summary"))
        n = 0
        for r in out:
            r.update(tool=tool, at=datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), params=params)
            try:
                _Q.put_nowait((tool, now, r))
                n += 1
            except queue.Full:
                _STATE["dropped_queue"] += 1
        if n:
            _start()
        return n
    except Exception:                                  # noqa: BLE001 - the record never breaks a build
        _STATE["errors"] += 1
        return 0


def _start():
    with _LOCK:
        t = _STATE["thread"]
        if t is not None and t.is_alive():
            return
        t = _STATE["thread"] = threading.Thread(target=_run, name="ev-log", daemon=True)
        t.start()


def _run():
    while True:
        try:
            item = _Q.get(timeout=30)
        except queue.Empty:
            continue
        batch = [item]
        while len(batch) < 500:
            try:
                batch.append(_Q.get_nowait())
            except queue.Empty:
                break
        try:
            _write(batch)
        except Exception:                              # noqa: BLE001
            _STATE["errors"] += 1
        finally:
            for _ in batch:
                _Q.task_done()


def _write(batch):
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    by = {}
    for tool, t, r in batch:
        day = datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%d")
        by.setdefault(d / f"{tool}-{day}.jsonl", []).append(json.dumps(r, default=str, separators=(",", ":")))
    for path, lines in by.items():
        size = path.stat().st_size if path.exists() else 0
        keep = []
        for ln in lines:
            if size + len(ln) + 1 > MAX_DAY_BYTES:
                _STATE["dropped_size"] += 1
                continue
            size += len(ln) + 1
            keep.append(ln)
        if keep:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(keep) + "\n")
            _STATE["written"] += len(keep)
            _STATE["last_write"] = time.time()
    if time.time() - _STATE["last_prune"] > PRUNE_S:
        prune()


def files():
    """[(path, tool, day, bytes)] oldest day first."""
    d = _dir()
    out = []
    if d.is_dir():
        for p in d.iterdir():
            m = _FILE.match(p.name)
            if m:
                try:
                    out.append((p, m.group(1), m.group(2), p.stat().st_size))
                except OSError:
                    pass
    return sorted(out, key=lambda x: (x[2], x[1]))


def prune(now=None):
    """Drop days past RETENTION_DAYS, then the oldest days until the folder is under MAX_TOTAL_BYTES. -> files removed."""
    now = time.time() if now is None else now
    _STATE["last_prune"] = now
    cut = datetime.fromtimestamp(now - RETENTION_DAYS * 86400, timezone.utc).strftime("%Y%m%d")
    fs, gone = files(), 0
    total = sum(f[3] for f in fs)
    today = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")
    for p, _tool, day, size in fs:
        if day < cut or (total > MAX_TOTAL_BYTES and day < today):
            try:
                p.unlink()
                total -= size
                gone += 1
            except OSError:
                pass
    return gone


def flush(timeout=5.0):
    """Wait until everything queued so far is on disk (tests and the admin read)."""
    end = time.time() + timeout
    while _Q.unfinished_tasks and time.time() < end:
        time.sleep(0.01)
    return not _Q.unfinished_tasks


def status():
    fs = files()
    return {"enabled": enabled(), "files": [{"tool": t, "day": d, "bytes": s} for _p, t, d, s in fs], "bytes": sum(f[3] for f in fs),
            "written": _STATE["written"], "queued": _Q.qsize(), "dropped_queue_full": _STATE["dropped_queue"],
            "dropped_day_full": _STATE["dropped_size"], "errors": _STATE["errors"],
            "last_write": None if _STATE["last_write"] is None else datetime.fromtimestamp(_STATE["last_write"], timezone.utc).isoformat(timespec="seconds"),
            "limits": {"retention_days": RETENTION_DAYS, "max_total_bytes": MAX_TOTAL_BYTES, "max_day_bytes": MAX_DAY_BYTES,
                       "queue_max": QUEUE_MAX, "heartbeat_s": HEARTBEAT_S, "summary_s": SUMMARY_S, "page_max": PAGE_MAX}}


def page(tool, day, after=0, limit=200):
    """One page of a day's file, oldest first. `after` = the byte offset the previous page returned as `next`."""
    if tool not in TOOLS or not re.fullmatch(r"\d{8}", str(day or "")):
        return {"error": "tool = ev | price | arb and day = YYYYMMDD are required"}
    limit = max(1, min(PAGE_MAX, int(limit or 200)))
    after = max(0, int(after or 0))
    path = _dir() / f"{tool}-{day}.jsonl"
    rows, pos = [], after
    if path.exists():
        with open(path, "rb") as f:
            f.seek(after)
            while len(rows) < limit:
                ln = f.readline()
                if not ln or not ln.endswith(b"\n"):
                    break                              # a line still being written is left for the next page
                pos += len(ln)
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
        size = path.stat().st_size
    else:
        size = 0
    return {"tool": tool, "day": day, "after": after, "next": pos, "more": pos < size, "bytes": size, "rows": rows}
