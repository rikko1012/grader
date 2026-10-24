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
]
FORBIDDEN_IN_MIGRATIONS = ("drop ", "rename ", "delete ", "update ", "replace into", "truncate")

_WRITE_LOCK = threading.Lock()


class LeakageError(RuntimeError):
    """A historical join tried to use information not known at forecast time."""


def db_path() -> Path:
    return config.DATA_DIR / "canonical.db"


def migrate(con):
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    row = con.execute("SELECT v FROM meta WHERE k = 'schema_level'").fetchone()
    level = int(row[0]) if row else 0
    for n, stmts in MIGRATIONS:
        if n <= level:
            continue
        for s in stmts:
            con.execute(s)
        con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('schema_level', ?)", (str(n),))
        level = n
    con.commit()
    return level


def connect(path=None, read_only=False):
    p = Path(path) if path else db_path()
    if read_only:
        if not p.exists():
            raise FileNotFoundError(f"sports store not created yet: {p}")
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10, check_same_thread=False)
        con.row_factory = sqlite3.Row
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


def put(con, rec, known_at=None, commit=True) -> dict:
    """Validate and store one record. -> {"status": inserted|duplicate|revision, "record_id", "revision"}.
    Raises ContractError for an invalid record (callers quarantine instead of storing a bad row).
    commit=False lets put_many write a whole batch in one transaction."""
    rec.validate_or_raise()
    source, source_key = rec.identity()
    h = rec.content_hash()
    known = known_at or getattr(rec, "known_at", None) or getattr(rec, "observed_at", None) or \
        getattr(rec, "forecast_at", None) or getattr(rec, "settled_at", None) or now_utc()
    with _WRITE_LOCK:
        # duplicate = the same content as the LATEST revision only. Content equal to an older revision (a line that moved
        # 20.5 -> 21.5 -> back to 20.5) is a new revision, otherwise "latest" would keep the 21.5 that no longer exists.
        dup = con.execute("SELECT record_id, revision, content_hash FROM records WHERE kind=? AND source=? AND "
                          "source_key=? ORDER BY revision DESC LIMIT 1", (rec.KIND, source, source_key)).fetchone()
        if dup and dup["content_hash"].split("~", 1)[0] != h:
            dup = None
        if dup:                                    # same content seen again: only the sighting counter moves
            seen = getattr(rec, "observed_at", None) or now_utc()
            con.execute("UPDATE records SET last_seen_at = MAX(COALESCE(last_seen_at, ''), ?), seen_count = seen_count + 1 "
                        "WHERE record_id = ?", (seen, dup["record_id"]))
            if commit:
                con.commit()
            return {"status": "duplicate", "record_id": dup["record_id"], "revision": dup["revision"]}
        if isinstance(rec, Forecast):
            clash = con.execute("SELECT 1 FROM records WHERE kind='forecast' AND source_key=?",
                                (rec.forecast_id,)).fetchone()
            if clash:
                raise ContractError(f"forecast {rec.forecast_id} already stored with other content (immutable)")
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
                    "last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec.KIND, rid, source, source_key, rev, stored_h, rec.schema_version, c["league"], c["event_id"],
                     c["player_id"], c["stat"], c["platform"], c["line"], c["observed_at"], known, now_utc(),
                     _pack(rec.to_dict()), c["observed_at"]))
        if commit:
            con.commit()
    return {"status": "inserted" if rev == 1 else "revision", "record_id": rid, "revision": rev}


def put_many(con, recs) -> dict:
    counts = {"inserted": 0, "duplicate": 0, "revision": 0, "invalid": 0}
    ids, bad = [], []
    try:
        for r in recs:
            try:
                res = put(con, r, commit=False)
            except ContractError as e:
                counts["invalid"] += 1
                bad.append((r, str(e)))
                continue
            counts[res["status"]] += 1
            ids.append(res["record_id"])
    finally:
        con.commit()
    for r, e in bad:
        quarantine(con, r.KIND, r.identity()[0], r.identity()[1], f"invalid: {e[:160]}", r.to_dict(),
                   getattr(r, "league", None))
    return {**counts, "record_ids": ids}


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
    row = con.execute("SELECT record_id, kind, source, source_key, revision, content_hash, known_at, stored_at, "
                      "last_seen_at, seen_count FROM records WHERE record_id = ?", (record_id,)).fetchone()
    return dict(row) if row else None


def revisions(con, kind, source, source_key):
    """Every stored revision, oldest first: [(meta, record)]."""
    rows = con.execute("SELECT * FROM records WHERE kind=? AND source=? AND source_key=? ORDER BY revision",
                       (kind, source, source_key)).fetchall()
    return [({k: r[k] for k in ("record_id", "revision", "known_at", "content_hash")}, _rec(r)) for r in rows]


def latest(con, kind, source, source_key, as_of=None):
    q = "SELECT * FROM records WHERE kind=? AND source=? AND source_key=?"
    a = [kind, source, source_key]
    if as_of:
        q += " AND known_at <= ?"
        a.append(as_of)
    row = con.execute(q + " ORDER BY revision DESC LIMIT 1", a).fetchone()
    return _rec(row)


def _latest_rows(con, kind, where, args, as_of, limit):
    """Latest revision per identity among rows matching `where`, known at as_of."""
    cond = "kind = ?" + (f" AND {where}" if where else "")
    a = [kind] + list(args)
    if as_of:
        cond += " AND known_at <= ?"
        a.append(as_of)
    q = (f"SELECT r.* FROM records r JOIN (SELECT source, source_key, MAX(revision) AS rv FROM records WHERE {cond} "
         f"GROUP BY source, source_key) z ON r.source = z.source AND r.source_key = z.source_key AND r.revision = z.rv "
         f"WHERE r.kind = ? ORDER BY r.known_at DESC LIMIT ?")
    return con.execute(q, a + [kind, int(limit)]).fetchall()


def offers(con, league=None, event_id=None, player_id=None, stat=None, platform=None, as_of=None, limit=5000):
    """Latest revision of each offer (per platform + provider offer id) known at as_of -> [(record_id, OfferSnapshot)]."""
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
    row = con.execute("SELECT * FROM records WHERE kind='event' AND source_key=? AND known_at <= ? "
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
