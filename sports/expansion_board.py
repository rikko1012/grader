"""Isolated soccer and tennis quote collection, snapshots and member API.

One process per sport. No writes to existing NBA/NFL data, models or settlement.
Quotes are source-native research offers until sport-specific identity, historical
features, model validation and house-rule settlement have been connected.
"""
import argparse
import hashlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
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
        _PREPARED[key] = {'data':data, 'body':encoded, 'checked':instant, 'expires':min(deadlines)}
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
           _signature(directory / 'forecasts.json'))
    instant = datetime.now(timezone.utc).timestamp()
    with _SNAPSHOT_LOCK:
        hit = _RESEARCH_BODIES.get(key)
        if hit and instant < hit['expires']:
            return hit['body']
        expires = next((v['expires'] for v in _PREPARED.values() if v['data'] is data), instant + 30)
    body = json.dumps(attach(sport, data), default=str).encode('utf-8')
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
    shown on the board), latest start first, until it fits. Standard and Underdog lines are never dropped."""
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
    return [r for i, r in enumerate(rows) if i not in drop]


def save(sport, rows, sources, observed_at, directory=None, game_odds=None):
    directory = Path(directory) if directory else root(sport)
    game_odds=game_odds or []
    if any(r.get("sport") != sport for r in rows+game_odds):
        raise ValueError("mixed sport batch refused")
    directory.mkdir(parents=True, exist_ok=True)
    db = directory / "quotes.db"
    # Stop before growing past the isolated budget; no silent history deletion.
    if sum(p.stat().st_size for p in directory.glob("quotes.db*")) > 250 * 1024 * 1024:
        raise RuntimeError("expansion quote history storage budget reached")
    con = sqlite3.connect(db)
    try:
        with con:
            con.execute("CREATE TABLE IF NOT EXISTS ownership (sport TEXT NOT NULL UNIQUE)")
            owners = con.execute("SELECT sport FROM ownership").fetchall()
            if owners and owners != [(sport,)]:
                raise ValueError("database belongs to another sport")
            con.execute("INSERT OR IGNORE INTO ownership VALUES (?)", (sport,))
            con.execute("CREATE TABLE IF NOT EXISTS revisions (id INTEGER PRIMARY KEY, platform TEXT, offer_id TEXT, revision TEXT, observed_at TEXT, payload TEXT)")
            con.execute("CREATE INDEX IF NOT EXISTS quote_latest ON revisions(platform, offer_id, id DESC)")
            # Namespace native sportsbook markets so their IDs cannot collide
            # with player-prop quotes from a future book adapter.
            for row in rows+[dict(r,platform='odds:'+r['platform']) for r in game_odds]:
                previous = con.execute("SELECT revision FROM revisions WHERE platform=? AND offer_id=? ORDER BY id DESC LIMIT 1",
                                       (row['platform'], row['offer_id'])).fetchone()
                if not previous or previous[0] != row['revision']:
                    con.execute("INSERT INTO revisions(platform,offer_id,revision,observed_at,payload) VALUES (?,?,?,?,?)",
                                (row['platform'], row['offer_id'], row['revision'], observed_at, json.dumps(row)))
    finally:
        con.close()
    rows = fit(rows, SNAPSHOT_BYTES - len(json.dumps(game_odds, separators=(",", ":"))))
    snapshot = {"sport": sport, "built_at": observed_at, "sources": sources, "offers": rows,
                "mode": "lines", "grading_ready": False, "game_odds":game_odds}
    fd, temporary = tempfile.mkstemp(prefix="board-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, separators=(",", ":"))
        os.replace(temporary, directory / "board.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return snapshot


PP_URL = "https://partner-api.prizepicks.com/projections"
PP_LEAGUES_URL = "https://partner-api.prizepicks.com/leagues"
PP_STATE = "pp-soccer-state.json"      # per-league last good rows + the cached league list (worker-only)
PP_STATUS = "pp-soccer-status.json"    # small per-league counts for admin ops (GET only)
LEAGUES_TTL_S = 3600                   # /leagues at most hourly
LEAGUES_MAX_AGE_S = 86400              # an older cached list is not trusted: every mapped tab is tried instead
LEAGUES_REFRESH_MIN_S = 600            # an empty answer from a listed tab re-reads /leagues at most every 10 min
COVER_SAFETY_S = 60                    # coverage leaves this much of the 300 s rule for a late scheduler start
DISCOVERY_S = 7200                     # a listed tab outside coverage is re-read at most every 2 h (ranking only)
ODDS_EVERY_S = 170                     # soccer book markets keep the 180 s cadence while quotes run every 90 s


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


def league_list(body):
    """/leagues -> {id: {name, display_name, count, parent, icon, active}} for every row (the caller decides)."""
    out = {}
    for x in body.get("data") or []:
        a = x.get("attributes") or {}
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
    """Seconds between soccer quotes runs: SPORTS_JOB_SOCCER_QUOTES_EVERY_S (the scheduler's own override), else 90 with
    the per-league tabs on and 180 (as at 5036ce4) with them off. The scheduler and the coverage rule both read it."""
    default = 90 if (sport == "soccer" and pp_leagues_on()) else 180
    return max(30.0, _env_num("SPORTS_JOB_" + sport.upper() + "_QUOTES_EVERY_S", default))


def coverage_slots(every_s):
    """How many PrizePicks soccer sources (tab 82 + tabs) one request a run keeps continuously inside the 300 s rule:
    each is re-read every slots x every_s seconds, with COVER_SAFETY_S left for a late scheduler start."""
    return max(1, int((MAX_AGE - COVER_SAFETY_S) // every_s))


def _rank_key(lid, per):
    """Tabs that fill the coverage slots: competitions whose full-match lines the model can grade first
    (expansion_forecasts.REGULATION_LEAGUES), then the earliest standard-line start, then table order."""
    from .expansion_forecasts import REGULATION_LEAGUES
    code = adapters.PP_SOCCER_LEAGUES[lid][1]
    e = per.get(str(lid)) or {}
    return (0 if code in REGULATION_LEAGUES else 1, e.get("first_start") or "9999", list(adapters.PP_SOCCER_LEAGUES).index(lid))


def _soccer_prizepicks(get_json, directory, started=None, clock=None):
    """Every PrizePicks soccer tab (2026-11-19), inside PrizePicks' request limit.

    Measured from one IP (ppsoccer-1119 logs): two requests back-to-back pass, a third within seconds draws 429, and one
    request every ~40-45 s is sustained - i.e. about 1.3 requests a minute for the WHOLE IP (tennis and the NBA / NFL
    ingest share it). So: ONE PrizePicks request per run (the job runs every quotes_every_s(), default 90 s), plus at
    most one housekeeping request (the hourly /leagues read, or a 2-hourly discovery read of a tab outside coverage).

    Coverage (fixed, never a flicker): tab 82 plus the best coverage_slots()-1 tabs with standard lines (gradeable
    competitions first, then earliest start) are each re-read every slots x every_s seconds (default 2 x 90 = 180 s, so
    tab 82 keeps its 5036ce4 freshness). Rows of tabs outside coverage are kept for ranking but never put on the board
    (ops lists them as not_covered). A source not read keeps its last good rows with their OWN observed_at; the 300 s
    rule hides them exactly when they go stale; a failed read never moves or invents a line.

    429: the shared Retry-After hold skips PrizePicks until it ends (no request at all); a second 429 within 15 min backs
    the tabs off for SCOUT_SOCCER_PP_BACKOFF_S (600 s): tab 82 only, at most every 180 s (the 5036ce4 request rate).
    Per-league rows are standard lines only, and tab 82 wins a projection id listed in two tabs.
    -> (rows, the 'prizepicks' source entry, status for ops)."""
    from . import net
    from .expansion_sources import held_until, pace_gap_s, PP_HOST
    max_calls = max(1, int(_env_num("SCOUT_SOCCER_PP_MAX_CALLS", 2)))
    every = quotes_every_s()
    slots = coverage_slots(every)
    state = _read_json(directory / PP_STATE, {})
    if not isinstance(state, dict):
        state = {}
    per = state.setdefault("per", {})
    leagues = state.setdefault("leagues", {})
    calls, stopped, run_errors, attempted, roles = 0, None, {}, [], {}
    now = now_utc()

    def can_call():
        nonlocal stopped
        if stopped:
            return False
        if calls >= max_calls:
            stopped = "call_cap"
        elif held_until("soccer", PP_URL):
            stopped = "retry_after_hold"
        return not stopped

    def fetch(url, params):
        nonlocal calls, stopped
        calls += 1
        try:
            return get_json("soccer", url, params=params, timeout=15, tries=1)
        except net.HttpError as e:
            stopped = "http_429" if e.status == 429 else "request_failed"
            if e.status == 429:
                state["recent_429"] = [t for t in state.get("recent_429") or []
                                       if 0 <= (_age_s(t, now_utc()) or 10 ** 9) <= 900] + [now_utc()]
            raise

    def read(lid, role):
        entry = per.setdefault(str(lid), {})
        entry["attempt_at"] = now_utc()
        attempted.append(lid)
        roles[lid] = role
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
            del body
            parsed = len(found)
            if lid != 82:
                found = [r for r in found if r["variant"] == "standard"]
            starts = sorted(r["start"] for r in found if r["variant"] == "standard")
            entry.update(ok_at=observed, rows=found, parsed=parsed, kept=len(found), error=None,
                         first_start=starts[0] if starts else None)
        except (net.HttpError, ValueError, OSError) as e:
            entry["error"] = str(e)[:60] if type(e) is ValueError else type(e).__name__
            entry["error_at"] = now_utc()
            run_errors[lid] = entry["error"]

    list_age = _age_s(leagues.get("fetched_at"), now)
    trusted = leagues.get("listing") if list_age is not None and list_age < LEAGUES_MAX_AGE_S else None
    use, unmapped, renamed = plan_leagues(trusted)
    known = [lid for lid in use if (per.get(str(lid)) or {}).get("kept", 0) > 0]
    covered = sorted(known, key=lambda lid: _rank_key(lid, per))[:slots - 1]
    unread = [lid for lid in use if not (per.get(str(lid)) or {}).get("ok_at")]
    if len(covered) < slots - 1:          # a free slot: learn a never-read tab there (table order)
        covered += unread[:slots - 1 - len(covered)]
    fresh_set = [82] + covered
    backoff_left = -(_age_s(state.get("backoff_until"), now) or 0)

    # 1. the coverage request: the covered source read longest ago (never read first; tab 82 on a tie)
    order = sorted(fresh_set, key=lambda lid: ((per.get(str(lid)) or {}).get("ok_at") or "", lid != 82))
    if backoff_left > 0:
        e82 = per.get("82") or {}
        stopped = None
        if (_age_s(e82.get("attempt_at"), now) or 10 ** 9) >= 170 and can_call():
            read(82, "coverage")
        stopped = stopped or "backoff"
    elif can_call():
        read(order[0], "coverage")
    # 2. housekeeping, only after a clean first request: the hourly league list, else a 2-hourly discovery read of a
    #    listed tab outside coverage (its lines stay off the board; the read keeps the ranking honest)
    if not stopped and calls < max_calls:
        forced = leagues.get("refresh") and (list_age is None or list_age >= LEAGUES_REFRESH_MIN_S)
        if (list_age is None or list_age >= LEAGUES_TTL_S or forced) and can_call():
            roles["leagues"] = "housekeeping"
            try:
                listing = league_list(fetch(PP_LEAGUES_URL, None))
                if not listing:
                    raise ValueError("empty league list")
                leagues.update(listing=listing, fetched_at=now_utc(), refresh=False, error=None)
            except (net.HttpError, ValueError, OSError) as e:
                leagues["error"] = type(e).__name__
                leagues["error_at"] = now_utc()
        else:
            outside = [lid for lid in use if lid not in fresh_set and
                       (_age_s((per.get(str(lid)) or {}).get("attempt_at"), now) or 10 ** 9) >= DISCOVERY_S]
            outside.sort(key=lambda lid: (per.get(str(lid)) or {}).get("attempt_at") or "")
            if outside and can_call():
                read(outside[0], "discovery")
    if len(state.get("recent_429") or []) >= 2 and stopped == "http_429":
        until = parse_utc(now_utc()) + timedelta(seconds=_env_num("SCOUT_SOCCER_PP_BACKOFF_S", 600))
        state["backoff_until"] = until.strftime("%Y-%m-%dT%H:%M:%SZ")
        state["recent_429"] = []

    # Compose: tab 82 first (it wins a shared projection id), then the covered tabs. Only rows still inside the
    # board's own age rule and not started are carried; observed_at is never re-stamped.
    now = now_utc()
    rows, seen, counts = [], set(), {}
    for lid in fresh_set:
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
    from .expansion_history import atomic_json
    atomic_json(directory / PP_STATE, state)

    # Member-facing source: the one 'prizepicks' key, as before. ok while every covered source was read OK recently
    # enough to stay on the board until the next run (age <= 300 s - every_s); otherwise ok False 'as of' the oldest
    # good read, so the strip "PrizePicks lines as of ..." shows whenever covered lines are (about to be) missing.
    oks = [(per.get(str(lid)) or {}).get("ok_at") for lid in fresh_set]
    if all(x and _age_s(x, now) is not None and 0 <= _age_s(x, now) <= MAX_AGE - every for x in oks):
        source = {"ok": True, "checked_at": min(oks), "offers": len(rows)}
    else:
        good = [x for x in oks if x]
        source = {"ok": False, "checked_at": min(good) if good else now_utc(),
                  "error": next(iter(run_errors.values()), "not_read_recently"), "offers": len(rows)}
    listing = leagues.get("listing") or {}
    not_covered = [lid for lid in use if lid not in fresh_set and (per.get(str(lid)) or {}).get("kept", 0) > 0]
    status = {"at": now_utc(), "mode": "leagues", "calls": calls, "max_calls": max_calls, "every_s": every,
              "slots": slots, "covered": fresh_set, "not_covered": not_covered, "gap_s": pace_gap_s(PP_HOST),
              "stopped": stopped, "source": source, "backoff_until": state.get("backoff_until"),
              "recent_429": state.get("recent_429") or [],
              "league_list": {"fetched_at": leagues.get("fetched_at"), "trusted": trusted is not None,
                              "error": leagues.get("error"), "error_at": leagues.get("error_at")},
              "leagues": [], "unmapped_with_lines": unmapped, "renamed": renamed}
    for lid, (name, code) in adapters.PP_SOCCER_LEAGUES.items():
        e = per.get(str(lid)) or {}
        status["leagues"].append({
            "id": lid, "name": name, "competition": code, "listed": (listing.get(str(lid)) or {}).get("count"),
            "fetched": lid == 82 or lid in use, "covered": lid in fresh_set,
            "read_this_run": roles.get(lid), "last_ok_at": e.get("ok_at"), "last_attempt_at": e.get("attempt_at"),
            "last_error": e.get("error"), "last_error_at": e.get("error_at"), "parsed": e.get("parsed"),
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
        if req.query.get('version') == version:
            # A source-wide sighting. Since 2026-11-19 the PrizePicks soccer tabs carry
            # a tab's last good rows after a failed / skipped read, but with their own
            # observed_at, so they leave the version exactly when they go stale (and
            # members never use this shortcut: workspace_expansion reads full boards).
            # Started/removed/changed offers change the version and force a full
            # replacement.
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
