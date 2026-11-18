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


def publish(sport, budget_s=20):
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
    order = sorted(unique, key=lambda k: previous.get(k, {}).get('checked_at', ''))
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
        entry = {'identity': identity, 'scope': scope, 'games': [], 'checked_at': now_utc()}
        if identity:
            path = isolated_database(boards.root(sport).parent, identity['competition'])
            before = min(now_utc(), offer['start'])
            before = parse_utc(before).replace(second=0, microsecond=0).isoformat().replace('+00:00', 'Z')
            try:
                entry['games'] = history._history(str(path), history._file_signature(path),
                    identity['player_id'], offer['stat'], scope, before)
                comp = identity['competition']
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
    doc = {'sport': sport, 'at': now_utc(), 'results_at': history.results_at(sport), 'players': players}
    history.atomic_json(boards.root(sport) / 'research.json', doc)
    return {'offers': len(board['offers']), 'markets': len(players),
            'with_history': sum(bool(x['games']) for x in players.values()), 'at': doc['at']}


def attach(sport, data):
    doc = saved(sport)
    players = doc.get('players', {})
    relevant = {key(o) for o in data['offers']}
    from .expansion_forecasts import attach as attach_forecasts
    return attach_forecasts(sport, {**data, 'research': {k: v for k, v in players.items() if k in relevant},
            'research_at': doc.get('at'), 'results_at': doc.get('results_at')})
