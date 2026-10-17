"""
LoL data center storage (one SQLite file, DATA_DIR/lol/center/lol.db - separate from every CS2 store and from the
lol scout's cache / history files).

Rules kept here:
- raw upstream responses are kept compressed and never edited (raw_snapshots);
- a source row that changes upstream gets a NEW revision; earlier revisions stay (lp_rows); lp_current points at the
  latest and records when a row stopped appearing upstream (gone_at) instead of deleting it;
- internal ids are ours ("lol:<kind>:000001"), never Cargo row ids; each id keeps its source mappings and a history;
- ambiguous identities go to quarantine instead of being guessed;
- market quotes are append-only events (markets.py); market_state is only a cache rebuilt from them.
"""
import gzip
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SPORT = "lol"
DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent.parent)


def db_path():
    return Path(os.environ.get("LOL_CENTER_DB") or DATA_DIR / "lol" / "center" / "lol.db")


def now_iso(t=None):
    d = datetime.now(timezone.utc) if t is None else datetime.fromtimestamp(t, timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False).encode()).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS raw_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, endpoint TEXT,
    params TEXT, fetched_at TEXT NOT NULL, status TEXT, n_rows INTEGER, sha256 TEXT, body BLOB);
CREATE INDEX IF NOT EXISTS raw_src ON raw_snapshots (source, fetched_at);
CREATE INDEX IF NOT EXISTS raw_ep ON raw_snapshots (source, endpoint, params, id);
CREATE TABLE IF NOT EXISTS lp_rows (tbl TEXT NOT NULL, skey TEXT NOT NULL, rev INTEGER NOT NULL, sha TEXT NOT NULL,
    data TEXT NOT NULL, event_time TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, snapshot_id INTEGER,
    PRIMARY KEY (tbl, skey, rev));
CREATE TABLE IF NOT EXISTS lp_current (tbl TEXT NOT NULL, skey TEXT NOT NULL, rev INTEGER NOT NULL, sha TEXT NOT NULL,
    event_time TEXT, last_seen TEXT NOT NULL, gone_at TEXT, PRIMARY KEY (tbl, skey));
CREATE INDEX IF NOT EXISTS lp_cur_time ON lp_current (tbl, event_time);
CREATE TABLE IF NOT EXISTS checkpoints (job TEXT PRIMARY KEY, state TEXT NOT NULL, detail TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ref_state (kind TEXT NOT NULL, name TEXT NOT NULL, checked_at TEXT NOT NULL, found INTEGER,
    PRIMARY KEY (kind, name));
CREATE TABLE IF NOT EXISTS job_log (id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, at TEXT, ok INTEGER, requests INTEGER,
    rows INTEGER, new_rows INTEGER, changed_rows INTEGER, seconds REAL, detail TEXT);
CREATE TABLE IF NOT EXISTS entities (eid TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entity_sources (kind TEXT NOT NULL, source TEXT NOT NULL, skey TEXT NOT NULL, eid TEXT NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, note TEXT,
    PRIMARY KEY (kind, source, skey));
CREATE INDEX IF NOT EXISTS es_eid ON entity_sources (eid);
CREATE TABLE IF NOT EXISTS entity_history (id INTEGER PRIMARY KEY AUTOINCREMENT, eid TEXT, at TEXT, change TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS aliases (kind TEXT NOT NULL, norm TEXT NOT NULL, eid TEXT NOT NULL, source TEXT NOT NULL,
    raw TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, PRIMARY KEY (kind, norm, eid, source));
CREATE INDEX IF NOT EXISTS alias_norm ON aliases (kind, norm);
CREATE TABLE IF NOT EXISTS quarantine (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, skey TEXT NOT NULL,
    reason TEXT NOT NULL, detail TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, resolved_at TEXT,
    UNIQUE (kind, skey, reason));
CREATE TABLE IF NOT EXISTS c_series (sid TEXT PRIMARY KEY, lp_match_id TEXT, tournament_page TEXT, tournament TEXT,
    best_of INTEGER, team1 TEXT, team2 TEXT, team1_name TEXT, team2_name TEXT, score1 INTEGER, score2 INTEGER,
    winner INTEGER, ff INTEGER, nullified INTEGER, scheduled_utc TEXT, first_game_utc TEXT, n_games INTEGER,
    n_remakes INTEGER, status TEXT, flags TEXT);
CREATE INDEX IF NOT EXISTS cs_time ON c_series (scheduled_utc);
CREATE TABLE IF NOT EXISTS c_games (gid TEXT PRIMARY KEY, sid TEXT, game_n INTEGER, lp_game_id TEXT, riot_game_id TEXT,
    start_utc TEXT, patch TEXT, length_s REAL, team1 TEXT, team2 TEXT, team1_side TEXT, winner_tid TEXT,
    team1_kills INTEGER, team2_kills INTEGER, remake INTEGER, ff INTEGER, chronobreak INTEGER, n_players INTEGER,
    tournament_page TEXT, flags TEXT);
CREATE INDEX IF NOT EXISTS cg_sid ON c_games (sid, game_n);
CREATE INDEX IF NOT EXISTS cg_time ON c_games (start_utc);
CREATE TABLE IF NOT EXISTS c_player_games (gid TEXT NOT NULL, pid TEXT NOT NULL, tid TEXT, opp_tid TEXT, sid TEXT,
    game_n INTEGER, start_utc TEXT, tournament_page TEXT, patch TEXT, side TEXT, role TEXT, champion TEXT, kills INTEGER,
    deaths INTEGER, assists INTEGER, team_kills INTEGER, gold INTEGER, cs INTEGER, damage INTEGER, vision INTEGER,
    win INTEGER, name_shown TEXT, PRIMARY KEY (gid, pid));
CREATE INDEX IF NOT EXISTS cpg_pid ON c_player_games (pid, start_utc);
CREATE INDEX IF NOT EXISTS cpg_sid ON c_player_games (sid, game_n);
CREATE TABLE IF NOT EXISTS market_polls (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
    polled_at TEXT NOT NULL, ok INTEGER NOT NULL, http INTEGER, n_markets INTEGER, n_events INTEGER, error TEXT,
    body_sha TEXT, raw_id INTEGER, seconds REAL);
CREATE INDEX IF NOT EXISTS mp_at ON market_polls (platform, polled_at);
CREATE TABLE IF NOT EXISTS market_events (id INTEGER PRIMARY KEY AUTOINCREMENT, sport TEXT NOT NULL, platform TEXT NOT NULL,
    market_key TEXT NOT NULL, event TEXT NOT NULL, observed_at TEXT NOT NULL, poll_id INTEGER, raw_id INTEGER,
    source_updated_at TEXT, line_id TEXT, player TEXT, player_source_id TEXT, team TEXT, opponent TEXT,
    match_source_id TEXT, match_title TEXT, start_time TEXT, stat_label TEXT, stat TEXT, scope TEXT, market_type TEXT,
    odds_type TEXT, line REAL, sides TEXT, status TEXT, state_sha TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS me_key ON market_events (platform, market_key, id);
CREATE INDEX IF NOT EXISTS me_time ON market_events (observed_at);
CREATE TABLE IF NOT EXISTS market_links (platform TEXT NOT NULL, market_key TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
    sid TEXT, tid TEXT, opp_tid TEXT, pid TEXT, role TEXT, detail TEXT, linked_at TEXT NOT NULL, PRIMARY KEY (platform, market_key));
CREATE TABLE IF NOT EXISTS market_link_history (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, market_key TEXT, at TEXT,
    status TEXT, reason TEXT, sid TEXT, tid TEXT, opp_tid TEXT, pid TEXT);
CREATE TABLE IF NOT EXISTS lol_settlements (id INTEGER PRIMARY KEY AUTOINCREMENT, unit_key TEXT NOT NULL, version INTEGER NOT NULL,
    settled_at TEXT NOT NULL, platform TEXT, sid TEXT, pid TEXT, stat TEXT, scope TEXT, line REAL, status TEXT NOT NULL,
    actual REAL, over_hit INTEGER, push INTEGER, games TEXT, detail TEXT, fingerprint TEXT, supersedes INTEGER,
    UNIQUE (unit_key, version));
CREATE TABLE IF NOT EXISTS lol_forecasts (id INTEGER PRIMARY KEY AUTOINCREMENT, forecast_at TEXT NOT NULL, state_sha TEXT,
    sport TEXT, platform TEXT, market_key TEXT, line_id TEXT, quote_event_id INTEGER, quote_observed_at TEXT, stat TEXT, scope TEXT,
    market_type TEXT, odds_type TEXT, line REAL, sides TEXT, sid TEXT, pid TEXT, tid TEXT, opp_tid TEXT, role TEXT, best_of INTEGER,
    series_start TEXT, model_version TEXT, fit_sha TEXT, form TEXT, lambda REAL, nb_size REAL, p_over REAL, p_under REAL,
    p_push REAL, model_side TEXT, side_offered INTEGER, inputs TEXT, flags TEXT, slip_eligible INTEGER, slip_reasons TEXT);
CREATE INDEX IF NOT EXISTS lf_key ON lol_forecasts (platform, market_key, id);
CREATE TABLE IF NOT EXISTS crosscheck (sid TEXT NOT NULL, le_match_id TEXT NOT NULL, item TEXT NOT NULL, game_n INTEGER,
    pid TEXT, lp_value TEXT, le_value TEXT, agree INTEGER NOT NULL, checked_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS cc_item ON crosscheck (item, agree);
CREATE TABLE IF NOT EXISTS reconciliation (sid TEXT, le_match_id TEXT, game_n INTEGER, tid TEXT, item TEXT NOT NULL,
    lp_value TEXT, le_value TEXT, evidence TEXT, resolution TEXT NOT NULL, status TEXT NOT NULL, checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS market_state (platform TEXT NOT NULL, market_key TEXT NOT NULL, state_sha TEXT, open INTEGER,
    last_event_id INTEGER, first_seen TEXT, last_seen TEXT, PRIMARY KEY (platform, market_key));
"""


# columns added after the first production database was created (2026-10-17): added in place, never a rebuild
ADDED_COLUMNS = {"c_series": {"source": "TEXT", "source_match_id": "TEXT"},
                 "c_games": {"source": "TEXT", "source_game_id": "TEXT", "team1_kills_raw": "INTEGER", "team2_kills_raw": "INTEGER",
                             "team_kills_status": "TEXT", "winner_status": "TEXT", "winner_inferred_tid": "TEXT"},
                 "lol_forecasts": {"grade": "TEXT", "grade_label": "TEXT", "units_policy": "REAL", "units": "REAL",
                                   "stake_restriction": "TEXT"}}


def connect(path=None, tries=6):
    """One connection (WAL). Several threads open theirs at start-up: a 'database is locked' while the schema is checked
    is retried (seen once in production, 2026-10-17 02:40 UTC)."""
    p = Path(path or db_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    for i in range(tries):
        con = sqlite3.connect(str(p), timeout=60)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.executescript(SCHEMA)
            for tbl, cols in ADDED_COLUMNS.items():
                have = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
                for c, typ in cols.items():
                    if c not in have:
                        con.execute(f"ALTER TABLE {tbl} ADD COLUMN {c} {typ}")
            con.commit()
            return con
        except sqlite3.OperationalError as e:
            con.close()
            if "locked" not in str(e) or i == tries - 1:
                raise
            import time
            time.sleep(1 + 2 * i)


def get_meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def set_meta(con, k, v):
    con.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, json.dumps(v, default=str)))


def save_raw(con, source, endpoint, params, body, status="ok", n_rows=None, fetched_at=None, dedupe=False):
    """Keep one upstream response exactly as received (gzip). Returns the snapshot id. dedupe=True: when the last copy of
    the same endpoint + params has the same bytes, that copy's id is returned and nothing new is stored."""
    if isinstance(body, str):
        body = body.encode()
    if dedupe:
        h = hashlib.sha256(body or b"").hexdigest()
        last = con.execute("SELECT id, sha256 FROM raw_snapshots WHERE source = ? AND endpoint = ? AND params = ? ORDER BY id DESC LIMIT 1",
                           (source, endpoint, json.dumps(params, sort_keys=True, default=str))).fetchone()
        if last and last[1] == h:
            return last[0]
    cur = con.execute("INSERT INTO raw_snapshots (source, endpoint, params, fetched_at, status, n_rows, sha256, body) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (source, endpoint, json.dumps(params, sort_keys=True, default=str), fetched_at or now_iso(), status,
                       n_rows, hashlib.sha256(body or b"").hexdigest(), gzip.compress(body or b"", 6)))
    return cur.lastrowid


def raw_body(con, snapshot_id):
    r = con.execute("SELECT body FROM raw_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return gzip.decompress(r[0]) if r else None


def put_rows(con, tbl, rows, key, event_time, snapshot_id, seen_at):
    """Store source rows with revision history. Unchanged row -> last_seen only; changed row -> a new revision (the
    earlier one is kept); a row that had been marked gone and reappears gets a new revision too. Returns counts."""
    new = changed = same = 0
    for r in rows:
        k = key(r)
        if not k:
            continue
        h = sha(r)
        ev = event_time(r) if event_time else None
        cur = con.execute("SELECT rev, sha, gone_at FROM lp_current WHERE tbl = ? AND skey = ?", (tbl, k)).fetchone()
        if cur and cur["sha"] == h and not cur["gone_at"]:
            con.execute("UPDATE lp_rows SET last_seen = ? WHERE tbl = ? AND skey = ? AND rev = ?", (seen_at, tbl, k, cur["rev"]))
            con.execute("UPDATE lp_current SET last_seen = ? WHERE tbl = ? AND skey = ?", (seen_at, tbl, k))
            same += 1
            continue
        rev = cur["rev"] + 1 if cur else 1
        con.execute("INSERT INTO lp_rows (tbl, skey, rev, sha, data, event_time, first_seen, last_seen, snapshot_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (tbl, k, rev, h, json.dumps(r, sort_keys=True, ensure_ascii=False), ev, seen_at, seen_at, snapshot_id))
        con.execute("INSERT INTO lp_current (tbl, skey, rev, sha, event_time, last_seen, gone_at) VALUES (?, ?, ?, ?, ?, ?, NULL) "
                    "ON CONFLICT(tbl, skey) DO UPDATE SET rev = excluded.rev, sha = excluded.sha, event_time = excluded.event_time, "
                    "last_seen = excluded.last_seen, gone_at = NULL", (tbl, k, rev, h, ev, seen_at))
        if cur:
            changed += 1
        else:
            new += 1
    return {"new": new, "changed": changed, "same": same}


def mark_gone(con, tbl, lo, hi, sweep_started, at):
    """After a COMPLETE sweep of [lo, hi): rows of that window not seen in the sweep stopped appearing upstream. They are
    marked gone (kept, with every revision), never deleted."""
    cur = con.execute("UPDATE lp_current SET gone_at = ? WHERE tbl = ? AND event_time >= ? AND event_time < ? "
                      "AND last_seen < ? AND gone_at IS NULL", (at, tbl, lo, hi, sweep_started))
    return cur.rowcount


def current_rows(con, tbl, where="", args=()):
    """The latest revision of every row of a source table still present upstream (dicts)."""
    q = ("SELECT r.data FROM lp_current c JOIN lp_rows r ON r.tbl = c.tbl AND r.skey = c.skey AND r.rev = c.rev "
         "WHERE c.tbl = ? AND c.gone_at IS NULL" + (" AND " + where if where else ""))
    return [json.loads(x[0]) for x in con.execute(q, (tbl,) + tuple(args))]


# ---------------------------------------------------------------- internal identities
def _next_id(con, kind):
    n = (get_meta(con, f"seq:{kind}", 0) or 0) + 1
    set_meta(con, f"seq:{kind}", n)
    return f"{SPORT}:{kind}:{n:06d}"


def find_entity(con, kind, source, skey):
    r = con.execute("SELECT eid FROM entity_sources WHERE kind = ? AND source = ? AND skey = ?", (kind, source, skey)).fetchone()
    return r[0] if r else None


def entity(con, kind, source, skey, label=None, at=None):
    """The internal id mapped to (source, skey); a new id when the source key has never been seen."""
    at = at or now_iso()
    eid = find_entity(con, kind, source, skey)
    if eid:
        con.execute("UPDATE entity_sources SET last_seen = ? WHERE kind = ? AND source = ? AND skey = ?", (at, kind, source, skey))
        return eid
    eid = _next_id(con, kind)
    con.execute("INSERT INTO entities (eid, kind, label, created_at) VALUES (?, ?, ?, ?)", (eid, kind, label or skey, at))
    con.execute("INSERT INTO entity_sources (kind, source, skey, eid, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                (kind, source, skey, eid, at, at))
    con.execute("INSERT INTO entity_history (eid, at, change, detail) VALUES (?, ?, 'created', ?)",
                (eid, at, json.dumps({"source": source, "skey": skey})))
    return eid


def detach_source(con, kind, source, skey, reason, detail=None, at=None):
    """A source key that was linked to the wrong internal id moves to a NEW id of its own (the evidence decides where it
    belongs next). Both ids record it in their history; the stale aliases of the old id from that source are dropped
    (the build re-adds the valid ones). -> the new id, or None when the key is unknown."""
    at = at or now_iso()
    cur = find_entity(con, kind, source, skey)
    if cur is None:
        return None
    new = _next_id(con, kind)
    con.execute("INSERT INTO entities (eid, kind, label, created_at) VALUES (?, ?, ?, ?)", (new, kind, skey, at))
    con.execute("UPDATE entity_sources SET eid = ?, note = COALESCE(note || '; ', '') || ? WHERE kind = ? AND source = ? AND skey = ?",
                (new, f"detached from {cur}: {reason}", kind, source, skey))
    con.execute("DELETE FROM aliases WHERE eid = ? AND source = ?", (cur, source.split(":")[0]))
    for eid, change in ((cur, f"released {source}:{skey} to {new}"), (new, f"took {source}:{skey} from {cur}")):
        con.execute("INSERT INTO entity_history (eid, at, change, detail) VALUES (?, ?, 'detached', ?)",
                    (eid, at, json.dumps({"change": change, "reason": reason, "evidence": detail}, default=str)))
    quarantine(con, kind, f"{source}:{skey}", "cross_source_link_corrected", {"from": cur, "to": new, "reason": reason,
                                                                             "evidence": detail}, at)
    # the id first created for this source key (merged into `cur` earlier) now resolves to where the key went
    x = created_from(con, kind, source, skey)
    if x and x not in (cur, new) and canonical_id(con, x) == canonical_id(con, cur):
        set_meta(con, f"merged:{x}", new)
    return new


def created_from(con, kind, source, skey):
    """The internal id that was first created for (source, skey), from the entity history - or None."""
    r = con.execute("SELECT eid FROM entity_history WHERE change = 'created' AND detail = ? AND eid LIKE ? ORDER BY id LIMIT 1",
                    (json.dumps({"source": source, "skey": skey}), f"{SPORT}:{kind}:%")).fetchone()
    return r[0] if r else None


def page_variant(con, page):
    """The Leaguepedia page a page-less spelling was resolved to (build.player_resolver), else the page itself."""
    return get_meta(con, f"page_variant:{page}") or page


def link_source(con, eid, kind, source, skey, note, at=None):
    """Map one more source key to an existing internal id (a rename, a changed upstream key), with a history entry."""
    at = at or now_iso()
    prev = find_entity(con, kind, source, skey)
    if prev == eid:
        return eid
    if prev:
        raise ValueError(f"{source}:{skey} already maps to {prev}")
    con.execute("INSERT INTO entity_sources (kind, source, skey, eid, first_seen, last_seen, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, source, skey, eid, at, at, note))
    con.execute("INSERT INTO entity_history (eid, at, change, detail) VALUES (?, ?, 'linked', ?)",
                (eid, at, json.dumps({"source": source, "skey": skey, "note": note})))
    return eid


def add_alias(con, kind, raw, eid, source, at=None):
    from .names import norm
    n = norm(raw)
    if not n:
        return
    at = at or now_iso()
    con.execute("INSERT INTO aliases (kind, norm, eid, source, raw, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(kind, norm, eid, source) DO UPDATE SET last_seen = excluded.last_seen", (kind, n, eid, source, raw, at, at))


def quarantine(con, kind, skey, reason, detail=None, at=None):
    at = at or now_iso()
    con.execute("INSERT INTO quarantine (kind, skey, reason, detail, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(kind, skey, reason) DO UPDATE SET last_seen = excluded.last_seen, detail = excluded.detail",
                (kind, skey, reason, json.dumps(detail, default=str, ensure_ascii=False) if detail is not None else None, at, at))


def resolve_quarantine(con, kind, skey, reason, at=None):
    con.execute("UPDATE quarantine SET resolved_at = ? WHERE kind = ? AND skey = ? AND reason = ? AND resolved_at IS NULL",
                (at or now_iso(), kind, skey, reason))


def merge(con, keep, drop, note, at=None):
    """Two internal ids turned out to be one player / team (e.g. its LoL Esports id and its Leaguepedia page, linked
    through a series both sources hold): every source mapping and alias of `drop` moves to `keep`; both ids record it;
    `drop` stays resolvable through canonical_id(). Nothing is deleted except the moved alias duplicates."""
    if keep == drop:
        return keep
    at = at or now_iso()
    con.execute("UPDATE entity_sources SET eid = ?, note = substr(COALESCE(note || '; ', '') || ?, -1000) WHERE eid = ?",
                (keep, f"merged from {drop}", drop))     # the full record is entity_history; the note keeps the latest moves
    for r in con.execute("SELECT kind, norm, source, raw, first_seen, last_seen FROM aliases WHERE eid = ?", (drop,)).fetchall():
        con.execute("INSERT OR IGNORE INTO aliases (kind, norm, eid, source, raw, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (r[0], r[1], keep, r[2], r[3], r[4], r[5]))
    con.execute("DELETE FROM aliases WHERE eid = ?", (drop,))
    set_meta(con, f"merged:{drop}", keep)
    for eid, change in ((keep, f"absorbed {drop}"), (drop, f"merged into {keep}")):
        con.execute("INSERT INTO entity_history (eid, at, change, detail) VALUES (?, ?, 'merged', ?)", (eid, at, json.dumps({"change": change, "note": note})))
    return keep


def canonical_id(con, eid):
    """The surviving id of an id that was merged (follows chains); the id itself otherwise."""
    seen = set()
    while eid and eid not in seen:
        seen.add(eid)
        nxt = get_meta(con, f"merged:{eid}")
        if not nxt:
            return eid
        eid = nxt
    return eid
