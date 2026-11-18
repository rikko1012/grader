"""football-data.co.uk results, team stats and closing 1X2 / O-U 2.5 prices for soccer research (weight 0).

Owner cleared automated collection (2026-11-18). One CSV per run at most, each file at most once a day, rotating
(about 21 requests a day). Rows live in their own soccer/football-data/fd.db - never in canonical.db, so the history
reader and the forecasts never see them. A fixture joins an ESPN event only when the division's competition, the UK
kick-off converted to UTC (within 2 h), both teams (exact name or a verified alias, never partial names) and the final
score all agree and exactly one event qualifies. A truncated / HTML answer is refused and the last good rows stay.
The 2026-27 main files carry no Pinnacle (PS*) closing columns; the new/ league files do.
"""
from contextlib import closing
import csv
from datetime import datetime, timedelta, timezone
import io
import json
import re
import sqlite3
from zoneinfo import ZoneInfo

from . import net
from .contracts import now_utc, parse_utc

BASE = 'https://www.football-data.co.uk/'
SEASON = '2627'
MAIN = {'E0': 'eng.1', 'E1': 'eng.2', 'SP1': 'esp.1', 'SP2': 'esp.2', 'D1': 'ger.1', 'D2': 'ger.2', 'I1': 'ita.1',
        'I2': 'ita.2', 'F1': 'fra.1', 'F2': 'fra.2', 'N1': 'ned.1', 'P1': 'por.1', 'SC0': 'sco.1', 'B1': 'bel.1',
        'T1': 'tur.1', 'G1': 'gre.1'}
EXTRA = {'USA': 'usa.1', 'BRA': 'bra.1', 'ARG': 'arg.1', 'MEX': 'mex.1', 'JPN': 'jpn.1'}
# (ESPN slug, football-data team name) -> ESPN team id. Verified entries only (both feeds, same fixture, same score);
# candidates come from `python -m sports.expansion_aliases football-data` and are reviewed by hand, never applied.
ALIASES = {}
UK = ZoneInfo('Europe/London')
DB_MAX_BYTES = 50 * 1024 * 1024
KEEP_DAYS = 150
STAT_COLUMNS = {'S': 'shots', 'ST': 'shots_on_target', 'C': 'corners', 'F': 'fouls', 'Y': 'yellow_cards',
                'R': 'red_cards', 'xG': 'xg'}
BOOKS = ('Avg', 'Max', 'B365', 'PS', 'BFE', 'BFD', 'BV', 'BW', 'PP', 'SKB')
_CLOSE = re.compile(r'^(%s)C(H|D|A|>2\.5|<2\.5)$' % '|'.join(BOOKS))


def url(name):
    return BASE + (f'mmz4281/{SEASON}/{name}.csv' if name in MAIN else f'new/{name}.csv')


def _num(v):
    try:
        x = float(v)
        return x if x == x and x >= 0 else None
    except (TypeError, ValueError):
        return None


def kickoff_utc(day, clock):
    """football-data Date (dd/mm/yyyy or dd/mm/yy) + Time (UK local) -> UTC ISO, or None."""
    for fmt in ('%d/%m/%Y %H:%M', '%d/%m/%y %H:%M'):
        try:
            local = datetime.strptime(f'{day} {clock}', fmt).replace(tzinfo=UK)
            return local.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
        except (ValueError, TypeError):
            continue
    return None


def parse(text, name, today=None):
    """One football-data CSV -> fixture rows (recent KEEP_DAYS only). Raises ValueError for a non-CSV / wrong file."""
    if not isinstance(text, str) or '<html' in text[:500].lower():
        raise ValueError('football-data answer is not a CSV')
    reader = csv.reader(io.StringIO(text.lstrip('﻿')))
    header = next(reader, None) or []
    main = name in MAIN
    need = (['Div', 'Date', 'Time', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'] if main
            else ['Country', 'League', 'Season', 'Date', 'Time', 'Home', 'Away', 'HG', 'AG'])
    if not header or header[0] != need[0] or any(c not in header for c in need):
        raise ValueError('unexpected football-data columns')
    col = {c: i for i, c in enumerate(header) if c}
    slug = MAIN.get(name) or EXTRA[name]
    floor = ((today or datetime.now(timezone.utc)) - timedelta(days=KEEP_DAYS)).isoformat()
    rows = []
    for raw in reader:
        if len(raw) < len(need) or not any(raw):
            continue
        get = lambda c: raw[col[c]].strip() if c in col and col[c] < len(raw) else ''
        if main and get('Div') != name:
            continue
        start = kickoff_utc(get('Date'), get('Time'))
        home, away = get('HomeTeam' if main else 'Home'), get('AwayTeam' if main else 'Away')
        hg, ag = _num(get('FTHG' if main else 'HG')), _num(get('FTAG' if main else 'AG'))
        if not start or start < floor or not home or not away or hg is None or ag is None:
            continue
        stats = {'home': {}, 'away': {}}
        if main:
            for suffix, stat in STAT_COLUMNS.items():
                for side, letter in (('home', 'H'), ('away', 'A')):
                    v = _num(get(letter + suffix))
                    if v is not None:
                        stats[side][stat] = v
        close = {}
        for c in col:
            m = _CLOSE.match(c)
            v = _num(get(c))
            if m and v is not None and v > 1:
                close.setdefault(m[1], {})[{'H': 'home', 'D': 'draw', 'A': 'away', '>2.5': 'over25',
                                            '<2.5': 'under25'}[m[2]]] = v
        rows.append({'file': name, 'slug': slug, 'start': start, 'home': home, 'away': away, 'fthg': hg, 'ftag': ag,
                     'stats': stats, 'closing': close})
    return rows


def join(row, matches):
    """-> (event_id, {'home': team_id, 'away': team_id}) for exactly one ESPN final, else None."""
    from .expansion_history import normalized
    found = []
    for m in matches:
        if (m.get('competition') != row['slug'] or m.get('status') != 'final' or not m.get('start')
                or not m.get('scores') or abs((parse_utc(m['start']) - parse_utc(row['start'])).total_seconds()) > 7200):
            continue
        ids = {}
        for side in ('home', 'away'):
            alias = ALIASES.get((row['slug'], row[side]))
            hits = [p['id'] for p in m.get('participants') or []
                    if (alias and p['id'] == alias) or (not alias and normalized(row[side]) in
                        {normalized(p.get(k)) for k in ('name', 'short_name', 'abbreviation')} - {''})]
            if len(hits) != 1:
                break
            ids[side] = hits[0]
        if len(ids) != 2 or ids['home'] == ids['away']:
            continue
        if m['scores'].get(ids['home']) != row['fthg'] or m['scores'].get(ids['away']) != row['ftag']:
            continue
        found.append((m['event_id'], ids))
    return found[0] if len(found) == 1 else None


def connect(directory):
    path = directory / 'football-data' / 'fd.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=3)
    con.execute('''CREATE TABLE IF NOT EXISTS fixtures (key TEXT PRIMARY KEY, file TEXT, slug TEXT, start TEXT,
        home TEXT, away TEXT, fthg REAL, ftag REAL, event_id TEXT, home_team_id TEXT, away_team_id TEXT,
        stats TEXT, fetched_at TEXT)''')
    con.execute('''CREATE TABLE IF NOT EXISTS closing (key TEXT, book TEXT, home REAL, draw REAL, away REAL,
        over25 REAL, under25 REAL, fetched_at TEXT, PRIMARY KEY (key, book))''')
    con.execute('CREATE INDEX IF NOT EXISTS fixtures_home ON fixtures(home_team_id, start)')
    con.execute('CREATE INDEX IF NOT EXISTS fixtures_away ON fixtures(away_team_id, start)')
    return con


def store(directory, rows, matches, fetched_at):
    """Upsert one file's rows; -> (stored, joined). Never deletes; a later file corrects its own rows."""
    if sum(p.stat().st_size for p in (directory / 'football-data').glob('fd.db*')) > DB_MAX_BYTES:
        raise OSError('football-data store budget reached')
    joined = 0
    with closing(connect(directory)) as con, con:
        for r in rows:
            key = f"{r['file']}|{r['start']}|{r['home']}|{r['away']}"
            hit = join(r, matches)
            joined += bool(hit)
            eid, ids = hit if hit else (None, {})
            con.execute('''INSERT INTO fixtures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
                fthg=excluded.fthg, ftag=excluded.ftag, stats=excluded.stats, fetched_at=excluded.fetched_at,
                event_id=COALESCE(excluded.event_id, fixtures.event_id),
                home_team_id=COALESCE(excluded.home_team_id, fixtures.home_team_id),
                away_team_id=COALESCE(excluded.away_team_id, fixtures.away_team_id)''',
                (key, r['file'], r['slug'], r['start'], r['home'], r['away'], r['fthg'], r['ftag'], eid,
                 ids.get('home'), ids.get('away'), json.dumps(r['stats'], sort_keys=True), fetched_at))
            for book, p in r['closing'].items():
                con.execute('INSERT OR REPLACE INTO closing VALUES (?,?,?,?,?,?,?,?)',
                            (key, book, p.get('home'), p.get('draw'), p.get('away'), p.get('over25'),
                             p.get('under25'), fetched_at))
    return len(rows), joined


def team_form(directory, limit_teams=800):
    """team-form.json: per ESPN team id, last 5 / 10 joined fixtures - shots, shots on target, corners, cards, xG for
    and against, and the market-average closing 1X2 of the last 3. Weight 0 research."""
    from .expansion_history import atomic_json
    out = {}
    with closing(connect(directory)) as con:
        rows = con.execute('''SELECT f.*, c.home AS ch, c.draw AS cd, c.away AS ca FROM fixtures f
            LEFT JOIN closing c ON c.key=f.key AND c.book='Avg' WHERE f.event_id IS NOT NULL ORDER BY f.start DESC''').fetchall()
        names = [d[0] for d in con.execute('SELECT f.*, 1 AS ch, 1 AS cd, 1 AS ca FROM fixtures f LIMIT 0').description]
    for raw in rows:
        r = dict(zip(names, raw))
        stats = json.loads(r['stats'] or '{}')
        for side, other in (('home', 'away'), ('away', 'home')):
            tid = r[f'{side}_team_id']
            games = out.setdefault(tid, [])
            if len(games) >= 10:
                continue
            won = r['fthg'] > r['ftag'] if side == 'home' else r['ftag'] > r['fthg']
            games.append({'start': r['start'], 'event_id': r['event_id'], 'for': stats.get(side, {}),
                          'against': stats.get(other, {}), 'result': 'W' if won else 'D' if r['fthg'] == r['ftag'] else 'L',
                          'close_avg': {'win': r['ch'] if side == 'home' else r['ca'], 'draw': r['cd'],
                                        'loss': r['ca'] if side == 'home' else r['ch']} if r['ch'] else None,
                          'fetched_at': r['fetched_at']})
    form = {}
    for tid, games in list(out.items())[:limit_teams]:
        entry = {'known_at': max(g['fetched_at'] for g in games), 'through': games[0]['start'], 'source': 'football-data'}
        for n in (5, 10):
            window = games[:n]
            for part in ('for', 'against'):
                for stat in ('shots', 'shots_on_target', 'corners', 'xg'):
                    vals = [g[part][stat] for g in window if stat in g[part]]
                    if len(vals) == len(window) and vals:
                        entry[f'{part}_{stat}_l{n}'] = round(sum(vals) / len(vals), 2)
                cards = [g[part]['yellow_cards'] + g[part].get('red_cards', 0) for g in window if 'yellow_cards' in g[part]]
                if len(cards) == len(window) and cards:
                    entry[f'{part}_cards_l{n}'] = round(sum(cards) / len(cards), 2)
            entry[f'games_l{n}'] = len(window)
        entry['closing'] = [{'start': g['start'][:10], 'result': g['result'], **g['close_avg']}
                            for g in games[:3] if g['close_avg']]
        form[tid] = entry
    atomic_json(directory / 'team-form.json', form)
    return len(form)


def collect(get_text, directory, catalogs):
    """At most one CSV this run: the least recently checked file not read in 24 h (a failure retries after 1 h)."""
    from .expansion_history import atomic_json
    path = directory / 'football-data' / 'state.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    now = datetime.now(timezone.utc)

    def due(name):
        s = state.get(name, {})
        return ((not s.get('checked_at') or (now - parse_utc(s['checked_at'])).total_seconds() >= 86400)
                and (not s.get('attempt_at') or (now - parse_utc(s['attempt_at'])).total_seconds() >= 3600))
    order = list(MAIN) + list(EXTRA)
    todo = sorted((n for n in order if due(n)), key=lambda n: (state.get(n, {}).get('checked_at') or '', order.index(n)))
    result = {'file': None, 'rows': 0, 'joined': 0, 'errors': []}
    if not todo:
        return result
    name = todo[0]
    result['file'] = name
    state.setdefault(name, {})['attempt_at'] = now_utc()
    try:
        text = get_text(url(name), {})
        rows = parse(text, name, now)
        matches = [m for m in catalogs.get(MAIN.get(name) or EXTRA[name], {}).values()]
        stored, joined = store(directory, rows, matches, now_utc())
        state[name].update(checked_at=now_utc(), rows=stored, joined=joined)
        result.update(rows=stored, joined=joined)
        result['teams'] = team_form(directory)
    except (net.HttpError, ValueError, OSError, sqlite3.Error) as e:
        result['errors'].append(type(e).__name__)
    finally:
        atomic_json(path, state)
    return result


def load_form(directory):
    try:
        doc = json.loads((directory / 'team-form.json').read_text(encoding='utf-8'))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}
