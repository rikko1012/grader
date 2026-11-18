"""Tennis-only serve statistics from theScore's public match feed.

Source percentages compare the two opponents in several fields; they are NOT
serve-success probabilities. Only explicitly reported COUNT fields are imported
as counts; the serve RATE fields are stored under their own names with unit
'percent' (research only, never forecast inputs).
The ESPN match/player identity must be independently matched first.
"""
from datetime import timezone
from email.utils import parsedate_to_datetime
import re

from .contracts import StatObservation, utc
from .expansion import Feed, _number

COUNT_STATS = ('aces', 'double_faults', 'breakpoints_won', 'points_won', 'tiebreaks_won')
# 2026-11-18, research only (never forecast inputs: expansion_forecasts.FORECAST_INPUTS). New stat names, so no other
# source's rows can disagree with them. The serve fields are RATES (0-100), checked on a real match:
# first_serve_points_won 67 against points_won 58, second_serve_successful 97 - unit 'percent', never counts.
# games_won would collide with ESPN's games_won, so it is never written.
EXTRA_STATS = {'total_breakpoints': ('bp_chances', 'count'), 'max_points_in_a_row': ('max_points_streak', 'count'),
               'max_games_in_a_row': ('max_games_streak', 'count'),
               'first_serve_successful': ('first_serve_in_pct', 'percent'),
               'first_serve_points_won': ('first_serve_won_pct', 'percent'),
               'second_serve_successful': ('second_serve_in_pct', 'percent'),
               'second_serve_points_won': ('second_serve_won_pct', 'percent')}
CONTEXT_MAX = 400


def source_time(value):
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            if not value.endswith('-0000'):
                return None
            parsed = parsed.replace(tzinfo=timezone.utc)
        return utc(parsed)
    except (ValueError, TypeError, OverflowError):
        return None


def names(body):
    result = []
    for i in (1, 2):
        player = (body.get(f'team_{i}') or {}).get('player1') or {}
        result.append(' '.join(str(player.get(k) or '').strip()
                               for k in ('first_name', 'last_name')).strip())
    return result


def parse_match(body, matches, observed_at):
    from .expansion_history import resolve
    out = Feed()
    tour = (body.get('league') or {}).get('slug')
    if tour not in ('atp','wta') or not body.get('id'):
        return out
    if body.get('is_double') or body.get('canceled') or body.get('loser_withdraw'):
        return out
    if body.get('status') != 'final' or re.search(r'\b(retired|retirement|walkover|abandoned|defaulted)\b', str(body.get('description') or ''), re.I):
        return out
    start = source_time(body.get('match_date'))
    people = names(body)
    if not start or not all(people) or people[0] == people[1]:
        return out
    offer = {'sport':'tennis','tour':tour,'player':people[0],'opponent':people[1],'start':start}
    identity = resolve(offer,matches)
    if not identity:
        return out
    match = next(m for m in matches if m['event_id']==identity['event_id'])
    if match.get('status') != 'final':
        return out
    scores = [body.get(f'team_{i}_scores') or [] for i in (1,2)]
    if not scores[0] or len(scores[0]) != len(scores[1]):
        return out
    expected = match.get('set_scores')
    # Matching names alone cannot attach a result from another round or a badly
    # linked feed event. Require each player's exact ordered set scores as well.
    if not expected:
        return out
    observed_at = utc(observed_at)
    for i, person in enumerate(people):
        found = resolve({**offer, 'player':person, 'opponent':people[1-i]}, [match])
        if not found or expected.get(found['player_id']) != scores[i]:
            return Feed()
    for i, person in enumerate(people):
        found = resolve({**offer,'player':person,'opponent':people[1-i]},[match])
        stats = body.get(f'team_{i+1}_stats') or {}
        for name, (stat, unit) in [(n, (n, 'count')) for n in COUNT_STATS] + list(EXTRA_STATS.items()):
            value = _number(stats.get(name))
            if value is None or not value.is_integer() or (unit == 'percent' and value > 100):
                continue
            out.records.append(StatObservation(identity['event_id'],tour,found['player_id'],'player','full_game','no',
                stat,unit,value,'played','thescore',str(body['id']),True,observed_at,observed_at,
                source_updated_at=source_time(body.get('updated_at'))))
    for item in body.get('tournament_details') or []:
        if item.get('identifier') == 'tournament_details.surface' and item.get('content'):
            match['surface'] = item['content']
            match['surface_source'] = 'thescore'
            match['surface_known_at'] = observed_at
    return out


def _pair_event(people, start, catalogs, tour):
    """The ESPN match for an exact player pair within 2 h, unique, else None. -> (match, {name: player_id})."""
    from .contracts import parse_utc
    from .expansion_history import normalized
    want = {normalized(n) for n in people}
    if len(want) != 2 or not start:
        return None
    found = []
    for m in catalogs.get(tour, {}).values():
        ids = {normalized(p['name']): p['id'] for p in m.get('participants') or []}
        if set(ids) == want and len(ids) == 2 and m.get('start') and abs(
                (parse_utc(m['start']) - parse_utc(start)).total_seconds()) <= 7200:
            found.append((m, {n: ids[normalized(n)] for n in people}))
    return found[0] if len(found) == 1 else None


def prematch(body, catalogs, observed_at):
    """Pre-match context for one upcoming theScore singles match joined to its scheduled ESPN event (exact pair,
    2 h, unique). Facts as the source lists them at fetch time: known_at = fetch time. No odds (theScore names no book).
    Never written into the catalog (surface / format stay ESPN + tournament_details only). -> (event id, entry) or None."""
    tour = (body.get('league') or {}).get('slug')
    if (tour not in ('atp', 'wta') or body.get('status') != 'pre_game' or body.get('is_double')
            or body.get('canceled') or not str(body.get('id', '')).isdigit()):
        return None
    people = names(body)
    start = source_time(body.get('match_date'))
    if not all(people) or people[0] == people[1]:
        return None
    joined = _pair_event(people, start, catalogs, tour)
    if not joined or joined[0].get('status') != 'scheduled':
        return None
    match, ids = joined
    comparison = body.get('teams_comparison') or {}
    players = {}
    for i, person in enumerate(people, 1):
        row = {'name': person}
        standing = body.get(f'team_{i}_standing') or {}
        if isinstance(standing.get('place'), int) and standing['place'] > 0:
            row['rank'] = standing['place']
            if isinstance(standing.get('points'), int):
                row['points'] = standing['points']
        if isinstance(body.get(f'team_{i}_seed'), int):
            row['seed'] = body[f'team_{i}_seed']
        side = comparison.get(f'team_{i}') or {}
        for key, label in (('head_to_head', 'h2h'), ('last_10_matches_wins_losses', 'last10'), ('streak', 'streak'),
                           ('wins_losses', 'season')):
            if isinstance(side.get(key), str) and re.fullmatch(r'\d+-\d+|[WL]\d+', side[key]):
                row[label] = side[key]
        for key, value in side.items():
            surface = re.fullmatch(r'(head_to_head|wins_losses)_([a-z]+)', key)
            if surface and isinstance(value, str) and re.fullmatch(r'\d+-\d+', value):
                row[('h2h_' if surface[1] == 'head_to_head' else 'season_') + 'surface'] = value
                row['surface'] = surface[2]
        players[ids[person]] = row
    entry = {'event_id': match['event_id'], 'start': match['start'], 'tour': tour, 'source': 'thescore',
             'source_match_id': str(body['id']), 'known_at': utc(observed_at), 'players': players}
    if body.get('round_name'):
        entry['round'] = str(body['round_name'])
    if body.get('court_name'):
        entry['court'] = str(body['court_name'])
    return match['event_id'], entry


def offered_events(directory, catalogs):
    """ESPN tennis event ids that a current board offer resolves to (the existing exact resolve())."""
    import json
    from .expansion_history import resolve
    try:
        snapshot = directory / 'board.json'
        offers = (json.loads(snapshot.read_text(encoding='utf-8')).get('offers', [])
                  if snapshot.stat().st_size <= 3_000_000 else [])
    except (OSError, ValueError):
        return set()
    matches = [m for rows in catalogs.values() for m in rows.values() if m.get('status') == 'scheduled']
    found = set()
    for offer in offers:
        try:
            ident = resolve(offer, matches)
        except (KeyError, ValueError, TypeError):
            continue
        if ident:
            found.add(ident['event_id'])
    return found


def load_contexts(directory):
    import json
    try:
        doc = json.loads((directory / 'match-context.json').read_text(encoding='utf-8'))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def save_contexts(directory, contexts, now):
    """Bounded match-context.json: upcoming (and up to 6 h old) entries, soonest first, at most CONTEXT_MAX."""
    from datetime import timedelta
    from .expansion_history import atomic_json
    floor = utc(now - timedelta(hours=6))
    keep = sorted((v for v in contexts.values() if (v.get('start') or '') >= floor), key=lambda v: v['start'])
    atomic_json(directory / 'match-context.json', {v['event_id']: v for v in keep[:CONTEXT_MAX]})


def collect_details(catalogs, get, persist, directory):
    """Resume a bounded queue through the caller's request/time budget.

    Cache only identities and check times, not large headshot/history responses.
    Every stored stat still requires an independently verified ESPN match and
    matching set scores. A source failure cannot erase already collected facts.
    """
    import json
    from datetime import datetime, timedelta
    from .contracts import now_utc, parse_utc
    from .expansion_history import atomic_json, resolve
    from . import net
    path = directory / 'serve-history-state.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    now = datetime.now(timezone.utc)
    cutoff = utc(now - timedelta(days=100))
    tournaments = state.get('tournaments', {})
    pending = state.get('matches', {})
    upcoming = state.get('upcoming', {})
    contexts = load_contexts(directory)
    errors = []
    def due(checked, seconds):
        return not checked or (now - parse_utc(checked)).total_seconds() >= seconds
    try:
        for tour in ('atp', 'wta'):
            if due(state.get(tour + '_checked_at'), 21600):
                body = get(f'https://api.thescore.com/{tour}/events', {})
                if not isinstance(body, list):
                    raise ValueError('unexpected tennis tournament response')
                for row in body:
                    end = source_time(row.get('end_date'))
                    start = source_time(row.get('start_date'))
                    if ((row.get('league') or {}).get('slug') != tour or not str(row.get('id', '')).isdigit()
                            or not start or not end or end < cutoff or start > utc(now + timedelta(days=3))
                            or 'singles' not in str(row.get('match_type')).lower()):
                        continue
                    key = f"{tour}:{row['id']}"
                    tournaments[key] = {**tournaments.get(key, {}), 'tour':tour, 'id':row['id'], 'start':start, 'end':end}
                state[tour + '_checked_at'] = now_utc()
        # Two tournament lists per batch reserve most requests for actual stats.
        todo = [t for t in tournaments.values() if due(t.get('checked_at'), 1800 if t['end'] >= utc(now) else 86400)]
        todo.sort(key=lambda t:(t.get('checked_at') or '', -parse_utc(t['end']).timestamp()))
        for row in todo[:2]:
            body = get(f"https://api.thescore.com/{row['tour']}/events/{row['id']}/matches", {})
            if not isinstance(body, list):
                raise ValueError('unexpected tennis matches response')
            for match in body:
                people = names(match)
                start = source_time(match.get('match_date'))
                if (match.get('status') == 'pre_game' and not match.get('is_double') and all(people) and start
                        and str(match.get('id', '')).isdigit() and start <= utc(now + timedelta(days=4))
                        and (match.get('league') or {}).get('slug') == row['tour']):
                    key = f"{row['tour']}:{match['id']}"
                    upcoming[key] = {**upcoming.get(key, {}), 'id': match['id'], 'tour': row['tour'],
                                     'player': people[0], 'opponent': people[1], 'start': start}
                if (match.get('status') != 'final' or match.get('is_double') or match.get('canceled')
                        or match.get('loser_withdraw') or not start or start < cutoff
                        or not str(match.get('id', '')).isdigit()
                        or (match.get('league') or {}).get('slug') != row['tour'] or not all(people)):
                    continue
                key = f"{row['tour']}:{match['id']}"
                pending[key] = {**pending.get(key, {}), 'id':match['id'], 'tour':row['tour'],
                                'player':people[0], 'opponent':people[1], 'start':start}
            row['checked_at'] = now_utc()
        # Pre-match context for OFFERED upcoming matches first (at most 4 details a run, each re-read every 6 h),
        # so a long finals backlog on a new store cannot starve it; the finals stats take the rest of the budget.
        offered = offered_events(directory, catalogs)
        todo = []
        for m in upcoming.values():
            joined = _pair_event([m['player'], m['opponent']], m['start'], catalogs, m['tour'])
            if (joined and joined[0]['event_id'] in offered and m['start'] > utc(now)
                    and due(m.get('checked_at'), 21600) and due(m.get('attempt_at'), 1800)):
                todo.append(m)
        todo.sort(key=lambda m: (m.get('checked_at') or '', m['start']))
        for row in todo[:4]:
            try:
                row['attempt_at'] = now_utc()
                body = get(f"https://api.thescore.com/{row['tour']}/matches/{row['id']}", {})
                if str(body.get('id')) != str(row['id']) or (body.get('league') or {}).get('slug') != row['tour']:
                    raise ValueError('tennis detail identity mismatch')
                found = prematch(body, catalogs, now_utc())
                if found:
                    contexts[found[0]] = found[1]
                row['checked_at'] = now_utc()
            except (net.HttpError, ValueError, OSError) as e:
                errors.append(type(e).__name__)
        todo = [m for m in pending.values() if due(m.get('checked_at'), 86400) and due(m.get('attempt_at'), 1800)
                and resolve({**m, 'sport':'tennis'}, list(catalogs.get(m['tour'], {}).values()))]
        todo.sort(key=lambda m:(m.get('checked_at') or '', -parse_utc(m['start']).timestamp()))
        for row in todo:
            try:
                body = get(f"https://api.thescore.com/{row['tour']}/matches/{row['id']}", {})
                row['attempt_at'] = now_utc()
                if str(body.get('id')) != str(row['id']) or (body.get('league') or {}).get('slug') != row['tour']:
                    raise ValueError('tennis detail identity mismatch')
                feed = parse_match(body, list(catalogs.get(row['tour'], {}).values()), now_utc())
                persist(feed, row['tour'])
                row['checked_at'] = now_utc()
                row['stored_stats'] = len(feed.records)
            except (net.HttpError, ValueError, OSError) as e:
                row['attempt_at'] = now_utc()
                errors.append(type(e).__name__)
    except (net.HttpError, ValueError, OSError) as e:
        errors.append(type(e).__name__)
    finally:
        state['tournaments'] = {k:v for k,v in tournaments.items() if v['end'] >= cutoff}
        state['matches'] = {k:v for k,v in pending.items() if v['start'] >= cutoff}
        state['upcoming'] = {k:v for k,v in upcoming.items() if v['start'] >= utc(now - timedelta(hours=6))}
        atomic_json(path, state)
        save_contexts(directory, contexts, now)
    return errors
