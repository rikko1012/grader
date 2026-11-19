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
SLOW_POLL_S = 900                      # a tab whose last good read had no standard line: re-read every 15 min


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


def _soccer_prizepicks(get_json, directory, started, clock=None):
    """Every PrizePicks soccer tab (2026-11-19). Tab 82 first, every run, read exactly as before; then the mapped
    per-league tabs that /leagues lists with projections (oldest good read first; a tab whose last good read had no
    standard line only every SLOW_POLL_S). Bounded: at most SCOUT_SOCCER_PP_MAX_CALLS PrizePicks requests a run (the
    /leagues read counts), none started after SCOUT_SOCCER_PP_BUDGET_S, SCOUT_PP_GAP_S between any two (shared with the
    tennis job through request-holds.db), and the first 429 / Retry-After hold / dropped request stops every further
    PrizePicks request this run. A tab not read this run keeps its last good rows with their OWN observed_at, so the
    300 s age rule hides them exactly when they go stale; a failed read never moves or invents a line. Per-league rows
    are standard lines only (members see standard only; keeps quotes.db and the board size flat), and tab 82 wins a
    projection id listed in two tabs. -> (rows, the 'prizepicks' source entry, status for ops)."""
    import time
    from . import net
    from .expansion_sources import held_until
    clock = clock or time.monotonic
    max_calls = int(_env_num("SCOUT_SOCCER_PP_MAX_CALLS", 5))
    budget = _env_num("SCOUT_SOCCER_PP_BUDGET_S", 35)
    state = _read_json(directory / PP_STATE, {})
    if not isinstance(state, dict):
        state = {}
    per = state.setdefault("per", {})
    leagues = state.setdefault("leagues", {})
    calls, stopped, run_errors, attempted = 0, None, {}, []

    def elapsed():
        return clock() - started

    def can_call():
        nonlocal stopped
        if stopped:
            return False
        if calls >= max_calls:
            stopped = "call_cap"
        elif elapsed() >= budget:
            stopped = "time_budget"
        elif held_until("soccer", PP_URL):
            stopped = "retry_after_hold"
        return not stopped

    def fetch(url, params):
        nonlocal calls, stopped
        calls += 1
        try:
            return get_json("soccer", url, params=params, timeout=20, tries=1)
        except net.HttpError as e:
            # 429 / hold / 5xx / dropped connection: no further PrizePicks request this run (never a 429 storm)
            stopped = "http_429" if e.status == 429 else "request_failed"
            raise

    def read(lid):
        entry = per.setdefault(str(lid), {})
        entry["attempt_at"] = now_utc()
        attempted.append(lid)
        try:
            body = fetch(PP_URL, {"league_id": lid, "per_page": 1000, "single_stat": "true", "game_mode": "pickem"})
            observed = now_utc()
            listed = ((leagues.get("listing") or {}).get(str(lid)) or {}).get("count")
            if not (body.get("data") or []) and listed:
                # A tab /leagues lists with lines answered empty (the comma-list answer looked exactly like this):
                # a failed read, never "no lines". The list is re-read next run.
                leagues["refresh"] = True
                raise ValueError("empty_listed_tab")
            found = adapters.prizepicks(body, "soccer", observed, None if lid == 82 else lid)
            del body
            parsed = len(found)
            if lid != 82:
                found = [r for r in found if r["variant"] == "standard"]
            entry.update(ok_at=observed, rows=found, parsed=parsed, kept=len(found), error=None)
        except (net.HttpError, ValueError, OSError) as e:
            entry["error"] = str(e)[:60] if type(e) is ValueError else type(e).__name__
            entry["error_at"] = now_utc()
            run_errors[lid] = entry["error"]

    if can_call():
        read(82)
    # Back-off: after PrizePicks answered 429 (or a shared Retry-After hold was found), the per-league tabs pause for
    # SCOUT_SOCCER_PP_BACKOFF_S (600) and soccer makes only the one tab-82 request a run, as at 5036ce4, so the extra
    # tabs never keep pressing a limit the NBA / NFL / tennis requests share.
    backoff_left = -(_age_s(state.get("backoff_until"), now_utc()) or 0)
    if backoff_left > 0 and not stopped:
        stopped = "backoff"
    list_age = _age_s(leagues.get("fetched_at"), now_utc())
    if (list_age is None or list_age >= LEAGUES_TTL_S or leagues.get("refresh")) and can_call():
        try:
            listing = league_list(fetch(PP_LEAGUES_URL, None))
            if not listing:
                raise ValueError("empty league list")
            leagues.update(listing=listing, fetched_at=now_utc(), refresh=False, error=None)
        except (net.HttpError, ValueError, OSError) as e:
            leagues["error"] = type(e).__name__
            leagues["error_at"] = now_utc()
    list_age = _age_s(leagues.get("fetched_at"), now_utc())
    trusted = leagues.get("listing") if list_age is not None and list_age < LEAGUES_MAX_AGE_S else None
    use, unmapped, renamed = plan_leagues(trusted)
    now = now_utc()

    def due(lid):
        e = per.get(str(lid)) or {}
        age = _age_s(e.get("ok_at"), now)
        return age is None or e.get("kept", 0) > 0 or age >= SLOW_POLL_S
    table = list(adapters.PP_SOCCER_LEAGUES)
    order = sorted((lid for lid in use if due(lid)),
                   key=lambda lid: ((per.get(str(lid)) or {}).get("ok_at") or "", table.index(lid)))
    for lid in order:
        if not can_call():
            break
        read(lid)

    # Compose: 82 first (it wins a shared projection id), then the mapped tabs in table order. Only rows still inside
    # the board's own age rule (MAX_AGE) and not started are carried; observed_at is never re-stamped.
    now = now_utc()
    rows, seen, counts = [], set(), {}
    for lid in table:
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
    if stopped in ("http_429", "retry_after_hold"):
        until = parse_utc(now_utc()) + timedelta(seconds=_env_num("SCOUT_SOCCER_PP_BACKOFF_S", 600))
        state["backoff_until"] = until.strftime("%Y-%m-%dT%H:%M:%SZ")
        state["backoff_reason"] = stopped
    for sid in list(per):                      # the state file only holds what can still be carried
        if not sid.isdigit() or int(sid) not in adapters.PP_SOCCER_LEAGUES:
            per.pop(sid)
            continue
        per[sid]["rows"] = [r for r in per[sid].get("rows") or []
                            if _age_s(r.get("observed_at"), now) is not None
                            and 0 <= _age_s(r.get("observed_at"), now) <= MAX_AGE]
    from .expansion_history import atomic_json
    atomic_json(directory / PP_STATE, state)

    # Member-facing source: the one 'prizepicks' key, as before. ok only when every tab with standard lines was read
    # this run; otherwise ok False 'as of' the oldest good read among them (the board strip says so honestly).
    # (a tab never read OK yet counts: nothing says it has no standard line)
    current = [82] + [lid for lid in use if not (per.get(str(lid)) or {}).get("ok_at")
                      or (per.get(str(lid)) or {}).get("kept", 0) > 0]
    oks = [(per.get(str(lid)) or {}).get("ok_at") for lid in current]
    if all(lid in attempted and lid not in run_errors for lid in current):
        source = {"ok": True, "checked_at": min(oks), "offers": len(rows)}
    else:
        known = [x for x in oks if x]
        source = {"ok": False, "checked_at": min(known) if known else now_utc(),
                  "error": next(iter(run_errors.values()), "not_read_this_run"), "offers": len(rows)}
    listing = leagues.get("listing") or {}
    status = {"at": now_utc(), "mode": "leagues", "calls": calls, "max_calls": max_calls, "budget_s": budget,
              "gap_s": None, "stopped": stopped, "elapsed_s": round(elapsed(), 1), "source": source,
              "backoff_until": state.get("backoff_until"), "backoff_reason": state.get("backoff_reason"),
              "league_list": {"fetched_at": leagues.get("fetched_at"), "trusted": trusted is not None,
                              "error": leagues.get("error"), "error_at": leagues.get("error_at")},
              "leagues": [], "unmapped_with_lines": unmapped, "renamed": renamed}
    from .expansion_sources import pace_gap_s, PP_HOST
    status["gap_s"] = pace_gap_s(PP_HOST)
    for lid, (name, code) in adapters.PP_SOCCER_LEAGUES.items():
        e = per.get(str(lid)) or {}
        status["leagues"].append({
            "id": lid, "name": name, "competition": code, "listed": (listing.get(str(lid)) or {}).get("count"),
            "fetched": lid == 82 or lid in use, "read_this_run": lid in attempted,
            "poll": ("off" if lid != 82 and lid not in use else
                     "every_run" if lid == 82 or not e.get("ok_at") or e.get("kept", 0) > 0 else "slow"),
            "last_ok_at": e.get("ok_at"), "last_attempt_at": e.get("attempt_at"),
            "last_error": e.get("error"), "last_error_at": e.get("error_at"),
            "parsed": e.get("parsed"), "kept": e.get("kept"), "on_board": counts.get(lid, 0)})
    atomic_json(directory / PP_STATUS, status)
    return rows, source, status


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
    if sport == "soccer" and pp_leagues_on():
        import time
        directory = root(sport)
        directory.mkdir(parents=True, exist_ok=True)
        found, sources["prizepicks"], _ = _soccer_prizepicks(get_json, directory, time.monotonic())
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
