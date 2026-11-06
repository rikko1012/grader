"""Pre-game news archive (pgn-1): what was KNOWN before a game, saved going forward, append-only, timestamped at the
moment it was observed. Research input only - nothing here is read by a grader, a board, a card or any member page.

Why (vault note "Projection upgrade 2026-11-05"): every full-history rebuild beat live on all past outcomes and none beat
it at the offered lines - the books know injury / minutes news, inactives and late lineups that our history does not.
That news has to be archived as it is published so a later walk-forward test can use it with pre-match timing.

What is saved (each row = one subject's news as observed at observed_at; a row is written only when the news CHANGED):
  sports (sports/pregame_news.py job, every 15 min)   injury   ESPN league injury report: status, type, short detail,
                                                               return date (never ESPN's news text)
                                                      depth    ESPN depth charts: position, depth, role (announced
                                                               starters for NBA / the offence chart for NFL)
                                                      game_injury / lineup  ESPN game summary for games starting soon:
                                                               the game's injury list (inactives / outs) and the box
                                                               score's starter / active / did-not-play flags once ESPN
                                                               lists them before the start
  cs2 (pregame_news/cs2_picks.py thread, every forecast) roster   roster confirmed, stand-in, stand-in for, on listed
                                                               team, scout - from each forecast's saved grader inputs, read
                                                               READ-ONLY from the pick log (the frozen writer is untouched)
  lol (data-center forecast cycle, next to every saved forecast)  player_role / lineup_assumed: the posted player's team
                                                               + role and the lineup each team fielded in its last game

Shared package (not a CS2 module: LoL, CS2 and sports all import it).

Storage: one SQLite file per service (sports: <SPORTS_DATA_DIR>/pregame_news.db, CS2: <CS2_DATA_DIR>/pregame_news.db,
LoL: <CS2_DATA_DIR>/lol/center/pregame_news.db).
  news     append-only (UPDATE / DELETE abort by trigger): sport, league, kind, scope, subject, event, observed_at,
           start_at, source, content_hash, payload (compact JSON, <= MAX_PAYLOAD bytes)
  current  the last hash per (sport, kind, scope, subject, event) + first / last seen - what makes writes change-only
A subject that leaves a COMPLETE list (full=True: the whole league injury report, one team's depth chart, one game's
injury list) gets one tombstone row {"absent": true} - "no longer listed" is news too. A failed or partial read is never
complete, so a source outage never writes tombstones (transient-failure rule).

Bounded: change-only writes (a few hundred rows a day), payloads capped at MAX_PAYLOAD, at most MAX_ROWS rows per call,
and a hard size budget PREGAME_NEWS_MAX_MB (default 256): once the file reaches it new rows are skipped and counted
(meta budget_skipped), nothing is ever deleted. PREGAME_NEWS=0 turns every writer off.

Point-in-time read: as_of(subject, at) -> the latest row per (kind, scope, event) observed at or before `at`, tombstones
dropped - what a forecast made at `at` could have known.

    python -m pregame_news stats <db>
    python -m pregame_news as-of <db> <sport> <subject> <iso time>
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

VERSION = "pgn-1"
MAX_PAYLOAD = 4096
MAX_ROWS = 20000
_DEDUPE_MAX = 20000          # in-memory last-hash cache for hot writers

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (id INTEGER PRIMARY KEY AUTOINCREMENT, sport TEXT NOT NULL, league TEXT,
  kind TEXT NOT NULL, scope TEXT NOT NULL, subject TEXT NOT NULL, event TEXT NOT NULL DEFAULT '',
  observed_at TEXT NOT NULL, start_at TEXT, source TEXT, content_hash TEXT NOT NULL, payload TEXT NOT NULL,
  version TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS news_subject ON news (sport, subject, observed_at);
CREATE INDEX IF NOT EXISTS news_event ON news (sport, event, observed_at);
CREATE TRIGGER IF NOT EXISTS news_no_update BEFORE UPDATE ON news
  BEGIN SELECT RAISE(ABORT, 'pregame news is append-only'); END;
CREATE TRIGGER IF NOT EXISTS news_no_delete BEFORE DELETE ON news
  BEGIN SELECT RAISE(ABORT, 'pregame news is append-only'); END;
CREATE TABLE IF NOT EXISTS current (sport TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL,
  subject TEXT NOT NULL, event TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL, absent INTEGER NOT NULL DEFAULT 0,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, PRIMARY KEY (sport, kind, scope, subject, event));
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def enabled() -> bool:
    return os.environ.get("PREGAME_NEWS", "1").strip().lower() not in ("0", "false", "no", "off")


def max_bytes() -> int:
    try:
        return int(float(os.environ.get("PREGAME_NEWS_MAX_MB", "256")) * 1_000_000)
    except ValueError:
        return 256_000_000


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canon(payload) -> str:
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    if len(s.encode("utf-8")) > MAX_PAYLOAD:
        keys = sorted(payload)[:50] if isinstance(payload, dict) else None
        s = json.dumps({"truncated": True, "bytes": len(s.encode("utf-8")), "keys": keys,
                        "sha256": hashlib.sha256(s.encode("utf-8")).hexdigest()}, sort_keys=True, separators=(",", ":"))
    return s


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def connect(path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10, check_same_thread=False)   # HotWriter serialises use with its lock
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 10000")
    try:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")       # WAL + NORMAL: durable at checkpoints, no fsync per row
    except sqlite3.OperationalError:
        pass
    con.executescript(SCHEMA)
    return con


def _size(con) -> int:
    pc = con.execute("PRAGMA page_count").fetchone()[0]
    ps = con.execute("PRAGMA page_size").fetchone()[0]
    fl = con.execute("PRAGMA freelist_count").fetchone()[0]
    return (pc - fl) * ps


def _bump(con, k, n):
    con.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = CAST(v AS INTEGER) + ?",
                (k, str(n), n))


def observe(con, sport, league, kind, scope, items, *, observed_at=None, source=None, start_at=None, full=False,
            touch=True) -> dict:
    """items: {(subject, event): payload} (event '' for league-wide news). Appends a row for every new or changed item;
    full=True = items is the COMPLETE list for (sport, kind, scope), so a subject listed before and missing now gets one
    tombstone. touch=False skips the last_seen update of unchanged items (hot writers). One transaction.
    -> {"new", "changed", "unchanged", "absent", "budget_skipped"}."""
    out = {"new": 0, "changed": 0, "unchanged": 0, "absent": 0, "budget_skipped": 0}
    if not enabled():
        out["disabled"] = True
        return out
    at = observed_at or now_iso()
    if len(items) > MAX_ROWS:
        full = False                     # a cut list is not complete: no tombstones from it
    budget = max_bytes()
    over = _size(con) >= budget
    if full:                             # the whole scope: needed for tombstones
        cur = {(r["subject"], r["event"]): (r["content_hash"], r["absent"]) for r in con.execute(
            "SELECT subject, event, content_hash, absent FROM current WHERE sport = ? AND kind = ? AND scope = ?",
            (sport, kind, scope))}
    else:                                # only the listed subjects (a hot writer's scope can hold every subject)
        cur = {}
        for subject, event in list(items)[:MAX_ROWS]:
            r = con.execute("SELECT content_hash, absent FROM current WHERE sport = ? AND kind = ? AND scope = ? AND "
                            "subject = ? AND event = ?", (sport, kind, scope, str(subject), str(event or ""))).fetchone()
            if r:
                cur[(str(subject), str(event or ""))] = (r["content_hash"], r["absent"])
    rows, touched = [], []
    for (subject, event), payload in list(items.items())[:MAX_ROWS]:
        subject, event = str(subject), str(event or "")
        s = _canon(payload)
        h = _hash(s)
        old = cur.get((subject, event))
        if old and old[0] == h and not old[1]:
            out["unchanged"] += 1
            if touch:
                touched.append((at, sport, kind, scope, subject, event))
            continue
        rows.append((subject, event, s, h, "changed" if old else "new"))
    if full:
        listed = {(str(a), str(b or "")) for a, b in items}
        for (subject, event), (h, absent) in cur.items():
            if not absent and (subject, event) not in listed:
                s = _canon({"absent": True})
                rows.append((subject, event, s, _hash(s), "absent"))
    with con:
        if touched:
            con.executemany("UPDATE current SET last_seen_at = ? WHERE sport = ? AND kind = ? AND scope = ? AND "
                            "subject = ? AND event = ?", touched)
        if over and rows:
            out["budget_skipped"] = len(rows)
            _bump(con, "budget_skipped", len(rows))
            con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('budget_full_at', ?)", (at,))
            return out
        for subject, event, s, h, what in rows:
            con.execute("INSERT INTO news (sport, league, kind, scope, subject, event, observed_at, start_at, source, "
                        "content_hash, payload, version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sport, league, kind, scope, subject, event, at, start_at, source, h, s, VERSION))
            con.execute("INSERT INTO current (sport, kind, scope, subject, event, content_hash, absent, first_seen_at, "
                        "last_seen_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(sport, kind, scope, subject, event) DO "
                        "UPDATE SET content_hash = excluded.content_hash, absent = excluded.absent, "
                        "last_seen_at = excluded.last_seen_at",
                        (sport, kind, scope, subject, event, h, int(what == "absent"), at, at))
            out[what] += 1
    return out


def as_of(con, sport, subject, at, kinds=None, event=None) -> list:
    """Latest row per (kind, scope, event) for `subject` observed at or before `at`; tombstones dropped."""
    q = ("SELECT * FROM news WHERE id IN (SELECT MAX(id) FROM news WHERE sport = ? AND subject = ? AND observed_at <= ?"
         + (" AND event IN (?, '')" if event is not None else "")
         + " GROUP BY kind, scope, event) ORDER BY kind, observed_at")
    args = [sport, str(subject), at] + ([str(event)] if event is not None else [])
    out = []
    for r in con.execute(q, args):
        if kinds and r["kind"] not in kinds:
            continue
        p = json.loads(r["payload"])
        if isinstance(p, dict) and p.get("absent"):
            continue
        out.append({"kind": r["kind"], "scope": r["scope"], "event": r["event"] or None,
                    "observed_at": r["observed_at"], "start_at": r["start_at"], "source": r["source"], "news": p})
    return out


def stats(con) -> dict:
    by = {}
    for r in con.execute("SELECT sport, kind, COUNT(*) n, MIN(observed_at) a, MAX(observed_at) b FROM news "
                         "GROUP BY sport, kind"):
        by[f"{r['sport']}:{r['kind']}"] = {"rows": r["n"], "first": r["a"], "last": r["b"]}
    meta = {r["k"]: r["v"] for r in con.execute("SELECT k, v FROM meta")}
    return {"version": VERSION, "bytes": _size(con), "budget_bytes": max_bytes(), "kinds": by, "meta": meta}


class HotWriter:
    """Change-only writer for a busy thread: an in-memory last-hash cache skips the database
    when nothing changed; one lock; every error swallowed (the archive never breaks the caller)."""

    def __init__(self, path_fn):
        self._path_fn = path_fn
        self._lock = threading.Lock()
        self._seen = OrderedDict()
        self._con = None             # (path, connection): one connection kept open, reopened after an error
        self.errors = 0

    def _connection(self):
        path = str(self._path_fn())
        if self._con is None or self._con[0] != path:
            self._close()
            self._con = (path, connect(path))
        return self._con[1]

    def _close(self):
        if self._con is not None:
            try:
                self._con[1].close()
            except Exception:       # noqa: BLE001
                pass
            self._con = None

    def write(self, sport, league, kind, scope, subject, event, payload, observed_at=None, source=None, start_at=None):
        if not enabled():
            return False
        key = (sport, kind, scope, str(subject), str(event or ""))
        h = _hash(_canon(payload))
        with self._lock:
            if self._seen.get(key) == h:
                self._seen.move_to_end(key)
                return False
            try:
                r = observe(self._connection(), sport, league, kind, scope, {(subject, event or ""): payload},
                            observed_at=observed_at, source=source, start_at=start_at, touch=False)
            except Exception:       # noqa: BLE001 - research archive: never fails the caller
                self.errors += 1
                self._close()
                return False
            self._seen[key] = h
            if len(self._seen) > _DEDUPE_MAX:
                self._seen.popitem(last=False)
            return bool(r.get("new") or r.get("changed"))


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) >= 2 and a[0] == "stats":
        con = connect(a[1])
        print(json.dumps(stats(con), indent=1))
        return 0
    if len(a) >= 5 and a[0] == "as-of":
        con = connect(a[1])
        print(json.dumps(as_of(con, a[2], a[3], a[4]), indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
