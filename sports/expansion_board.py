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

from .contracts import now_utc, parse_utc, utc
from . import expansion_offers as adapters

MAX_AGE = 300
REFERENCE_KEEP_DAYS = 14   # quotes.db history of exchange reference prices (`ref:` rows); fixtures are at most 8 days out
SPORTS = ("soccer", "tennis")
_SNAPSHOT_LOCK = threading.Lock()
_PREPARED = {}  # At most two eligible, encoded boards; shared by concurrent members.


@lru_cache(maxsize=2)
def _read_snapshot(path, stamp, size):
    # At most one parsed snapshot per new sport, rather than per member request.
    return json.loads(Path(path).read_text(encoding="utf-8"))



def _current(rows, sport, instant, deadlines):
    """Rows of this sport that have not started and were seen within MAX_AGE (the offers / game_odds rule)."""
    kept = []
    for row in rows:
        if row.get('sport') != sport:
            continue
        start = parse_utc(row['start']).timestamp()
        observed = parse_utc(row['observed_at']).timestamp()
        if start > instant and 0 <= instant-observed <= MAX_AGE:
            kept.append(row)
            deadlines.extend((start, observed+MAX_AGE))
        elif observed > instant:
            deadlines.append(observed)
    return kept


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
        # Exchange reference prices (Kalshi, Polymarket): their own key, never mixed into offers or game_odds. A snapshot
        # without the key answers exactly as before.
        reference = []
        if 'reference_prices' in data:
            reference = data['reference_prices'] = _current(data['reference_prices'], sport, instant, deadlines)
        data['version'] = hashlib.sha256(json.dumps(sorted(
            [(r['platform'],r['offer_id'],r['revision']) for r in rows]+
            [('odds:'+r['platform'],r['offer_id'],r['revision']) for r in odds]+
            [('ref:'+r['platform'],r['offer_id'],r['revision']) for r in reference]),separators=(',',':')).encode()).hexdigest()
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


def save(sport, rows, sources, observed_at, directory=None, game_odds=None, reference_prices=None):
    directory = Path(directory) if directory else root(sport)
    game_odds=game_odds or []
    # Exchange reference prices: stored and published under their own key (None: the key is not written at all).
    reference = list(reference_prices or [])
    if any(r.get("sport") != sport for r in rows+game_odds+reference):
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
            for row in (rows+[dict(r,platform='odds:'+r['platform']) for r in game_odds]
                        +[dict(r,platform='ref:'+r['platform']) for r in reference]):
                previous = con.execute("SELECT revision FROM revisions WHERE platform=? AND offer_id=? ORDER BY id DESC LIMIT 1",
                                       (row['platform'], row['offer_id'])).fetchone()
                if not previous or previous[0] != row['revision']:
                    con.execute("INSERT INTO revisions(platform,offer_id,revision,observed_at,payload) VALUES (?,?,?,?,?)",
                                (row['platform'], row['offer_id'], row['revision'], observed_at, json.dumps(row)))
            # Exchange reference prices tick far more often than lines and nothing reads their history back, so only
            # the last REFERENCE_KEEP_DAYS of `ref:` revisions are kept (freed pages are reused: the file stops
            # growing). Line and game-odds revisions are never deleted.
            con.execute("DELETE FROM revisions WHERE platform >= 'ref:' AND platform < 'ref;' AND observed_at < ?",
                        (utc(parse_utc(observed_at) - timedelta(days=REFERENCE_KEEP_DAYS)),))
    finally:
        con.close()
    rows = fit(rows, SNAPSHOT_BYTES - len(json.dumps(game_odds, separators=(",", ":")))
               - (len(json.dumps(reference, separators=(",", ":"))) if reference_prices is not None else 0))
    snapshot = {"sport": sport, "built_at": observed_at, "sources": sources, "offers": rows,
                "mode": "lines", "grading_ready": False, "game_odds":game_odds}
    if reference_prices is not None:
        snapshot["reference_prices"] = reference
    fd, temporary = tempfile.mkstemp(prefix="board-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, separators=(",", ":"))
        os.replace(temporary, directory / "board.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return snapshot


def collect(sport):
    from . import net
    from .expansion_sources import get_json
    rows, sources = [], {}
    pp = lambda league: ("https://partner-api.prizepicks.com/projections",
                         {"league_id": league, "per_page": 1000, "single_stat": "true", "game_mode": "pickem"})
    # (source, provider, request): one serial request each, through the shared Retry-After holds.
    jobs = [("prizepicks", "prizepicks", pp(82 if sport == "soccer" else 5))]
    if sport == "soccer" and os.environ.get("SCOUT_SOCCER_PP_MLS") == "1":
        # MLS is its own PrizePicks league. Off by default: a third PrizePicks call in the same few seconds as the
        # soccer and tennis calls drew HTTP 429 locally (2026-11-18), and a failed source drops its rows that run.
        jobs.append(("prizepicks_mls", "prizepicks", pp(6)))
    if sport == "tennis":
        jobs.append(("underdog", "underdog", ("https://api.underdogfantasy.com/v1/over_under_lines", {"sport_id": "TENNIS"})))
    if sport == "soccer" and os.environ.get("SCOUT_SOCCER_UNDERDOG") == "1":
        # Always filtered by sport_id: the unfiltered answer is ~26 MB.
        jobs.append(("underdog", "underdog", ("https://api.underdogfantasy.com/v1/over_under_lines", {"sport_id": "FIFA"})))
    seen = set()
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
    reference = None
    from . import expansion_kalshi, expansion_polymarket
    from .expansion_sources import get_limited
    # Exchange match-winner prices: a price reference only. Last, so their pacing never delays a line source; any
    # failure costs only that source's rows. Off switches: SCOUT_<SPORT>_KALSHI=0, SCOUT_TENNIS_POLYMARKET=0.
    for name, module in (('kalshi', expansion_kalshi), ('polymarket', expansion_polymarket)):
        if not module.switched_on(sport):
            continue
        reference = reference or []
        try:
            found, sources[name] = module.collect(
                sport, lambda url, limit=module.MAX_BYTES, **kw: get_limited(sport, url, limit, **kw), directory)
            reference.extend(found)
        except Exception as e:      # a reference source can never cost the run its lines
            sources[name] = {'ok': False, 'reference': True, 'checked_at': now_utc(), 'error': type(e).__name__}
    return save(sport, rows, sources, now_utc(),game_odds=odds,reference_prices=reference)


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
            # A source-wide sighting only applies because collect() never carries
            # forward rows after a failed source fetch. Started/removed/changed
            # offers change the version and force a full replacement.
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
    if not any(s["ok"] for s in result["sources"].values() if not s.get("reference")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
