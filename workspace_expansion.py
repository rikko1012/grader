"""Soccer/tennis data adapter for Scout's existing board and player drawer.

Uses the existing private Sports connection. No sports imports, model fitting,
provider calls or history database reads occur in the main web process.
"""
import hashlib
import json
import time
from datetime import datetime, timezone

import workspace_sports as remote

SPORTS = ('soccer', 'tennis')
PERIODS = {'full_game': 'Full match', 'regulation': 'Regulation time', 'set1': 'First set',
           'first_half': 'First half', 'second_half': 'Second half'}
_SNAP = {}


def reset():
    _SNAP.clear()


def _key(offer):
    return hashlib.sha256(json.dumps([offer[k] for k in
        ('sport', 'platform', 'event_id', 'player_id', 'stat', 'scope')],
        separators=(',', ':')).encode()).hexdigest()[:32]


def _tally(games, line, n):
    window = games[:n]
    return {'n': len(window), 'over': sum(g['value'] > line for g in window),
            'under': sum(g['value'] < line for g in window), 'push': sum(g['value'] == line for g in window)}


def _eligible(offer, sport, now):
    try:
        return (offer['sport'] == sport and
                datetime.fromisoformat(offer['start'].replace('Z', '+00:00')).timestamp() > now and
                0 <= now-datetime.fromisoformat(offer['observed_at'].replace('Z', '+00:00')).timestamp() < 300)
    except (KeyError, TypeError, ValueError):
        return False


def _record(o, research):
    identity = research['identity']
    games = research['games'][:20]
    rid = 'e-' + hashlib.sha256(str(o['offer_id']).encode()).hexdigest()[:24]
    def side(name):
        value = o['sides'].get(name)
        return {'offered': value is not None, 'payout': value.get('multiplier') if value else None, 'price': None}
    period = o['scope']
    scope = research['scope']
    context_note = ('Historical soccer results cover regulation time. The app’s full-match settlement rules still apply.'
                    if scope != period else 'Historical results at this line, not a predicted chance.')
    return {'sport': o['sport'], 'key': '|'.join([identity['event_id'], identity['player_id'], o['stat'], period, str(o['line'])]),
        'event': {'id': identity['event_id'], 'id_kind': 'canonical', 'label': o['match'],
                  'teams': [v for v in (o.get('team'), o.get('opponent')) if v],
                  'competition': identity['competition'], 'best_of': research.get('context', {}).get('best_of'),
                  'start_utc': o['start'], 'lock_utc': o['start'], 'status': 'scheduled'},
        'player': {'id': identity['player_id'], 'key': identity['player_id'], 'name': o['player'],
                   'team': o.get('team') or '', 'opponent': o.get('opponent') or '', 'role': research.get('role') or o.get('role') or ''},
        'market': {'stat': o['stat'], 'stat_label': o['label'], 'period': period,
                   'period_label': PERIODS.get(period, period), 'line_type': o.get('variant') or 'standard'},
        'offer': {'platform': o['platform'], 'platform_label': remote.LABEL.get(o['platform'], o['platform']),
                  'offer_id': rid, 'revision': o['revision'], 'revision_kind': 'exact', 'line': o['line'],
                  'seen_at': o['observed_at'], 'over': side('over'), 'under': side('under'),
                  'moved_from': None, 'moved_at': None},
        'scout': {'side': None, 'read_rev': 'history-only', 'p_side': None, 'p_over': None, 'p_under': None,
                  'p_push': None, 'projection': None, 'range80': None, 'grade': None, 'units': None,
                  'stake_reason': None, 'model_version': None, 'forecast_at': None,
                  'hold': {'short': 'Player research', 'reason': 'Verified game history. Scout projections are not released.'},
                  'validation': {'kind': 'history', 'label': 'Player research', 'detail': context_note}},
        'history': {**{f'l{n}': _tally(games, o['line'], n) for n in (5, 10, 20)},
                    'avg': sum(g['value'] for g in games[:10])/len(games[:10]), 'recent': None,
                    'scope': scope, 'scope_label': PERIODS.get(scope, scope)},
        'market_chance': None, 'state': {'kind': 'ok', 'reason': None},
        'research_only': True}


def _build(srv, sport):
    doc, _ = remote.call(srv, '/api/sports/expansion/board', {'sport': sport, 'research': '1'})
    if doc.get('sport') != sport:
        raise remote.Down('wrong sport')
    records, index, props = [], {}, {}
    eligible = 0
    now = time.time()
    for o in doc.get('offers', []):
        if not _eligible(o, sport, now):
            continue
        eligible += 1
        research = (doc.get('research') or {}).get(_key(o)) or {}
        # Do not publish an empty prop card. A saved, verified exact-stat log is required.
        if not research.get('identity') or not research.get('games'):
            continue
        r = _record(o, research)
        records.append(r)
        k = (o['platform'], r['offer']['offer_id'])
        index[k] = {'rec': r, 'research': research, 'offer': o}
        pk = (r['event']['id'], r['player']['key'], r['market']['stat'], r['market']['period'])
        props.setdefault(pk, []).append(k)
    feeds = {k: {'state': 'ok' if v.get('ok') else 'failed', 'at': v.get('checked_at'),
                 'error': None if v.get('ok') else 'Source unavailable'} for k, v in doc.get('sources', {}).items()}
    body = json.dumps({'sport': sport, 'records': records, 'feeds': feeds,
        'coverage': {'current_offers': eligible, 'with_history': len(records), 'pending_history': eligible-len(records),
                     'history_at': doc.get('research_at')},
        'platforms': {k: {'label': v, 'kind': 'pickem'} for k, v in remote.LABEL.items()}}, separators=(',', ':')).encode()
    _SNAP[sport] = {'body': body, 'index': index, 'props': props, 'at': now}
    return 200, body


def snapshot(srv, sport):
    if sport not in SPORTS:
        return None
    try:
        srv.shared(('ws_expansion', sport), lambda: _build(srv, sport), ttl=20)
    except Exception:
        # A failed read must not keep a once-fresh quote tradable or silently current.
        return None
    return _SNAP.get(sport)


def line_doc(srv, sport, book, rid):
    snap = _SNAP.get(sport) or {}
    x = snap.get('index', {}).get((book, rid))
    if not x or not _eligible(x['offer'], sport, time.time()):
        return 404, {'error': 'offer_unavailable'}
    r, research = x['rec'], x['research']
    games = [{**g, 'opponent': g.get('opponent') or '', 'date': g['date'][:10]} for g in research['games']]
    context = [{'label': 'History period', 'value': PERIODS.get(research['scope'], research['scope']), 'group': 'Game log'},
               {'label': 'Verified results', 'value': len(games), 'group': 'Game log'},
               {'label': 'Sources', 'value': ', '.join(sorted({g['source'] for g in games})), 'group': 'Game log'}]
    if r['player']['role']:
        context.append({'label': 'Role', 'value': r['player']['role'], 'group': 'Player'})
    context.extend(research.get('context_rows') or [])
    for k, value in research.get('context', {}).items():
        context.append({'label': k.replace('_', ' ').title(), 'value': value, 'group': 'Match'})
    for stat in research.get('stats', []):
        context.append({'label': f"{stat['stat'].replace('_', ' ').title()} · last {stat['games']}",
                        'value': round(stat['average'], 2),
                        'group': PERIODS.get(stat['scope'], stat['scope']) + ' averages'})
    pk = (r['event']['id'], r['player']['key'], r['market']['stat'], r['market']['period'])
    books = []
    for k in snap['props'].get(pk, []):
        item = snap['index'][k]['rec']
        books.append({**item['offer'], 'row_id': item['offer']['offer_id'], 'key': item['key'],
                      'line_type': item['market']['line_type'], 'scout_side': None, 'p_side': None, 'grade': None})
    return 200, {'record': r, 'forecast': None, 'history': {'series': games, 'tally': r['history']['l10'],
        'avg': r['history']['avg'], 'scope': research['scope']}, 'context': {'rows': context}, 'books': books,
        'market_books': {'same': [], 'other': []}, 'h2h': None,
        'market': {'available': False, 'reason': 'No verified price for this exact player market.'}}
