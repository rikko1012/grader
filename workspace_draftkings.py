"""ESPN DraftKings reference lines in the existing board, never executable prices."""
import json
import math
import time

import workspace_books as wb
import workspace_game_books


def build(srv, sport, now=None):
    now = time.time() if now is None else now
    if sport not in ('nba', 'nfl'):
        return 400, {'error': 'unsupported sport'}
    recs, _ = wb.records(srv, sport)
    if recs is None:
        return 503, {'error': 'board_unavailable'}
    status, body = workspace_game_books.shared(srv, sport)
    if status != 200:
        return status, {'error': 'draftkings_unavailable'}
    doc = json.loads(body)
    if doc.get('league') != sport:
        return 503, {'error': 'snapshot_identity'}
    index = {}
    for r in recs:
        key = (r['event']['id'], r['player']['id'], r['market']['stat'], wb._per(r['market']['period']))
        index.setdefault(key, []).append(r)
    out, seen, stale, newest = [], set(), 0, None
    for e in doc.get('events', []):
        start = wb._ts(e.get('start_utc'))
        if start is None or start <= now or not str(e.get('id', '')).startswith('espn:'):
            continue
        for p in e.get('reference_props', []):
            at = wb._ts(p.get('seen_at'))
            if doc.get('stale') or at is None or not 0 <= now-at <= 5400:
                stale += 1
                continue
            key = (sport+':'+e['id'], f"{sport}:player:espn:{p.get('player_id')}", p.get('market'), wb._per(p.get('period')))
            bases = index.get(key)
            if not bases or p.get('kind') not in ('line', 'milestone'):
                continue
            try:
                line = float(p.get('line'))
            except (ValueError, TypeError):
                continue
            if not math.isfinite(line) or line < 0:
                continue
            identity = (key, line, p['kind'])
            if line is None or identity in seen:
                continue
            seen.add(identity)
            unknown = {'offered': False, 'price': None, 'payout': None}
            r = wb.derive('draftkings', 'DraftKings', 'sportsbook', sport, bases, line,
                          unknown, unknown, None, at, identity)
            # A saved estimate may describe this threshold, but the source cannot
            # identify an executable side. Never inherit an actionable grade.
            r['scout'].update(grade=None, units=None, side=None, p_side=None, hold=None)
            r['market_chance'] = None
            r['offer'].update(reported_prices=p.get('reported_prices') or [],
                              price_side_known=False, reference_kind=p['kind'],
                              revision=str(p.get('revision_id') or r['offer']['revision']))
            r['extra'].update(reference_only=True, price_comparable=False, source='ESPN-supplied DraftKings')
            out.append(r)
            newest = max(newest or at, at)
    return 200, {'sport': sport, 'book': 'draftkings', 'label': 'DraftKings', 'kind': 'sportsbook',
                 'records': out, 'feed': {'state': 'ok' if out else 'stale' if stale else 'empty',
                                        'at': wb._iso(newest), 'stale_after_s': 5400},
                 'coverage': {'matched': len(out), 'stale': stale},
                 'platforms': {'draftkings': {'label': 'DraftKings', 'short': 'DK', 'slip': False,
                                             'kind': 'sportsbook', 'price_comparable': False}}}
