"""Kalshi match-winner prices for soccer and tennis: a price REFERENCE only, never a forecast or grading input.

Read 2026-11-18 (two real answers, recorded as sports/tests/fixtures/kalshi_*_events.json):
  KXEPLGAME   one event per match with exactly three binary markets (team, team, "Tie"), settled after 90 minutes
              plus stoppage time - a regulation-time 3-way.
  KXATPMATCH  one event per singles match with two binary markets, one per player (full name in yes_sub_title).
A series with verified False has never been read; it is not requested until a real answer is recorded as a fixture.

Identity is Scout's own ESPN fixture (<expansion>/<sport>/current-matches.json): exact normalized names, both sides,
one fixture per event and one event per fixture. Anything else is dropped and counted - never guessed. Kalshi's
abbreviations are never used (its CFC is Chelsea, ESPN's CFC is Coritiba) and its times are never used as a start:
the ATP events read on 2026-11-18 carried ticker date Nov 17 while ESPN had the same pairs scheduled Nov 19-20, so
the ticker date is only a sanity window and the row's start is always ESPN's.

Rows are stored under the snapshot key `reference_prices` (quotes.db platform `ref:kalshi`), not `game_odds`: every
game_odds reader assumes American odds, and a separate key keeps these rows out of forecasts and learning.
"""
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from . import net
from .contracts import now_utc, parse_utc
from .expansion_history import normalized
from .expansion_offers import finish, number, timestamp
from .kalshi import API, MAX_SPREAD, event_date

HOST = urlsplit(API).hostname
SERIES = {
    'soccer': {'KXEPLGAME': {'competition': 'eng.1', 'market': 'match_3way', 'verified': True}},
    'tennis': {'KXATPMATCH': {'competition': 'atp', 'market': 'match_winner', 'verified': True},
               # Never read: tickers, liveness and payload shape are unknown, so these are never requested.
               'KXWTAMATCH': {'competition': 'wta', 'market': 'match_winner', 'verified': False},
               'KXATPCHALLENGERMATCH': {'competition': 'atp_challenger', 'market': 'match_winner', 'verified': False},
               'KXWTACHALLENGERMATCH': {'competition': 'wta125', 'market': 'match_winner', 'verified': False}},
}
REGULATION = 'after 90 minutes plus stoppage time'
WINDOW = {'soccer': (-1, 1), 'tennis': (-1, 4)}   # ESPN start date minus Kalshi ticker date, in days
MAX_REQUESTS = 3          # per quotes run, per sport
WALL_S = 12               # the whole Kalshi step
MAX_BYTES = 2_000_000     # a larger answer is refused before it is parsed
MAX_ROWS = 150
HORIZON_DAYS = 8          # no scheduled fixture for the series' competition this soon: the series is not requested
HOLD_429_S = 15 * 60
HOLD_403_S = 6 * 3600
DESCRIPTION = 'Exchange bid/ask midpoint · price reference, not used in grades'


def switched_on(sport):
    return os.environ.get(f'SCOUT_{sport.upper()}_KALSHI') != '0'


def aliases(sport, competition):
    """Hand-checked Kalshi name -> ESPN name. Never generated."""
    try:
        doc = json.loads((Path(__file__).parent / 'kalshi_expansion_aliases.json').read_text(encoding='utf-8'))
        return {normalized(k): normalized(v) for k, v in doc[sport][competition].items()}
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return {}


def _instant(value):
    try:
        return parse_utc(value).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def _open(fixtures, competition, now):
    """This competition's fixtures that have not started: scheduled, two different named participants."""
    out = []
    for f in fixtures or []:
        start, parts = _instant(timestamp(f.get('start'))), f.get('participants') or []
        if (f.get('competition') != competition or f.get('status') != 'scheduled' or start is None or start <= now
                or not f.get('event_id') or len(parts) != 2 or not all(p.get('id') and p.get('name') for p in parts)
                or parts[0]['id'] == parts[1]['id']):
            continue
        out.append(f)
    return out


def _quote(market):
    """(bid, ask) of one YES contract, 'wide' when the spread is over MAX_SPREAD, None when it is not a real quote."""
    bid, ask = number(market.get('yes_bid_dollars')), number(market.get('yes_ask_dollars'))
    if bid is None or ask is None or not 0 < bid < ask < 1:
        return None
    return 'wide' if round(ask - bid, 4) > MAX_SPREAD else (bid, ask)


def _markets(event, count):
    markets = event.get('markets')
    if (not isinstance(markets, list) or len(markets) != count or not all(isinstance(m, dict) for m in markets)
            or any(m.get('status') != 'active' or m.get('market_type') != 'binary' or not m.get('ticker')
                   for m in markets)
            or len({m['ticker'] for m in markets}) != count or not event.get('event_ticker')):
        return None
    return markets


def _soccer_legs(event):
    """[(team name, market), (None, tie market), (team name, market)] in title order, or None."""
    markets = _markets(event, 3)
    halves = [x.strip() for x in str(event.get('title') or '').split(' vs ')]
    if not markets or len(halves) != 2 or not all(halves) or halves[0] == halves[1] or 'Tie' in halves:
        return None
    is_tie = lambda m: m.get('yes_sub_title') == 'Tie' or str(m['ticker']).endswith('-TIE')
    ties = [m for m in markets if m.get('yes_sub_title') == 'Tie' and str(m['ticker']).endswith('-TIE')]
    teams = {m.get('yes_sub_title'): m for m in markets if not is_tie(m)}
    if (len(ties) != 1 or sum(1 for m in markets if is_tie(m)) != 1 or set(teams) != set(halves)
            or not all(REGULATION in str(m.get('rules_primary') or '') for m in markets)):
        return None
    return [(halves[0], teams[halves[0]]), (None, ties[0]), (halves[1], teams[halves[1]])]


def _tennis_legs(event):
    """[(full name, market), (full name, market)], or None. Doubles never pass."""
    markets = _markets(event, 2)
    if not markets:
        return None
    names = [str(m.get('yes_sub_title') or '').strip() for m in markets]
    text = ' '.join([str(event.get('title') or ''), str((event.get('product_metadata') or {}).get('competition') or '')]
                    + [str(m.get('rules_primary') or '') for m in markets]).casefold()
    if (not all(names) or normalized(names[0]) == normalized(names[1]) or any('/' in n for n in names)
            or '/' in str(event.get('title') or '') or 'doubles' in text):
        return None
    return list(zip(names, markets))


def _soccer_order(legs, fixture, alias):
    """Kalshi leg index per fixture participant, or None: each team equals exactly one participant's full or short
    name (or its hand-checked alias), and the two teams are the two different participants. Never the abbreviation."""
    def same(team, participant):
        mine = {normalized(team), alias.get(normalized(team))} - {'', None}
        return bool(mine & ({normalized(participant.get('name')), normalized(participant.get('short_name'))} - {''}))
    a, b = fixture['participants']
    first, second = legs[0][0], legs[2][0]
    hits = (same(first, a), same(first, b), same(second, a), same(second, b))
    if hits == (True, False, False, True):
        return [0, 2]
    if hits == (False, True, True, False):
        return [2, 0]
    return None


def _tennis_order(legs, fixture, alias):
    mine = [normalized(name) for name, _ in legs]
    theirs = [normalized(p.get('name')) for p in fixture['participants']]
    if theirs[0] == theirs[1] or sorted(mine) != sorted(theirs):
        return None
    return [mine.index(theirs[0]), mine.index(theirs[1])]


def _home_first(fixture):
    """ESPN names its soccer fixtures '<away> at <home>'; fall back to the catalogue's own order."""
    a, b = fixture['participants']
    return [1, 0] if fixture.get('name') == f"{a['name']} at {b['name']}" else [0, 1]


def _parse(sport, events, competition, fixtures, alias, observed):
    now_text = timestamp(observed)
    now = _instant(now_text)
    if now is None or sport not in SERIES:
        raise ValueError('sport and observation time required')
    legs_of, order_of = (_soccer_legs, _soccer_order) if sport == 'soccer' else (_tennis_legs, _tennis_order)
    low, high = WINDOW[sport]
    candidates = _open(fixtures, competition, now)
    stats = {'events': 0, 'matched': 0, 'dropped_invalid': 0, 'dropped_wide': 0, 'ambiguous': 0, 'unmatched': []}
    found, claims = [], {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        stats['events'] += 1
        legs, day = legs_of(event), event_date(event.get('event_ticker'))
        if not legs or not day:
            stats['dropped_invalid'] += 1
            continue
        day = date.fromisoformat(day)
        hits = []
        for fixture in candidates:
            gap = (datetime.fromtimestamp(_instant(fixture['start']), timezone.utc).date() - day).days
            order = order_of(legs, fixture, alias) if low <= gap <= high else None
            if order:
                hits.append((fixture, order))
        for fixture, _ in hits:
            claims[fixture['event_id']] = claims.get(fixture['event_id'], 0) + 1
        found.append((event, legs, hits))
    rows = []
    for event, legs, hits in found:
        if not hits:
            if len(stats['unmatched']) < 10:
                stats['unmatched'].append(str(event.get('title'))[:80])
            continue
        # One fixture per event AND one event per fixture; a rematch or duplicate listing drops everything involved.
        if len(hits) != 1 or any(claims[f['event_id']] != 1 for f, _ in hits):
            stats['ambiguous'] += 1
            continue
        quotes = [_quote(m) for _, m in legs]
        if None in quotes:
            stats['dropped_invalid'] += 1
            continue
        if 'wide' in quotes:                      # never a partial 3-way
            stats['dropped_wide'] += 1
            continue
        fixture, order = hits[0]
        parts = fixture['participants']
        side = _home_first(fixture) if sport == 'soccer' else [0, 1]
        picks = [(parts[i], order[i]) for i in side]
        if sport == 'soccer':
            picks.insert(1, (None, 1))
        outcomes, extras = [], []
        for participant, i in picks:
            bid, ask = quotes[i]
            market = legs[i][1]
            outcomes.append({'selection_id': str(market['ticker']), 'label': participant['name'] if participant else 'Draw',
                             'participant_id': participant['id'] if participant else None,
                             'bid': bid, 'ask': ask, 'mid': round((bid + ask) / 2, 4)})
            extras.append({'volume': number(market.get('volume_fp')),
                           'source_updated_at': timestamp(market.get('updated_time'))})
        teams = [p['name'] for p, _ in picks if p]
        row = finish({'sport': sport, 'platform': 'kalshi', 'source': 'kalshi', 'jurisdiction': 'US exchange',
            'offer_id': str(event['event_ticker']), 'event_id': fixture['event_id'], 'match': ' vs '.join(teams),
            'teams': teams, 'competition': competition, 'start': timestamp(fixture['start']),
            'label': 'Match result (3-way, 90 minutes)' if sport == 'soccer' else 'Match winner',
            'scope': 'regulation' if sport == 'soccer' else 'full_game', 'market_type': 'Exchange match', 'main': True,
            'outcomes': outcomes, 'observed_at': now_text, 'identity_status': 'scout_fixture_exact',
            'settlement_rules': 'exchange_native_reference_only', 'reference_only': True})
        # Volume and the exchange's own update time move without a price change: kept on the row, outside the
        # revision, so quotes.db grows only when a bid or ask really changes.
        for outcome, extra in zip(outcomes, extras):
            outcome.update(extra)
        rows.append(row)
    stats['matched'] = len(rows)
    return rows, stats


def parse_soccer(events, competition, fixtures, alias, observed):
    return _parse('soccer', events, competition, fixtures, alias, observed)


def parse_tennis(events, competition, fixtures, observed):
    return _parse('tennis', events, competition, fixtures, {}, observed)


def _catalogue(directory):
    path = Path(directory) / 'current-matches.json'
    try:
        if path.stat().st_size > 3_000_000:
            return []
        doc = json.loads(path.read_text(encoding='utf-8'))
        return [m for m in doc if isinstance(m, dict)] if isinstance(doc, list) else []
    except (OSError, ValueError):
        return []


def collect(sport, get, directory):
    """-> (reference rows, source status). At most MAX_REQUESTS paced requests inside WALL_S; a 429 or 403 becomes a
    persisted hold that the next runs honour without sending anything. A failed run returns no rows: nothing is
    carried forward from an earlier run."""
    from . import expansion_sources as sources
    observed = now_utc()
    status = {'ok': True, 'reference': True, 'checked_at': observed, 'markets': 0}
    now = time.time()
    fixtures = [f for f in _catalogue(directory) if f.get('status') == 'scheduled'
                and now < (_instant(f.get('start')) or 0) <= now + HORIZON_DAYS * 86400]
    wanted = [(ticker, spec) for ticker, spec in SERIES.get(sport, {}).items()
              if spec.get('verified') and any(f.get('competition') == spec['competition'] for f in fixtures)]
    if not wanted:
        return [], {**status, 'skipped': 'no_fixture'}
    until = sources.held_until(sport, HOST)
    if until - now > 3:                           # the 2 s pacing mark is waited out by net; a real hold is not
        return [], {**status, 'ok': False, 'error': 'held', 'held_until': net._iso(until)}
    rows, deadline, requests = [], time.monotonic() + WALL_S, 0
    totals = {'events': 0, 'matched': 0, 'dropped_invalid': 0, 'dropped_wide': 0, 'ambiguous': 0, 'unmatched': []}
    for ticker, spec in wanted:
        events, cursor = [], None
        while requests < MAX_REQUESTS and time.monotonic() < deadline:
            params = {'series_ticker': ticker, 'status': 'open', 'with_nested_markets': 'true', 'limit': 100}
            if cursor:
                params['cursor'] = cursor
            requests += 1
            try:
                body = get(API + '/events', params=params, tries=1,
                           timeout=max(1.0, min(10.0, deadline - time.monotonic())))
                if not isinstance(body, dict) or not isinstance(body.get('events') or [], list):
                    raise ValueError('unexpected Kalshi answer')
            except net.HttpError as e:
                status.update(ok=False, error='held' if e.held else f'http_{e.status}' if e.status else 'transport')
                stop = (e.not_before if e.held else max(e.not_before or 0, time.time() + HOLD_429_S)
                        if e.status == 429 else time.time() + HOLD_403_S if e.status == 403 else None)
                if stop:
                    sources.hold(sport, HOST, stop)
                    status['held_until'] = net._iso(stop)
                break
            except (ValueError, OSError) as e:
                status.update(ok=False, error=type(e).__name__)
                break
            finally:
                sources.hold(sport, HOST, time.time() + 2)   # the other sport's job is a separate process
            events.extend(body.get('events') or [])
            cursor = body.get('cursor')
            del body
            if not cursor:
                break
        if cursor:
            status['truncated'] = True
        if events:
            found, stats = _parse(sport, events, spec['competition'], fixtures,
                                  aliases(sport, spec['competition']), now_utc())
            rows.extend(found)
            for key, value in stats.items():
                totals[key] = (totals[key] + value)[:10] if key == 'unmatched' else totals[key] + value
        del events
        if not status['ok']:
            break
    rows.sort(key=lambda r: (r['start'], r['offer_id']))
    rows = rows[:MAX_ROWS]
    return rows, {**status, **totals, 'checked_at': now_utc(), 'markets': len(rows), 'requests': requests}
