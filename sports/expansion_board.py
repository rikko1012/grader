"""Isolated soccer and tennis quote collection, snapshots and member API.

One process per sport. No writes to existing NBA/NFL data, models or settlement.
Quotes are source-native research offers until sport-specific identity, historical
features, model validation and house-rule settlement have been connected.
"""
import argparse
import hashlib
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from functools import lru_cache

from .contracts import now_utc, parse_utc
from . import expansion_offers as adapters

MAX_AGE = 300
SPORTS = ("soccer", "tennis")
_SNAPSHOT_LOCK = threading.Lock()
_PREPARED = {}  # At most two eligible, encoded boards; shared by concurrent members.


@lru_cache(maxsize=2)
def _read_snapshot(path, stamp, size):
    # At most one parsed snapshot per new sport, rather than per member request.
    return json.loads(Path(path).read_text(encoding="utf-8"))



def _prepared(path, st, now, sport):
    key = (str(path), st.st_mtime_ns, st.st_size)
    instant = now.timestamp()
    with _SNAPSHOT_LOCK:
        hit = _PREPARED.get(key)
        if hit and hit['checked'] <= instant < hit['expires']:
            return hit['data']
        data = dict(_read_snapshot(*key))
        built = parse_utc(data['built_at']).timestamp()
        if not 0 <= instant - built <= MAX_AGE:
            raise ValueError('prices_stale')
        rows, deadlines = [], [built + MAX_AGE]
        for row in data['offers']:
            if row.get('sport') != sport:
                continue
            start = parse_utc(row['start']).timestamp()
            observed = parse_utc(row['observed_at']).timestamp()
            if start > instant and 0 <= instant-observed <= MAX_AGE:
                rows.append(row)
                deadlines.extend((start, observed+MAX_AGE))
            elif observed > instant:
                deadlines.append(observed)  # Never hide a future-dated sighting forever.
        data['offers'] = rows
        odds=[]
        for row in data.get('game_odds',[]):
            if row.get('sport') != sport:continue
            start=parse_utc(row['start']).timestamp(); observed=parse_utc(row['observed_at']).timestamp()
            if start>instant and 0<=instant-observed<=MAX_AGE:
                odds.append(row);deadlines.extend((start,observed+MAX_AGE))
            elif observed>instant:deadlines.append(observed)
        data['game_odds']=odds
        data['version'] = hashlib.sha256(json.dumps(sorted(
            [(r['platform'],r['offer_id'],r['revision']) for r in rows]+
            [('odds:'+r['platform'],r['offer_id'],r['revision']) for r in odds]),separators=(',',':')).encode()).hexdigest()
        # Serialize once per snapshot/eligibility boundary, not per member.
        encoded = json.dumps(data,separators=(',',':')).encode('utf-8')
        _PREPARED.pop(key, None)
        while len(_PREPARED) >= 2:
            _PREPARED.pop(next(iter(_PREPARED)))
        # A source whose rows carry more than one observed_at (PrizePicks soccer tabs read at different times,
        # 2026-11-19) cannot be re-stamped with one checked_at: no 'unchanged' shortcut for that snapshot.
        stamps = {}
        for r in rows:
            stamps.setdefault(r['platform'], set()).add(r['observed_at'])
        for r in odds:
            stamps.setdefault('odds:' + r['platform'], set()).add(r['observed_at'])
        restamp = all(len(v) <= 1 for v in stamps.values())
        _PREPARED[key] = {'data':data, 'body':encoded, 'checked':instant, 'expires':min(deadlines), 'restamp':restamp}
        return data


_RESEARCH_BODIES = {}  # (sport, board version/built_at, research.json + forecasts.json signatures) -> encoded answer


def _signature(path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


def research_body(sport, data):
    """The research=1 answer encoded ONCE per board / research / forecast file version and shared by every caller
    (2026-11-18: it was rebuilt and json-encoded inside the service on every request). Bytes are identical to the
    former per-request encoding (web._json: json.dumps(obj, default=str))."""
    from .expansion_research import attach
    directory = root(sport)
    key = (sport, data.get('version'), data.get('built_at'), _signature(directory / 'research.json'),
           _signature(directory / 'forecasts.json'), _signature(directory / 'images.json'))
    instant = datetime.now(timezone.utc).timestamp()
    with _SNAPSHOT_LOCK:
        hit = _RESEARCH_BODIES.get(key)
        if hit and instant < hit['expires']:
            return hit['body']
        expires = next((v['expires'] for v in _PREPARED.values() if v['data'] is data), instant + 30)
    out = attach(sport, data)
    from .expansion_images import for_board
    images = for_board(directory, out.get('offers') if isinstance(out, dict) else None)
    if images:                         # v3 card pictures (expansion_images.py); no file / no match = the same bytes as before
        out = {**out, 'images': images}
    body = json.dumps(out, default=str).encode('utf-8')
    with _SNAPSHOT_LOCK:
        _RESEARCH_BODIES.pop(key, None)
        while len(_RESEARCH_BODIES) >= 2:
            _RESEARCH_BODIES.pop(next(iter(_RESEARCH_BODIES)))
        _RESEARCH_BODIES[key] = {'body': body, 'expires': expires}
    return body


def http_board(req):
    if req.query.get('research') == '1':
        from types import SimpleNamespace
        # Research has its own publication clock. A quote-only version must not
        # suppress new history, nor make attach() consume an unchanged envelope.
        code, data = board(SimpleNamespace(query={k:v for k,v in req.query.items() if k != 'version'}))
        if code != 200:
            return code, data
        return code, research_body(req.query.get('sport', 'soccer'), data), 'application/json'
    code, data = board(req)
    if code == 200 and not data.get('unchanged'):
        with _SNAPSHOT_LOCK:
            body = next((v['body'] for v in _PREPARED.values() if v['data'] is data), None)
        if body is not None:
            return code, body, 'application/json'
    return code, data


def root(sport):
    if sport not in SPORTS:
        raise ValueError("unknown sport")
    from . import config
    return Path(os.environ.get("SCOUT_EXPANSION_DATA_DIR") or config.DATA_DIR / "expansion") / sport


def enabled(sport):
    return sport in SPORTS and os.environ.get(f"SCOUT_{sport.upper()}_ENABLED") == "1"


SNAPSHOT_BYTES = 2_850_000   # offers + game odds; board() refuses a board.json over 3,000,000 bytes (the whole sport would go dark)


def fit(rows, limit=SNAPSHOT_BYTES):
    """Keep the board under the member size limit: when too large, leave out PrizePicks demon / goblin rows (never
    shown on the board), latest start first, until it fits; then, only if still too large, the latest starts of any
    row (never the whole sport)."""
    sizes = [len(json.dumps(r, separators=(",", ":"))) + 1 for r in rows]
    total = sum(sizes)
    if total <= limit:
        return rows
    drop = set()
    for i in sorted((i for i, r in enumerate(rows) if r.get("platform") == "prizepicks"
                     and r.get("variant") not in ("standard",)), key=lambda i: rows[i].get("start") or "", reverse=True):
        if total <= limit:
            break
        drop.add(i)
        total -= sizes[i]
    # sg-stability 2026-11-20: still over (standard / Underdog lines alone - e.g. every league tab read at once):
    # leave out the furthest-out starts of any row until it fits. Before, board.json passed 3,000,000 bytes and board()
    # answered 503 for the WHOLE sport. Nearest matches stay; under the limit nothing changes.
    if total > limit:
        for i in sorted((i for i in range(len(rows)) if i not in drop),
                        key=lambda i: (rows[i].get("start") or "", i), reverse=True):
            if total <= limit:
                break
            drop.add(i)
            total -= sizes[i]
    return [r for i, r in enumerate(rows) if i not in drop]


def save(sport, rows, sources, observed_at, directory=None, game_odds=None):
    directory = Path(directory) if directory else root(sport)
    game_odds=game_odds or []
    if any(r.get("sport") != sport for r in rows+game_odds):
        raise ValueError("mixed sport batch refused")
    directory.mkdir(parents=True, exist_ok=True)
    db = directory / "quotes.db"
    try:
        con = sqlite3.connect(db, timeout=10)
        try:
            with con:
                con.execute("CREATE TABLE IF NOT EXISTS ownership (sport TEXT NOT NULL UNIQUE)")
                owners = con.execute("SELECT sport FROM ownership").fetchall()
                if owners and owners != [(sport,)]:
                    raise ValueError("database belongs to another sport")
                con.execute("INSERT OR IGNORE INTO ownership VALUES (?)", (sport,))
                con.execute("CREATE TABLE IF NOT EXISTS revisions (id INTEGER PRIMARY KEY, platform TEXT, offer_id TEXT, revision TEXT, observed_at TEXT, payload TEXT)")
                con.execute("CREATE INDEX IF NOT EXISTS quote_latest ON revisions(platform, offer_id, id DESC)")
        finally:
            con.close()
    except sqlite3.Error:
        pass        # a locked / damaged quote history never stops the board; record_quote_history records the error
    history_rows = rows+[dict(r,platform='odds:'+r['platform']) for r in game_odds]
    rows = fit(rows, SNAPSHOT_BYTES - len(json.dumps(game_odds, separators=(",", ":"))))
    snapshot = {"sport": sport, "built_at": observed_at, "sources": sources, "offers": rows,
                "mode": "lines", "grading_ready": False, "game_odds":game_odds}
    # sg-stability 2026-11-20: the member board is written FIRST. Before, the quote history's 250 MB cap raised ahead of
    # this write and the whole sport went dark (503 prices_stale) - the history can never take the board down now.
    fd, temporary = tempfile.mkstemp(prefix="board-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, separators=(",", ":"))
        os.replace(temporary, directory / "board.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    record_quote_history(directory, history_rows, observed_at)
    return snapshot


QUOTES_CAP_MB = 250             # quote revision history: live pages + WAL (expansion_archive.live_bytes)
QUOTE_STATUS = "quotes-history.json"
QUOTE_COLD_H = 72               # a revision first seen this long ago may move to the verified archive ...
QUOTE_KEEP_START_H = 24         # ... unless it is an offer's LATEST revision and that offer is on the board now, or
                                # its start is less than this long ago / unknown (it may still be offered: the dedupe
                                # lookup needs its latest revision hot)
QUOTE_ARCHIVE_EVERY_S = 3600    # an archive pass at most hourly while nothing is pending ...
QUOTE_ARCHIVE_BUDGET_S = 15     # ... and at most this long per quotes run (the board is already written)
QUOTE_PART_ROWS = 4000
QUOTE_SAMPLES_H = 48


def _history_connect(db):
    return sqlite3.connect(db, timeout=10)


def _read_status(directory):
    try:
        path = Path(directory) / QUOTE_STATUS
        if path.stat().st_size <= 262144:
            v = json.loads(path.read_text(encoding="utf-8"))
            return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def _write_status(directory, value):
    fd, temporary = tempfile.mkstemp(prefix="quotes-history-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, separators=(",", ":"))
        os.replace(temporary, Path(directory) / QUOTE_STATUS)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def record_quote_history(directory, history_rows, observed_at, now=None, budget_s=None):
    """Append changed quote revisions, then (when due) move cold revisions to verified archive files. Never raises:
    at the cap the revisions are skipped (state 'paused_at_cap', ops alerts), a database error is recorded - the board
    is already written. -> the status dict (also saved as quotes-history.json for ops)."""
    from . import expansion_archive as xa
    directory = Path(directory)
    db = directory / "quotes.db"
    status = _read_status(directory)
    out = {"at": observed_at, "state": "ok", "written": 0, "cap_mb": QUOTES_CAP_MB}
    cap = QUOTES_CAP_MB * 1024 * 1024
    live = xa.live_bytes(db)
    try:
        if live > cap:
            out["state"] = "paused_at_cap"
        else:
            con = _history_connect(db)
            try:
                with con:
                    # Namespace native sportsbook markets so their IDs cannot collide
                    # with player-prop quotes from a future book adapter.
                    for row in history_rows:
                        previous = con.execute("SELECT revision FROM revisions WHERE platform=? AND offer_id=? ORDER BY id DESC LIMIT 1",
                                               (row['platform'], row['offer_id'])).fetchone()
                        if not previous or previous[0] != row['revision']:
                            con.execute("INSERT INTO revisions(platform,offer_id,revision,observed_at,payload) VALUES (?,?,?,?,?)",
                                        (row['platform'], row['offer_id'], row['revision'], observed_at, json.dumps(row)))
                            out["written"] += 1
            finally:
                con.close()
    except sqlite3.Error as e:
        out["state"] = "error"
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    arch = dict(status.get("archive") or {})
    try:
        t = time.time()
        pressed = live > 0.6 * cap or out["state"] == "paused_at_cap"
        due = arch.get("pending") or t - float(arch.get("ran_at_epoch") or 0) >= (300 if pressed else QUOTE_ARCHIVE_EVERY_S)
        if due:
            arch = archive_quotes(directory, {(r['platform'], r['offer_id']) for r in history_rows},
                                  now=now, budget_s=QUOTE_ARCHIVE_BUDGET_S if budget_s is None else budget_s,
                                  cursor=int(arch.get("cursor") or 0) if arch.get("pending") else 0)
            arch["ran_at_epoch"] = round(t, 1)
    except Exception as e:                              # noqa: BLE001 - the archive never fails the quotes run
        arch = {**arch, "error": f"{type(e).__name__}: {str(e)[:160]}", "ran_at_epoch": round(time.time(), 1)}
    out["archive"] = arch
    live = xa.live_bytes(db)
    out["live_mb"] = round(live / 1048576, 1)
    try:
        out["file_mb"] = round(sum(p.stat().st_size for p in directory.glob("quotes.db*")) / 1048576, 1)
    except OSError:
        pass
    samples = [s for s in status.get("samples") or [] if isinstance(s, list) and len(s) == 2]
    try:
        ref = parse_utc(observed_at).timestamp()
        samples = [s for s in samples if 0 <= ref - parse_utc(s[0]).timestamp() <= QUOTE_SAMPLES_H * 3600]
        if not samples or ref - parse_utc(samples[-1][0]).timestamp() >= 1800:
            samples.append([observed_at, out["live_mb"]])
    except (TypeError, ValueError):
        pass
    out["samples"] = samples[-200:]
    try:
        _write_status(directory, out)
    except OSError:
        pass
    return out


def archive_quotes(directory, current, now=None, budget_s=QUOTE_ARCHIVE_BUDGET_S, cold_h=QUOTE_COLD_H, cursor=0):
    """Move cold quote revisions into <dir>/archive/quotes/quotes-<seq>.jsonl.xz (expansion_archive: verified before
    anything is removed). `current` = {(platform, offer_id)} written this run; `cursor` = the revision id a pass stopped
    at on its time budget (the next run resumes there). -> {"rows", "parts", "pending", "cursor", ...}."""
    from . import expansion_archive as xa
    directory = Path(directory)
    db = directory / "quotes.db"
    out = {"rows": 0, "parts": 0, "pending": False}
    if not db.exists():
        return out
    t0 = _monotonic()
    now_dt = parse_utc(now) if isinstance(now, str) else (now or datetime.now(timezone.utc))
    cutoff = (now_dt - timedelta(hours=cold_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    keep_start = (now_dt - timedelta(hours=QUOTE_KEEP_START_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    adir = directory / "archive" / "quotes"
    con = sqlite3.connect(db, timeout=10)
    try:
        xa.ensure(con)
        con.commit()
        out["leftovers_removed"] = xa.remove_leftovers(con, adir, "quotes")
        cols = ("id", "platform", "offer_id", "revision", "observed_at", "payload")
        last_id = int(cursor or 0)
        latest = {}
        slowest = 0.0
        while True:
            if _monotonic() - t0 + slowest > budget_s:
                out["pending"] = True
                out["stopped"] = "time budget"
                out["cursor"] = last_id
                break
            tp = _monotonic()
            # rowid order: old revisions come first, so the scan stops early on a store whose head is already cold
            cand = con.execute("SELECT id, platform, offer_id, observed_at FROM revisions WHERE id > ? ORDER BY id LIMIT ?",
                               (last_id, QUOTE_PART_ROWS * 2)).fetchall()
            if not cand:
                break
            last_id = cand[-1][0]
            ids, hot_seen = [], False
            for rid, plat, oid, seen in cand:
                if (seen or "") >= cutoff:
                    hot_seen = True
                    continue
                key = (plat, oid)
                if key not in latest:
                    latest[key] = con.execute("SELECT MAX(id) FROM revisions WHERE platform=? AND offer_id=?",
                                              key).fetchone()[0]
                if latest[key] == rid:
                    if key in current:
                        continue
                    start = con.execute("SELECT json_extract(payload, '$.start') FROM revisions WHERE id=?",
                                        (rid,)).fetchone()[0]
                    if not isinstance(start, str) or start >= keep_start:
                        continue
                ids.append(rid)
            for i in range(0, len(ids), QUOTE_PART_ROWS):
                chunk = ids[i:i + QUOTE_PART_ROWS]
                marks = ",".join("?" * len(chunk))
                rows = con.execute(f"SELECT {', '.join(cols)} FROM revisions WHERE id IN ({marks}) ORDER BY id",
                                   chunk).fetchall()
                r = xa.archive_part(con, adir, "quotes", "revisions", cols, rows, "DELETE FROM revisions WHERE id = ?",
                                    [(x[0],) for x in rows], key_col="id")
                out["rows"] += r["rows"]
                out["parts"] += 1
            slowest = max(slowest, _monotonic() - tp)
            if hot_seen and not ids and all((c[3] or "") >= cutoff for c in cand[-50:]):
                break                                   # reached the warm tail of the store
        out["archived"] = xa.stats(con)
    finally:
        con.close()
    out["seconds"] = round(_monotonic() - t0, 2)
    return out


PP_URL = "https://partner-api.prizepicks.com/projections"
PP_LEAGUES_URL = "https://partner-api.prizepicks.com/leagues"
PP_STATE = "pp-soccer-state.json"      # per-league last good rows + the cached league list + AIMD level (worker-only)
PP_STATUS = "pp-soccer-status.json"    # small per-league counts for admin ops (GET only)
LEAGUES_TTL_S = 3600                   # /leagues at most hourly
LEAGUES_MAX_AGE_S = 86400              # an older cached list is not trusted: every mapped tab is tried instead
LEAGUES_REFRESH_MIN_S = 600            # an empty answer from a listed tab re-reads /leagues at most every 10 min; a
                                       # FAILED list read waits 600 s, then 1200, 2400, capped at LEAGUES_TTL_S
DISCOVERY_S = 1800                     # a listed tab whose last read had no standard line is re-read every 30 min
ODDS_EVERY_S = 170                     # soccer book markets keep the 180 s cadence while quotes run every 90 s
REREAD_MIN_S = 170                     # no source is asked again sooner (tab 82: its old 180 s rate)
REFRESH_TARGET_S = 180                 # full coverage = every source re-read every 180 s (tab 82's 5036ce4 rate)
LEVEL_UNIT_S = 90                      # AIMD level = PrizePicks soccer requests per 90 s (spread over the 45 s runs)
GROW_S = 600                           # AIMD: 10 min with no PrizePicks 429 anywhere -> one more request a run
CEILING_S = 3600                       # AIMD: the level that drew a 429 is not tried again for an hour
COOL_S = 60                            # another consumer's 429 this recently: no soccer request this run
RUN_BUDGET_S = 30                      # no new request starts after this many seconds (job timeout_s 90)
SPACE_S = 4                            # seconds between two PrizePicks requests of one soccer run
_sleep, _monotonic = time.sleep, time.monotonic     # tests replace them


def pp_leagues_on():
    """Kill switch (2026-11-19): SCOUT_SOCCER_PP_LEAGUES=0 puts soccer back on the single tab-82 request exactly as
    at 5036ce4 (SCOUT_SOCCER_PP_MLS then works as before). Default on."""
    return os.environ.get("SCOUT_SOCCER_PP_LEAGUES", "1") != "0"


def _env_num(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _read_json(path, default):
    try:
        if path.stat().st_size > 8_000_000:
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _age_s(stamp, now):
    try:
        return (parse_utc(now) - parse_utc(stamp)).total_seconds()
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def _iso_plus(now, seconds):
    return (parse_utc(now) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def league_list(body):
    """/leagues -> {id: {name, display_name, count, parent, icon, active}} for every row (the caller decides)."""
    if not isinstance(body, dict) or not isinstance(body.get("data") or [], list):
        raise ValueError("bad_body")
    out = {}
    for x in body.get("data") or []:
        if not isinstance(x, dict):
            continue
        a = x.get("attributes") or {}
        if not isinstance(a, dict):
            continue
        try:
            lid = int(x.get("id"))
        except (TypeError, ValueError):
            continue
        count = a.get("projections_count")
        out[str(lid)] = {"name": a.get("name"), "display_name": a.get("display_name"),
                         "count": count if isinstance(count, int) and not isinstance(count, bool) else None,
                         "parent": a.get("parent_id"), "icon": a.get("icon"), "active": a.get("active")}
    return out


def plan_leagues(listing):
    """-> (mapped tabs to read besides 82, unmapped soccer tabs with lines, renamed tabs). A listed tab is read only
    when its id AND name equal PP_SOCCER_LEAGUES and it lists projections; with no trusted list every mapped tab is.
    An unmapped tab is never read (PrizePicks' "soccer" icon also marks HANDBALL): ops lists it for a person to add."""
    mapped = {k: v for k, v in adapters.PP_SOCCER_LEAGUES.items() if k != 82}
    if not listing:
        return sorted(mapped), [], []
    use, unmapped, renamed = [], [], []
    for sid, row in listing.items():
        lid = int(sid)
        if lid in mapped:
            if row.get("name") != mapped[lid][0]:
                renamed.append({"id": lid, "expected": mapped[lid][0], "listed": row.get("name")})
            elif (row.get("count") or 0) > 0:
                use.append(lid)
        elif (lid != 82 and row.get("icon") == "soccer" and row.get("parent") is None and (row.get("count") or 0) > 0
              and lid not in adapters.PP_SOCCER_NOT_MATCH):
            unmapped.append({"id": lid, "name": row.get("name"), "count": row.get("count")})
    return sorted(use), unmapped, renamed


def quotes_every_s(sport="soccer"):
    """Seconds between soccer quotes runs: SPORTS_JOB_SOCCER_QUOTES_EVERY_S (the scheduler's own override, clamped
    to >= 30 s there too), else 45 with the per-league tabs on and 180 (as at 5036ce4) with them off. Short, frequent
    runs (a few requests each) instead of long ones: the scheduler replay keeps base timing with 6-14 s runs every
    45 s, not with 15-25 s runs every 90 s (logs/ppsoccer_1119/adaptive/replay_sweep_*)."""
    default = 45 if (sport == "soccer" and pp_leagues_on()) else 180
    return max(30.0, _env_num("SPORTS_JOB_" + sport.upper() + "_QUOTES_EVERY_S", default))


def max_level(n_sources):
    """Requests per LEVEL_UNIT_S (90 s) that re-read every source (tab 82 + the tabs with standard lines) every
    REFRESH_TARGET_S s, capped by SCOUT_SOCCER_PP_MAX_LEVEL (default 5; 1 = one request per 90 s, the start level and
    the most conservative setting)."""
    need = math.ceil(max(1, n_sources) * LEVEL_UNIT_S / REFRESH_TARGET_S)
    return max(1, min(need, int(_env_num("SCOUT_SOCCER_PP_MAX_LEVEL", 5))))


def leagues_wait_s(fails):
    """Seconds a failed /leagues read (any failure: 429, 5xx, timeout, bad JSON, empty list) waits before the next
    try: 600, 1200, 2400, then hourly - never a retry every run."""
    if not fails:
        return 0
    return min(LEAGUES_TTL_S, LEAGUES_REFRESH_MIN_S * 2 ** min(10, max(0, int(fails) - 1)))


def _soccer_prizepicks(get_json, directory, started=None, clock=None):
    """Every PrizePicks soccer tab (2026-11-19), with ADAPTIVE coverage driven by real 429s (AIMD).

    PrizePicks limits requests per IP (measured locally: ~1 request every 40-45 s sustained; Render's limit is not
    known) and the IP is shared with tennis and the NBA / NFL lines ingest. So the number of soccer requests per 90 s
    (`level`, paid out over the short 45 s runs as credit) is learned: it starts at 1 (tab 82 every 180 s plus one tab
    in rotation, the review's "82 + 1 tab per 90 s run"), grows by one after GROW_S with
    no 429 on ANY PrizePicks consumer (soccer, tennis, NBA / NFL - expansion_sources.record_429), and halves at once on
    any 429 (the level that drew it is not tried again for CEILING_S). It never exceeds what re-reads every source
    every 180 s (max_level). A Retry-After hold (any consumer) means no request at all until it ends; another
    consumer's 429 in the last COOL_S skips this run. Two own 429s within 15 min back off to tab 82 only, every 180 s,
    for SCOUT_SOCCER_PP_BACKOFF_S (600 s). Requests of one run are SPACE_S apart (and the shared pace applies), and no
    request starts after RUN_BUDGET_S, so a run stays short.

    Rotation is fair: tab 82 whenever it is due (>= REREAD_MIN_S since its last attempt), then the due tabs with
    standard lines (or never read) least-recently-attempted first. By last ATTEMPT, so a failing tab never starves
    the others. Every source's last good rows stay on the board with their OWN observed_at until the 300 s rule hides
    them; below full coverage leagues therefore take turns on the board. A failed read never moves or invents a line.
    Per-league rows are standard lines only, and tab 82 wins a projection id listed in two tabs.
    The member-facing 'prizepicks' source follows tab 82 alone (a failing league tab only shows in ops).
    -> (rows, the 'prizepicks' source entry, status for ops)."""
    from . import net
    from .expansion_sources import held_until, pace_gap_s, PP_HOST, pp_events_since, pp_429_counts
    from .expansion_history import atomic_json
    t0 = _monotonic()
    every = quotes_every_s()
    state = _read_json(directory / PP_STATE, {})
    if not isinstance(state, dict):
        state = {}
    for k in ("per", "leagues", "aimd"):
        if not isinstance(state.get(k), dict):
            state[k] = {}
    per, leagues, aimd = state["per"], state["leagues"], state["aimd"]
    for sid in list(per):
        if not isinstance(per[sid], dict):
            per.pop(sid)
    hard_cap = os.environ.get("SCOUT_SOCCER_PP_MAX_CALLS")
    calls, stopped, run_errors, roles = 0, None, {}, {}
    constrained, halved = None, None
    now = now_utc()
    try:
        level = max(1, int(aimd.get("level") or _env_num("SCOUT_SOCCER_PP_START_LEVEL", 1)))
    except (TypeError, ValueError):
        level = 1

    def halve(why, own=False):
        nonlocal level, halved
        if halved:
            return                                     # at most one halving a run
        old = level
        level = max(1, old // 2)
        halved = why
        stamp = now_utc()
        if own and old >= 2:
            # the level SOCCER's own request drew a 429 at is not tried again for CEILING_S. Not for another
            # consumer's 429 (soccer may not have caused it) and not from level 1 (that would freeze coverage).
            aimd.update(ceiling=old, ceiling_until=_iso_plus(stamp, _env_num("SCOUT_SOCCER_PP_CEILING_S", CEILING_S)))
        aimd.update(clean_since=stamp, last_change=stamp, last_halving={"at": stamp, "why": why, "from": old,
                                                                        "to": level},
                    halvings=int(aimd.get("halvings") or 0) + 1)

    # 0. 429s seen by the OTHER PrizePicks consumers since the last run (tennis, NBA / NFL). Soccer's own 429s are
    #    handled where they happen (fetch), so they are never counted twice.
    try:
        cursor = int(aimd.get("cursor") or 0)
    except (TypeError, ValueError):
        cursor = 0
    events = pp_events_since(cursor)
    if events:
        aimd["cursor"] = max(e["id"] for e in events)
    other = [e for e in events if e["consumer"] != "soccer"]
    if other:
        halve("429:" + ",".join(sorted({e["consumer"] for e in other})))
    cool = any(e["at"] > time.time() - COOL_S for e in other)

    def can_call():
        nonlocal stopped
        if stopped:
            return False
        if hard_cap and calls >= max(1, int(_env_num("SCOUT_SOCCER_PP_MAX_CALLS", 1))):
            stopped = "call_cap"
        elif held_until("soccer", PP_URL):
            stopped = "retry_after_hold"
        return not stopped

    def gap():
        return max(pace_gap_s(PP_HOST), _env_num("SCOUT_SOCCER_PP_SPACE_S", SPACE_S)) if calls else 0.0

    def fits():
        """A next request may start only if, after its spacing, the run is still inside RUN_BUDGET_S."""
        return _monotonic() - t0 + gap() <= RUN_BUDGET_S

    def space():
        """SPACE_S between this run's requests (the shared cross-process pace still applies inside get_json)."""
        if gap():
            _sleep(gap())

    def fetch(url, params):
        nonlocal calls, stopped
        calls += 1
        try:
            body = get_json("soccer", url, params=params, timeout=15, tries=1)
        except net.HttpError as e:
            stopped = "http_429" if e.status == 429 else "request_failed"
            if e.status == 429 and not getattr(e, "held", False):
                state["recent_429"] = [t for t in state.get("recent_429") or []
                                       if 0 <= (_age_s(t, now_utc()) or 10 ** 9) <= 900] + [now_utc()]
                halve("429:soccer", own=True)
            raise
        if not isinstance(body, dict):
            raise ValueError("bad_body")
        return body

    def read(lid, role):
        entry = per.setdefault(str(lid), {})
        entry["attempt_at"] = now_utc()
        roles[lid] = role
        state["aimd"] = dict(aimd, level=level)
        # persisted BEFORE the request: a run killed mid-read (memory cap / timeout) still moves the rotation on
        atomic_json(directory / PP_STATE, state)
        try:
            body = fetch(PP_URL, {"league_id": lid, "per_page": 1000, "single_stat": "true", "game_mode": "pickem"})
            observed = now_utc()
            listed = ((leagues.get("listing") or {}).get(str(lid)) or {}).get("count")
            if not (body.get("data") or []) and listed:
                # A tab /leagues lists with lines answered empty (the comma-list answer looked exactly like this):
                # a failed read, never "no lines". /leagues is re-read (at most every LEAGUES_REFRESH_MIN_S).
                leagues["refresh"] = True
                raise ValueError("empty_listed_tab")
            found = adapters.prizepicks(body, "soccer", observed, None if lid == 82 else lid)
            from .expansion_images import remember
            remember(body, "soccer", directory)          # v3 card pictures only; never fails the read
            del body
            parsed = len(found)
            if lid != 82:
                found = [r for r in found if r["variant"] == "standard"]
            starts = sorted(r["start"] for r in found if r["variant"] == "standard")
            entry.update(ok_at=observed, rows=found, parsed=parsed, kept=len(found), error=None,
                         first_start=starts[0] if starts else None)
        except (net.HttpError, ValueError, OSError, KeyError, TypeError, AttributeError) as e:
            entry["error"] = str(e)[:60] if type(e) is ValueError else type(e).__name__
            entry["error_at"] = now_utc()
            run_errors[lid] = entry["error"]

    list_age = _age_s(leagues.get("fetched_at"), now)
    trusted = leagues.get("listing") if list_age is not None and list_age < LEAGUES_MAX_AGE_S else None
    if not isinstance(trusted, dict):
        trusted = None
    use, unmapped, renamed = plan_leagues(trusted)

    def attempt_age(lid):
        return _age_s((per.get(str(lid)) or {}).get("attempt_at"), now)

    def with_lines(lid):
        e = per.get(str(lid)) or {}
        return (e.get("kept") or 0) > 0 or not e.get("ok_at")

    def due(lid):
        a = attempt_age(lid)
        if a is None or a < 0:
            return True
        if lid == 82 or with_lines(lid):
            return a >= REREAD_MIN_S
        return a >= DISCOVERY_S                    # listed, but its last read had no standard line

    order_ix = {lid: i for i, lid in enumerate(adapters.PP_SOCCER_LEAGUES)}
    sources_n = 1 + sum(1 for lid in use if with_lines(lid))
    top = max_level(sources_n)
    ceiling_on = (_age_s(aimd.get("ceiling_until"), now) or 0) < 0
    cap = min(top, max(1, int(aimd.get("ceiling") or 1) - 1)) if ceiling_on else top
    level = min(level, top)                        # never more requests than every source needs
    # level = requests per 90 s, paid out per run as credit (time since the last run x level / 90 s), capped at one
    # run's share so a pause (held, skipped, restarted) never turns into a burst
    elapsed = _age_s(aimd.get("credit_at"), now)
    burst = max(1, math.ceil(level * every / LEVEL_UNIT_S))
    if elapsed is None or elapsed < 0:
        elapsed = 10 ** 6                          # first run (or a lost state): a full run's share, tab 82 first
    try:
        credit = float(aimd.get("credit") or 0)
    except (TypeError, ValueError):
        credit = 0.0
    credit = min(float(burst), max(0.0, credit) + level * elapsed / LEVEL_UNIT_S)
    aimd["credit_at"] = now
    allowed = int(credit + 1e-9)
    due_tabs = sorted((lid for lid in use if due(lid)),
                      key=lambda lid: ((per.get(str(lid)) or {}).get("attempt_at") or "", order_ix.get(lid, 99)))
    picks = ([82] if due(82) else []) + due_tabs
    backoff_left = -(_age_s(state.get("backoff_until"), now) or 0)

    # 1. coverage requests
    if backoff_left > 0:
        if due(82) and can_call():
            read(82, "coverage")
        stopped = stopped or "backoff"
        constrained = "backoff"
    elif cool:
        # another consumer's recent 429: like a back-off, tab 82 is still read when due (its pre-tab 180 s rate, so the
        # base board never goes dark); Retry-After holds still apply via can_call(); the other tabs wait
        if due(82) and can_call():
            read(82, "coverage")
        stopped = stopped or "recent_429_other_consumer"
        constrained = "cool_off"
    elif not can_call():
        constrained = stopped                      # held (Retry-After): no request at all this run
    else:
        for lid in picks[:allowed]:
            if not fits():
                constrained = "run_budget"
                break
            space()
            if not can_call():
                constrained = stopped
                break
            read(lid, "coverage")
            if stopped:
                break
        if not picks:
            roles["coverage"] = "recently_read"
        elif not allowed:
            roles["coverage"] = "credit"           # level x 45 s / 90 s < 1: this run's request comes next run
    credit -= sum(1 for k, r in roles.items() if k != "coverage" and r == "coverage")
    aimd["credit"] = round(max(0.0, credit), 4)
    # 2. housekeeping, only after clean requests: the hourly league list (a failed read backs off, never every run)
    forced = leagues.get("refresh") and (list_age is None or list_age >= LEAGUES_REFRESH_MIN_S)
    try:
        fails = max(0, int(leagues.get("fails") or 0))
    except (TypeError, ValueError):
        fails = 0
    err_age = _age_s(leagues.get("error_at"), now)
    list_due = ((list_age is None or list_age >= LEAGUES_TTL_S or forced)
                and (not fails or err_age is None or err_age < 0 or err_age >= leagues_wait_s(fails)))
    if list_due and not stopped and backoff_left <= 0 and not cool and fits():
        space()
        if can_call():
            roles["leagues"] = "housekeeping"
            try:
                listing = league_list(fetch(PP_LEAGUES_URL, None))
                if not listing:
                    raise ValueError("empty league list")
                leagues.update(listing=listing, fetched_at=now_utc(), refresh=False, error=None, fails=0)
            except (net.HttpError, ValueError, OSError, KeyError, TypeError, AttributeError) as e:
                leagues["error"] = type(e).__name__
                leagues["error_at"] = now_utc()
                leagues["fails"] = fails + 1
    if len(state.get("recent_429") or []) >= 2 and stopped == "http_429":
        state["backoff_until"] = _iso_plus(now_utc(), _env_num("SCOUT_SOCCER_PP_BACKOFF_S", 600))
        state["recent_429"] = []

    # 3. AIMD: grow by one after GROW_S of clean, unconstrained runs at the full level (a hold, a back-off, a cool-off
    #    or a cut run restarts the clock, so waiting out a hold never counts as "no 429s")
    now = now_utc()
    if halved or constrained:
        aimd["clean_since"] = now
    elif stopped == "request_failed" or run_errors or not any(
            r == "coverage" for k, r in roles.items() if k != "coverage"):
        pass                                       # growth only on a run with good reads and no failed request
                                                   # (5xx, timeout, 403, bad answer); one broken tab never blocks
                                                   # it for good, a full outage never grows the level
    elif level < cap:
        clean = _age_s(aimd.get("clean_since"), now)
        if clean is None or clean < 0:
            aimd["clean_since"] = now
        elif clean >= _env_num("SCOUT_SOCCER_PP_GROW_S", GROW_S):
            level += 1
            aimd.update(clean_since=now, last_change=now, last_growth=now)
    aimd["level"] = level

    # Compose: tab 82 first (it wins a shared projection id), then every tab in table order. Only rows still inside
    # the board's own age rule and not started are carried; observed_at is never re-stamped.
    rows, seen, counts = [], set(), {}
    for lid in adapters.PP_SOCCER_LEAGUES:
        if lid != 82 and lid not in use:
            continue
        n = 0
        for r in (per.get(str(lid)) or {}).get("rows") or []:
            age = _age_s(r.get("observed_at"), now)
            if age is None or not 0 <= age <= MAX_AGE or parse_utc(r["start"]) <= parse_utc(now):
                continue
            if r["offer_id"] in seen:
                continue
            seen.add(r["offer_id"])
            rows.append(r)
            n += 1
        counts[lid] = n
    for sid in list(per):                      # the state file only holds what can still be carried
        if not sid.isdigit() or int(sid) not in adapters.PP_SOCCER_LEAGUES:
            per.pop(sid)
            continue
        per[sid]["rows"] = [r for r in per[sid].get("rows") or []
                            if _age_s(r.get("observed_at"), now) is not None
                            and 0 <= _age_s(r.get("observed_at"), now) <= MAX_AGE]
    state["aimd"] = aimd
    atomic_json(directory / PP_STATE, state)

    # Member-facing source: the one 'prizepicks' key, as before, but it follows TAB 82 ONLY: ok while tab 82 was read
    # OK recently enough to stay on the board until the next run (age <= 300 s - every_s). A failing league tab is an
    # ops matter (per-tab state below); its last good rows leave the board by the 300 s rule. checked_at = the OLDEST
    # good read among the sources with rows on the board (the soccer page re-stamps rows with it on an 'unchanged'
    # answer, so it must never look fresher than any row it covers).
    e82 = per.get("82") or {}
    ok82 = e82.get("ok_at")
    on_board = [(per.get(str(lid)) or {}).get("ok_at") for lid, n in counts.items() if n]
    on_board = [x for x in on_board if x]
    checked = min(on_board) if on_board else (ok82 or now)
    if ok82 and _age_s(ok82, now) is not None and 0 <= _age_s(ok82, now) <= MAX_AGE - every:
        source = {"ok": True, "checked_at": checked, "offers": len(rows)}
    else:
        source = {"ok": False, "checked_at": checked if on_board else (ok82 or now),
                  "error": run_errors.get(82) or e82.get("error") or "not_read_recently", "offers": len(rows)}
    listing = leagues.get("listing") or {}
    grow_left = None
    if level < cap and not (halved or constrained or run_errors):
        c = _age_s(aimd.get("clean_since"), now)
        grow_left = max(0, round(_env_num("SCOUT_SOCCER_PP_GROW_S", GROW_S) - (c or 0)))
    status = {"at": now, "mode": "leagues", "calls": calls, "every_s": every, "stopped": stopped,
              "gap_s": pace_gap_s(PP_HOST), "space_s": max(pace_gap_s(PP_HOST), _env_num("SCOUT_SOCCER_PP_SPACE_S",
                                                                                             SPACE_S)),
              "coverage": {"level": level, "unit_s": LEVEL_UNIT_S, "max_level": top, "cap": cap,
                           "sources_with_lines": sources_n, "credit": aimd.get("credit"),
                           "full": level >= top, "constrained": constrained, "halved_this_run": halved,
                           "ceiling": aimd.get("ceiling") if (halved or ceiling_on) else None,
                           "ceiling_until": aimd.get("ceiling_until") if (halved or ceiling_on) else None,
                           "clean_since": aimd.get("clean_since"), "grow_in_s": grow_left,
                           "last_growth": aimd.get("last_growth"), "last_halving": aimd.get("last_halving"),
                           "halvings": aimd.get("halvings") or 0},
              "pp_429": pp_429_counts(),
              "covered": [lid for lid in counts if counts[lid]], "not_covered": [
                  lid for lid in use if not counts.get(lid) and (per.get(str(lid)) or {}).get("kept", 0) > 0],
              "source": source, "backoff_until": state.get("backoff_until"),
              "recent_429": state.get("recent_429") or [],
              "league_list": {"fetched_at": leagues.get("fetched_at"), "trusted": trusted is not None,
                              "error": leagues.get("error"), "error_at": leagues.get("error_at"),
                              "fails": leagues.get("fails") or 0,
                              "next_try_after_error_s": leagues_wait_s(leagues.get("fails") or 0) or None},
              "leagues": [], "unmapped_with_lines": unmapped, "renamed": renamed}
    for lid, (name, code) in adapters.PP_SOCCER_LEAGUES.items():
        e = per.get(str(lid)) or {}
        ok_age = _age_s(e.get("ok_at"), now)
        status["leagues"].append({
            "id": lid, "name": name, "competition": code, "listed": (listing.get(str(lid)) or {}).get("count"),
            "fetched": lid == 82 or lid in use, "covered": bool(counts.get(lid)),
            "ok": bool(ok_age is not None and 0 <= ok_age <= MAX_AGE and not (
                e.get("error_at") and (e.get("error_at") or "") > (e.get("ok_at") or ""))),
            "read_this_run": roles.get(lid), "last_ok_at": e.get("ok_at"),
            "refresh_age_s": round(ok_age) if ok_age is not None else None,
            "last_attempt_at": e.get("attempt_at"), "last_error": e.get("error"),
            "last_error_at": e.get("error_at"), "parsed": e.get("parsed"),
            "kept": e.get("kept"), "first_start": e.get("first_start"), "on_board": counts.get(lid, 0)})
    atomic_json(directory / PP_STATUS, status)
    return rows, source, status


def _soccer_odds(get_json, directory):
    """Book markets keep their own 180 s cadence while soccer quotes run every 90 s: between reads the last rows and
    source entries are carried with their own observed / checked times (the 300 s rule still applies to the rows)."""
    from .expansion_odds import collect as collect_odds
    path = directory / "odds-carry.json"
    saved = _read_json(path, {})
    now = now_utc()
    age = _age_s(saved.get("at"), now) if isinstance(saved, dict) else None
    if age is not None and 0 <= age < ODDS_EVERY_S:
        rows = [r for r in saved.get("rows") or []
                if _age_s(r.get("observed_at"), now) is not None and 0 <= _age_s(r.get("observed_at"), now) <= MAX_AGE]
        return rows, saved.get("sources") or {}
    odds, sources = collect_odds("soccer", lambda url, **kw: get_json("soccer", url, **kw), directory)
    from .expansion_history import atomic_json
    atomic_json(path, {"at": now, "rows": odds, "sources": sources})
    return odds, sources


def pp_soccer_status():
    """Admin ops (GET only): the soccer quotes job's own per-league status file, or None."""
    if not enabled("soccer"):
        return None
    status = _read_json(root("soccer") / PP_STATUS, None)
    if not isinstance(status, dict):
        return None
    status["kill_switch_off"] = not pp_leagues_on()
    status["age_s"] = _age_s(status.get("at"), now_utc())
    return status


def collect(sport):
    from . import net
    from .expansion_sources import get_json
    rows, sources = [], {}
    pp = lambda league: (PP_URL, {"league_id": league, "per_page": 1000, "single_stat": "true", "game_mode": "pickem"})
    leagues_path = sport == "soccer" and pp_leagues_on()
    if leagues_path:
        directory = root(sport)
        directory.mkdir(parents=True, exist_ok=True)
        found, sources["prizepicks"], _ = _soccer_prizepicks(get_json, directory)
        rows.extend(found)
        jobs = []      # SCOUT_SOCCER_PP_MLS is retired on this path: MLS (league 6) is one of the per-league tabs
    else:
        # (source, provider, request): one serial request each, through the shared Retry-After holds.
        jobs = [("prizepicks", "prizepicks", pp(82 if sport == "soccer" else 5))]
    if sport == "soccer" and not pp_leagues_on() and os.environ.get("SCOUT_SOCCER_PP_MLS") == "1":
        # MLS is its own PrizePicks league. Off by default: a third PrizePicks call in the same few seconds as the
        # soccer and tennis calls drew HTTP 429 locally (2026-11-18), and a failed source drops its rows that run.
        jobs.append(("prizepicks_mls", "prizepicks", pp(6)))
    if sport == "tennis":
        jobs.append(("underdog", "underdog", ("https://api.underdogfantasy.com/v1/over_under_lines", {"sport_id": "TENNIS"})))
    if sport == "soccer" and os.environ.get("SCOUT_SOCCER_UNDERDOG") == "1":
        # Always filtered by sport_id: the unfiltered answer is ~26 MB.
        jobs.append(("underdog", "underdog", ("https://api.underdogfantasy.com/v1/over_under_lines", {"sport_id": "FIFA"})))
    seen = {(r["platform"], r["offer_id"]) for r in rows}
    for source, provider, (url, params) in jobs:
        try:
            body = get_json(sport, url, params=params, timeout=25, tries=1)
            observed = now_utc()
            if provider == "prizepicks":
                found = adapters.prizepicks(body, sport, observed)
                from .expansion_images import remember
                remember(body, sport, root(sport))      # v3 card pictures only; never fails the read
            else:
                found = adapters.underdog(body, observed) if sport == "tennis" else adapters.underdog_soccer(body, observed)
            del body
            if source == "prizepicks_mls":
                found = [r for r in found if r["variant"] == "standard"]   # members see standard lines only
            found = [r for r in found if (r["platform"], r["offer_id"]) not in seen]   # one projection id once
            seen.update((r["platform"], r["offer_id"]) for r in found)
            rows.extend(found)
            sources[source] = {"ok": True, "checked_at": observed, "offers": len(found)}
        except (net.HttpError, ValueError, OSError) as e:
            sources[source] = {"ok": False, "checked_at": now_utc(), "error": type(e).__name__}
    from .expansion_odds import collect as collect_odds
    directory=root(sport);directory.mkdir(parents=True,exist_ok=True)
    if leagues_path:
        odds, odds_sources = _soccer_odds(get_json, directory)
    else:
        odds, odds_sources=collect_odds(sport,lambda url,**kw:get_json(sport,url,**kw),directory)
    sources.update(odds_sources)
    return save(sport, rows, sources, now_utc(),game_odds=odds)


def board(req):
    sport = req.query.get("sport", "soccer")
    if sport not in SPORTS:
        return 400, {"error": "sport must be soccer or tennis"}
    if not enabled(sport):
        return 404, {"error": "not_found"}
    path = root(sport) / "board.json"
    try:
        st = path.stat()
        if st.st_size > 3_000_000:
            raise ValueError("oversize")
        data = _prepared(path, st, datetime.now(timezone.utc), sport)
        version = data['version']
        with _SNAPSHOT_LOCK:
            restamp = next((v.get('restamp', True) for v in _PREPARED.values() if v['data'] is data), True)
        if req.query.get('version') == version and restamp:
            # The soccer page (web/sports/expansion.js) DOES use this shortcut: it keeps
            # its rows and re-stamps each one's observed_at with its source's checked_at
            # (and hides a row 300 s after that stamp). That is only right when every
            # row of a source was seen at one time. Since 2026-11-19 the PrizePicks
            # soccer tabs carry each tab's last good rows with their own observed_at,
            # so a snapshot holding rows from several reads (restamp False) always
            # gets the full board instead. Started/removed/changed offers change the
            # version and force a full replacement too.
            # (workspace_expansion asks with research=1 and never sends a version.)
            return 200, {k:data[k] for k in ('sport','built_at','sources','version')} | {'unchanged':True}
        return 200, data
    except (OSError, ValueError, KeyError, TypeError):
        return 503, {"error": "board_not_ready", "sport": sport}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("sport", choices=SPORTS)
    args = p.parse_args()
    if not enabled(args.sport):
        raise SystemExit("sport collector disabled")
    result = collect(args.sport)
    print(json.dumps({"sport": args.sport, "offers": len(result["offers"]), "sources": result["sources"]}))
    if not any(s["ok"] for s in result["sources"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
