"""Materialized player research for the shared Scout interface.

Only the background history worker reads canonical databases. Member requests
read a bounded file; no request fans out to providers or scans game history.
The source sport, competition, player, stat and period stay attached throughout.
"""
import hashlib
import json
import time
from functools import lru_cache
from pathlib import Path

from . import expansion_board as boards
from .contracts import now_utc


def key(offer):
    return hashlib.sha256(json.dumps([offer[k] for k in
        ('sport', 'platform', 'event_id', 'player_id', 'stat', 'scope')],
        separators=(',', ':')).encode()).hexdigest()[:32]


@lru_cache(maxsize=2)
def _read(path, stamp, size):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def saved(sport):
    path = boards.root(sport) / 'research.json'
    try:
        st = path.stat()
        if st.st_size > 4_000_000:
            return {}
        doc = _read(str(path), st.st_mtime_ns, st.st_size)
        return doc if doc.get('sport') == sport else {}
    except (OSError, ValueError):
        return {}


RESEARCH_BYTES = 3_600_000   # saved() ignores a research.json over 4,000,000 bytes (all research would vanish)


def fit(players, starts, limit=RESEARCH_BYTES):
    """Keep research.json under its read limit: when too large, leave out the entries for the latest-starting
    offers first (they are researched again on a later run)."""
    sizes = {k: len(json.dumps(v, separators=(',', ':'))) + 40 for k, v in players.items()}
    total = sum(sizes.values())
    if total <= limit:
        return players
    keep, used = {}, 0
    for k in sorted(players, key=lambda k: (starts.get(k, ''), k)):
        if used + sizes[k] > limit:
            break
        keep[k] = players[k]
        used += sizes[k]
    return keep


# The research-only match block (exchange prices, tennis context, soccer team form) has its OWN budget, outside the
# players' RESEARCH_BYTES, so events can never push a player out: 3,600,000 + 350,000 + the doc header stays under
# saved()'s 4,000,000-byte read limit (publish() drops events, never players, if a doc would still be over it).
EVENTS_BYTES = 350_000
READ_LIMIT = 4_000_000


def _cents(x):
    return f"{round(x * 100)}\u00a2"


def event_rows(sport, eid, match, prices, contexts, form):
    """Weight-0 match rows for one canonical event: never a grade input, labelled with their source."""
    rows = []
    for venue, entry in sorted((prices.get(eid) or {}).items()):
        parts = [f"{o['label']}{'' if o['label'] == 'Draw' or sport == 'tennis' else ' win'} {_cents(o['mid'])}"
                 for o in entry.get('outcomes') or [] if isinstance(o.get('mid'), (int, float))]
        if parts:
            rows.append({'label': f"Match market ({'Kalshi' if venue == 'kalshi' else 'Polymarket'}, mid)",
                         'value': ' \u00b7 '.join(parts), 'group': 'Match market (exchange)', 'source': venue,
                         'checked_at': entry.get('checked_at')})
    ctx = contexts.get(eid) or {}
    if ctx:
        tour = (ctx.get('tour') or '').upper()
        base = {'group': 'Match context', 'source': 'thescore', 'known_at': ctx.get('known_at')}
        for key, label in (('round', 'Round'), ('court', 'Court')):
            if ctx.get(key):
                rows.append({**base, 'label': label, 'value': ctx[key]})
        for p in (ctx.get('players') or {}).values():
            name = p.get('name') or ''
            if 'rank' in p:
                rows.append({**base, 'label': f'{name} \u00b7 {tour} rank'.strip(),
                             'value': f"{p['rank']}" + (f" ({p['points']} pts)" if 'points' in p else '')})
            for key, label in (('seed', 'Seed'), ('h2h', 'H2H'), ('last10', 'Last 10'), ('streak', 'Streak'),
                               ('season', 'Season W-L')):
                if key in p:
                    rows.append({**base, 'label': f'{name} \u00b7 {label}', 'value': str(p[key])})
            for key, label in (('h2h_surface', 'H2H'), ('season_surface', 'Season W-L')):
                if key in p:
                    rows.append({**base, 'label': f"{name} \u00b7 {label} ({p.get('surface')})", 'value': p[key]})
    for team in (match or {}).get('participants') or []:
        f = form.get(team.get('id'))
        if not f:
            continue
        base = {'group': 'Team form (football-data)', 'source': 'football-data', 'known_at': f.get('known_at')}
        for stat, label in (('shots', 'Shots'), ('shots_on_target', 'Shots on target'), ('corners', 'Corners'),
                            ('cards', 'Cards'), ('xg', 'xG')):
            for n in (5, 10):
                a, b = f.get(f'for_{stat}_l{n}'), f.get(f'against_{stat}_l{n}')
                if a is not None and b is not None and f.get(f'games_l{n}') == n:
                    rows.append({**base, 'label': f"{team['name']} \u00b7 {label} for / against \u00b7 last {n}",
                                 'value': f'{a:g} / {b:g}'})
        for c in (f.get('closing') or [])[:1]:
            if all(isinstance(c.get(k), (int, float)) for k in ('win', 'draw', 'loss')):
                rows.append({**base, 'label': f"{team['name']} \u00b7 Past closing price (market average)",
                             'value': f"{c['start']} ({c['result']}): win {c['win']:g} \u00b7 draw {c['draw']:g} \u00b7 loss {c['loss']:g}"})
    return rows


def fit_events(events, starts, limit=EVENTS_BYTES):
    # Real key length (event ids are longer than the 32-hex player keys): "key":value plus the comma.
    sizes = {k: len(json.dumps(k)) + 2 + len(json.dumps(v, separators=(',', ':'))) for k, v in events.items()}
    if sum(sizes.values()) <= limit:
        return events
    keep, used = {}, 0
    for k in sorted(events, key=lambda k: (starts.get(k, ''), k)):
        if used + sizes[k] > limit:
            break
        keep[k] = events[k]
        used += sizes[k]
    return keep


def _events(sport, players, matches):
    """research.json events: rows per offered canonical event, from the small files of the research-source phases."""
    from . import expansion_exchanges as exchanges
    directory = boards.root(sport)
    prices = exchanges.load(directory)
    contexts, form = {}, {}
    if sport == 'tennis':
        from .expansion_tennis import load_contexts
        contexts = load_contexts(directory)
    else:
        from .expansion_footballdata import load_form
        form = load_form(directory)
    by_id = {m['event_id']: m for m in matches if m.get('event_id')}
    out, starts = {}, {}
    for entry in players.values():
        eid = (entry.get('identity') or {}).get('event_id')
        if not eid or eid in out:
            continue
        rows = event_rows(sport, eid, by_id.get(eid), prices, contexts, form)
        if rows:
            out[eid] = rows
            starts[eid] = (by_id.get(eid) or {}).get('start') or ''
    return fit_events(out, starts)


def document(sport, players, starts, events, results_at):
    """research.json: players fitted to their own RESEARCH_BYTES (events never shrink it), events to EVENTS_BYTES
    (already fitted by _events), and a last guard that drops events - never players - if the file would still reach
    saved()'s read limit."""
    doc = {'sport': sport, 'at': now_utc(), 'results_at': results_at, 'players': fit(players, starts)}
    if events:
        doc['events'] = events
        if len(json.dumps(doc, separators=(',', ':'))) > READ_LIMIT - 1000:
            del doc['events']
    return doc


def publish(sport, budget_s=20, results_at=None):
    from . import expansion_history as history
    from .expansion import isolated_database
    from .contracts import parse_utc
    from .expansion_formats import context as match_context
    from types import SimpleNamespace
    deadline = time.monotonic() + budget_s
    code, board = boards.board(SimpleNamespace(query={'sport': sport}))
    if code != 200:
        return {'state': 'quotes_unavailable'}
    previous = saved(sport).get('players', {})
    players, unique, fact_cache, catalogs = {}, {}, {}, {}
    for offer in board['offers']:
        unique.setdefault(key(offer), offer)
    # Rotate pending identities without starving previously missing histories.
    # sg-stats component-model offers after every count-model offer: they never take research budget from a line the
    # count model already grades (both groups still rotate least-recently-checked first).
    from .expansion_stats import model_of, history_scope, games as component_games
    order = sorted(unique, key=lambda k: (model_of(unique[k]) is not None, previous.get(k, {}).get('checked_at', '')))
    matches_path = boards.root(sport) / 'current-matches.json'
    try:
        st = matches_path.stat()
        matches = history._matches(str(matches_path), st.st_mtime_ns, st.st_size) if st.st_size <= 3_000_000 else []
    except (OSError, ValueError):
        matches = []
    for k in order:
        if time.monotonic() >= deadline:
            if k in previous:
                players[k] = previous[k]
            continue
        offer = unique[k]
        identity = history.resolve(offer, matches)
        scope = 'regulation' if sport == 'soccer' and offer['scope'] == 'full_game' else offer['scope']
        component = model_of(offer)
        if component:
            scope = history_scope(offer)
        entry = {'identity': identity, 'scope': scope, 'games': [], 'checked_at': now_utc()}
        if identity:
            path = isolated_database(boards.root(sport).parent, identity['competition'])
            before = min(now_utc(), offer['start'])
            before = parse_utc(before).replace(second=0, microsecond=0).isoformat().replace('+00:00', 'Z')
            try:
                comp = identity['competition']
                if component:
                    if comp not in catalogs:
                        catalog = path.parent / 'matches.json'
                        catalogs[comp] = json.loads(catalog.read_text(encoding='utf-8')) if catalog.stat().st_size <= 5_000_000 else {}
                    from contextlib import closing
                    with closing(history.store.connect(path, read_only=True)) as con:
                        entry['games'] = component_games(con, history.store, offer, identity['player_id'], before,
                                                         catalogs[comp])
                else:
                    entry['games'] = history._history(str(path), history._file_signature(path),
                        identity['player_id'], offer['stat'], scope, before)
                if comp not in catalogs:
                    catalog = path.parent / 'matches.json'
                    catalogs[comp] = json.loads(catalog.read_text(encoding='utf-8')) if catalog.stat().st_size <= 5_000_000 else {}
                enriched = []
                for game in entry['games']:
                    past = catalogs[comp].get(game['event_id'], {})
                    team = next((p.get('team_id') for p in past.get('players', []) if p['id'] == identity['player_id']), None)
                    others = [p['name'] for p in past.get('participants', []) if p['id'] != (team or identity['player_id'])]
                    enriched.append({**game, 'opponent': others[0] if len(others) == 1 else '',
                                     'best_of': match_context(comp, past).get('best_of'), 'surface': past.get('surface'),
                                     'match': ' vs '.join(p['name'] for p in past.get('participants', []))})
                entry['games'] = enriched
                match = next((m for m in matches if m['event_id'] == identity['event_id']), {})
                entry['event'] = {k:match.get(k) for k in ('event_id','start','status')}
                entry['context'] = match_context(comp, match)
                from contextlib import closing
                with closing(history.store.connect(path, read_only=True)) as con:
                    contexts = history.store._latest_rows(con, 'context', 'event_id=?', [identity['event_id']], before, 64)
                entry['context_rows'] = []
                for row in contexts:
                    fact = history.store._rec(row)
                    if fact.subject_id != identity['player_id']:
                        continue
                    if fact.kind == 'starter' and fact.present in ('yes', 'no'):
                        entry['context_rows'].append({'label':'Starting lineup', 'value':'Confirmed starter' if fact.present=='yes' else 'Listed substitute',
                                                      'group':'Match', 'source':fact.source, 'known_at':fact.known_at})
                    elif fact.kind == 'role' and fact.detail:
                        entry['role'] = fact.detail
                fk = (comp, identity['player_id'], scope)
                if fk not in fact_cache:
                    with closing(history.store.connect(path, read_only=True)) as con:
                        stats = sorted({r['stat'] for r in history.store._latest_rows(con, 'stat',
                            'player_id=?', [identity['player_id']], before, 2000)})
                    facts = []
                    for stat in stats[:40]:
                        if time.monotonic() >= deadline:
                            break
                        past_games = history._history(str(path), history._file_signature(path), identity['player_id'], stat, scope, before)
                        if past_games:
                            n = min(10, len(past_games))
                            facts.append({'stat': stat, 'average': sum(g['value'] for g in past_games[:n])/n,
                                          'games': n, 'scope': scope})
                    fact_cache[fk] = facts
                entry['stats'] = fact_cache[fk]
            except (OSError, ValueError, history.sqlite3.Error):
                entry['error'] = 'history_unavailable'
        players[k] = entry
    try:
        events = _events(sport, players, matches)
    except (OSError, ValueError, KeyError, TypeError):
        events = {}                                   # research rows never block the player research
    doc = document(sport, players, {k: unique[k].get('start') or '' for k in players}, events,
                   results_at or history.results_at(sport))
    players = doc['players']
    history.atomic_json(boards.root(sport) / 'research.json', doc)
    return {'offers': len(board['offers']), 'markets': len(players),
            'with_history': sum(bool(x['games']) for x in players.values()), 'at': doc['at']}


def attach(sport, data):
    doc = saved(sport)
    players = doc.get('players', {})
    relevant = {key(o) for o in data['offers']}
    research = {k: v for k, v in players.items() if k in relevant}
    from .expansion_forecasts import attach as attach_forecasts
    out = {**data, 'research': research, 'research_at': doc.get('at'), 'results_at': doc.get('results_at')}
    events = doc.get('events') or {}
    if events:                                        # absent when empty: the research=1 bytes stay as before
        ids = {(v.get('identity') or {}).get('event_id') for v in research.values()}
        out['research_events'] = {k: v for k, v in events.items() if k in ids}
    return attach_forecasts(sport, out)
