"""Polymarket tennis match-winner prices: a price REFERENCE only, never a forecast or grading input.

Read 2026-11-18 (Gamma API, public, no key; two real answers trimmed into sports/tests/fixtures/polymarket_tennis.json):
  series 10365 "atp" carries the ATP Finals and the Challengers, 10366 "wta" the WTA events; doubles are other
  series and are never requested. One event per match: teams[2] with full names, startTime = the match start,
  endDate 4.0-7.2 days AFTER the match (so the window is on endDate and the start is filtered here), and one
  `moneyline` market whose outcomes are the two names and whose bestBid / bestAsk price the FIRST outcome.
  closed=false still returns live and finished matches (live / ended flags, startTime in the past).
Only the moneyline is stored. Set, total and handicap markets are left out (many sit at 1c / 99c); a market type
never seen before (e.g. tennis_completed_match, absent from all 80 events read) is listed in `unparsed_types`.

Identity is the same rule as the Kalshi rows (expansion_kalshi): Scout's own fixture in current-matches.json, exact
normalized full names on both sides, scheduled and not started, one fixture per event and one event per fixture -
plus Polymarket's start within MAX_START_GAP_S of the fixture's. Anything else is dropped and counted, never guessed.
The row's start is the fixture's.

Rows go beside Kalshi's under the snapshot key `reference_prices` (quotes.db platform `ref:polymarket`), never into
offers or game_odds. Polymarket cannot be traded from the US; only its public prices are read.
"""
from datetime import datetime, timedelta, timezone
import json
import os
import time
from urllib.parse import urlsplit

from . import net
from .contracts import now_utc
from .expansion_history import normalized
from .expansion_kalshi import HOLD_403_S, HOLD_429_S, HORIZON_DAYS, _catalogue, _instant, _open, _tennis_order
from .expansion_offers import finish, number, timestamp
from .polymarket import API, MAX_SPREAD, _time

HOST = urlsplit(API).hostname
SERIES = {'tennis': {'atp': {'id': '10365', 'competitions': ('atp', 'atp_challenger')},
                     'wta': {'id': '10366', 'competitions': ('wta', 'wta125')}}}
# Every market type in the 80 events read; only the moneyline is stored. Anything else is reported, never parsed.
SEEN_TYPES = {'moneyline', 'tennis_first_set_winner', 'tennis_set_winner', 'tennis_set_totals', 'tennis_match_totals',
              'tennis_set_handicap', 'tennis_game_handicap'}
PAGE = 25                 # ~1.1 MB a page as sent (a 100-event page answered HTTP 500 once)
MAX_REQUESTS = 6          # per quotes run
WALL_S = 18               # the whole Polymarket step
MAX_BYTES = 2_000_000     # a larger answer is refused before it is parsed
MAX_ROWS = 150
MAX_START_GAP_S = 7200    # same bound as expansion_history.resolve
MIN_LIQUIDITY = 500.0     # resting order-book dollars; below this a midpoint is one maker's quote
END_WINDOW_DAYS = (2, 9)  # endDate window that holds the matches starting in the next ~2 days
JURISDICTION = 'Not tradable from the US - public prices only'
RULES = 'polymarket_rules: retirement -> advancing player; walkover or cancellation -> 50-50; reference only'
_KEEP = ('slug', 'sportsMarketType', 'bestBid', 'bestAsk', 'outcomes', 'active', 'closed', 'acceptingOrders',
         'gameStartTime', 'liquidityNum')


def switched_on(sport):
    return sport in SERIES and os.environ.get(f'SCOUT_{sport.upper()}_POLYMARKET') != '0'


def trim(event):
    """The few fields the parser reads (an event is ~45 KB as sent, ~0.6 KB kept)."""
    markets = [m for m in event.get('markets') or [] if isinstance(m, dict)]
    return {'slug': event.get('slug'), 'title': event.get('title'), 'startTime': event.get('startTime'),
            'live': event.get('live'), 'ended': event.get('ended'),
            'league': (event.get('eventMetadata') or {}).get('league'),
            'teams': [t.get('name') for t in event.get('teams') or [] if isinstance(t, dict)],
            'moneyline': [{k: m.get(k) for k in _KEEP} for m in markets if m.get('sportsMarketType') == 'moneyline'],
            'types': sorted({str(m.get('sportsMarketType')) for m in markets})}


def _legs(event):
    """([name, name] in outcome order, moneyline market) of one singles match, or None."""
    names, markets = event.get('teams'), event.get('moneyline')
    if (not isinstance(names, list) or len(names) != 2 or not all(isinstance(n, str) and n.strip() for n in names)
            or not isinstance(markets, list) or len(markets) != 1 or not event.get('slug')):
        return None
    market = markets[0]
    try:
        outcomes = json.loads(market.get('outcomes') or '[]')
    except (ValueError, TypeError):
        return None
    text = f"{event.get('title') or ''} {event.get('league') or ''}".casefold()
    if (not isinstance(outcomes, list) or sorted(map(str, outcomes)) != sorted(names) or names[0] == names[1]
            or normalized(names[0]) == normalized(names[1]) or any('/' in n for n in names) or 'doubles' in text
            or market.get('active') is not True or market.get('closed') is not False
            or market.get('acceptingOrders') is False or not market.get('slug')):
        return None
    return [str(o) for o in outcomes], market


def _quote(market):
    """(bid, ask) of the FIRST outcome; 'wide' over MAX_SPREAD, 'thin' under MIN_LIQUIDITY, None when not a quote."""
    bid, ask, depth = number(market.get('bestBid')), number(market.get('bestAsk')), number(market.get('liquidityNum'))
    if bid is None or ask is None or not 0 < bid < ask < 1:
        return None
    if round(ask - bid, 4) > MAX_SPREAD:
        return 'wide'
    return (bid, ask) if depth is not None and depth >= MIN_LIQUIDITY else 'thin'


def parse(events, tour, fixtures, observed):
    """Trimmed events of one series -> (reference rows, counts)."""
    now_text = timestamp(observed)
    now = _instant(now_text)
    if now is None or tour not in SERIES['tennis']:
        raise ValueError('tour and observation time required')
    candidates = [f for c in SERIES['tennis'][tour]['competitions'] for f in _open(fixtures, c, now)]
    stats = {'events': 0, 'matched': 0, 'started': 0, 'dropped_invalid': 0, 'dropped_wide': 0, 'dropped_thin': 0,
             'ambiguous': 0, 'unmatched_count': 0, 'unmatched': [], 'unparsed_types': []}
    found, claims, unknown = [], {}, set()
    for event in events or []:
        if not isinstance(event, dict):
            continue
        stats['events'] += 1
        unknown.update(t for t in event.get('types') or [] if t not in SEEN_TYPES)
        legs, start = _legs(event), _instant(timestamp(event.get('startTime')))
        if not legs or start is None or _instant(_time(legs[1].get('gameStartTime'))) != start:
            stats['dropped_invalid'] += 1
            continue
        if start <= now or event.get('live') is True or event.get('ended') is True:
            stats['started'] += 1
            continue
        names, market = legs
        pairs = [(n, market) for n in names]
        hits = []
        for fixture in candidates:
            order = (_tennis_order(pairs, fixture, {})
                     if abs(_instant(fixture['start']) - start) <= MAX_START_GAP_S else None)
            if order:
                hits.append((fixture, order))
        if len(hits) > 1:
            # One match catalogued under two levels (a WTA 125 also listed as WTA): the tournament name decides,
            # and only by equality.
            named = [h for h in hits if normalized(h[0].get('name')) == normalized(event.get('league')) != '']
            hits = named if len(named) == 1 else hits
        for fixture, _ in hits:
            claims[fixture['event_id']] = claims.get(fixture['event_id'], 0) + 1
        found.append((event, names, market, hits))
    rows = []
    for event, names, market, hits in found:
        if not hits:
            stats['unmatched_count'] += 1
            if len(stats['unmatched']) < 10:
                stats['unmatched'].append(' vs '.join(names)[:80])
            continue
        if len(hits) != 1 or claims[hits[0][0]['event_id']] != 1:
            stats['ambiguous'] += 1
            continue
        quote = _quote(market)
        if not isinstance(quote, tuple):
            stats['dropped_invalid' if quote is None else f'dropped_{quote}'] += 1
            continue
        bid, ask = quote
        fixture, order = hits[0]
        outcomes = []
        for participant, index in zip(fixture['participants'], order):
            # bestBid / bestAsk price the first outcome; the second is its complement.
            b, a = (bid, ask) if index == 0 else (round(1 - ask, 4), round(1 - bid, 4))
            outcomes.append({'selection_id': f"{market['slug']}:{index}", 'label': participant['name'],
                             'participant_id': participant['id'], 'bid': b, 'ask': a, 'mid': round((b + a) / 2, 4)})
        teams = [p['name'] for p in fixture['participants']]
        row = finish({'sport': 'tennis', 'platform': 'polymarket', 'source': 'gamma', 'jurisdiction': JURISDICTION,
            'offer_id': str(market['slug']), 'event_id': fixture['event_id'], 'source_event': str(event['slug']),
            'match': ' vs '.join(teams), 'teams': teams, 'competition': fixture['competition'],
            'start': timestamp(fixture['start']), 'label': 'Match winner', 'scope': 'full_game',
            'market_type': 'Exchange match', 'main': True, 'outcomes': outcomes, 'observed_at': now_text,
            'identity_status': 'scout_fixture_exact', 'settlement_rules': RULES, 'reference_only': True})
        # Resting liquidity and Polymarket's own start move without a price change: kept on the row, outside the
        # revision, so quotes.db grows only when the bid or ask really changes.
        row.update(liquidity=round(number(market.get('liquidityNum'))), source_start=timestamp(event.get('startTime')),
                   tournament=str(event.get('league') or '')[:80] or None)
        rows.append(row)
    stats['matched'] = len(rows)
    stats['unparsed_types'] = sorted(unknown)[:10]
    return rows, stats


def _stamp(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def collect(sport, get, directory):
    """-> (reference rows, source status). At most MAX_REQUESTS paced requests inside WALL_S, none for a tour with
    no scheduled fixture in the catalogue. Any refusal ends the source for this run; a 429 or 403 becomes a persisted
    hold that later runs honour without sending anything. Nothing is carried forward from an earlier run."""
    from . import expansion_sources as sources
    observed = now_utc()
    status = {'ok': True, 'reference': True, 'checked_at': observed, 'markets': 0}
    now = time.time()
    fixtures = [f for f in _catalogue(directory) if f.get('status') == 'scheduled'
                and now < (_instant(f.get('start')) or 0) <= now + HORIZON_DAYS * 86400]
    wanted = [(tour, spec) for tour, spec in SERIES.get(sport, {}).items()
              if any(f.get('competition') in spec['competitions'] for f in fixtures)]
    if not wanted:
        return [], {**status, 'skipped': 'no_fixture'}
    until = sources.held_until(sport, HOST)
    if until - now > 3:                           # the 1 s pacing gap is waited out by net; a real hold is not
        return [], {**status, 'ok': False, 'error': 'held', 'held_until': net._iso(until)}
    moment = datetime.fromtimestamp(now, timezone.utc)
    window = {'end_date_min': _stamp(moment + timedelta(days=END_WINDOW_DAYS[0])),
              'end_date_max': _stamp(moment + timedelta(days=END_WINDOW_DAYS[1]))}
    rows, deadline, requests = [], time.monotonic() + WALL_S, 0
    totals = {'events': 0, 'matched': 0, 'started': 0, 'dropped_invalid': 0, 'dropped_wide': 0, 'dropped_thin': 0,
              'ambiguous': 0, 'unmatched_count': 0, 'unmatched': [], 'unparsed_types': []}
    for tour, spec in wanted:
        events, seen, offset, more = [], set(), 0, True
        while more and requests < MAX_REQUESTS and time.monotonic() < deadline:
            requests += 1
            try:
                body = get(API + '/events', params={'series_id': spec['id'], 'closed': 'false', 'order': 'endDate',
                                                    'ascending': 'true', 'limit': PAGE, 'offset': offset, **window},
                           tries=1, timeout=max(1.0, min(12.0, deadline - time.monotonic())))
                if not isinstance(body, list):
                    raise ValueError('unexpected Polymarket answer')
            except net.HttpError as e:
                status.update(ok=False, error='held' if e.held else f'http_{e.status}' if e.status else 'transport')
                stop = (e.not_before if e.held else max(e.not_before or 0, time.time() + HOLD_429_S)
                        if e.status == 429 else time.time() + HOLD_403_S if e.status == 403 else None)
                if stop:
                    sources.hold(sport, HOST, stop, shared=e.status != 403)
                    status['held_until'] = net._iso(stop)
                break
            except (ValueError, OSError) as e:
                status.update(ok=False, error=type(e).__name__)
                break
            more, offset = len(body) >= PAGE, offset + PAGE
            for event in body:                    # trimmed page by page: the full answer is never kept
                if isinstance(event, dict) and event.get('slug') not in seen:
                    seen.add(event.get('slug'))
                    events.append(trim(event))
            del body
        if more and status['ok']:
            status['truncated'] = True            # the request or time budget ended the paging
        if events:
            found, stats = parse(events, tour, fixtures, now_utc())
            rows.extend(found)
            for key, value in stats.items():
                totals[key] = (sorted(set(totals[key] + value))[:10] if key == 'unparsed_types' else
                               (totals[key] + value)[:10] if key == 'unmatched' else totals[key] + value)
        del events
        if not status['ok']:
            break
    rows.sort(key=lambda r: (r['start'], r['offer_id']))
    rows = rows[:MAX_ROWS]
    return rows, {**status, **totals, 'checked_at': now_utc(), 'markets': len(rows), 'requests': requests}
