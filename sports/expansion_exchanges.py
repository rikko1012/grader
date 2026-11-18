"""Kalshi + Polymarket match prices for soccer / tennis as a weight-0 market baseline (never a grade input).

Quote rule = sports/kalshi.py: the midpoint only when both sides are quoted and the spread is at most 5 cents. Each
YES is its own contract (home / tie / away), so nothing is de-vigged. Prices live in <sport>/market-prices.json
(at most 500 KB, keyed by the canonical ESPN / 365Scores event id) and an append-on-change <sport>/markets.db
(50 MB cap) - never in board.json, so a price tick never changes the member board version. A failed or skipped
series keeps its last good prices; readers drop any price older than 15 minutes by its own checked time.

Identity: series / tag -> competition; teams by exact name or a verified alias (Polymarket's "Manchester City FC"
style names need one each - stripping suffixes is not allowed); the match must be SCHEDULED on ESPN. Kalshi's
occurrence time is not the kick-off: the ticker date (+-1 day) picks the ESPN event, which must be unique, and the
start comes from ESPN. Polymarket's gameStartTime is the kick-off (+-2 h). Tennis: exact player pair, unique.
"""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import re
import sqlite3

from . import net
from .contracts import now_utc, parse_utc
from .kalshi import API as KALSHI, MAX_SPREAD, event_date

POLY = 'https://gamma-api.polymarket.com'
KALSHI_SERIES = {
    'soccer': {'KXEPLGAME': 'eng.1', 'KXLALIGAGAME': 'esp.1', 'KXSERIEAGAME': 'ita.1', 'KXBUNDESLIGAGAME': 'ger.1',
               'KXLIGUE1GAME': 'fra.1', 'KXMLSGAME': 'usa.1', 'KXUCLGAME': 'uefa.champions', 'KXUELGAME': 'uefa.europa',
               'KXUECLGAME': 'uefa.europa.conf', 'KXLIGAMXGAME': 'mex.1', 'KXBRASILEIROGAME': 'bra.1',
               'KXEREDIVISIEGAME': 'ned.1', 'KXSCOTTISHPREMGAME': 'sco.1', 'KXSUPERLIGGAME': 'tur.1',
               'KXBELGIANPLGAME': 'bel.1', 'KXARGPREMDIVGAME': 'arg.1', 'KXEFLCHAMPIONSHIPGAME': 'eng.2',
               'KXBUNDESLIGA2GAME': 'ger.2', 'KXSERIEBGAME': 'ita.2', 'KXLALIGA2GAME': 'esp.2',
               'KXJLEAGUEGAME': 'jpn.1', 'KXALEAGUEGAME': 'aus.1'},
    'tennis': {'KXATPMATCH': ('atp',), 'KXWTAMATCH': ('wta',), 'KXATPCHALLENGERMATCH': ('atp_challenger',),
               'KXWTACHALLENGERMATCH': ('wta125',)},
}
POLY_TAGS = {'soccer': {82: 'eng.1', 780: 'esp.1', 101962: 'ita.1', 1494: 'ger.1', 102070: 'fra.1', 100100: 'usa.1',
                        100977: 'uefa.champions', 101787: 'uefa.europa', 101735: 'ned.1', 101772: 'por.1',
                        102564: 'tur.1', 102648: 'bra.1', 102448: 'mex.1', 102561: 'arg.1', 102872: 'sco.1',
                        105725: 'bel.1', 102525: 'eng.2', 102864: 'ger.2', 102649: 'jpn.1', 102765: 'aus.1'}}
POLY_SERIES = {'tennis': {10365: ('atp', 'atp_challenger'), 10366: ('wta', 'wta125')}}
# (competition, venue team name) -> ESPN team id. Verified entries only (same fixture in both feeds); candidates come
# from `python -m sports.expansion_aliases exchanges` and are reviewed by hand, never applied automatically.
KALSHI_ALIASES = {}
POLY_ALIASES = {}
PRICES_BYTES = 500_000
DB_MAX_BYTES = 50 * 1024 * 1024
FRESH_S = 15 * 60


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def quote(bid, ask):
    """-> {'bid','ask','mid'} when both sides are quoted inside (0, 1) and the spread is <= 5 cents, else None."""
    bid, ask = _f(bid), _f(ask)
    if bid is None or ask is None or not (0 < bid < ask < 1) or round(ask - bid, 4) > MAX_SPREAD:
        return None
    return {'bid': bid, 'ask': ask, 'mid': round((bid + ask) / 2, 4)}


def _team(name, participants, comp, aliases):
    from .expansion_history import normalized
    alias = aliases.get((comp, name))
    hits = [p['id'] for p in participants if (alias and p['id'] == alias) or (not alias and normalized(name) in
            {normalized(p.get(k)) for k in ('name', 'short_name', 'abbreviation')} - {''})]
    return hits[0] if len(hits) == 1 else None


def _scheduled(matches, comps):
    return [m for m in matches if m.get('competition') in comps and m.get('status') == 'scheduled' and m.get('start')]


def kalshi_soccer(event, comp, matches, checked_at):
    """One Kalshi soccer game event -> (event_id, entry) or None."""
    day = event_date(event.get('event_ticker'))
    markets = [m for m in event.get('markets') or [] if m.get('status') == 'active']
    names_ = [m.get('yes_sub_title') or '' for m in markets]
    teams = [n for n in names_ if n and n.casefold() != 'tie']
    if not day or len(markets) != 3 or len(teams) != 2 or names_.count('Tie') + names_.count('tie') != 1:
        return None
    found = []
    for m in _scheduled(matches, {comp}):
        if abs((date.fromisoformat(m['start'][:10]) - date.fromisoformat(day)).days) > 1:
            continue
        ids = [_team(t, m['participants'], comp, KALSHI_ALIASES) for t in teams]
        if all(ids) and ids[0] != ids[1] and set(ids) == {p['id'] for p in m['participants']}:
            found.append((m, dict(zip(teams, ids))))
    if len(found) != 1:
        return None
    match, ids = found[0]
    outcomes = []
    for mk in markets:
        q = quote(mk.get('yes_bid_dollars'), mk.get('yes_ask_dollars'))
        label = mk.get('yes_sub_title')
        if q:
            outcomes.append({'label': 'Draw' if label.casefold() == 'tie' else label,
                             'participant_id': ids.get(label), 'market_id': mk.get('ticker'), **q})
    return (match['event_id'], {'venue': 'kalshi', 'checked_at': checked_at, 'start': match['start'],
                                'outcomes': outcomes}) if outcomes else None


def _pair(people, matches, comps, near, window_s):
    from .expansion_history import normalized
    want = {normalized(n) for n in people}
    if len(want) != 2:
        return None
    found = []
    for m in _scheduled(matches, comps):
        ids = {normalized(p['name']): p['id'] for p in m.get('participants') or []}
        if set(ids) == want and len(ids) == 2 and near(m) <= window_s:
            found.append((m, {n: ids[normalized(n)] for n in people}))
    return found[0] if len(found) == 1 else None


def kalshi_tennis(event, comps, matches, checked_at):
    day = event_date(event.get('event_ticker'))
    markets = [m for m in event.get('markets') or [] if m.get('status') == 'active']
    people = [m.get('yes_sub_title') or '' for m in markets]
    if not day or len(markets) != 2 or not all(people):
        return None
    joined = _pair(people, matches, set(comps), lambda m: abs(
        (date.fromisoformat(m['start'][:10]) - date.fromisoformat(day)).days) * 86400, 86400)
    if not joined:
        return None
    match, ids = joined
    outcomes = [{'label': mk['yes_sub_title'], 'participant_id': ids[mk['yes_sub_title']], 'market_id': mk.get('ticker'), **q}
                for mk in markets for q in [quote(mk.get('yes_bid_dollars'), mk.get('yes_ask_dollars'))] if q]
    return (match['event_id'], {'venue': 'kalshi', 'checked_at': checked_at, 'start': match['start'],
                                'outcomes': outcomes}) if outcomes else None


_WIN = re.compile(r'^Will (.+) win on (\d{4}-\d{2}-\d{2})\?$')
_DRAW = re.compile(r'^Will (.+) vs\. (.+) end in a draw\?$')


def _poly_start(m):
    s = str(m.get('gameStartTime') or '').strip()
    try:
        return datetime.fromisoformat(s.replace(' ', 'T').replace('+00', '+00:00') if s.endswith('+00') else s.replace(' ', 'T'))
    except ValueError:
        return None


def poly_soccer(markets, comp, matches, checked_at, now):
    """Polymarket moneyline markets of one league tag -> {event_id: entry}. The draw market names the pair."""
    groups = {}
    for m in markets or []:
        start = _poly_start(m)
        if not start or start.tzinfo is None or start <= now or m.get('closed') or not m.get('acceptingOrders', True):
            continue                                   # the server-side start filter does not work: filter here
        groups.setdefault(start.isoformat(), []).append(m)
    out = {}
    for start, rows in groups.items():
        draws = [(r, _DRAW.match(r.get('question') or '')) for r in rows]
        draws = [(r, d) for r, d in draws if d]
        for draw, d in draws:
            home, away = d[1], d[2]
            wins = {w[1]: r for r in rows for w in [_WIN.match(r.get('question') or '')] if w and w[1] in (home, away)}
            found = []
            for m in _scheduled(matches, {comp}):
                if abs((parse_utc(m['start']) - parse_utc(start)).total_seconds()) > 7200:
                    continue
                ids = {t: _team(t, m['participants'], comp, POLY_ALIASES) for t in (home, away)}
                if all(ids.values()) and set(ids.values()) == {p['id'] for p in m['participants']}:
                    found.append((m, ids))
            if len(found) != 1:
                continue
            match, ids = found[0]
            outcomes = []
            for label, market, pid in [(home, wins.get(home), ids[home]), ('Draw', draw, None), (away, wins.get(away), ids[away])]:
                q = market and quote(market.get('bestBid'), market.get('bestAsk'))
                if q:
                    outcomes.append({'label': label, 'participant_id': pid, 'market_id': str(market.get('id')), **q})
            if outcomes:
                out[match['event_id']] = {'venue': 'polymarket', 'checked_at': checked_at, 'start': match['start'],
                                          'outcomes': outcomes}
    return out


def poly_tennis(events, comps, matches, checked_at, now):
    out = {}
    for ev in events or []:
        for m in ev.get('markets') or []:
            if m.get('sportsMarketType') != 'moneyline' or m.get('closed'):
                continue
            try:
                people = json.loads(m.get('outcomes') or '[]')
            except ValueError:
                continue
            start = _poly_start(m)
            if len(people) != 2 or not start or start.tzinfo is None or start <= now:
                continue
            joined = _pair(people, matches, set(comps),
                           lambda x: abs((parse_utc(x['start']) - start).total_seconds()), 7200)
            q = quote(m.get('bestBid'), m.get('bestAsk'))
            if not joined or not q:
                continue
            match, ids = joined
            other = {'bid': round(1 - q['ask'], 4), 'ask': round(1 - q['bid'], 4), 'mid': round(1 - q['mid'], 4)}
            out[match['event_id']] = {'venue': 'polymarket', 'checked_at': checked_at, 'start': match['start'],
                                      'outcomes': [{'label': people[0], 'participant_id': ids[people[0]],
                                                    'market_id': str(m.get('id')), **q},
                                                   {'label': people[1], 'participant_id': ids[people[1]],
                                                    'market_id': str(m.get('id')), **other}]}
    return out


def load(directory):
    try:
        doc = json.loads((directory / 'market-prices.json').read_text(encoding='utf-8'))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def fresh(entry, now=None):
    """A saved venue price is shown only while its own check is under 15 minutes old."""
    try:
        now = now or datetime.now(timezone.utc)
        return 0 <= (now - parse_utc(entry['checked_at'])).total_seconds() < FRESH_S
    except (KeyError, TypeError, ValueError):
        return False


def save(directory, prices, now):
    """Drop started events; keep under PRICES_BYTES by leaving out the latest-starting events first."""
    from .expansion_history import atomic_json
    floor = now.isoformat()
    rows = {k: v for k, v in prices.items() if any((e.get('start') or '') > floor[:19] for e in v.values())}
    order = sorted(rows, key=lambda k: min(e.get('start') or '' for e in rows[k].values()))
    keep, used = {}, 2
    for k in order:
        size = len(json.dumps({k: rows[k]}, separators=(',', ':')))
        if used + size > PRICES_BYTES:
            break
        keep[k] = rows[k]
        used += size
    atomic_json(directory / 'market-prices.json', keep)
    return keep


def log(directory, found):
    """Append one row per outcome whose bid / ask changed (markets.db, 50 MB cap: stops logging past it)."""
    path = directory / 'markets.db'
    if path.exists() and sum(p.stat().st_size for p in directory.glob('markets.db*')) > DB_MAX_BYTES:
        return 0
    added = 0
    with closing(sqlite3.connect(path, timeout=3)) as con, con:
        con.execute('''CREATE TABLE IF NOT EXISTS quotes (id INTEGER PRIMARY KEY, venue TEXT, market_id TEXT,
            event_id TEXT, label TEXT, bid REAL, ask REAL, mid REAL, observed_at TEXT)''')
        con.execute('CREATE INDEX IF NOT EXISTS quotes_market ON quotes(venue, market_id, label, id DESC)')
        for eid, entry in found.items():
            for o in entry['outcomes']:
                last = con.execute('SELECT bid, ask FROM quotes WHERE venue=? AND market_id=? AND label=? ORDER BY id DESC LIMIT 1',
                                   (entry['venue'], o['market_id'], o['label'])).fetchone()
                if last != (o['bid'], o['ask']):
                    con.execute('INSERT INTO quotes(venue,market_id,event_id,label,bid,ask,mid,observed_at) VALUES (?,?,?,?,?,?,?,?)',
                                (entry['venue'], o['market_id'], eid, o['label'], o['bid'], o['ask'], o['mid'],
                                 entry['checked_at']))
                    added += 1
    return added


def collect(sport, get, directory, catalogs, recent=None):
    """Least recently checked series / tags first (offered competitions ahead); the caller's budget stops the run.
    -> {'requests': n, 'events': priced events this run, 'errors': [...]}"""
    from .expansion_history import atomic_json
    path = directory / 'exchanges-state.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    recent = recent or {}
    now = datetime.now(timezone.utc)
    matches = [m for rows in catalogs.values() for m in rows.values()]
    prices = load(directory)
    jobs = [('kalshi', s, c) for s, c in KALSHI_SERIES.get(sport, {}).items()]
    jobs += [('poly-tag', t, c) for t, c in POLY_TAGS.get(sport, {}).items()]
    jobs += [('poly-series', t, c) for t, c in POLY_SERIES.get(sport, {}).items()]
    comps_of = lambda c: c if isinstance(c, tuple) else (c,)
    jobs.sort(key=lambda j: (not any(c in recent for c in comps_of(j[2])) and sport == 'soccer',
                             state.get(f'{j[0]}:{j[1]}', ''), str(j[1])))
    result = {'requests': 0, 'events': 0, 'logged': 0, 'errors': []}
    try:
        for venue, key, comp in jobs:
            found = {}
            checked = now_utc()
            try:
                if venue == 'kalshi':
                    body = get(f'{KALSHI}/events', {'series_ticker': key, 'status': 'open',
                                                     'with_nested_markets': 'true', 'limit': 200})
                    for ev in body.get('events') or []:
                        hit = (kalshi_soccer(ev, comp, matches, checked) if sport == 'soccer'
                               else kalshi_tennis(ev, comp, matches, checked))
                        if hit:
                            found[hit[0]] = hit[1]
                elif venue == 'poly-tag':
                    body = get(f'{POLY}/markets', {'sports_market_types': 'moneyline', 'closed': 'false',
                                                   'limit': 100, 'tag_id': key})
                    if not isinstance(body, list):
                        raise ValueError('unexpected Polymarket answer')
                    found = poly_soccer(body, comp, matches, checked, now)
                else:
                    body = get(f'{POLY}/events', {'series_id': key, 'closed': 'false', 'limit': 25})
                    if not isinstance(body, list):
                        raise ValueError('unexpected Polymarket answer')
                    found = poly_tennis(body, comp, matches, checked, now)
                del body
                result['requests'] += 1
                state[f'{venue}:{key}'] = checked
            except (net.HttpError, ValueError, OSError, KeyError, TypeError) as e:
                result['requests'] += 1
                state[f'{venue}:{key}'] = checked                  # rotate on; the last good prices stay
                result['errors'].append(type(e).__name__)
                continue
            vname = 'kalshi' if venue == 'kalshi' else 'polymarket'
            for eid, entry in found.items():
                prices.setdefault(eid, {})[vname] = entry
            result['events'] += len(found)
            try:
                result['logged'] += log(directory, found)
            except (OSError, sqlite3.Error) as e:
                result['errors'].append(type(e).__name__)
    finally:
        atomic_json(path, state)
        save(directory, prices, now)
    return result
