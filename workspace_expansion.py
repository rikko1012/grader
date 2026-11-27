"""Soccer/tennis data adapter for Grader's existing board and player drawer.

Uses the existing private Sports connection. No sports imports, model fitting,
provider calls or history database reads occur in the main web process.
"""
import hashlib
import json
import math
import time
from datetime import datetime, timezone

import expansion_leagues
import workspace_sports as remote

SPORTS = ('soccer', 'tennis')
Down = remote.Down
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


PUBLICATION_FRESH_S = 86400      # a forecasts.json publication older than this is not read (the publisher stopped)


def _forecast(o, research, forecasts, published_at=None):
    """The saved forecast of this exact offer revision, or None. A frozen forecast keeps its first-issue made_at for as long as
    its offer revision is unchanged (sports/expansion_forecasts.publish republishes it), so its age is not a freshness test:
    sweep 2026-11-23 ST-1 - letters disappeared 24 h after first issue even before kickoff. The publication's own time
    (forecasts_at, when the sports service sends it) is; made_at must still be in the past and before the start."""
    key = hashlib.sha256(json.dumps([o[k] for k in ('sport','platform','offer_id','revision')],
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    f = forecasts.get(key) or {}
    try:
        if (f['identity'] != research['identity'] or
                any(f[k] != o[k] for k in ('sport','stat','scope','line','platform','offer_id','start')) or
                f['offer_revision'] != o['revision'] or f['side'] not in ('over','under') or
                (f['side'] not in o['sides'] and f.get('grade') is not None) or
                not 0 <= time.time()-datetime.fromisoformat(f['made_at'].replace('Z','+00:00')).timestamp() or
                (published_at is not None and
                 not 0 <= time.time()-datetime.fromisoformat(str(published_at).replace('Z','+00:00')).timestamp() < PUBLICATION_FRESH_S) or
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


def member_stake_reason(f, saved_games=None):
    """Decision 2 (2026-11-20): the member-visible stake reason is a short, definite fact. The stored forecast keeps its
    full reason (admin / ops / logs); its bytes and id never change for this. "under 5 past games" only when the card's
    own saved game log really holds fewer than 5 (a thin model can also run on weight-0 / other-format history)."""
    if f.get('letter_basis') == 'chance_payout_unknown':
        return 'Stake 0U · payout not published'
    if f.get('thin_model') and (saved_games is None or saved_games < 5):
        return 'Stake 0U · under 5 past matches' if f.get('sport') == 'tennis' else 'Stake 0U · under 5 past games'
    if f.get('thin_model'):
        return 'Stake 0U · new model'
    units = f.get('units')
    if isinstance(units, (int, float)) and units > 0:
        return f'Stake {units:g}U'
    # never the stored text (it carries test / process detail for admin and logs only)
    return 'Stake 0U · new model'


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
                  'competition': identity['competition'], 'competition_label': expansion_leagues.label(identity['competition']),
                  'best_of': research.get('context', {}).get('best_of'),
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
                  'hold': {'short': 'Player research', 'reason': 'Verified game history. Grader projections are not released.'},
                  'validation': {'kind': 'history', 'label': 'Player research', 'detail': context_note}},
        'history': {**{f'l{n}': _tally(games, o['line'], n) for n in (5, 10, 20)},
                    'avg': sum(g['value'] for g in games[:10])/len(games[:10]) if games else None, 'recent': None,
                    'scope': scope, 'scope_label': PERIODS.get(scope, scope)},
        'market_chance': None, 'state': {'kind': 'ok', 'reason': None},
        'research_only': True}
    if forecast:
        f = forecast
        unit = 'matches' if o['sport'] == 'tennis' else 'games'
        basis = (f"Model estimate from {f['sample']} completed {unit} at the same stat and period and the average of "
                 "similar players. " if f.get('thin_model') or f.get('population_model') else
                 f"Model estimate from {f['sample']} completed {unit} at the same stat and period. ")
        detail = (basis +
                  f"The forecast allows for variation between {unit}. Chances are conditional on a non-push result.")
        record['scout'].update(side=f['side'],read_rev=f['id'],p_side=f['p_side'],
            p_over=f['p_over']/(1-f['p_push']),p_under=f['p_under']/(1-f['p_push']),p_push=f['p_push'],
            projection=f['projection'],range80=f['range80'],grade=f['grade'],
            model_version=f['model_version'],forecast_at=f['made_at'],stake_reason=member_stake_reason(f, len(games)),
            units=0.0 if f['grade'] else None,   # every soccer / tennis stake is 0U today: the card says so, never a dash
            # no letter: a short fact, never the stored grade_reason (test / process detail stays in admin and logs)
            hold=None if f['grade'] else {'short':'Model estimate','reason':'No grade for this line.'},
            validation={'kind':'model_estimate','label':'Model estimate','detail':detail})
        # owner 2026-11-26: a line with a saved forecast can go on a slip (recheck, track and settle: check_leg / settle below);
        # research_only stays for the history notes the card shows. Not a line whose book total may include extra time: its review
        # can fall back to the 90-minute value (expansion_forecasts settle_scopes), which is not what the app settles.
        record['slip_ok'] = f.get('settlement_scope') != 'regulation_plus_extra_time'
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
        forecast = _forecast(o, research, doc.get('forecasts') or {}, doc.get('forecasts_at'))
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
    # v3 card pictures (img_index.py): the sports service's per-player PrizePicks picture map, kept beside the board (never in it)
    images = doc.get('images') if isinstance(doc.get('images'), dict) else {}
    _SNAP[sport] = {'body': body, 'index': index, 'props': props, 'at': now, 'images': images, 'feeds': feeds}   # feeds: slip pool + recheck
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
        # the board record's own unit (tennis counts matches; sweep 2026-11-23 ST-3: the card said "games" for tennis)
        tennis = r['sport'] == 'tennis'
        context.extend([{'label':'Forecast sample','value':f"{f['sample']} completed {'matches' if tennis else 'games'}",'group':'Used in forecast'},
                        {'label':'Prediction method','value':('Match-to-match' if tennis else 'Game-to-game')+' variation and historical scoring rate','group':'Used in forecast'}])
        context.extend({'label':'Surface' if k=='surface' else 'Match format',
                        'value':v if k=='surface' else f'Best of {v} sets','group':'Used in forecast'}
                       for k,v in f.get('context_used',{}).items())
    return 200, {'record': r, 'forecast': x.get('forecast'), 'history': {'series': games, 'tally': r['history']['l10'],
        'avg': r['history']['avg'], 'scope': research['scope']}, 'context': {'rows': context}, 'books': books,
        'market_books': {'same': [], 'other': []}, 'h2h': None,
        'market': {'available': False, 'reason': 'No verified price for this exact player market.'}}


# ---------------------------------------------------------------------------------------------------------------- slips
# owner 2026-11-26 "add soccer & tennis to the auto builder": a soccer / tennis line with a saved forecast goes on a slip like an
# NBA / NFL one - rechecked against this snapshot (exact offer revision, line, offered side, both sides offered, the forecast the
# member saw), tracked with its forecast id, and settled from that forecast's own review on the sports service
# (sports/expansion_settle.py). Every stake stays 0U; no EV is rated (workspace_rules.EV_NOT_VALIDATED).

SETTLE_IDS = 40                         # forecast ids per settle read (64-hex each: a short GET)
SETTLE_CALLS = 6                        # reads per sport per settler pass: 6 x 40 covers a whole pass (2 tiers x workspace_api.LEG_PAGE)


def slip_team(rec):
    """The team the entry rules and the slip pool read: a tennis player is their own side of the match (the feed gives no team)."""
    p = rec.get('player') or {}
    if rec.get('sport') == 'tennis':
        return p.get('team') or p.get('name') or p.get('key') or ''
    return p.get('team') or ''


def _side_p(rec, side):
    sc = rec['scout']
    return sc.get('p_over') if side == 'over' else sc.get('p_under')


def check_leg(srv, leg, sport, now):
    """One soccer / tennis leg against the current snapshot. -> {ok, problems, current} plus what the entry needs."""
    from workspace_api import SIDE_WORD, _n, _pr, DISPLAY_MAX_GAP
    snap = snapshot(srv, sport)
    book, side = leg['book'], leg['side']
    if snap is None:
        return {'ok': False, 'problems': [_pr(1, 'retry', 'Grader could not check this line just now.', retry=True)], 'current': None}
    x = snap['index'].get((book, leg['row_id']))
    if x is None:
        return {'ok': False, 'problems': [_pr(1, 'gone', 'This line is no longer on the board.')], 'current': None}
    if book not in remote.PICKEM:
        return {'ok': False, 'problems': [_pr(1, 'gone', 'Slips cover PrizePicks and Underdog.')], 'current': None}
    r, f = x['rec'], x.get('forecast')
    o, sc, label = r['offer'], r['scout'], remote.LABEL.get(book, book)
    problems = []
    if (snap['feeds'].get(book) or {}).get('state') != 'ok' or now.timestamp() - snap['at'] > 120:
        problems.append(_pr(1, 'board_failed', f'Grader could not read the {sport} board just now.', retry=True))
    lock = remote._ts(r['event'].get('lock_utc') or r['event'].get('start_utc'))
    if lock is None:
        problems.append(_pr(0, 'start_unknown', 'No start time on the feed for this line.'))
    elif lock <= now:
        problems.append(_pr(0, 'locked', 'Match started.'))
    if o['revision'] != leg.get('rev') or float(leg['line']) != float(o['line']):
        moved = float(leg['line']) != float(o['line'])
        problems.append(_pr(1, 'moved', f"Line moved from {_n(leg['line'])} to {_n(o['line'])}." if moved else f'This offer changed on {label}.', swap=True))
    if not o[side]['offered']:
        problems.append(_pr(1, 'side', f'{SIDE_WORD[side]} is not offered on {label}.'))
    if not (o['over']['offered'] and o['under']['offered']):       # owner 2026-11-06: one-sided lines are never on a slip
        only = 'Over only' if o['over']['offered'] else 'Under only'
        problems.append(_pr(1, 'not_gradable', f'{only}: Grader grades lines that offer both sides.'))
    now_p = _side_p(r, side)
    if not f or sc.get('read_rev') in (None, 'history-only') or now_p is None:
        problems.append(_pr(2, 'no_forecast', 'No saved forecast for this line, so it cannot be tracked.'))
    elif not r.get('slip_ok'):
        problems.append(_pr(1, 'not_gradable', 'Extra time may count on this line, so Grader cannot settle it like the app.'))
    else:
        shown_p = leg.get('shown_p')
        if leg.get('read_rev') != sc['read_rev'] or shown_p is None or abs(shown_p - now_p) > DISPLAY_MAX_GAP:
            problems.append(_pr(2, 'display_mismatch', f"Grader's read changed: now {SIDE_WORD[side]} {now_p * 100:.1f}%.", swap=True))
    team = slip_team(r)
    return {'ok': not problems, 'problems': problems,
            'current': {'row_id': o['offer_id'], 'line': o['line'], 'rev': o['revision'], 'read_rev': sc.get('read_rev'), 'scout_side': sc.get('side'),
                        'p_side': now_p, 'forecast_id': f['id'] if f else None, 'forecast_at': sc.get('forecast_at'),
                        'model_version': sc.get('model_version'), 'start_utc': r['event'].get('start_utc'), 'lock_utc': r['event'].get('lock_utc')},
            '_x': x,
            '_ident': {'nick': r['player']['key'], 'prop': r['market']['stat'], 'scope': r['market']['period'], 'match_slug': r['event']['id'],
                       'match_key': r['event']['id'], 'platform': book, 'line': o['line'], 'side': side},
            '_rule': {'sport': sport, 'book': book, 'team': f'{sport}:{team}' if team else '', 'nick': r['player']['name'], 'side': side,
                      'event': f"{sport}|{r['event']['id']}"},
            '_ev': {'name': r['player']['name'], 'sport': sport, 'match': r['event']['id'], 'line': o['line'], 'multiplier': o[side].get('payout'),
                    'on_side': side == sc.get('side'), 'p': now_p, 'tag': sc.get('model_version'), 'prop': r['market']['stat'],
                    'scope': r['market']['period']}}


def payload(x, leg, e):
    """What the member saw, frozen: the letter only on its published side, stakes 0."""
    r = x['rec']
    sc, side = r['scout'], leg['side']
    on = side == sc.get('side')
    grade = sc.get('grade') if on else None
    p = _side_p(r, side)
    return {'player': r['player']['name'], 'team': r['player']['team'], 'opponent': r['player']['opponent'], 'matchup': r['event']['label'],
            'competition': r['event'].get('competition_label') or r['event']['competition'], 'stat_label': r['market']['stat_label'],
            'period_label': r['market']['period_label'], 'platform_label': r['offer']['platform_label'], 'scout_side': sc.get('side'), 'on_side': on,
            'displayed_p': p, 'grade': grade, 'units': 0 if grade else None, 'stake_reason': sc.get('stake_reason') if grade else None,
            'research': True, 'hold': None, 'projection': sc.get('projection'), 'forecast_p': p, 'forecast_at': sc.get('forecast_at'),
            'forecast_grade': grade, 'forecast_units': 0 if grade else None, 'l10': r['history']['l10'], 'feed_at': r['offer']['seen_at'],
            'rules_version': e.get('rules_version'), 'entry_type': e.get('entry_type')}


def receipt_fields(x, leg, sport, res, e):
    r, f = x['rec'], x['forecast']
    o, side = r['offer'], leg['side']
    return {'sport': sport, 'row_id': o['offer_id'], 'offer_rev': o['revision'], 'side': side, 'line': o['line'],
            'line_type': r['market'].get('line_type') or 'standard', 'pick_id': None, 'forecast_id': f['id'], 'match_slug': r['event']['id'],
            'match_id': r['event']['id'], 'match_key': r['event']['id'], 'nick': r['player']['key'], 'prop': r['market']['stat'],
            'scope': r['market']['period'], 'start_utc': r['event'].get('lock_utc') or r['event'].get('start_utc'),
            'model_version': r['scout'].get('model_version'), 'payload': payload(x, leg, e),
            'settle_key': f"{sport}|{f['id']}|{side}|{o['line']}",
            'settle_ref': {'sport': sport, 'forecast_id': f['id'], 'platform': o['platform'], 'offer_id': x['offer']['offer_id'],
                           'side': side, 'line': o['line']}}


def entry_link(x, side):
    return None                                  # the soccer / tennis feeds carry no per-line Underdog link


def entry_projection(x, side):
    o = x['offer']
    if o.get('platform') != 'prizepicks':
        return None
    return {'book': 'prizepicks', 'projection_id': str(o.get('offer_id')), 'side': side, 'line': o.get('line'),
            'odds_type': o.get('variant') or 'standard'}


def leg_result(actual, side, line):
    if actual == line:
        return 'push'
    return 'hit' if (actual > line if side == 'over' else actual < line) else 'miss'


def settle(srv, sport, rows, now=None):
    """Each due leg's result from its own forecast's latest review, at the leg's own line and side. No review or no actual (did not
    play, retired, conflicting sources) = no answer: the leg stays pending. remote.Down raises: every result stays as it was."""
    now = now or datetime.now(timezone.utc)
    due = {}
    for w in rows:
        t, ref = remote._ts(w.get('start_utc')), w.get('ref') or {}
        if t is not None and t <= now and ref.get('forecast_id') and ref.get('side') in ('over', 'under'):
            due.setdefault(ref['forecast_id'], []).append(w)
    out, ids = {}, list(due)               # page order: the open tier first (workspace_store._settle_sport)
    for i in range(0, min(len(ids), SETTLE_IDS * SETTLE_CALLS), SETTLE_IDS):
        part = ids[i:i + SETTLE_IDS]
        doc, _ = remote.call(srv, '/api/sports/expansion/settle', {'sport': sport, 'ids': ','.join(part)})
        got = doc.get('results') or {}
        for fid in part:
            a = got.get(fid)
            void = bool(a and a.get('void'))
            if not a or (not void and (isinstance(a.get('actual'), bool) or not isinstance(a.get('actual'), (int, float)))):
                continue
            for w in due[fid]:
                line, side = w['ref'].get('line'), w['ref']['side']
                if isinstance(line, bool) or not isinstance(line, (int, float)) or a.get('line') != line:
                    continue                     # the forecast was made at another line: never settle a leg at a line it did not hold
                res = 'void' if void else leg_result(a['actual'], side, line)   # void: every source says the player did not play
                out[w['settle_key']] = {'result': res, 'actual': a['actual'], 'source': 'grader-review', 'settled_at': a.get('known_at'),
                                        'final': True, 'mark': remote._hash(res, a['actual'], a.get('review_id'))}
    return out


def record(srv, sport):
    """The sports service's settled chance bands for one sport, in the Results-file shape slip_quality.sport_bands_from_results reads."""
    doc, _ = remote.call(srv, '/api/sports/expansion/record', {'sport': sport})
    return {'groups': {'chance': [{'name': b.get('name'), 'wins': b.get('wins'), 'losses': b.get('losses'), 'printed': b.get('printed')}
                                  for b in doc.get('bands') or []]}}
