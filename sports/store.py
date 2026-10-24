"""Canonical sports storage: one SQLite file <SPORTS_DATA_DIR>/canonical.db (separate from the data branch's research
log sports.db and its cache.db). Nothing here touches a CS2 / LoL file.

Model
  records      every contract record (sports/contracts). Unique on (kind, source, source_key, content_hash):
               - same identity + same content  -> "duplicate" (not stored again; first observation kept, the
                 sighting columns last_seen_at / seen_count move - they are bookkeeping, not history)
               - same identity + new content   -> "revision" (revision = previous + 1; every earlier revision kept)
               Forecasts are immutable: a forecast_id is stored once; different content under one id is refused.
  quarantine   records that could not be typed reliably, with a reason (never silently substituted)
  jobs         the worker's resumable job ledger (sports/worker.py)
  meta         migration level

Migrations are ADDITIVE ONLY (CREATE TABLE / CREATE INDEX / ADD COLUMN); tests/test_store refuses DROP, RENAME, DELETE
and UPDATE-of-history statements in MIGRATIONS. Rollback of Sports is flags-off, never a database restore.

Point-in-time rule: every historical read takes `as_of` and returns only rows with known_at <= as_of (the latest
revision KNOWN at that time). assert_known_before() is the guard model code uses for joins.

CURRENT (the one rule every reader shares - sports/LINE_CORRECTNESS.md):
  the current revision of an identity = its highest revision among rows with late = 0 (SQL: CURRENT_SQL), known at
  as_of when one is given. put() is the only writer that decides it, inside BEGIN IMMEDIATE (one writer at a time per
  SQLite file, across processes), and UNIQUE (kind, source, source_key, revision) makes a second row with the same
  revision impossible - so no identity can ever have two current revisions.
  - same content as the CURRENT revision -> duplicate: only the sighting columns move (last_seen_at = the newest
    observation, last_source_updated_at = the newest source time, seen_count + 1); never a new revision
  - different content (including content equal to an OLDER revision: A -> B -> A) -> a new revision, current
  - an OLDER, delayed response (is_late()) -> stored as history with late = 1, never current: ordered by the SOURCE
    update time when both the new record and the current revision carry one and they differ, otherwise by our
    observation time (new observed_at < the current revision's last_seen_at)
  Source time (source_updated_at / last_source_updated_at) and observation time (observed_at / last_seen_at) are kept
  in separate columns; the payload is the record exactly as first observed (replay reads it, never the columns).

Reads from the web process use connect(read_only=True) (mode=ro): the web never writes the sports store.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import zlib
from contextlib import contextmanager
from pathlib import Path

from . import config
from .contracts import ContractError, Forecast, from_dict, now_utc

MIGRATIONS = [
    (1, [
        """CREATE TABLE IF NOT EXISTS records (
             rid INTEGER PRIMARY KEY AUTOINCREMENT,
             kind TEXT NOT NULL, record_id TEXT NOT NULL, source TEXT NOT NULL, source_key TEXT NOT NULL,
             revision INTEGER NOT NULL, content_hash TEXT NOT NULL, schema_version TEXT NOT NULL,
             league TEXT, event_id TEXT, player_id TEXT, stat TEXT, platform TEXT, line REAL,
             observed_at TEXT, known_at TEXT NOT NULL, stored_at TEXT NOT NULL, payload BLOB NOT NULL,
             last_seen_at TEXT, seen_count INTEGER NOT NULL DEFAULT 1,
             UNIQUE (kind, source, source_key, content_hash))""",
        "CREATE UNIQUE INDEX IF NOT EXISTS records_rid ON records (record_id)",
        "CREATE INDEX IF NOT EXISTS records_ident ON records (kind, source, source_key, revision)",
        "CREATE INDEX IF NOT EXISTS records_event ON records (kind, event_id, known_at)",
        "CREATE INDEX IF NOT EXISTS records_player ON records (kind, player_id, stat, known_at)",
        "CREATE INDEX IF NOT EXISTS records_league ON records (kind, league, known_at)",
        """CREATE TABLE IF NOT EXISTS quarantine (
             qid INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, source TEXT NOT NULL, source_key TEXT,
             league TEXT, reason TEXT NOT NULL, payload_hash TEXT NOT NULL, observed_at TEXT NOT NULL,
             payload TEXT, UNIQUE (kind, source, source_key, reason, payload_hash))""",
        "CREATE INDEX IF NOT EXISTS quarantine_reason ON quarantine (kind, source, reason)",
        """CREATE TABLE IF NOT EXISTS jobs (
             job_key TEXT PRIMARY KEY, job TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
             cursor TEXT, result TEXT, error TEXT, created_at TEXT NOT NULL, started_at TEXT, heartbeat_at TEXT,
             finished_at TEXT)""",
        "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)",
    ]),
    (2, [                                          # line correctness (LINE_CORRECTNESS.md): additive columns only
        "ALTER TABLE records ADD COLUMN late INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE records ADD COLUMN source_updated_at TEXT",
        "ALTER TABLE records ADD COLUMN last_source_updated_at TEXT",
        "ALTER TABLE records ADD COLUMN origin TEXT",
        "CREATE INDEX IF NOT EXISTS records_current ON records (kind, source, source_key, late, revision)",
    ]),
    (3, [                                          # incremental forecasting (LINE_CORRECTNESS.md C11)
        """CREATE TABLE IF NOT EXISTS forecast_keys (
             input_key TEXT PRIMARY KEY, forecast_id TEXT NOT NULL, league TEXT, known_at TEXT NOT NULL)""",
    ]),
]
CURRENT_SQL = "late = 0"                           # the predicate every 'current' reader adds (see module docstring)
UNIQUE_REVISION_INDEX = ("CREATE UNIQUE INDEX IF NOT EXISTS records_ident_rev ON records "
                         "(kind, source, source_key, revision)")
REPAIR_ORIGIN = "repair:prefix-duplicate"          # revisions written by repair_prefix_duplicates()
FORBIDDEN_IN_MIGRATIONS = ("drop ", "rename ", "delete ", "update ", "replace into", "truncate")

_WRITE_LOCK = threading.RLock()


class LeakageError(RuntimeError):
    """A historical join tried to use information not known at forecast time."""


def db_path() -> Path:
    return config.DATA_DIR / "canonical.db"


def _level(con):
    row = con.execute("SELECT v FROM meta WHERE k = 'schema_level'").fetchone()
    return int(row[0]) if row else 0


def migrate(con):
    """Apply missing migrations atomically (BEGIN IMMEDIATE: two processes starting together migrate once; a crash
    mid-migration rolls back to the previous level), then the one-time repair of pre-fix duplicate sightings."""
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    con.commit()
    if _level(con) >= MIGRATIONS[-1][0] and _meta(con, "repair_prefix_duplicates") is not None and             _meta(con, "forecast_keys_backfill") is not None:
        return _level(con)
    with _WRITE_LOCK:
        con.execute("BEGIN IMMEDIATE")
        try:
            level = _level(con)
            for n, stmts in MIGRATIONS:
                if n <= level:
                    continue
                for st in stmts:
                    con.execute(st)
                con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('schema_level', ?)", (str(n),))
                level = n
            if _meta(con, "revision_unique") is None:
                try:
                    con.execute(UNIQUE_REVISION_INDEX)
                    _set_meta(con, "revision_unique", "on")
                except sqlite3.IntegrityError:          # an old race left two rows with one revision: kept, reported
                    _set_meta(con, "revision_unique", "blocked: duplicate revisions exist")
            if _meta(con, "repair_prefix_duplicates") is None:
                report = repair_prefix_duplicates(con, _in_tx=True)
                _set_meta(con, "repair_prefix_duplicates", json.dumps(report, sort_keys=True))
            if _meta(con, "forecast_keys_backfill") is None:
                _set_meta(con, "forecast_keys_backfill", json.dumps(backfill_forecast_keys(con)))
            con.commit()
        except BaseException:
            con.rollback()
            raise
    return level


def _meta(con, k):
    row = con.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return row[0] if row else None


def _set_meta(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (k, v))


_COMPAT = ("CREATE TEMP VIEW IF NOT EXISTS records AS SELECT *, 0 AS late, NULL AS source_updated_at, "
           "NULL AS last_source_updated_at, NULL AS origin FROM main.records")


def connect(path=None, read_only=False):
    p = Path(path) if path else db_path()
    if read_only:
        if not p.exists():
            raise FileNotFoundError(f"sports store not created yet: {p}")
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:                                       # a store the worker has not migrated yet (level 1) is read
            cols = {r[1] for r in con.execute("PRAGMA main.table_info(records)").fetchall()}
            if cols and "late" not in cols:        # through a temp view with the level-2 columns' defaults
                con.execute(_COMPAT)
        except sqlite3.OperationalError:
            pass
        return con
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    migrate(con)
    return con


@contextmanager
def opened(path=None, read_only=False):
    con = connect(path, read_only)
    try:
        yield con
    finally:
        con.close()


# ------------------------------------------------------------------------------------------------------------ writes
def _cols(rec):
    d = rec.to_dict()
    return {"league": d.get("league"), "event_id": d.get("event_id"),
            "player_id": d.get("player_id") or d.get("participant_id") or d.get("subject_id"),
            "stat": d.get("stat"), "platform": d.get("platform"), "line": d.get("line"),
            "observed_at": d.get("observed_at")}


def _pack(d):
    return zlib.compress(json.dumps(d, separators=(",", ":"), default=str).encode("utf-8"), 6)


def _unpack(blob):
    if isinstance(blob, str):
        return json.loads(blob)
    return json.loads(zlib.decompress(blob))


def is_late(new_observed, new_source, cur_seen, cur_source) -> bool:
    """THE ordering rule for a response that arrives while a current revision exists (LINE_CORRECTNESS.md R3):
    by SOURCE update time when both carry one and they differ, else by our observation time. Late = older."""
    if new_source and cur_source and new_source != cur_source:
        return new_source < cur_source
    if new_observed and cur_seen:
        return new_observed < cur_seen
    return False


def _payload_source_time(row):
    try:
        return _unpack(row["payload"]).get("source_updated_at")
    except (ValueError, zlib.error, TypeError, AttributeError):
        return None


def current_times(row):
    """(newest observation, newest source time) of a stored revision. Rows stored before migration 2 have no source
    columns: their source time is the payload's (the first observation's)."""
    seen = row["last_seen_at"] or row["observed_at"] or row["known_at"]
    src = row["last_source_updated_at"] or row["source_updated_at"] or _payload_source_time(row)
    return seen, src


def _current_row(con, kind, source, source_key):
    return con.execute(f"SELECT * FROM records WHERE kind=? AND source=? AND source_key=? AND {CURRENT_SQL} "
                       "ORDER BY revision DESC LIMIT 1", (kind, source, source_key)).fetchone()


@contextmanager
def write_tx(con):
    """One atomic write: BEGIN IMMEDIATE (the SQLite write lock, across processes) unless the caller already holds a
    transaction; commit on success, roll back on error."""
    with _WRITE_LOCK:
        own = not con.in_transaction
        if own:
            con.execute("BEGIN IMMEDIATE")
        try:
            yield con
            if own:
                con.commit()
        except BaseException:
            if own:
                con.rollback()
            raise


def put(con, rec, known_at=None, commit=True) -> dict:
    """Validate and store one record. -> {"status": inserted|duplicate|revision|late, "record_id", "revision"}.
    Raises ContractError for an invalid record (callers quarantine instead of storing a bad row).
    commit=False lets put_many write a whole batch in one transaction (the caller commits)."""
    rec.validate_or_raise()
    if commit:
        with write_tx(con):
            return _put(con, rec, known_at)
    with _WRITE_LOCK:
        if not con.in_transaction:
            con.execute("BEGIN IMMEDIATE")
        return _put(con, rec, known_at)


def _put(con, rec, known_at):
    source, source_key = rec.identity()
    h = rec.content_hash()
    known = known_at or getattr(rec, "known_at", None) or getattr(rec, "observed_at", None) or \
        getattr(rec, "forecast_at", None) or getattr(rec, "settled_at", None) or now_utc()
    obs = getattr(rec, "observed_at", None)
    src = getattr(rec, "source_updated_at", None)
    cur = _current_row(con, rec.KIND, source, source_key)
    # duplicate = the same content as the CURRENT revision only. Content equal to an older revision (a line that moved
    # 20.5 -> 21.5 -> back to 20.5) is a new revision, otherwise "latest" would keep the 21.5 that no longer exists.
    if cur is not None and cur["content_hash"].split("~", 1)[0] == h:
        seen = obs or now_utc()
        _, cur_src = current_times(cur)
        new_src = max(x for x in (cur_src, src) if x) if (cur_src or src) else None
        con.execute("UPDATE records SET last_seen_at = MAX(COALESCE(last_seen_at, ''), ?), seen_count = seen_count + 1, "
                    "last_source_updated_at = ? WHERE record_id = ?", (seen, new_src, cur["record_id"]))
        return {"status": "duplicate", "record_id": cur["record_id"], "revision": cur["revision"]}
    if isinstance(rec, Forecast):
        fkey = forecast_key(rec)
        ex = con.execute("SELECT forecast_id FROM forecast_keys WHERE input_key = ?", (fkey,)).fetchone()
        if ex:                                 # the same forecast, made again from unchanged inputs: nothing written
            return {"status": "unchanged", "record_id": ex[0], "revision": 1}
        clash = con.execute("SELECT 1 FROM records WHERE kind='forecast' AND source_key=?",
                            (rec.forecast_id,)).fetchone()
        if clash:
            raise ContractError(f"forecast {rec.forecast_id} already stored with other content (immutable)")
    late = 0
    if cur is not None and rec.KIND != "forecast":
        late = int(is_late(obs, src, *current_times(cur)))
    last = con.execute("SELECT MAX(revision) FROM records WHERE kind=? AND source=? AND source_key=?",
                       (rec.KIND, source, source_key)).fetchone()[0]
    rev = (last or 0) + 1
    rid = f"{rec.KIND}:{source}:{source_key}:r{rev}:{h[:10]}" if rec.KIND != "forecast" else rec.forecast_id
    stored_h = h                               # UNIQUE (kind, source, source_key, content_hash) is additive-only: a
    if con.execute("SELECT 1 FROM records WHERE kind=? AND source=? AND source_key=? AND content_hash=?",
                   (rec.KIND, source, source_key, h)).fetchone():
        stored_h = f"{h}~r{rev}"               # returning content keeps its hash, tagged with the revision
    c = _cols(rec)
    con.execute("INSERT INTO records (kind, record_id, source, source_key, revision, content_hash, schema_version, "
                "league, event_id, player_id, stat, platform, line, observed_at, known_at, stored_at, payload, "
                "last_seen_at, late, source_updated_at, last_source_updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rec.KIND, rid, source, source_key, rev, stored_h, rec.schema_version, c["league"], c["event_id"],
                 c["player_id"], c["stat"], c["platform"], c["line"], c["observed_at"], known, now_utc(),
                 _pack(rec.to_dict()), c["observed_at"], late, src, src))
    if isinstance(rec, Forecast):
        con.execute("INSERT OR IGNORE INTO forecast_keys (input_key, forecast_id, league, known_at) VALUES (?,?,?,?)",
                    (forecast_key(rec), rid, rec.league, known))
    if late:
        return {"status": "late", "record_id": rid, "revision": rev}
    return {"status": "inserted" if rev == 1 else "revision", "record_id": rid, "revision": rev}


def forecast_key(fc) -> str:
    """The INPUT KEY of a forecast (LINE_CORRECTNESS.md C11): everything in its content except the time it was made -
    offer revision (offer_snapshot_ids), every input record id, model / artifact / data / calibration / grade-policy /
    stake-policy versions (data_version = the hash of the feature row), and the output those inputs determine. Two
    forecasts with one key are the same forecast made twice: only the first is stored."""
    import hashlib
    d = fc.hash_payload()
    d.pop("forecast_at", None)
    return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                     default=str).encode("utf-8")).hexdigest()[:32]


def backfill_forecast_keys(con) -> dict:
    """Index the input keys of forecasts stored before migration 3 (oldest first: the first forecast of a key is the
    one kept as its reference). Additive: INSERT OR IGNORE into forecast_keys; no forecast is touched."""
    n = 0
    for r in con.execute("SELECT record_id, league, known_at, payload FROM records WHERE kind='forecast' "
                         "ORDER BY rid").fetchall():
        try:
            fc = _rec(r)
        except (ContractError, TypeError, ValueError):
            continue
        n += con.execute("INSERT OR IGNORE INTO forecast_keys (input_key, forecast_id, league, known_at) "
                         "VALUES (?,?,?,?)", (forecast_key(fc), r["record_id"], r["league"], r["known_at"])).rowcount
    return {"indexed": n}


def withdrawn(rec, observed_at):
    """The 'removed' revision for an offer a COMPLETE read no longer lists. Its time is OUR observation (the source
    published nothing), so source_updated_at is cleared: a delayed older response is then ordered by observation
    time and cannot reopen it (is_late)."""
    from dataclasses import replace
    return replace(rec, availability="removed", observed_at=observed_at, known_at=observed_at,
                   source_updated_at=None, source_time_unknown=True)


def put_many(con, recs) -> dict:
    """One transaction for the whole batch (BEGIN IMMEDIATE): a crash or kill mid-batch leaves none of it."""
    counts = {"inserted": 0, "duplicate": 0, "revision": 0, "invalid": 0, "late": 0, "unchanged": 0}
    ids, bad = [], []
    with write_tx(con):
        for r in recs:
            try:
                res = put(con, r, commit=False)
            except ContractError as e:
                counts["invalid"] += 1
                bad.append((r, str(e)))
                continue
            counts[res["status"]] += 1
            ids.append(res["record_id"])
    for r, e in bad:
        quarantine(con, r.KIND, r.identity()[0], r.identity()[1], f"invalid: {e[:160]}", r.to_dict(),
                   getattr(r, "league", None))
    return {**counts, "record_ids": ids}


def repair_prefix_duplicates(con, dry_run=False, _in_tx=False) -> dict:
    """One-time, ADDITIVE repair of stores written before c0cc754: put() then matched a returning content against ANY
    earlier revision, so A -> B -> A only moved revision 1's sighting columns (last_seen_at / seen_count: "revision 1
    counted twice") and 'latest' kept B. Evidence in the old rows: an earlier revision SEEN AFTER the current one was
    last seen. For each such identity a new revision is appended with the most recently seen content, known_at =
    observed_at = that sighting time (the only time we know it was listed again; the exact return time is lost),
    origin = REPAIR_ORIGIN:<source record id>. Nothing is updated or deleted. -> report."""
    q = (f"SELECT o.kind, o.source, o.source_key, o.record_id, o.revision, o.last_seen_at, o.content_hash, "
         f"cur.content_hash AS cur_h FROM records o JOIN (SELECT r.* FROM records r JOIN (SELECT kind, source, "
         f"source_key, MAX(revision) rv FROM records WHERE kind != 'forecast' AND {CURRENT_SQL} GROUP BY kind, source, "
         f"source_key HAVING COUNT(*) > 1) mx ON r.kind = mx.kind AND r.source = mx.source AND "
         f"r.source_key = mx.source_key AND r.revision = mx.rv) cur ON o.kind = cur.kind AND o.source = cur.source "
         f"AND o.source_key = cur.source_key AND o.revision < cur.revision WHERE o.late = 0 AND "
         f"o.last_seen_at IS NOT NULL AND o.last_seen_at > COALESCE(cur.last_seen_at, cur.observed_at, cur.known_at)")
    best = {}
    for r in con.execute(q).fetchall():
        if r["content_hash"].split("~", 1)[0] == r["cur_h"].split("~", 1)[0]:
            continue
        k = (r["kind"], r["source"], r["source_key"])
        b = best.get(k)
        if b is None or (r["last_seen_at"], r["revision"]) > (b["last_seen_at"], b["revision"]):
            best[k] = r
    report = {"identities": len(best), "by_kind": {}, "dry_run": bool(dry_run), "repaired": 0}
    for k in sorted(best):
        report["by_kind"][k[0]] = report["by_kind"].get(k[0], 0) + 1
    if dry_run or not best:
        return report

    def apply():
        n = 0
        for (kind, source, source_key), r in sorted(best.items()):
            old = con.execute("SELECT * FROM records WHERE record_id = ?", (r["record_id"],)).fetchone()
            d = _unpack(old["payload"])
            t = r["last_seen_at"]
            if "observed_at" in d:
                d["observed_at"] = t
            if "known_at" in d:
                d["known_at"] = t
            rev = con.execute("SELECT MAX(revision) FROM records WHERE kind=? AND source=? AND source_key=?",
                              (kind, source, source_key)).fetchone()[0] + 1
            h = old["content_hash"].split("~", 1)[0]
            rid = f"{kind}:{source}:{source_key}:r{rev}:{h[:10]}"
            src = old["last_source_updated_at"] or old["source_updated_at"] or d.get("source_updated_at")
            con.execute("INSERT INTO records (kind, record_id, source, source_key, revision, content_hash, "
                        "schema_version, league, event_id, player_id, stat, platform, line, observed_at, known_at, "
                        "stored_at, payload, last_seen_at, late, source_updated_at, last_source_updated_at, origin) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
                        (kind, rid, source, source_key, rev, f"{h}~r{rev}", old["schema_version"], old["league"],
                         old["event_id"], old["player_id"], old["stat"], old["platform"], old["line"], t, t, now_utc(),
                         _pack(d), t, src, src, f"{REPAIR_ORIGIN}:{r['record_id']}"))
            n += 1
        return n
    if _in_tx:
        report["repaired"] = apply()
    else:
        with write_tx(con):
            report["repaired"] = apply()
    return report


def quarantine(con, kind, source, source_key, reason, payload=None, league=None, observed_at=None, commit=True):
    """Keep an unreliable record OUT of the canonical tables, with its reason. Idempotent per (reason, payload)."""
    import hashlib
    raw = json.dumps(payload, sort_keys=True, default=str) if payload is not None else ""
    ph = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with _WRITE_LOCK:
        con.execute("INSERT OR IGNORE INTO quarantine (kind, source, source_key, league, reason, payload_hash, "
                    "observed_at, payload) VALUES (?,?,?,?,?,?,?,?)",
                    (kind, source, source_key, league, reason, ph, observed_at or now_utc(), raw[:4000]))
        if commit:
            con.commit()


def quarantine_many(con, items, league=None, observed_at=None):
    for x in items:
        quarantine(con, x["kind"], x["source"], x.get("source_key"), x["reason"], x.get("payload"), league,
                   observed_at, commit=False)
    con.commit()
    return len(items)


def size_mb(path=None):
    p = Path(path) if path else db_path()
    tot = 0
    for suffix in ("", "-wal", "-shm"):
        q = Path(str(p) + suffix)
        if q.exists():
            tot += q.stat().st_size
    return tot / 1e6


# ------------------------------------------------------------------------------------------------------------- reads
def _rec(row):
    return from_dict(_unpack(row["payload"])) if row else None


def get(con, record_id):
    row = con.execute("SELECT payload FROM records WHERE record_id = ?", (record_id,)).fetchone()
    return _rec(row)


def row_meta(con, record_id):
    """Bookkeeping of one stored revision: observation times (observed_at = first, last_seen_at = newest), source
    times (source_updated_at = first, last_source_updated_at = newest), late / origin, revision."""
    row = con.execute("SELECT record_id, kind, source, source_key, revision, content_hash, observed_at, known_at, "
                      "stored_at, last_seen_at, seen_count, late, source_updated_at, last_source_updated_at, origin, "
                      "payload FROM records WHERE record_id = ?", (record_id,)).fetchone()
    if not row:
        return None
    out = {k: row[k] for k in row.keys() if k != "payload"}
    out["last_source_updated_at"] = current_times(row)[1]
    return out


def revisions(con, kind, source, source_key):
    """Every stored revision, oldest first: [(meta, record)]."""
    rows = con.execute("SELECT * FROM records WHERE kind=? AND source=? AND source_key=? ORDER BY revision",
                       (kind, source, source_key)).fetchall()
    return [({k: r[k] for k in ("record_id", "revision", "known_at", "content_hash", "late", "origin", "last_seen_at",
                                "observed_at", "source_updated_at")}, _rec(r)) for r in rows]


def latest(con, kind, source, source_key, as_of=None):
    q = f"SELECT * FROM records WHERE kind=? AND source=? AND source_key=? AND {CURRENT_SQL}"
    a = [kind, source, source_key]
    if as_of:
        q += " AND known_at <= ?"
        a.append(as_of)
    row = con.execute(q + " ORDER BY revision DESC LIMIT 1", a).fetchone()
    return _rec(row)


def current_record_id(con, kind, source, source_key):
    """The CURRENT revision's record id of one identity (one indexed lookup, records_current) - the cheap live check
    slip copy uses against a cached pool."""
    row = con.execute(f"SELECT record_id FROM records WHERE kind=? AND source=? AND source_key=? AND {CURRENT_SQL} "
                      "ORDER BY revision DESC LIMIT 1", (kind, source, source_key)).fetchone()
    return row[0] if row else None


def _latest_rows(con, kind, where, args, as_of, limit):
    """Latest revision per identity among rows matching `where`, known at as_of."""
    cond = f"kind = ? AND {CURRENT_SQL}" + (f" AND {where}" if where else "")
    a = [kind] + list(args)
    if as_of:
        cond += " AND known_at <= ?"
        a.append(as_of)
    q = (f"SELECT r.* FROM records r JOIN (SELECT source, source_key, MAX(revision) AS rv FROM records WHERE {cond} "
         f"GROUP BY source, source_key) z ON r.source = z.source AND r.source_key = z.source_key AND r.revision = z.rv "
         f"WHERE r.kind = ? ORDER BY r.known_at DESC LIMIT ?")
    return con.execute(q, a + [kind, int(limit)]).fetchall()


def offers(con, league=None, event_id=None, player_id=None, stat=None, platform=None, as_of=None, limit=5000):
    """CURRENT revision of each offer (per platform + provider offer id) known at as_of -> [(record_id, OfferSnapshot)].
    The board, the player card, the slip pool / validation and new forecasts all read offers through this one rule."""
    w, a = [], []
    for col, v in (("league", league), ("event_id", event_id), ("player_id", player_id), ("stat", stat),
                   ("platform", platform)):
        if v is not None:
            w.append(f"{col} = ?")
            a.append(v)
    rows = _latest_rows(con, "offer", " AND ".join(w), a, as_of, limit)
    return [(r["record_id"], _rec(r)) for r in rows]


def events(con, league=None, as_of=None, limit=2000):
    rows = _latest_rows(con, "event", "league = ?" if league else "", [league] if league else [], as_of, limit)
    return [(r["record_id"], _rec(r)) for r in rows]


def history(con, player_id, stat, before, as_of=None, limit=200):
    """A player's stat observations from events that STARTED before `before`, as known at as_of (default = before).
    -> [(record_id, StatObservation, event_start)] newest first. Enforces chronology: an event that starts at or after
    `before` is never returned, and nothing known after as_of is used."""
    as_of = as_of or before
    if as_of > before:
        raise LeakageError("history as_of later than the forecast time")
    rows = _latest_rows(con, "stat", "player_id = ? AND stat = ?", [player_id, stat], as_of, 100000)
    out = []
    for r in rows:
        obs = _rec(r)
        ev = latest(con, "event", "espn", obs.event_id, as_of=as_of) or _any_event(con, obs.event_id, as_of)
        start = ev.start_utc if ev else None
        if start is None or start >= before:
            continue
        out.append((r["record_id"], obs, start))
    out.sort(key=lambda x: x[2], reverse=True)
    return out[:limit]


def _any_event(con, event_id, as_of):
    row = con.execute(f"SELECT * FROM records WHERE kind='event' AND source_key=? AND known_at <= ? AND {CURRENT_SQL} "
                      "ORDER BY revision DESC LIMIT 1", (event_id, as_of)).fetchone()
    return _rec(row)


def forecasts(con, league=None, event_id=None, player_id=None, model_version=None, limit=5000):
    w, a = ["kind = 'forecast'"], []
    for col, v in (("league", league), ("event_id", event_id), ("player_id", player_id)):
        if v is not None:
            w.append(f"{col} = ?")
            a.append(v)
    rows = con.execute(f"SELECT * FROM records WHERE {' AND '.join(w)} ORDER BY known_at DESC LIMIT ?",
                       a + [int(limit)]).fetchall()
    recs = [(r["record_id"], _rec(r)) for r in rows]
    return [x for x in recs if model_version is None or x[1].model_version == model_version]


def settlements(con, offer_id=None, platform=None, forecast_id=None):
    """Every settlement revision (oldest first). The CURRENT one per (offer, side) is the highest revision."""
    w, a = ["kind = 'settlement'"], []
    if platform:
        w.append("platform = ?")
        a.append(platform)
    if offer_id:
        w.append("source_key LIKE ?")
        a.append(f"{offer_id}|%")
    rows = con.execute(f"SELECT * FROM records WHERE {' AND '.join(w)} ORDER BY source_key, revision", a).fetchall()
    recs = [(r["record_id"], _rec(r)) for r in rows]
    if forecast_id:
        recs = [x for x in recs if forecast_id in x[1].affected_forecast_ids]
    return recs


def assert_known_before(con, record_ids, forecast_at):
    """The leakage guard for model joins: every input record must have been known at or before forecast_at."""
    bad = []
    for rid in record_ids:
        m = row_meta(con, rid)
        if m is None:
            bad.append(f"{rid}: unknown record")
        elif m["known_at"] > forecast_at:
            bad.append(f"{rid}: known_at {m['known_at']} > forecast_at {forecast_at}")
    if bad:
        raise LeakageError("; ".join(bad[:5]))
    return True


def quarantine_counts(con, kind=None):
    q = "SELECT kind, source, reason, COUNT(*) n FROM quarantine"
    a = []
    if kind:
        q += " WHERE kind = ?"
        a.append(kind)
    return [dict(r) for r in con.execute(q + " GROUP BY kind, source, reason ORDER BY n DESC", a).fetchall()]


def counts(con):
    rows = con.execute("SELECT kind, league, platform, COUNT(*) n, COUNT(DISTINCT source || '|' || source_key) ids, "
                       "MIN(known_at) first, MAX(known_at) last FROM records GROUP BY kind, league, platform").fetchall()
    return [dict(r) for r in rows]
