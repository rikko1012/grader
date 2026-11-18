"""Tennis-only serve statistics from theScore's public match feed.

Source percentages compare the two opponents in several fields; they are NOT
serve-success probabilities. Only explicitly reported COUNT fields are imported.
The ESPN match/player identity must be independently matched first.
"""
from datetime import timezone
from email.utils import parsedate_to_datetime
import re

from .contracts import StatObservation, utc
from .expansion import Feed, _number

COUNT_STATS = ('aces', 'double_faults', 'breakpoints_won', 'points_won', 'tiebreaks_won')


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
        for name in COUNT_STATS:
            value = _number(stats.get(name))
            if value is None or not value.is_integer():
                continue
            out.records.append(StatObservation(identity['event_id'],tour,found['player_id'],'player','full_game','no',
                name,'count',value,'played','thescore',str(body['id']),True,observed_at,observed_at,
                source_updated_at=source_time(body.get('updated_at'))))
    for item in body.get('tournament_details') or []:
        if item.get('identifier') == 'tournament_details.surface' and item.get('content'):
            match['surface'] = item['content']
            match['surface_source'] = 'thescore'
            match['surface_known_at'] = observed_at
    return out


def collect_details(catalogs, get, persist, directory):
    """Resume a bounded queue through the caller's shared request/time budget.

    Cache only identities and check times, not large headshot/history responses.
    Every stored stat still requires an independently verified ESPN match and
    matching set scores. A source failure cannot erase already collected facts.
    """
    import json
    from datetime import datetime, timedelta
    from .contracts import now_utc, parse_utc
    from .expansion_history import atomic_json, error_label, resolve
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
                if (match.get('status') != 'final' or match.get('is_double') or match.get('canceled')
                        or match.get('loser_withdraw') or not start or start < cutoff
                        or not str(match.get('id', '')).isdigit()
                        or (match.get('league') or {}).get('slug') != row['tour'] or not all(people)):
                    continue
                key = f"{row['tour']}:{match['id']}"
                pending[key] = {**pending.get(key, {}), 'id':match['id'], 'tour':row['tour'],
                                'player':people[0], 'opponent':people[1], 'start':start}
            row['checked_at'] = now_utc()
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
                errors.append(error_label(e))
    except (net.HttpError, ValueError, OSError) as e:
        errors.append(error_label(e))
    finally:
        state['tournaments'] = {k:v for k,v in tournaments.items() if v['end'] >= cutoff}
        state['matches'] = {k:v for k,v in pending.items() if v['start'] >= cutoff}
        atomic_json(path, state)
    return errors
