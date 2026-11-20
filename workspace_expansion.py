"""Soccer/tennis data adapter for Scout's existing board and player drawer.

Uses the existing private Sports connection. No sports imports, model fitting,
provider calls or history database reads occur in the main web process.
"""
import hashlib
import json
import math
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
                ('stat' not in offer or offer.get('variant') == 'standard' or
                 (offer.get('platform') == 'underdog' and offer.get('variant') == 'balanced')) and
                datetime.fromisoformat(offer['start'].replace('Z', '+00:00')).timestamp() > now and
                0 <= now-datetime.fromisoformat(offer['observed_at'].replace('Z', '+00:00')).timestamp() < 300)
    except (KeyError, TypeError, ValueError):
        return False


def _forecast(o, research, forecasts):
    key = hashlib.sha256(json.dumps([o[k] for k in ('sport','platform','offer_id','revision')],
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    f = forecasts.get(key) or {}
    try:
        if (f['identity'] != research['identity'] or
                any(f[k] != o[k] for k in ('sport','stat','scope','line','platform','offer_id','start')) or
                f['offer_revision'] != o['revision'] or f['side'] not in ('over','under') or
                (f['side'] not in o['sides'] and f.get('grade') is not None) or
                not 0 <= time.time()-datetime.fromisoformat(f['made_at'].replace('Z','+00:00')).timestamp() < 86400 or
                datetime.fromisoformat(f['made_at'].replace('Z','+00:00')) >= datetime.fromisoformat(o['start'].replace('Z','+00:00'))):
            return None
        probabilities = [f['p_'+s] for s in ('over','under','push')]
        if (any(not isinstance(p,(float,int)) or not math.isfinite(p) or not 0<=p<=1 for p in probabilities) or
                abs(sum(probabilities)-1)>1e-6 or f['p_push']>=1 or
                not math.isfinite(f['projection']) or f['projection']<0):
            return None
        if abs(f['p_side']-f['p_'+f['side']]/(1-f['p_push']))>1e-6:
            return None
        # soccer-grades-1120 decision 1: the graded side is the higher-chance OFFERED side (on a one-sided line it may be
        # the lower-chance side). Two-sided lines: the same check as before.
        offered = [f['p_'+s] for s in ('over','under') if s in o['sides']] or probabilities[:2]
        if f['p_side']+1e-6 < max(offered)/(1-f['p_push']):
            return None
        return f
    except (KeyError,ValueError,TypeError):
        return None


def member_stake_reason(f):
    """Decision 2 (2026-11-20): the member-visible stake reason is a short, definite fact. The stored forecast keeps its
    full reason (admin / ops / logs); its bytes and id never change for this."""
    if f.get('letter_basis') == 'chance_payout_unknown':
        return 'Stake 0U · payout not published'
    if f.get('thin_model'):
        return 'Stake 0U · under 5 past matches' if f.get('sport') == 'tennis' else 'Stake 0U · under 5 past games'
    if f.get('member_stake_reason'):
        return f['member_stake_reason']
    if f.get('units') is None or f.get('component_model') or f.get('units') == 0:
        return 'Stake 0U · new model'
    return f.get('stake_reason')


def _record(o, research, forecast=None):
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
    record = {'sport': o['sport'], 'key': '|'.join([identity['event_id'], identity['player_id'], o['stat'], period, str(o['line'])]),
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
                    'avg': sum(g['value'] for g in games[:10])/len(games[:10]) if games else None, 'recent': None,
                    'scope': scope, 'scope_label': PERIODS.get(scope, scope)},
        'market_chance': None, 'state': {'kind': 'ok', 'reason': None},
        'research_only': True}
    if forecast:
        f = forecast
        basis = (f"Model estimate from {f['sample']} completed games at the same stat and period and the average of "
                 "similar players. " if f.get('thin_model') or f.get('population_model') else
                 f"Model estimate from {f['sample']} completed games at the same stat and period. ")
        detail = (basis +
                  "The forecast allows for variation between games. Chances are conditional on a non-push result. "
                  "This new model has not yet been calibrated against forward results.")
        record['scout'].update(side=f['side'],read_rev=f['id'],p_side=f['p_side'],
            p_over=f['p_over']/(1-f['p_push']),p_under=f['p_under']/(1-f['p_push']),p_push=f['p_push'],
            projection=f['projection'],range80=f['range80'],grade=f['grade'],
            model_version=f['model_version'],forecast_at=f['made_at'],stake_reason=member_stake_reason(f),
            hold=None if f['grade'] else {'short':'Model estimate','reason':'; '.join(f['grade_reason'])},
            validation={'kind':'model_estimate','label':'Model estimate','detail':detail})
    return record


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
        # Do not publish an empty prop card: a saved, verified exact-stat log is required - or (soccer-grades-1120, every
        # offered prop graded) a current lettered forecast for this exact revision (thin-history / player-only models).
        if not research.get('identity'):
            continue
        forecast = _forecast(o, research, doc.get('forecasts') or {})
        if not research.get('games') and not (forecast and forecast.get('grade')):
            continue
        r = _record(o, research, forecast)
        records.append(r)
        k = (o['platform'], r['offer']['offer_id'])
        index[k] = {'rec': r, 'research': research, 'offer': o, 'forecast': forecast,
                    'events': (doc.get('research_events') or {}).get(research['identity'].get('event_id')) or []}
        pk = (r['event']['id'], r['player']['key'], r['market']['stat'], r['market']['period'])
        props.setdefault(pk, []).append(k)
    feeds = {k: {'state': 'ok' if v.get('ok') else 'failed', 'at': v.get('checked_at'),
                 'error': None if v.get('ok') else 'Source unavailable'} for k, v in doc.get('sources', {}).items()}
    body = json.dumps({'sport': sport, 'records': records, 'feeds': feeds,
        'coverage': {'current_offers': eligible, 'with_history': len(records), 'pending_history': eligible-len(records),
                     # when results were last stored, not when research.json was rebuilt (2026-11-18)
                     'history_at': doc.get('results_at') if 'results_at' in doc else doc.get('research_at')},
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


PRICE_FRESH_S = 15 * 60


def _event_rows(rows, now):
    """Research-only match rows (exchange prices, tennis context, soccer team form) as drawer context rows, weight 0.
    A price is shown only while its own check is under 15 minutes old, with its age; older prices are left out."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get('label') or row.get('value') in (None, ''):
            continue
        item = {'label': row['label'], 'value': row['value'], 'group': row.get('group') or 'Match'}
        if row.get('checked_at') is not None:
            try:
                age = now - datetime.fromisoformat(str(row['checked_at']).replace('Z', '+00:00')).timestamp()
            except ValueError:
                continue
            if not 0 <= age < PRICE_FRESH_S:
                continue
            item['value'] = f"{row['value']} \u00b7 checked {max(1, round(age / 60))} min ago"
        out.append(item)
    return out


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
        if k in ('surface','best_of'):
            context.append({'label': 'Surface' if k=='surface' else 'Match format',
                            'value':value if k=='surface' else f'Best of {value} sets', 'group': 'Match'})
    context.extend(_event_rows(x.get('events'), time.time()))
    for stat in research.get('stats', []):
        context.append({'label': f"{stat['stat'].replace('_', ' ').title()} · last {stat['games']}",
                        'value': round(stat['average'], 2),
                        'group': PERIODS.get(stat['scope'], stat['scope']) + ' averages'})
    pk = (r['event']['id'], r['player']['key'], r['market']['stat'], r['market']['period'])
    books = []
    for k in snap['props'].get(pk, []):
        item = snap['index'][k]['rec']
        books.append({**item['offer'], 'row_id': item['offer']['offer_id'], 'key': item['key'],
                      'line_type': item['market']['line_type'], 'scout_side': item['scout']['side'],
                      'p_side': item['scout']['p_side'], 'grade': item['scout']['grade']})
    if x.get('forecast'):
        f=x['forecast']
        context.extend([{'label':'Forecast sample','value':f"{f['sample']} completed games",'group':'Used in forecast'},
                        {'label':'Prediction method','value':'Game-to-game variation and historical scoring rate','group':'Used in forecast'}])
        context.extend({'label':'Surface' if k=='surface' else 'Match format',
                        'value':v if k=='surface' else f'Best of {v} sets','group':'Used in forecast'}
                       for k,v in f.get('context_used',{}).items())
    return 200, {'record': r, 'forecast': x.get('forecast'), 'history': {'series': games, 'tally': r['history']['l10'],
        'avg': r['history']['avg'], 'scope': research['scope']}, 'context': {'rows': context}, 'books': books,
        'market_books': {'same': [], 'other': []}, 'h2h': None,
        'market': {'available': False, 'reason': 'No verified price for this exact player market.'}}
