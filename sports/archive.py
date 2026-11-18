"""Compaction of COLD canonical-store rows into verified archive files - nothing is deleted, every row stays readable
(owner 2026-10-24: "make this work with the cheapest plan without getting rid of any data").

What is cold: every revision of an OFFER identity (platform + provider offer id) whose newest sighting
(max of last_seen_at / observed_at / known_at over its revisions) is older than SPORTS_ARCHIVE_AFTER_H (72 h) - the
book removed it or the game started three days ago. Events, players, stats, contexts, forecasts and settlements stay
hot (small, or read in full by learning / results).

Where it goes (same crash-safe protocol as the esports ml_store archive, ml_store.py):
  <SPORTS_DATA_DIR>/archive/records/offer-<league>-<day>-<seq>.jsonl.xz  one JSON line per row with EVERY column
      verbatim (payload as its decompressed JSON text, content_hash with any "~r<rev>" tag), lzma preset 6
  canonical.db  archive_files  manifest: file, rows, sha256 of the lines, bytes, rid range, version
                archive_rows   one slim row per archived row (identity, dims, line, times, file + line number) -
                               what queries filter on; archive_dims holds the repeated strings once
  Pass per part: write temp file -> fsync -> re-read + check (rows, sha256, every payload re-parses to its stored
  content hash) -> rename -> fsync dir -> re-read the final file -> ONE transaction: manifest row + archive_rows +
  DELETE of exactly those rids (rows re-checked unchanged first; the delete must touch exactly that many rows).
  A crash before the commit leaves every row hot (an orphan final file is never listed and is redone); after the
  commit the rows exist only in the verified file + index. `verify` re-reads every listed file against the manifest.

Reads (transparent): install(con) adds the TEMP VIEWs records_all (every column) and records_meta_all (metadata only,
no file reads - what selections GROUP BY over before fetching the chosen rows by rid) - records_all (hot records UNION ALL archived rows, same columns;
payload / content_hash / schema_version / stored_at come from the file through the SQL function sports_arch, files
cached in a bounded LRU) - store.py's record lookups, point-in-time and event / player-scoped reads use it, so callers
cannot tell where a row lives. League-wide CURRENT listings (store.offers without as_of, raw SQL on `records`) read
the hot window - an offer not seen for 72 h is not current; this is what keeps the builders' memory bounded.

    python -m sports.archive pass [--max-parts 40]     # one bounded pass (the scheduler runs it every 6 h)
    python -m sports.archive verify                    # re-read every listed file (daily)
    python -m sports.archive stats                     # sizes / counts (read-only)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
import sqlite3
import threading
import time
import zlib
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

VERSION = "arch-2"           # arch-2 (2026-10-24): one index row per identity segment + a tiny row per revision
AFTER_H = float(os.environ.get("SPORTS_ARCHIVE_AFTER_H", "72"))
PART_ROWS = int(os.environ.get("SPORTS_ARCHIVE_PART_ROWS", "5000"))
DISK_FLOOR_MB = float(os.environ.get("SPORTS_ARCHIVE_DISK_FLOOR_MB", "300"))
LRU_BYTES = int(float(os.environ.get("SPORTS_ARCHIVE_CACHE_MB", "32")) * 1_000_000)
COLS = ("rid", "kind", "record_id", "source", "source_key", "revision", "content_hash", "schema_version", "league",
        "event_id", "player_id", "stat", "platform", "line", "observed_at", "known_at", "stored_at", "payload",
        "last_seen_at", "seen_count")
# line correctness (store migration 3, sports/LINE_CORRECTNESS.md): the late flag is indexed (archive_rows.late), the
# source / origin columns live in the file; a store or file written before migration 3 reads them as 0 / NULL
LINE_COLS = ("late", "source_updated_at", "last_source_updated_at", "origin")
COLS = COLS + LINE_COLS
LINE_DEFAULTS = {"late": "0", "source_updated_at": "NULL", "last_source_updated_at": "NULL", "origin": "NULL"}
DIM_COLS = ("kind", "source", "league", "event_id", "player_id", "stat", "platform")
FILE_COLS = ("content_hash", "schema_version", "stored_at", "payload", "source_updated_at", "last_source_updated_at",
             "origin")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS archive_files (
         id INTEGER PRIMARY KEY, file TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, league TEXT, day TEXT NOT NULL,
         seq INTEGER NOT NULL, rows INTEGER NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
         min_rid INTEGER NOT NULL, max_rid INTEGER NOT NULL, archived_at TEXT NOT NULL, version TEXT NOT NULL,
         verified_at TEXT)""",
    "CREATE TABLE IF NOT EXISTS archive_dims (id INTEGER PRIMARY KEY, v TEXT NOT NULL UNIQUE)",
    """CREATE TABLE IF NOT EXISTS archive_rows (
         rid INTEGER PRIMARY KEY, kind INTEGER NOT NULL, source INTEGER NOT NULL, source_key TEXT NOT NULL,
         revision INTEGER NOT NULL, h10 TEXT NOT NULL, rid_text TEXT, league INTEGER, event_id INTEGER,
         player_id INTEGER, stat INTEGER, platform INTEGER, line REAL, observed_at TEXT, known_at TEXT NOT NULL,
         last_seen_at TEXT, seen_count INTEGER NOT NULL, file INTEGER NOT NULL, ln INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS archive_rows_ident ON archive_rows (source, source_key, revision)",
    "CREATE INDEX IF NOT EXISTS archive_rows_event ON archive_rows (event_id)",
    "CREATE INDEX IF NOT EXISTS archive_rows_player ON archive_rows (player_id, stat)",
    "CREATE INDEX IF NOT EXISTS archive_rows_league ON archive_rows (kind, league, known_at)",
    "CREATE INDEX IF NOT EXISTS archive_rows_ridtext ON archive_rows (rid_text) WHERE rid_text IS NOT NULL",
]


# arch-2 index: per archived revision only (rid, segment, revision, hash prefix as an integer, known time as epoch
# seconds, late flag, line number in the file) - ~25-45 B; identity + dims once per SEGMENT (consecutive revisions of
# one identity with the same dims, in one file). arch_text holds the rare values that cannot be rebuilt exactly.
SCHEMA2 = [
    """CREATE TABLE IF NOT EXISTS arch_segs (
         id INTEGER PRIMARY KEY, kind INTEGER NOT NULL, source INTEGER NOT NULL, source_key TEXT NOT NULL,
         league INTEGER, event_id INTEGER, player_id INTEGER, stat INTEGER, platform INTEGER, file INTEGER NOT NULL,
         min_rev INTEGER NOT NULL, max_rev INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS arch_segs_ident ON arch_segs (source, source_key)",
    "CREATE INDEX IF NOT EXISTS arch_segs_event ON arch_segs (event_id)",
    "CREATE INDEX IF NOT EXISTS arch_segs_player ON arch_segs (player_id, stat)",
    "CREATE INDEX IF NOT EXISTS arch_segs_league ON arch_segs (kind, league)",
    """CREATE TABLE IF NOT EXISTS arch_revs (
         rid INTEGER PRIMARY KEY, seg INTEGER NOT NULL, revision INTEGER NOT NULL, h INTEGER NOT NULL,
         known INTEGER NOT NULL, late INTEGER NOT NULL DEFAULT 0, ln INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS arch_revs_seg ON arch_revs (seg, revision)",
    "CREATE TABLE IF NOT EXISTS arch_text (rid INTEGER PRIMARY KEY, record_id TEXT, known_at TEXT)",
]
TS = "%Y-%m-%dT%H:%M:%SZ"
META = ("rid", "kind", "record_id", "source", "source_key", "revision", "league", "event_id", "player_id", "stat",
        "platform", "line", "observed_at", "known_at", "last_seen_at", "seen_count", "late")


def archive_dir() -> Path:
    return config.DATA_DIR / "archive" / "records"


def ensure_schema(con):
    for s in SCHEMA + SCHEMA2:
        con.execute(s)
    con.commit()


def _has2(con):
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='arch_revs'").fetchone() is not None
    except sqlite3.Error:
        return False


def _has_tables(con):
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_rows'").fetchone() is not None
    except sqlite3.Error:
        return False


# --------------------------------------------------------------------------------------------- file cache (reads)
class _Files:
    def __init__(self):
        self.lock = threading.Lock()
        self.lru = OrderedDict()                        # file id -> (lines list, bytes)
        self.size = 0
        self.names = {}                                 # (db path, file id) -> file name
        self.stats = {"loads": 0, "hits": 0}

    def rows(self, con_path, fid, name_lookup):
        key = (con_path, fid)
        with self.lock:
            hit = self.lru.get(key)
            if hit is not None:
                self.lru.move_to_end(key)
                self.stats["hits"] += 1
                return hit[0]
        name = name_lookup(fid)
        path = Path(con_path).parent / "archive" / "records" / name
        with lzma.open(path, "rb") as f:
            raw = f.read()
        lines = raw.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        parsed = _Lines(lines)
        with self.lock:
            self.stats["loads"] += 1
            have = self.lru.get(key)
            if have is not None:                        # another thread loaded the same file meanwhile: keep ITS entry,
                self.lru.move_to_end(key)               # never count the bytes twice (bug sweep 2026-11-06 #32)
                return have[0]
            self.lru[key] = (parsed, len(raw))
            self.size += len(raw)
            while self.size > LRU_BYTES and len(self.lru) > 1:
                _k, (_p, n) = self.lru.popitem(last=False)
                self.size -= n
        return parsed


class _Lines:
    """One archive file's lines, each JSON-parsed only when a column of that row is asked for."""
    __slots__ = ("raw", "done")

    def __init__(self, raw):
        self.raw, self.done = raw, {}

    def __getitem__(self, i):
        d = self.done.get(i)
        if d is None:
            d = self.done[i] = json.loads(self.raw[i])
        return d

    def __len__(self):
        return len(self.raw)


FILES = _Files()


def install(con, db_path=None):
    """Make records_all available on this connection (a TEMP VIEW + the sports_arch SQL function). Safe on every
    connection (read-only too); without archive tables, records_all is just the hot table."""
    try:
        if not _has_tables(con):
            src = "temp.records" if con.execute("SELECT 1 FROM temp.sqlite_master WHERE type='view' AND "
                                                "name='records'").fetchone() else "main.records"
            con.execute(f"CREATE TEMP VIEW IF NOT EXISTS records_all AS SELECT * FROM {src}")
            con.execute(f"CREATE TEMP VIEW IF NOT EXISTS records_meta_all AS SELECT * FROM {src}")
            return False
        path = str(db_path or con.execute("PRAGMA database_list").fetchone()[2])
        names = {}

        def name_of(fid):
            n = names.get(fid)
            if n is None:
                row = con.execute("SELECT file FROM main.archive_files WHERE id = ?", (fid,)).fetchone()
                if row is None:
                    raise sqlite3.DatabaseError(f"archive file id {fid} not in the manifest")
                n = names[fid] = row[0]
            return n

        def arch(fid, ln, col):
            rows = FILES.rows(path, fid, name_of)
            return rows[ln].get(col)
        con.create_function("sports_arch", 3, arch, deterministic=True)
        hot_have = {r[1] for r in con.execute("PRAGMA main.table_info(records)").fetchall()}
        arch_have = {r[1] for r in con.execute("PRAGMA main.table_info(archive_rows)").fetchall()}
        hot = [c if c in hot_have else f"{LINE_DEFAULTS[c]} AS {c}" for c in COLS]
        cols = []
        for c in COLS:
            if c == "record_id":
                cols.append("COALESCE(a.rid_text, dk.v || ':' || ds.v || ':' || a.source_key || ':r' || a.revision || "
                            "':' || a.h10) AS record_id")
            elif c == "kind":
                cols.append("dk.v AS kind")
            elif c == "source":
                cols.append("ds.v AS source")
            elif c in DIM_COLS:                         # joined (not a correlated subquery): filters use indexes
                cols.append(f"d_{c}.v AS {c}")
            elif c == "late":
                cols.append("a.late AS late" if "late" in arch_have else "0 AS late")
            elif c in FILE_COLS:
                cols.append(f"sports_arch(a.file, a.ln, '{c}') AS {c}")
            else:
                cols.append(f"a.{c} AS {c}")
        dim_joins = "".join(f" LEFT JOIN main.archive_dims d_{c} ON d_{c}.id = a.{c}"
                            for c in DIM_COLS if c not in ("kind", "source"))
        arm2, arm2_meta = "", ""
        if _has2(con):
            c2, m2 = [], []
            meta_set = ("rid", "kind", "record_id", "source", "source_key", "revision", "league", "event_id",
                        "player_id", "stat", "platform", "known_at", "late")
            for c in COLS:
                if c == "rid":
                    e = "r.rid"
                elif c == "record_id":
                    e = ("COALESCE(t.record_id, k2.v || ':' || s2.v || ':' || g.source_key || ':r' || r.revision || ':' || "
                         "printf('%010x', r.h))")
                elif c == "kind":
                    e = "k2.v"
                elif c == "source":
                    e = "s2.v"
                elif c == "source_key":
                    e = "g.source_key"
                elif c == "revision":
                    e = "r.revision"
                elif c in DIM_COLS:
                    e = f"e_{c}.v"
                elif c == "known_at":
                    e = f"COALESCE(t.known_at, strftime('{TS}', r.known, 'unixepoch'))"
                elif c == "late":
                    e = "r.late"
                else:                                   # line, times, counts, payload ...: from the file
                    e = f"sports_arch(g.file, r.ln, '{c}')"
                c2.append(f"{e} AS {c}")
                if c in META:                           # the same columns as records_meta_all's other arms
                    m2.append(f"{e} AS {c}" if c in meta_set else f"NULL AS {c}")   # selection only, no file reads
            joins2 = (" FROM main.arch_revs r JOIN main.arch_segs g ON g.id = r.seg JOIN main.archive_dims k2 ON "
                      "k2.id = g.kind JOIN main.archive_dims s2 ON s2.id = g.source LEFT JOIN main.arch_text t ON "
                      "t.rid = r.rid" + "".join(f" LEFT JOIN main.archive_dims e_{c} ON e_{c}.id = g.{c}"
                                                for c in DIM_COLS if c not in ("kind", "source")))
            arm2 = " UNION ALL SELECT " + ", ".join(c2) + joins2
            arm2_meta = " UNION ALL SELECT " + ", ".join(m2) + joins2
        con.execute("DROP VIEW IF EXISTS temp.records_all")
        src = "temp.records" if con.execute("SELECT 1 FROM temp.sqlite_master WHERE type='view' AND "
                                            "name='records'").fetchone() else "main.records"
        hot = list(COLS) if src == "temp.records" else hot     # store.connect's compat view already has them
        con.execute("CREATE TEMP VIEW records_all AS SELECT " + ", ".join(hot) + f" FROM {src} UNION ALL "
                    "SELECT " + ", ".join(cols) + " FROM main.archive_rows a JOIN main.archive_dims dk ON dk.id = a.kind"
                    " JOIN main.archive_dims ds ON ds.id = a.source" + dim_joins + arm2)
        meta = META
        meta_cols = [x for x, c in zip(cols, COLS) if c in meta]
        meta_hot = [x for x, c in zip(hot if src != "temp.records" else list(COLS), COLS) if c in meta]
        con.execute("DROP VIEW IF EXISTS temp.records_meta_all")
        con.execute("CREATE TEMP VIEW records_meta_all AS SELECT " + ", ".join(meta_hot) + f" FROM {src} UNION ALL "
                    "SELECT " + ", ".join(meta_cols) + " FROM main.archive_rows a JOIN main.archive_dims dk ON "
                    "dk.id = a.kind JOIN main.archive_dims ds ON ds.id = a.source" + dim_joins + arm2_meta)
        return True
    except sqlite3.Error:
        return False


def fetch_order(con, rids, batch=400):
    """rids -> batches for `SELECT * FROM records_all WHERE rid IN (...)`: hot rids first, then archived rids grouped by
    their file (in line order), so a selection spanning many archived rows decodes each file once."""
    rids = list(rids)
    if not rids:
        return []
    where = {}
    for i in range(0, len(rids), 900):
        part = rids[i:i + 900]
        q = ",".join("?" * len(part))
        if _has2(con):
            for rid, fid, ln in con.execute(f"SELECT r.rid, g.file, r.ln FROM arch_revs r JOIN arch_segs g ON g.id = r.seg "
                                            f"WHERE r.rid IN ({q})", part):
                where[rid] = (fid, ln)
        if _has_tables(con):
            for rid, fid, ln in con.execute(f"SELECT rid, file, ln FROM archive_rows WHERE rid IN ({q})", part):
                where[rid] = (fid, ln)
    hot = [r for r in rids if r not in where]
    arch = sorted((r for r in rids if r in where), key=lambda r: where[r])
    out = [hot[i:i + batch] for i in range(0, len(hot), batch)]
    cur, cur_f = [], None
    for r in arch:
        f = where[r][0]
        if cur and (f != cur_f or len(cur) >= batch):
            out.append(cur)
            cur = []
        cur.append(r)
        cur_f = f
    if cur:
        out.append(cur)
    return out


def _heads(head):
    """Every (kind, source, source_key) a record-id head 'kind:source:source_key' can stand for. A source may itself
    contain ':' ('oddsapi:fanduel', 'oddspapi:draftkings'), so each split point is tried - the plain one first; the
    index query then confirms the exact identity (bug sweep 2026-11-06 #31)."""
    kind, sep, rest = head.partition(":")
    if not sep:
        return []
    cuts = [i for i, ch in enumerate(rest) if ch == ":"]
    return [(kind, rest[:i], rest[i + 1:]) for i in cuts]


def row_by_record_id(con, record_id):
    """The archived row (records_all columns) for one record id, through the identity index - never a scan."""
    rid = _archived_rid(con, record_id)
    if rid is None:
        return None
    return con.execute("SELECT * FROM records_all WHERE rid = ?", (rid,)).fetchone()


def identity_by_record_id(con, record_id):
    """(kind, source, source_key) of one ARCHIVED record id - the same index lookups as row_by_record_id, then the
    metadata view (records_meta_all), so no archive file is read or decoded (results / ledger links of archived offer
    revisions, 2026-11-08: one file decode per revision). None when the id is not archived."""
    rid = _archived_rid(con, record_id)
    if rid is None:
        return None
    return con.execute("SELECT kind, source, source_key FROM records_meta_all WHERE rid = ?", (rid,)).fetchone()


def _archived_rid(con, record_id):
    if not record_id or not _has_tables(con):
        return None
    rid = None
    parts = str(record_id).rsplit(":", 2)
    ok = len(parts) == 3 and parts[1][:1] == "r" and parts[1][1:].isdigit()
    heads = _heads(parts[0]) if ok else []
    for head in heads:
        row = con.execute("SELECT a.rid FROM archive_rows a JOIN archive_dims dk ON dk.id = a.kind "
                          "JOIN archive_dims ds ON ds.id = a.source WHERE dk.v = ? AND ds.v = ? AND "
                          "a.source_key = ? AND a.revision = ? AND a.h10 = ? AND a.rid_text IS NULL",
                          (head[0], head[1], head[2], int(parts[1][1:]), parts[2])).fetchone()
        if row:
            rid = row[0]
            break
    if rid is None and heads and _has2(con):
        try:
            h = int(parts[2], 16)
        except ValueError:
            h = None
        for head in heads if h is not None else ():
            row = con.execute("SELECT r.rid FROM arch_segs g JOIN arch_revs r ON r.seg = g.id JOIN archive_dims dk ON "
                              "dk.id = g.kind JOIN archive_dims ds ON ds.id = g.source LEFT JOIN arch_text t ON "
                              "t.rid = r.rid WHERE dk.v = ? AND ds.v = ? AND g.source_key = ? AND r.revision = ? AND "
                              "r.h = ? AND t.record_id IS NULL",
                              (head[0], head[1], head[2], int(parts[1][1:]), h)).fetchone()
            if row:
                rid = row[0]
                break
    if rid is None:
        row = con.execute("SELECT rid FROM archive_rows WHERE rid_text = ?", (record_id,)).fetchone()
        rid = row[0] if row else None
    if rid is None and _has2(con):
        row = con.execute("SELECT rid FROM arch_text WHERE record_id = ?", (record_id,)).fetchone()
        rid = row[0] if row else None
    return rid


def identity_tail(con, kind, source, source_key):
    """(max archived revision, its content_hash) for one identity, or None - store.put uses it so a returning offer
    continues its revision numbers (record ids never collide with archived ones)."""
    if not _has_tables(con):
        return None
    row = con.execute("SELECT a.revision, a.file, a.ln FROM archive_rows a JOIN archive_dims dk ON dk.id = a.kind "
                      "JOIN archive_dims ds ON ds.id = a.source WHERE dk.v = ? AND ds.v = ? AND a.source_key = ? "
                      "ORDER BY a.revision DESC LIMIT 1", (kind, source, source_key)).fetchone()
    if _has2(con):
        r2 = con.execute("SELECT r.revision, g.file, r.ln FROM arch_segs g JOIN arch_revs r ON r.seg = g.id JOIN "
                         "archive_dims dk ON dk.id = g.kind JOIN archive_dims ds ON ds.id = g.source WHERE dk.v = ? AND "
                         "ds.v = ? AND g.source_key = ? ORDER BY r.revision DESC LIMIT 1",
                         (kind, source, source_key)).fetchone()
        if r2 is not None and (row is None or r2[0] > row[0]):
            row = r2
    if row is None:
        return None
    path = con.execute("PRAGMA database_list").fetchone()[2]
    names = {}

    def name_of(fid):
        return con.execute("SELECT file FROM archive_files WHERE id = ?", (fid,)).fetchone()[0]
    rows = FILES.rows(path, row[1], name_of)
    return row[0], rows[row[2]]["content_hash"]


# -------------------------------------------------------------------------------------------------- archive pass
_STEP_HOOK = None


def _step(name, **kw):
    if _STEP_HOOK:
        _STEP_HOOK(name, **kw)


def _fsync(path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _line(row):
    keys = row.keys()
    d = {c: (row[c] if c in keys else (0 if c == "late" else None)) for c in COLS}
    p = d["payload"]
    d["payload"] = zlib.decompress(p).decode("utf-8") if isinstance(p, (bytes, bytearray)) else p
    return json.dumps(d, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _read_file(path):
    """-> (rows, sha256 over the lines, [rids], [content hashes re-derived from payloads OK?])"""
    h = hashlib.sha256()
    rids, n = [], 0
    with lzma.open(path, "rb") as f:
        for raw in f.read().split(b"\n"):
            if not raw:
                continue
            h.update(raw + b"\n")
            d = json.loads(raw)
            rids.append(d["rid"])
            n += 1
    return n, h.hexdigest(), rids


def _payload_ok(d):
    """The archived payload still describes the record its hash names (no silent corruption / truncation)."""
    from .contracts import from_dict
    try:
        return from_dict(json.loads(d["payload"])).content_hash() == str(d["content_hash"]).split("~", 1)[0]
    except Exception:                                   # noqa: BLE001
        return False


def _dim(con, cache, v):
    if v is None:
        return None
    i = cache.get(v)
    if i is None:
        con.execute("INSERT OR IGNORE INTO archive_dims (v) VALUES (?)", (v,))
        i = cache[v] = con.execute("SELECT id FROM archive_dims WHERE v = ?", (v,)).fetchone()[0]
    return i


def candidates(con, now=None):
    """[(league, day, [rids...])] cold offer rows grouped by league and the day of the identity's last sighting."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=AFTER_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = ("WITH ident AS (SELECT source, source_key, MAX(COALESCE(last_seen_at, observed_at, known_at)) AS seen "
         "FROM records WHERE kind = 'offer' GROUP BY source, source_key HAVING seen < ?) "
         "SELECT r.rid, r.league, substr(i.seen, 1, 10) AS day FROM records r JOIN ident i "
         "ON r.source = i.source AND r.source_key = i.source_key WHERE r.kind = 'offer' ORDER BY r.league, day, "
         "r.source, r.source_key, r.revision")
    parts = OrderedDict()
    for rid, league, day in con.execute(q, (cutoff,)):
        parts.setdefault((league or "-", day), []).append(rid)
    out = []
    for (league, day), rids in parts.items():
        for i in range(0, len(rids), PART_ROWS):
            out.append((league, day, rids[i:i + PART_ROWS]))
    return out


def _archive_part(con, league, day, rids, now_s):
    adir = archive_dir()
    adir.mkdir(parents=True, exist_ok=True)
    try:
        import shutil
        free = shutil.disk_usage(adir).free / 1e6
    except OSError:
        free = None
    if free is not None and free < DISK_FLOOR_MB:
        raise RuntimeError(f"disk free {free:.0f} MB < floor {DISK_FLOOR_MB:.0f} MB - archive postponed")
    seq = con.execute("SELECT COALESCE(MAX(seq) + 1, 0) FROM archive_files WHERE kind='offer' AND league IS ? AND day=?",
                      (league, day)).fetchone()[0]
    name = f"offer-{league}-{day}-{seq:04d}.jsonl.xz"
    final, tmp = adir / name, adir / f".{name}.tmp"
    con.execute("CREATE TEMP TABLE IF NOT EXISTS arch_ids (r INTEGER PRIMARY KEY)")
    con.execute("DELETE FROM temp.arch_ids")
    con.executemany("INSERT INTO temp.arch_ids (r) VALUES (?)", ((r,) for r in rids))
    stat = con.execute("SELECT COUNT(*), TOTAL(rid), MAX(COALESCE(last_seen_at, '')), TOTAL(seen_count) FROM records "
                       "WHERE rid IN (SELECT r FROM temp.arch_ids) AND kind = 'offer'").fetchone()
    rows = con.execute("SELECT * FROM records WHERE rid IN (SELECT r FROM temp.arch_ids) "
                       "ORDER BY event_id, player_id, source, source_key, revision").fetchall()
    # event-clustered and identity-contiguous: an event's offers sit in few files, an identity's revisions are
    # adjacent (lzma sees the repeats)
    if len(rows) != len(rids):
        raise RuntimeError("archive candidates changed before the write")
    h = hashlib.sha256()
    with lzma.open(tmp, "wb", preset=6) as f:
        for r in rows:
            ln = _line(r) + b"\n"
            h.update(ln)
            f.write(ln)
    _fsync(tmp)
    digest = h.hexdigest()
    _step("written", tmp=tmp)
    n, d2, back = _read_file(tmp)
    if n != len(rows) or d2 != digest or back != [r["rid"] for r in rows]:
        raise RuntimeError(f"archive {name} failed verification ({len(rows)} expected, {n} re-read)")
    with lzma.open(tmp, "rb") as f:
        bad = sum(1 for raw in f.read().split(b"\n") if raw and not _payload_ok(json.loads(raw)))
    if bad:
        raise RuntimeError(f"archive {name}: {bad} payloads do not re-derive their content hash; nothing removed")
    os.replace(tmp, final)
    _fsync(adir)
    _step("renamed", final=final)
    n3, d3, _ = _read_file(final)
    if n3 != len(rows) or d3 != digest:
        raise RuntimeError(f"archive {name} re-read after the rename does not match; nothing removed")
    size = final.stat().st_size
    dims = {}
    con.execute("BEGIN IMMEDIATE")
    try:
        again = con.execute("SELECT COUNT(*), TOTAL(rid), MAX(COALESCE(last_seen_at, '')), TOTAL(seen_count) FROM "
                            "records WHERE rid IN (SELECT r FROM temp.arch_ids) AND kind = 'offer'").fetchone()
        if tuple(again) != tuple(stat):
            raise RuntimeError(f"rows of {name} changed during the archive; nothing removed")
        cur = con.execute("INSERT INTO archive_files (file, kind, league, day, seq, rows, sha256, bytes, min_rid, max_rid, "
                          "archived_at, version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (name, "offer", league, day, seq, len(rows), digest, size, min(r["rid"] for r in rows),
                           max(r["rid"] for r in rows), now_s, VERSION))
        fid = cur.lastrowid
        _step("manifest", final=final)
        _write_index2(con, rows, fid, dims)
        cur = con.execute("DELETE FROM records WHERE rid IN (SELECT r FROM temp.arch_ids) AND kind = 'offer'")
        if cur.rowcount != len(rows):
            raise RuntimeError(f"delete of {name} touched {cur.rowcount} rows, expected {len(rows)}")
        _step("deleted", final=final)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return {"file": name, "rows": len(rows), "bytes": size}


def _epoch(ts):
    try:
        return int(datetime.strptime(str(ts), TS).replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def _write_index2(con, rows, fid, dims):
    """arch-2 index rows for one file (inside the archive transaction): a SEGMENT per run of one identity with the
    same dims, a tiny row per revision. Anything that would not rebuild exactly (record id, known_at text) is kept
    verbatim in arch_text - the view returns the original value either way."""
    seg_key, seg_id, seg_rows = None, None, []
    revs, texts = [], []

    def close_seg():
        if seg_id is not None:
            con.execute("UPDATE arch_segs SET min_rev = ?, max_rev = ? WHERE id = ?",
                        (min(seg_rows), max(seg_rows), seg_id))
    for i, r in enumerate(rows):
        dimv = tuple(r[c] for c in ("kind", "source", "league", "event_id", "player_id", "stat", "platform"))
        key = (r["source"], r["source_key"], dimv)
        if key != seg_key:
            close_seg()
            cur = con.execute("INSERT INTO arch_segs (kind, source, source_key, league, event_id, player_id, stat, "
                              "platform, file, min_rev, max_rev) VALUES (?,?,?,?,?,?,?,?,?,0,0)",
                              (_dim(con, dims, r["kind"]), _dim(con, dims, r["source"]), r["source_key"],
                               _dim(con, dims, r["league"]), _dim(con, dims, r["event_id"]),
                               _dim(con, dims, r["player_id"]), _dim(con, dims, r["stat"]),
                               _dim(con, dims, r["platform"]), fid))
            seg_key, seg_id, seg_rows = key, cur.lastrowid, []
        seg_rows.append(r["revision"])
        h10 = str(r["content_hash"]).split("~", 1)[0][:10]
        try:
            hv = int(h10, 16) if len(h10) == 10 else None
        except ValueError:
            hv = None
        rebuilt = f"{r['kind']}:{r['source']}:{r['source_key']}:r{r['revision']}:{h10}"
        ep = _epoch(r["known_at"])
        ok_id = hv is not None and rebuilt == r["record_id"]
        ok_t = ep is not None and datetime.fromtimestamp(ep, timezone.utc).strftime(TS) == r["known_at"]
        revs.append((r["rid"], seg_id, r["revision"], hv if hv is not None else 0, ep if ep is not None else 0,
                     (r["late"] if "late" in r.keys() else 0) or 0, i))
        if not (ok_id and ok_t):
            texts.append((r["rid"], None if ok_id else r["record_id"], None if ok_t else r["known_at"]))
    close_seg()
    con.executemany("INSERT INTO arch_revs (rid, seg, revision, h, known, late, ln) VALUES (?,?,?,?,?,?,?)", revs)
    if texts:
        con.executemany("INSERT INTO arch_text (rid, record_id, known_at) VALUES (?,?,?)", texts)


def _remove_stale_tmp():
    n = 0
    d = archive_dir()
    if d.exists():
        for f in d.glob(".*.tmp"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


def _remove_orphans(con):
    """Final files without a manifest row (a crash between rename and commit): their rows are still hot - redone."""
    d = archive_dir()
    if not d.exists():
        return 0
    listed = {r[0] for r in con.execute("SELECT file FROM archive_files")}
    n = 0
    for f in d.glob("offer-*.jsonl.xz"):
        if f.name not in listed:
            f.unlink()
            n += 1
    return n


def run_pass(con=None, now=None, max_parts=40, time_budget_s=1200):
    from . import store
    own = con is None
    con = con or store.connect()
    con.isolation_level = None
    out = {"at": None, "parts": [], "rows": 0, "bytes": 0, "error": None}
    t0 = time.time()
    try:
        ensure_schema(con)
        install(con)
        now = now or datetime.now(timezone.utc)
        out["at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        out["removed_tmp"] = _remove_stale_tmp()
        out["removed_orphans"] = _remove_orphans(con)
        parts = candidates(con, now)
        out["pending_parts"] = len(parts)
        for league, day, rids in parts[:max_parts]:
            if time.time() - t0 > time_budget_s:
                break
            r = _archive_part(con, league, day, rids, out["at"])
            out["parts"].append(r["file"])
            out["rows"] += r["rows"]
            out["bytes"] += r["bytes"]
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
    except Exception as e:                              # noqa: BLE001 - every row stays hot on any failure
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        out["took_s"] = round(time.time() - t0, 2)
        if own:
            con.close()
    return out


def verify(con=None):
    """Re-read every manifest-listed file: rows, sha256 and the archive_rows count per file must match."""
    from . import store
    own = con is None
    con = con or store.connect()
    out = {"files": 0, "rows": 0, "bad": []}
    try:
        if not _has_tables(con):
            return out
        for fid, name, rows, sha in con.execute("SELECT id, file, rows, sha256 FROM archive_files").fetchall():
            try:
                n, d, _ = _read_file(archive_dir() / name)
                idx = con.execute("SELECT COUNT(*) FROM archive_rows WHERE file = ?", (fid,)).fetchone()[0] + (
                    con.execute("SELECT COUNT(*) FROM arch_revs r JOIN arch_segs g ON g.id = r.seg WHERE g.file = ?",
                                (fid,)).fetchone()[0] if _has2(con) else 0)
                if n != rows or d != sha or idx != rows:
                    out["bad"].append({"file": name, "rows": [rows, n, idx], "sha_ok": d == sha})
            except OSError as e:
                out["bad"].append({"file": name, "error": str(e)[:200]})
            out["files"] += 1
            out["rows"] += rows
        if not out["bad"]:
            con.execute("UPDATE archive_files SET verified_at = ?",
                        (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),))
            con.commit()
    finally:
        if own:
            con.close()
    return out


def stats(con):
    if not _has_tables(con):
        return {"files": 0, "rows": 0, "file_mb": 0.0}
    f, r, b = con.execute("SELECT COUNT(*), TOTAL(rows), TOTAL(bytes) FROM archive_files").fetchone()
    return {"files": f, "rows": int(r), "file_mb": round(b / 1e6, 2),
            "hot_offer_rows": con.execute("SELECT COUNT(*) FROM records WHERE kind='offer'").fetchone()[0]}


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m sports.archive")
    p.add_argument("cmd", choices=("pass", "verify", "stats"))
    p.add_argument("--max-parts", type=int, default=40)
    p.add_argument("--time-budget-s", type=int, default=1200)
    a = p.parse_args(argv)
    from . import flags, store
    if a.cmd == "pass":
        flags.require("SPORTS_INGEST")
        out = run_pass(max_parts=a.max_parts, time_budget_s=a.time_budget_s)
    elif a.cmd == "verify":
        out = verify()
    else:
        with store.opened(read_only=True) as con:
            out = stats(con)
    print(json.dumps(out, indent=1, default=str))
    return 1 if (out.get("error") or out.get("bad")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
