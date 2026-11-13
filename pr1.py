"""pr1.py - pr-1 reproducible pre-match prediction records (2026-11-13). Measurement only: never a grade input, never shown.

For every graded CS2 line (kills and HS, every period; board and member card grades - owner load tests excluded) one
append-only record is written when a prediction is produced or changes, before the match. A record holds, together:
  - identity: prediction id (stable per line + source), player (BO3.gg id / HLTV ids), team, opponent, canonical match id
    (hltv:<id> when known), series date + event, match slug and listed start
  - offer: prop, period (scope 12 / 1 / 123), line, line type, app, offer id, offer revision (hash of the offer CONTENT:
    line, sides offered, prices / payouts, market type) and the times that revision was seen / stamped by the provider
  - prediction: projection, P(Over), side, chance of the side, letter, stake, state, shown (False = sr-bo3-2 shadow),
    the sides offered and whether the chosen side is offered
  - model: model tag (projection_source), version hash (picklog.MODEL_VERSION), deployed commit, the grade-time flags,
    rating (Elo) snapshot = cm-2 tables sha + weekly refit
  - inputs: the exact grader inputs (cs2_scout.grader_inputs -> grader.grade_leg) split into value groups, each stored ONCE
    by content hash (pr1_featuresets); the per-read metadata (read times, quote ages, snapshot ids) kept per record so the
    input dict is rebuilt exactly; per source an availability entry {t, status verified | unverified | n/a, basis, ...}
  - created_at (UTC, ms)

Availability (strict: when the value became available to us, never the match date):
  board_quote   the board's fetch time of the offer (our clock)                                         verified
  member        a typed member line: the request time                                                    verified
  odds          market_odds provenance retrieved_at (our fetch; the feeds publish no quote time)          verified
  hltv_player   HLTV DB: per feeding match max(raw_pages.fetched_at, mapstats_pages.fetched_at), read at
                record time; t = the newest. Verified only when every feeding match has a fetch time and
                none was re-fetched after the grade read it (fetched_at is the LAST fetch)
  hltv_opp      the same for the opponent team's maps (performance model's opponent input)
  rating        cm-2 tables built_at for the tables sha the grade used (Elo + weekly refit snapshot)      verified when known
  bo3           BO3.gg player match list: the packed cache's saved time (kvstore) for that request        verified when <= grade
  roles         HLTV weapons_status.fetched_at / player_roles.updated_at for his HLTV ids                 verified when present
  rank          HLTV team_rankings snapshot DATE only                                                     unverified (date only)
  maps          map chances / veto model behind map_probs: no time carried into the grade                  unverified
  other         anything not classified above                                                              unverified

Rules: records, feature sets and corrections are immutable (SQLite triggers refuse UPDATE / DELETE). Later facts - an
HLTV re-fetch of a feeding match, a superseded rating snapshot, a moved offer - are appended to pr1_corrections with the
record id. A new record is written only when an input value, the offer revision or the prediction changes (exact).
Writes happen on one background thread in batches (bounded queue, drop-and-count when full - never blocks a grade).
Separate SQLite file (pr1_records.db) - never the pick log's lock. Flag PR1_RECORDS (default on; 0 = off, read per call).
"""
import hashlib
import json
import os
import queue
import re
import sqlite3
import threading
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

VERSION = "pr-1"
DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent)
DB = DATA_DIR / "pr1_records.db"
QUEUE_MAX = 3000          # items hold references to the grade inputs; the writer drains in ms, a burst of 700 cards fits
BATCH = 200
PAGE_MAX = 200           # egress: a summary page is ~1.5 KB per record
PAGE_MAX_FULL = 20       # a full record (inputs + availability) is ~20 KB
HLTV_TTL = 10 * 60
DAY_BYTES_WARN = 25 * 1024 * 1024
FLAGS = ("SR_BO3_1", "CS2_AWP_HS_LIFT", "CM2_LIVE", "CM2_HS_LIVE", "BO3_PROJ_SHADOW")

_Q = queue.Queue(maxsize=QUEUE_MAX)
_LOCK = threading.Lock()
_WRITER = [None]
STATS = {"submitted": 0, "dropped_full": 0, "written": 0, "deduped": 0, "after_start": 0, "errors": 0, "corrections": 0,
         "bodies_new": 0, "bytes_written": 0, "last_error": None, "last_write_at": None, "batch_ms_max": 0}


def enabled():
    return os.environ.get("PR1_RECORDS", "1").strip() != "0"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _sha(b):
    return hashlib.sha256(b).hexdigest()[:16]


def _canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str)


def _utc(s):
    try:
        t = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


# ------------------------------------------------------------------------------------------------------- schema
_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS pr1_records (id INTEGER PRIMARY KEY, prediction_id TEXT NOT NULL, chain TEXT NOT NULL,
       seq INTEGER NOT NULL, created_at TEXT NOT NULL, source TEXT, test INTEGER DEFAULT 0,
       player TEXT, player_slug TEXT, player_bo3_id INTEGER, hltv_player_ids TEXT, team TEXT, opponent TEXT,
       match_slug TEXT, match_id TEXT, hltv_match INTEGER, series_date TEXT, event TEXT, match_start TEXT, bo INTEGER,
       prop TEXT, period TEXT, line REAL, line_type TEXT, app TEXT, offer_id TEXT, offer_rev TEXT, offer_seen_at TEXT,
       offer_updated_at TEXT, projection REAL, p_more REAL, side TEXT, chance REAL, letter TEXT, stake REAL, state TEXT,
       shown INTEGER, offered TEXT, side_offered INTEGER, model_tag TEXT, model_version TEXT, git_commit TEXT, flags TEXT,
       rating_snapshot TEXT, inputs TEXT NOT NULL, vol BLOB, sig TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS pr1_chain ON pr1_records (chain, id)",
    "CREATE INDEX IF NOT EXISTS pr1_match ON pr1_records (match_id)",
    "CREATE TABLE IF NOT EXISTS pr1_featuresets (sha TEXT PRIMARY KEY, kind TEXT, created_at TEXT, body BLOB)",
    """CREATE TABLE IF NOT EXISTS pr1_corrections (id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL REFERENCES pr1_records(id),
       created_at TEXT NOT NULL, kind TEXT NOT NULL, field TEXT, old TEXT, new TEXT, source TEXT, note TEXT)""",
    "CREATE INDEX IF NOT EXISTS pr1_corr_rec ON pr1_corrections (record_id)",
]
for _t in ("pr1_records", "pr1_featuresets", "pr1_corrections"):
    _SCHEMA += [f"CREATE TRIGGER IF NOT EXISTS {_t}_no_update BEFORE UPDATE ON {_t} BEGIN SELECT RAISE(ABORT, 'pr-1 {_t} is append-only'); END",
                f"CREATE TRIGGER IF NOT EXISTS {_t}_no_delete BEFORE DELETE ON {_t} BEGIN SELECT RAISE(ABORT, 'pr-1 {_t} is append-only'); END"]
_READY = [None]


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA synchronous=NORMAL")
    if _READY[0] != str(DB):
        con.execute("PRAGMA journal_mode=WAL")
        for s in _SCHEMA:
            con.execute(s)
        con.commit()
        _READY[0] = str(DB)
    return con


def _read_db():
    con = _db()
    con.execute("PRAGMA query_only=ON")
    return con


# ------------------------------------------------------------------------------------------------------- submit
_G_KEYS = ("lambda_final", "p_more", "p_less", "line", "line_type", "prop", "scope", "projection_source", "less_available")


def submit(res, v, test=0, source="board"):
    """Called right after picklog.log_async for the same grade. Captures references only (the request thread replaces,
    never mutates, these dicts afterwards) and returns at once; everything else runs on the writer thread."""
    if not enabled() or int(test or 0) == 1:
        return False
    try:
        g = res.get("grade") or {}
        if not v or v.get("state") != "GRADE" or "p_more" not in g:
            return False
        pb = res.get("player_block") or {}
        item = {"t": _now(), "test": int(test or 0), "source": source, "g": {k: g.get(k) for k in _G_KEYS if k in g}, "v": dict(v),
                "gi": res.get("grader_inputs"),
                "nm": res.get("next_match") or {}, "player": pb.get("player") or {}, "playing_for": pb.get("playing_for"),
                "current_team": pb.get("current_team"), "profile_team": pb.get("profile_team"), "team_hint": res.get("team_hint"),
                "book": res.get("book"), "generated": res.get("generated"),
                "opp_ranking": ((res.get("hltv_context") or {}).get("opp_ranking") if isinstance(res.get("hltv_context"), dict) else None)}
        _Q.put_nowait(item)
        STATS["submitted"] += 1
    except queue.Full:
        STATS["dropped_full"] += 1
        return False
    except Exception as e:          # noqa: BLE001 - never stops a grade
        STATS["errors"] += 1
        STATS["last_error"] = f"submit: {type(e).__name__}: {e}"[:300]
        return False
    if _WRITER[0] is None or not _WRITER[0].is_alive():
        with _LOCK:
            if _WRITER[0] is None or not _WRITER[0].is_alive():
                _WRITER[0] = threading.Thread(target=_writer, daemon=True, name="pr1-writer")
                _WRITER[0].start()
    return True


def _writer():
    while True:
        items = [_Q.get()]
        while len(items) < BATCH:
            try:
                items.append(_Q.get_nowait())
            except queue.Empty:
                break
        try:
            write_batch(items)
        except Exception as e:      # noqa: BLE001
            STATS["errors"] += 1
            STATS["last_error"] = f"batch: {type(e).__name__}: {e}"[:300]
        finally:
            for _ in items:
                _Q.task_done()


def flush(timeout=30):
    """Tests / verification: wait until the queue is written."""
    t0 = time.time()
    while _Q.unfinished_tasks and time.time() - t0 < timeout:
        time.sleep(0.02)
    return not _Q.unfinished_tasks


# ------------------------------------------------------------------------------------------------------- inputs
GROUPS = {
    "offer": ("prop", "line", "line_type", "less_available", "scope", "bo"),
    "market": ("market_quality", "odds_provenance", "p_team_market", "odds_policy", "mapping_rev", "team_ml", "opp_ml",
               "map_win_probs"),
    "stats": ("maps_played", "kpr", "hs_pct", "sample_maps", "kpr_last5", "form_trend", "environment", "round_swing", "per_map",
              "map_stats", "rating", "teammate_ratings", "dpr", "kast", "adr", "days_since_last_map", "trade_pr", "firepower",
              "trading", "opening", "recent_avg", "recent_median", "recent_n", "recent", "hltv_series", "h2h_series",
              "regional_series"),
    "perf": ("perf",),
    "roster": ("is_igl", "sniping", "standin_for", "is_standin", "roster_confirmed", "on_listed_team", "gate_sources", "scout"),
    "rank": ("opp_rank", "rank_band"),
    "maps": ("map_probs",),
}
_GROUP_OF = {k: g for g, ks in GROUPS.items() for k in ks}
# which availability sources each value group depends on
GROUP_SOURCES = {"offer": ("offer",), "market": ("odds",), "stats": ("hltv_player", "bo3"), "perf": ("hltv_player", "hltv_opp", "rating"),
                 "roster": ("roles", "bo3"), "rank": ("rank",), "maps": ("maps",), "other": ("other",)}
VOL_TOP = ("odds_check",)                    # the re-check record: metadata the grader never reads
VOL_LEAF = {"as_of", "age_min", "retrieved_at", "quote_age_s", "event_last_observed", "endpoint_last_success_at",
            "endpoint_last_nonempty_at", "selected_at", "snapshot_sha256", "checked_at", "tables_sha", "input_revision",
            "input_revision_stale"}
VOL_STATE_UNDER = ("hltv_series", "player_maps", "opp_maps")    # 'state' (live / memory copy) under these read records


def _strip(o, path, vol):
    """Copy of o without the per-read metadata leaves (moved to vol as [path, value])."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            p = path + [k]
            if k in VOL_LEAF or (k == "state" and path and path[-1] in VOL_STATE_UNDER):
                vol.append([p, v])
            else:
                out[k] = _strip(v, p, vol)
        return out
    return o


def split_inputs(gi):
    """grader inputs -> ({group: body}, vol). Exact inverse: merge_inputs."""
    groups, vol = {}, []
    for k, v in (gi or {}).items():
        if k in VOL_TOP:
            vol.append([[k], v])
            continue
        g = _GROUP_OF.get(k, "other")
        groups.setdefault(g, {})[k] = _strip(v, [k], vol)
    return groups, vol


def merge_inputs(groups, vol, order=None):
    gi = {}
    for body in groups.values():
        for k, v in body.items():
            gi[k] = json.loads(_canon(v))
    for p, v in vol:
        cur = gi
        for k in p[:-1]:
            cur = cur.setdefault(k, {})
        cur[p[-1]] = v
    if order:
        gi = {k: gi[k] for k in order if k in gi} | {k: v for k, v in gi.items() if k not in order}
    return gi


# ------------------------------------------------------------------------------------------------------- availability
_HCON = [None]
_HCACHE = {}            # key -> (read_at, value, {matchid: fetched_iso})
_RECS_BY_HLTV = {}      # ("p"|"t", id) -> [(record_id, created_at)] (bounded) for re-fetch corrections
_HLTV_SQL_PLAYER = """
WITH m AS (SELECT DISTINCT ps.matchid, mo.date FROM player_stats ps JOIN match_overview mo ON mo.matchid = ps.matchid
           WHERE ps.playerid = ANY(%(ids)s) AND mo.date >= %(since)s)
SELECT m.matchid, m.date,
       (SELECT max(r.fetched_at) FROM raw_pages r WHERE r.matchid = m.matchid),
       (SELECT max(p.fetched_at) FROM match_maps mm JOIN mapstats_pages p ON p.mapstatsid = mm.mapstatsid WHERE mm.matchid = m.matchid),
       (SELECT min(q.found_at) FROM scrape_queue q WHERE q.matchid = m.matchid)
FROM m
"""
_HLTV_SQL_TEAM = _HLTV_SQL_PLAYER.replace(
    "SELECT DISTINCT ps.matchid, mo.date FROM player_stats ps JOIN match_overview mo ON mo.matchid = ps.matchid\n           WHERE ps.playerid = ANY(%(ids)s) AND mo.date >= %(since)s",
    "SELECT DISTINCT t.matchid, mo.date FROM matches t JOIN match_overview mo ON mo.matchid = t.matchid\n           WHERE t.teamid = %(tid)s AND mo.date >= %(since)s")
_HLTV_SQL_ROLES = """SELECT (SELECT max(fetched_at) FROM weapons_status WHERE playerid = ANY(%(ids)s)),
                            (SELECT max(updated_at) FROM player_roles WHERE playerid = ANY(%(ids)s))"""
HISTORY_DAYS = 420
HCACHE_MAX = 400
SEEN_MAX = 500


def _hltv_cursor():
    import hltv
    if not hltv.dsn():
        return None
    con = _HCON[0]
    if con is None or getattr(con, "closed", 1):
        import psycopg2
        con = psycopg2.connect(hltv.dsn(), connect_timeout=10)
        con.set_session(readonly=True, autocommit=True)
        with con.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
        _HCON[0] = con
    return con.cursor()


def _iso(t):
    if t is None:
        return None
    if isinstance(t, datetime):
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return t.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(t)


def _hltv_fetch_times(kind, key, since):
    """{matchid: (date, last_fetch_iso, first_queued_iso)} for the matches feeding an input, cached HLTV_TTL."""
    ck = (kind, key, since)
    hit = _HCACHE.get(ck)
    if hit and time.time() - hit[0] < HLTV_TTL:
        return hit[1]
    cur = _hltv_cursor()
    if cur is None:
        return None
    try:
        if kind == "p":
            cur.execute(_HLTV_SQL_PLAYER, {"ids": list(key), "since": since})
        else:
            cur.execute(_HLTV_SQL_TEAM, {"tid": key, "since": since})
        rows = cur.fetchall()
    finally:
        cur.close()
    out = {}
    for mid, d, rf, mf, fq in rows:
        last = max([x for x in (rf, mf) if x is not None], default=None)
        out[int(mid)] = (str(d), _iso(last), _iso(fq))
    if len(_HCACHE) > HCACHE_MAX:          # memory: each entry holds one player's / team's match -> fetch-time map
        _HCACHE.clear()
    _HCACHE[ck] = (time.time(), out)
    return out


def _hltv_entry(fetch, read_at, created_at):
    if fetch is None:
        return {"t": None, "status": "unverified", "basis": "HLTV database not reachable at record time"}
    if not fetch:
        return {"t": None, "status": "n/a", "basis": "no HLTV maps in the window", "n": 0}
    rd = _utc(read_at) or _utc(created_at)
    no_ts = [m for m, (_, f, _) in fetch.items() if f is None]
    after = [m for m, (_, f, _) in fetch.items() if f is not None and rd is not None and _utc(f) > rd]
    stamped = [(f, m) for m, (_, f, _) in fetch.items() if f is not None]
    newest = max(stamped) if stamped else (None, None)
    newest_day = max(fetch.items(), key=lambda kv: kv[1][0])
    ok = not no_ts and not after
    return {"t": newest[0], "status": "verified" if ok else "unverified",
            "basis": "max HLTV fetch time (raw_pages / mapstats_pages, last fetch) over the feeding matches, read at record time",
            "n": len(fetch), "no_ts": len(no_ts), "after_read": len(after), "newest_fetch_match": newest[1],
            "newest_match": newest_day[0], "newest_match_date": newest_day[1][0], "read_at": read_at,
            "set_sha": _sha(_canon(sorted((m, f) for m, (_, f, _) in fetch.items())).encode())}


_BO3_PARAMS = {"scope": "widget-matches", "page[offset]": 0, "page[limit]": 100, "sort": "-start_date",
               "filter[matches.status][in]": "finished,defwin,upcoming,current",
               "filter[matches.discipline_id][eq]": 1, "with": "teams,tournament,ai_predictions,games,streams"}


def bo3_key(player_id):
    """The packed-cache name of BO3.gg's player match list (bo3.BO3._request: endpoint + '?' + sorted params JSON)."""
    params = dict(_BO3_PARAMS, **{"filter[matches.player_ids][overlap]": str(player_id)})
    return hashlib.sha1(("/matches?" + json.dumps(params, sort_keys=True, default=str)).encode()).hexdigest()


def _bo3_entry(player_id, grade_at):
    if not player_id:
        return {"t": None, "status": "n/a", "basis": "no BO3.gg player"}
    try:
        import kvstore
        row = kvstore._con().execute("SELECT at FROM kv WHERE bucket = 'bo3' AND name = ?", (bo3_key(player_id),)).fetchone()
    except Exception as e:      # noqa: BLE001
        return {"t": None, "status": "unverified", "basis": f"BO3.gg cache unreadable ({type(e).__name__})"}
    if not row:
        return {"t": None, "status": "unverified", "basis": "BO3.gg match list not in the cache at record time"}
    t = datetime.fromtimestamp(float(row[0]), timezone.utc)
    g = _utc(grade_at)
    ok = g is None or t <= g
    return {"t": _iso(t), "status": "verified" if ok else "unverified",
            "basis": "BO3.gg player match list: cache save time (our fetch)" + ("" if ok else "; re-fetched after the grade read it")}


def _rating_entry(cm2, created_at):
    if not isinstance(cm2, dict) or cm2.get("ineligible") or not cm2.get("tables_sha"):
        return {"t": None, "status": "n/a", "basis": "no Elo input (cm-2 not used)"}
    sha = cm2.get("tables_sha")
    built = _TABLES_SEEN.get(sha)
    if built is None:
        try:
            import cm2 as _cm2
            m = (_cm2.status() or {}).get("tables") or {}
            if m.get("tables_sha"):
                _TABLES_SEEN[m["tables_sha"]] = m.get("built_at")
            built = _TABLES_SEEN.get(sha)
        except Exception:       # noqa: BLE001
            built = None
    if not built:
        return {"t": None, "status": "unverified", "basis": "cm-2 tables build time unknown for this sha", "snapshot": sha}
    ok = (_utc(built) or _utc(created_at)) <= _utc(created_at)
    return {"t": _iso(_utc(built) or built), "status": "verified" if ok else "unverified",
            "basis": "cm-2 tables built_at (Elo at start of the match day + weekly opponent refit, from HLTV rows as stored then)",
            "snapshot": sha, "refit": (cm2.get("inputs") or {}).get("refit")}


_TABLES_SEEN = {}


def _roles_entry(ids, created_at):
    if not ids:
        return {"t": None, "status": "unverified", "basis": "no HLTV player id: role / AWP source time unknown"}
    cur = None
    try:
        cur = _hltv_cursor()
        if cur is None:
            return {"t": None, "status": "unverified", "basis": "HLTV database not configured"}
        ck = ("roles", tuple(ids))
        hit = _HCACHE.get(ck)
        if hit and time.time() - hit[0] < HLTV_TTL:
            w, r = hit[1]
        else:
            cur.execute(_HLTV_SQL_ROLES, {"ids": list(ids)})
            w, r = cur.fetchone()
            w, r = _iso(w), _iso(r)
            _HCACHE[ck] = (time.time(), (w, r))
    except Exception as e:      # noqa: BLE001
        return {"t": None, "status": "unverified", "basis": f"HLTV read failed ({type(e).__name__})"}
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:   # noqa: BLE001
                pass
    t = max([x for x in (w, r) if x], default=None)
    if t is None:
        return {"t": None, "status": "unverified", "basis": "no weapons / roles fetch time stored"}
    ok = _utc(t) <= _utc(created_at)
    return {"t": t, "status": "verified" if ok else "unverified", "weapons_fetched_at": w, "roles_updated_at": r,
            "basis": "HLTV weapons_status.fetched_at / player_roles.updated_at (last refresh)"}


def availability(item, gi, created_at):
    """{source: entry} - see the module doc. HLTV / cache reads happen here, on the writer thread."""
    q = ((item.get("book") or {}).get("quote") or {}) if isinstance(item.get("book"), dict) else {}
    a = {}
    if item.get("book"):
        a["offer"] = {"t": q.get("board_at"), "status": "verified" if q.get("board_at") else "unverified",
                      "basis": "board fetch time of the offer (our clock)", "provider_updated_at": q.get("updated_at")}
    else:
        a["offer"] = {"t": created_at, "status": "verified", "basis": "member-typed line: request time"}
    pv = gi.get("odds_provenance") if isinstance(gi.get("odds_provenance"), dict) else {}
    if gi.get("p_team_market") is None:
        a["odds"] = {"t": None, "status": "n/a", "basis": "no market input"}
    else:
        a["odds"] = {"t": pv.get("retrieved_at"), "status": "verified" if pv.get("retrieved_at") else "unverified",
                     "basis": f"{pv.get('source') or 'market'} quote: our retrieval time", "source_updated_at": pv.get("source_updated_at")}
    pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
    hd = pf.get("hltv_data") or {}
    ids = tuple(int(x) for x in (pf.get("hltv_player_ids") or []) if str(x).isdigit())
    day = pf.get("newest_map_day")
    since = None
    try:
        st = _utc((item.get("nm") or {}).get("start")) or _utc(created_at)
        from datetime import timedelta
        since = (st.date() - timedelta(days=HISTORY_DAYS)).isoformat()
    except Exception:           # noqa: BLE001
        since = "2000-01-01"
    a["hltv_player"] = a["hltv_opp"] = None
    try:
        a["hltv_player"] = _hltv_entry(_hltv_fetch_times("p", ids, since) if ids else {}, (hd.get("player_maps") or {}).get("as_of"), created_at)
    except Exception as e:      # noqa: BLE001
        a["hltv_player"] = {"t": None, "status": "unverified", "basis": f"HLTV read failed ({type(e).__name__})"}
        _reset_hcon()
    opp = pf.get("hltv_opp_team")
    try:
        a["hltv_opp"] = _hltv_entry(_hltv_fetch_times("t", int(opp), since) if opp else {}, (hd.get("opp_maps") or {}).get("as_of"), created_at)
    except Exception as e:      # noqa: BLE001
        a["hltv_opp"] = {"t": None, "status": "unverified", "basis": f"HLTV read failed ({type(e).__name__})"}
        _reset_hcon()
    if day is not None and a["hltv_player"].get("t") is None and a["hltv_player"].get("status") == "n/a":
        a["hltv_player"]["note"] = "performance input names a newest map day but no HLTV map was found"
    a["rating"] = _rating_entry(pf.get("cm2_live"), created_at)
    a["bo3"] = _bo3_entry((item.get("player") or {}).get("id"), item.get("generated") or created_at)
    a["roles"] = _roles_entry(ids, created_at)
    orr = item.get("opp_ranking") if isinstance(item.get("opp_ranking"), dict) else {}
    a["rank"] = {"t": None, "status": "unverified" if gi.get("opp_rank") is not None else "n/a",
                 "basis": "HLTV team_rankings snapshot date only (no fetch time)", "date": orr.get("date")}
    a["maps"] = {"t": None, "status": "unverified" if gi.get("map_probs") else "n/a",
                 "basis": "map chances / veto model: no time carried into the grade"}
    a["other"] = {"t": None, "status": "unverified", "basis": "unclassified inputs: no time carried into the grade"}
    return a


def _reset_hcon():
    try:
        if _HCON[0] is not None:
            _HCON[0].close()
    except Exception:           # noqa: BLE001
        pass
    _HCON[0] = None


# ------------------------------------------------------------------------------------------------------- build / write
def _offer(item):
    bk = item.get("book") if isinstance(item.get("book"), dict) else None
    g = item["g"]
    if not bk:
        return None, None, {"over": True, "under": g.get("less_available", True)}
    q = bk.get("quote") or {}
    content = {"line": g.get("line"), "line_type": g.get("line_type"), "market_id": bk.get("market_id"),
               "feed_odds_type": q.get("feed_odds_type"), "over": q.get("over_available"), "under": q.get("under_available"),
               "price_over": q.get("price_over"), "price_under": q.get("price_under"),
               "payout_over": q.get("payout_over"), "payout_under": q.get("payout_under")}
    return bk, _sha(_canon(content).encode())[:12], {"over": q.get("over_available"), "under": q.get("under_available")}


def build(item):
    """(record, {sha: (kind, body)}) for one submitted grade - pure apart from the availability reads."""
    g, v, nm, gi = item["g"], item["v"], item["nm"], item.get("gi") or {}
    created = item["t"]
    pl = item.get("player") or {}
    scope = g.get("scope") or "12"
    bk, offer_rev, offered = _offer(item)
    app = (bk or {}).get("book") or ("member" if item.get("source") == "member" else None)
    slug = nm.get("slug") or (f"hltv-{nm['hltv_match']}" if nm.get("hltv_match") else None)
    chain = "|".join(str(x) for x in (pl.get("slug"), g.get("prop"), g.get("line"), g.get("line_type"), slug, scope,
                                       app or "-", item.get("source")))
    try:
        import veto_serve
        mid = veto_serve.match_id_of({"player_block": {"playing_for": item.get("playing_for"), "current_team": item.get("current_team")},
                                      "next_match": nm})
    except Exception:           # noqa: BLE001
        mid = None
    hm = re.fullmatch(r"hltv:(\d+)", str(mid or ""))
    st = _utc(nm.get("start"))
    groups, vol = split_inputs(gi)
    bodies, vref = {}, {}
    for name, body in groups.items():
        b = _canon(body).encode()
        h = _sha(b)
        vref[name] = h
        bodies[h] = ("v:" + name, b)
    pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
    cm = pf.get("cm2_live") if isinstance(pf.get("cm2_live"), dict) else {}
    rating = f"cm2:{cm.get('tables_sha')}:refit={(cm.get('inputs') or {}).get('refit')}" if cm.get("tables_sha") and not cm.get("ineligible") else None
    side = v.get("side")
    try:
        import picklog
        mv = picklog.MODEL_VERSION
    except Exception:           # noqa: BLE001
        mv = None
    try:
        import grade_display
        shown = not grade_display.bo3_shadow(g)
    except Exception:           # noqa: BLE001
        shown = None
    so = None
    if side in ("More", "Less") and offered:
        k = "over" if side == "More" else "under"
        so = None if offered.get(k) is None else int(bool(offered.get(k)))
    rec = {"prediction_id": _sha(chain.encode()), "chain": chain, "created_at": created, "source": item.get("source"),
           "test": item.get("test", 0), "player": pl.get("nick"), "player_slug": pl.get("slug"), "player_bo3_id": pl.get("id"),
           "hltv_player_ids": _canon(pf.get("hltv_player_ids")) if pf.get("hltv_player_ids") is not None else None,
           "team": item.get("playing_for") or item.get("team_hint") or item.get("current_team"), "opponent": nm.get("opponent"),
           "match_slug": slug, "match_id": mid, "hltv_match": int(hm.group(1)) if hm else (int(nm["hltv_match"]) if str(nm.get("hltv_match") or "").isdigit() else None),
           "series_date": st.date().isoformat() if st else None, "event": nm.get("event"), "match_start": nm.get("start"),
           "bo": nm.get("bo") if isinstance(nm.get("bo"), int) else None,
           "prop": g.get("prop"), "period": scope, "line": g.get("line"), "line_type": g.get("line_type"), "app": app,
           "offer_id": str(bk.get("market_id")) if bk and bk.get("market_id") is not None else None, "offer_rev": offer_rev,
           "offer_seen_at": ((bk or {}).get("quote") or {}).get("board_at"), "offer_updated_at": ((bk or {}).get("quote") or {}).get("updated_at"),
           "projection": g.get("lambda_final"), "p_more": g.get("p_more"), "side": side, "chance": v.get("p"), "letter": v.get("grade"),
           "stake": v.get("units"), "state": v.get("state"), "shown": None if shown is None else int(shown),
           "offered": _canon(offered), "side_offered": so, "model_tag": g.get("projection_source"), "model_version": mv,
           "git_commit": os.environ.get("RENDER_GIT_COMMIT") or None,
           "flags": _canon({f: os.environ.get(f) for f in FLAGS}), "rating_snapshot": rating,
           "inputs": None,
           "vol": zlib.compress(_canon(vol).encode(), 6)}
    pred = [rec[k] for k in ("projection", "p_more", "side", "chance", "letter", "stake", "state", "shown", "offered", "side_offered",
                             "model_tag", "model_version")]
    rec["sig"] = _sha(_canon([sorted(vref.items()), offer_rev, pred]).encode())
    rec["_vref"], rec["_order"] = vref, list(gi.keys())
    return rec, bodies


def attach_availability(rec, bodies, item):
    """The availability entries (HLTV / cache reads) - only for a record that will be written (after the dedupe check)."""
    av = availability(item, item.get("gi") or {}, rec["created_at"])
    aref = {}
    for src, ent in av.items():
        b = _canon(ent).encode()
        h = _sha(b)
        aref[src] = h
        bodies[h] = ("a:" + src, b)
    rec["inputs"] = _canon({"v": rec.pop("_vref"), "a": aref, "order": rec.pop("_order"), "groups_rule": "pr1-g1"})
    return rec


_LAST = {}              # chain -> (sig, record_id, offer_rev, rating_snapshot, hltv_player set_sha) (bounded)
_COLS = None


def _before_start(created, start):
    a, b = _utc(created), _utc(start)
    return True if a is None or b is None else a < b


def write_batch(items):
    t0 = time.time()
    built = []
    for it in items:
        try:
            if not _before_start(it["t"], (it.get("nm") or {}).get("start")):
                STATS["after_start"] += 1
                continue
            built.append(build(it) + (it,))
        except Exception as e:  # noqa: BLE001
            STATS["errors"] += 1
            STATS["last_error"] = f"build: {type(e).__name__}: {e}"[:300]
    if not built:
        return 0
    n = 0
    con = _db()
    try:
        for rec, bodies, it in built:
            last = _LAST.get(rec["chain"])
            if last is None:
                r = con.execute("SELECT id, seq, sig, offer_rev, rating_snapshot FROM pr1_records WHERE chain = ? "
                                "ORDER BY id DESC LIMIT 1", (rec["chain"],)).fetchone()
                last = (r["sig"], r["id"], r["offer_rev"], r["rating_snapshot"], r["seq"]) if r else None
            if last and last[0] == rec["sig"]:
                STATS["deduped"] += 1
                continue
            attach_availability(rec, bodies, it)
            for h, (kind, body) in bodies.items():
                cur = con.execute("INSERT OR IGNORE INTO pr1_featuresets (sha, kind, created_at, body) VALUES (?,?,?,?)",
                                  (h, kind, rec["created_at"], zlib.compress(body, 6)))
                if cur.rowcount:
                    STATS["bodies_new"] += 1
                    STATS["bytes_written"] += len(body) // 3 + 60
            rec = dict(rec, seq=(last[4] + 1) if last else 1)
            cols = list(rec)
            con.execute(f"INSERT INTO pr1_records ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [rec[c] for c in cols])
            rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            STATS["bytes_written"] += sum(len(x) if isinstance(x, (str, bytes)) else 8 for x in rec.values()) + 40
            if last:                                   # corrections to the previous record of this line (never an update)
                if last[2] != rec["offer_rev"]:
                    _correct(con, last[1], "offer_moved", "offer_rev", last[2], rec["offer_rev"], "board feed",
                             f"superseded by record {rid} (line {rec['line']})")
                if last[3] and rec["rating_snapshot"] and last[3] != rec["rating_snapshot"]:
                    _correct(con, last[1], "rating_snapshot_superseded", "rating_snapshot", last[3], rec["rating_snapshot"],
                             "cm-2 tables rebuild", f"Elo / refit recomputed; see record {rid}")
            _refetch_corrections(con, rec, rid)
            _LAST[rec["chain"]] = (rec["sig"], rid, rec["offer_rev"], rec["rating_snapshot"], rec["seq"])
            n += 1
        con.commit()
    finally:
        con.close()
    if len(_LAST) > 20000:
        _LAST.clear()
    STATS["written"] += n
    STATS["last_write_at"] = _now()
    STATS["batch_ms_max"] = max(STATS["batch_ms_max"], int((time.time() - t0) * 1000))
    return n


def _correct(con, record_id, kind, field, old, new, source, note):
    con.execute("INSERT INTO pr1_corrections (record_id, created_at, kind, field, old, new, source, note) VALUES (?,?,?,?,?,?,?,?)",
                (record_id, _now(), kind, field, None if old is None else str(old), None if new is None else str(new), source, note))
    STATS["corrections"] += 1


def add_correction(record_id, kind, field=None, old=None, new=None, source=None, note=None):
    """Append a correction for a record (admin / later jobs). Never changes the record."""
    con = _db()
    try:
        if not con.execute("SELECT 1 FROM pr1_records WHERE id = ?", (record_id,)).fetchone():
            raise KeyError(record_id)
        _correct(con, record_id, kind, field, old, new, source, note)
        con.commit()
    finally:
        con.close()


_FETCH_SEEN = {}        # ("p", ids) / ("t", tid) -> {matchid: last_fetch_iso} as last read


def _refetch_corrections(con, rec, rid):
    """An HLTV feeding match re-fetched since an earlier record read it (its stats may have been edited): a correction on
    every earlier record of this player / team still remembered here. In memory, best effort."""
    try:
        ids = tuple(json.loads(rec["hltv_player_ids"] or "[]"))
    except ValueError:
        ids = ()
    for kind, key in (("p", ids),):
        if not key:
            continue
        cache = [v for k, v in _HCACHE.items() if k[0] == kind and k[1] == key]
        if not cache:
            continue
        now_set = {m: f for m, (_, f, _) in cache[-1][1].items()}
        prev = _FETCH_SEEN.get((kind, key))
        refetched = [m for m, f in now_set.items() if prev and m in prev and prev[m] and f and f > prev[m]]
        if refetched:
            for old_rid, _ in _RECS_BY_HLTV.get((kind, key), [])[-50:]:
                _correct(con, old_rid, "hltv_refetch", "hltv_player", ",".join(map(str, refetched[:20])),
                         ",".join(now_set[m] for m in refetched[:20]), "HLTV scraper re-fetch",
                         f"{len(refetched)} feeding match(es) re-fetched after this record read them; values may differ")
        _FETCH_SEEN[(kind, key)] = now_set
        lst = _RECS_BY_HLTV.setdefault((kind, key), [])
        lst.append((rid, rec["created_at"]))
        del lst[:-50]
    if len(_RECS_BY_HLTV) > SEEN_MAX or len(_FETCH_SEEN) > SEEN_MAX:
        _RECS_BY_HLTV.clear()
        _FETCH_SEEN.clear()


# ------------------------------------------------------------------------------------------------------- read / verify
def _body(con, sha):
    r = con.execute("SELECT body FROM pr1_featuresets WHERE sha = ?", (sha,)).fetchone()
    return json.loads(zlib.decompress(r["body"])) if r else None


def expand(con, row):
    """A stored record as a dict with its inputs rebuilt (grader input dict) and its availability expanded."""
    d = {k: row[k] for k in row.keys() if k != "vol"}
    inp = json.loads(row["inputs"])
    groups = {g: _body(con, h) for g, h in inp["v"].items()}
    vol = json.loads(zlib.decompress(row["vol"])) if row["vol"] else []
    d["grader_inputs"] = merge_inputs(groups, vol, inp.get("order"))
    d["availability"] = {s: _body(con, h) for s, h in inp["a"].items()}
    d["group_sources"] = GROUP_SOURCES
    d["inputs"] = inp
    d["corrections"] = [dict(r) for r in con.execute("SELECT * FROM pr1_corrections WHERE record_id = ? ORDER BY id", (row["id"],))]
    return d


def reconstruct(rec):
    """Re-run the grader on a record's saved inputs: {"ok", "fields": {name: [stored, rebuilt]}, "flags_match"}."""
    import grader
    from pick_verdict import verdict
    g = grader.grade_leg(rec["grader_inputs"])
    v = verdict(g)
    got = {"projection": g.get("lambda_final"), "p_more": g.get("p_more"), "side": v.get("side"), "chance": v.get("p"),
           "letter": v.get("grade"), "stake": v.get("units"), "state": v.get("state"), "model_tag": g.get("projection_source")}
    fields = {k: [rec.get(k), got[k]] for k in got}
    flags = json.loads(rec.get("flags") or "{}")
    flags_match = all((os.environ.get(f) or None) == (flags.get(f) or None) for f in FLAGS)
    return {"id": rec.get("id"), "ok": all(a == b for a, b in fields.values()), "fields": fields, "flags_match": flags_match,
            "model_version_match": _mv() == rec.get("model_version")}


def _mv():
    try:
        import picklog
        return picklog.MODEL_VERSION
    except Exception:           # noqa: BLE001
        return None


def page(after_id=0, limit=None, full=False, verify=False, cutoff_id=None, match_id=None):
    """Bounded keyset page (never the whole table): ?after=<next_after> until done."""
    cap = PAGE_MAX_FULL if (full or verify) else PAGE_MAX
    limit = max(1, min(int(limit or cap), cap))
    con = _read_db()
    try:
        if cutoff_id is None:
            cutoff_id = con.execute("SELECT COALESCE(MAX(id), 0) FROM pr1_records").fetchone()[0]
        q, a = "SELECT * FROM pr1_records WHERE id > ? AND id <= ?", [int(after_id or 0), int(cutoff_id)]
        if match_id:
            q += " AND match_id = ?"
            a.append(match_id)
        rows = con.execute(q + " ORDER BY id LIMIT ?", a + [limit]).fetchall()
        out = []
        for r in rows:
            if full or verify:
                d = expand(con, r)
                if verify:
                    try:
                        d["reconstruct"] = reconstruct(d)
                    except Exception as e:  # noqa: BLE001
                        d["reconstruct"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
                    if not full:
                        d = {k: d[k] for k in ("id", "prediction_id", "created_at", "player", "prop", "period", "line", "app",
                                               "match_id", "reconstruct")}
            else:
                d = {k: r[k] for k in r.keys() if k not in ("vol",)}
            out.append(d)
        last = rows[-1]["id"] if rows else int(after_id or 0)
        return {"records": out, "next_after": last, "cutoff_id": cutoff_id, "done": last >= cutoff_id or len(rows) < limit,
                "page_max": cap}
    finally:
        con.close()


def status():
    out = {"version": VERSION, "enabled": enabled(), "db": DB.name, "queue": _Q.qsize(), "stats": dict(STATS)}
    try:
        con = _read_db()
        try:
            out["records"] = con.execute("SELECT COUNT(*) FROM pr1_records").fetchone()[0]
            out["featuresets"] = con.execute("SELECT COUNT(*) FROM pr1_featuresets").fetchone()[0]
            out["corrections"] = con.execute("SELECT COUNT(*) FROM pr1_corrections").fetchone()[0]
            out["first_at"], out["last_at"] = con.execute("SELECT MIN(created_at), MAX(created_at) FROM pr1_records").fetchone()
            pc, ps = con.execute("PRAGMA page_count").fetchone()[0], con.execute("PRAGMA page_size").fetchone()[0]
            out["db_bytes"] = pc * ps
        finally:
            con.close()
        try:
            out["wal_bytes"] = (DB.parent / (DB.name + "-wal")).stat().st_size
        except OSError:
            out["wal_bytes"] = 0
        if out.get("first_at") and out.get("last_at"):
            span = (_utc(out["last_at"]) - _utc(out["first_at"])).total_seconds()
            if span > 3600:
                out["bytes_per_day_est"] = int(out["db_bytes"] / span * 86400)
                out["over_budget"] = out["bytes_per_day_est"] > DAY_BYTES_WARN
    except Exception as e:      # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out
