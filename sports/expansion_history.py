"""Bounded, isolated soccer / tennis history ingestion and exact-match research.

Nothing in this module publishes probabilities or feeds existing sport models.
Historical downloads are known when collected, never backdated to match day.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unicodedata

from . import expansion_board as boards, net, store
from .contracts import now_utc, parse_utc
from .expansion import COMPETITIONS, isolated_database, parse_scoreboard
from .expansion_soccer import parse_summary


def normalized(value):
    # Full names only: accent/case/punctuation equivalence, never initials,
    # substring guesses or fuzzy matching to a more famous player.
    return ' '.join(re.sub(r'[^\w\s]', ' ', ''.join(c for c in unicodedata.normalize(
        'NFKD', str(value or '')) if not unicodedata.combining(c))).casefold().split())


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='history-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, separators=(',', ':'), allow_nan=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def soccer_competition(event):
    found = set()
    for link in event.get('links') or []:
        match = re.search(r'/league/([a-z0-9.]+)(?:[/?#]|$)', link.get('href') or '')
        if match and COMPETITIONS.get(match[1], (None,))[0] == 'soccer':
            found.add(match[1])
    return next(iter(found)) if len(found) == 1 else None


def roster_identity(summary, match):
    """Match-specific roster facts, not inferred starts or historical membership."""
    competition = (summary.get('header', {}).get('league') or {}).get('slug')
    if competition != match['competition']:
        return []
    event_id = match['event_id'].split(':')[-2]
    if str(summary.get('header', {}).get('id')) != event_id:
        return []
    teams = {p['id'].split(':')[-1] for p in match['participants']}
    rows = []
    for roster in summary.get('rosters') or []:
        team = str((roster.get('team') or {}).get('id') or '')
        if team not in teams:
            continue
        for item in roster.get('roster') or []:
            p = item.get('athlete') or {}
            if p.get('id') and p.get('displayName'):
                rows.append({'id': f"soccer:player:espn:{p['id']}", 'name': p['displayName'],
                             'team_id': f'soccer:team:espn:{team}'})
    return rows


def resolve(offer, matches):
    """A single matching event AND player, otherwise abstain. No name-only join."""
    answer = {}
    for match in matches:
        competition = match.get('competition')
        if COMPETITIONS.get(competition, (None,))[0] != offer['sport']:
            continue
        if not match.get('start') or abs((parse_utc(match['start']) - parse_utc(offer['start'])).total_seconds()) > 7200:
            continue
        if offer['sport'] == 'tennis':
            if offer.get('tour') != competition or not offer.get('opponent'):
                continue
            players = match['participants']
            pair = {normalized(p['name']) for p in players}
            if len(pair) != 2 or pair != {normalized(offer['player']), normalized(offer['opponent'])}:
                continue
            player = next((p for p in players if normalized(p['name']) == normalized(offer['player'])), None)
        else:
            teams = offer.get('teams') or []
            if len(teams) != 2 or not all(teams):
                continue
            participants = match['participants']
            aliases = [{normalized(p.get('name')), normalized(p.get('abbreviation'))} - {''} for p in participants]
            if len(aliases) != 2 or not ((normalized(teams[0]) in aliases[0] and normalized(teams[1]) in aliases[1]) or
                                       (normalized(teams[0]) in aliases[1] and normalized(teams[1]) in aliases[0])):
                continue
            owners = [p['id'] for p, names in zip(participants, aliases) if normalized(offer.get('team')) in names]
            candidates = [p for p in match.get('players', []) if p['team_id'] in owners
                          and normalized(p['name']) == normalized(offer['player'])]
            player = candidates[0] if len(candidates) == 1 else None
        if player:
            answer[(match['event_id'], player['id'])] = {'competition': competition,
                'event_id': match['event_id'], 'player_id': player['id']}
    return next(iter(answer.values())) if len(answer) == 1 else None


def collect(sport, max_requests=16, max_seconds=150):
    """One resumable small batch; live dates first, one older date per run.

    Separate files and connections per competition/tour. Existing sports jobs
    keep their own locks/budgets. No provider request occurs on a member request.
    """
    if sport not in boards.SPORTS:
        raise ValueError('unknown sport')
    directory = boards.root(sport)
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / 'history-state.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    offset = int(state.get('offset', 1))
    today = datetime.now(timezone.utc).date()
    counts = {'requests': 0, 'records': 0, 'matches': 0, 'errors': [], 'finished_at': None}
    deadline = time.monotonic() + max_seconds
    catalogs = {}
    for comp, (kind, _) in COMPETITIONS.items():
        if kind != sport:
            continue
        path = isolated_database(directory.parent, comp).parent / 'matches.json'
        try:
            if path.stat().st_size <= 5_000_000:
                catalogs[comp] = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass

    def get(url, params):
        from .expansion_sources import get_json
        if counts['requests'] >= max_requests or time.monotonic() >= deadline:
            raise StopIteration
        counts['requests'] += 1
        return get_json(sport, url, params=params, timeout=10, tries=1)

    def persist(feed, competition):
        path = isolated_database(directory.parent, competition)
        if sum(p.stat().st_size for p in directory.glob('*/canonical.db*')) > 128 * 1024 * 1024:
            raise ValueError('isolated history storage budget reached')
        con = store.connect(path)
        try:
            result = store.put_many(con, feed.records)
            counts['records'] += len(result.get('record_ids', []))
        finally:
            con.close()
        if competition not in catalogs:
            try:
                catalogs[competition] = json.loads((path.parent / 'matches.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                catalogs[competition] = {}
        for match in feed.matches:
            previous = catalogs[competition].get(match['event_id'], {})
            catalogs[competition][match['event_id']] = {**previous, **match}
        counts['matches'] += len(feed.matches)

    # A one-week scoreboard window also returns a tennis tournament's completed
    # rounds; the older cursor advances only after its scoreboard was fetched.
    requests = ([('live', f'{today:%Y%m%d}'), ('live', f'{today+timedelta(days=1):%Y%m%d}')]
                if sport == 'soccer' else [('live', f'{today:%Y%m%d}-{today+timedelta(days=2):%Y%m%d}')])
    requests.append(('backfill', f'{today-timedelta(days=offset):%Y%m%d}'))
    advanced = False
    try:
        for phase, dates in requests:
            for comp in (('all',) if sport == 'soccer' else ('atp', 'wta')):
                body = get(f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{comp}/scoreboard',
                           {'dates': dates, 'limit': 200})
                grouped = {}
                for event in body.get('events') or []:
                    actual = soccer_competition(event) if sport == 'soccer' else comp
                    if actual:
                        grouped.setdefault(actual, []).append(event)
                for actual, events in grouped.items():
                    feed = parse_scoreboard({'events': events}, actual, now_utc())
                    persist(feed, actual)
            if phase == 'backfill':
                advanced = True
        if sport == 'soccer':
            # Scoreboards get their budget first. Pending summaries stay in the
            # catalog across runs, so advancing the date cannot lose a busy day.
            pending = [m for matches in catalogs.values() for m in matches.values()
                       if (not m.get('summary_checked_at') or
                           (datetime.now(timezone.utc)-parse_utc(m['summary_checked_at'])).total_seconds() >=
                           (86400 if m.get('status') == 'final' else 21600))]
            pending.sort(key=lambda m:(m.get('summary_checked_at') or '', m.get('start') or '', m['event_id']))
            for match in pending:
                actual = match['competition']
                eid = match['event_id'].split(':')[-2]
                try:
                    summary = get(f'https://site.api.espn.com/apis/site/v2/sports/soccer/{actual}/summary', {'event':eid})
                    if (summary.get('header', {}).get('league') or {}).get('slug') != actual:
                        raise ValueError('summary competition mismatch')
                    players = roster_identity(summary, match)
                    if players:
                        match['players'] = players
                    records = parse_summary(summary, actual, eid, now_utc())
                    persist(records, actual)
                    match['summary_checked_at'] = now_utc()
                except (net.HttpError, ValueError, OSError) as e:
                    counts['errors'].append(type(e).__name__)
    except StopIteration:
        pass
    except (net.HttpError, ValueError, OSError, sqlite3.Error) as e:
        counts['errors'].append(type(e).__name__)
    cutoff = (today - timedelta(days=100)).isoformat()
    for competition, matches in catalogs.items():
        atomic_json(isolated_database(directory.parent, competition).parent / 'matches.json',
                    {key: match for key, match in matches.items() if (match.get('start') or '') >= cutoff})
    earliest = (datetime.now(timezone.utc)-timedelta(hours=2)).isoformat().replace('+00:00','Z')
    atomic_json(directory / 'current-matches.json',
                [m for matches in catalogs.values() for m in matches.values() if (m.get('start') or '') >= earliest])
    counts['finished_at'] = now_utc()
    atomic_json(state_path, {**counts, 'offset': (offset % 90 + 1) if advanced else offset})
    return counts


def _file_signature(path):
    return tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else (0,0)
                 for p in (path, Path(str(path)+'-wal')))


@lru_cache(maxsize=2)
def _matches(path, stamp, size):
    return json.loads(Path(path).read_text(encoding='utf-8'))


@lru_cache(maxsize=64)
def _history(path, signature, player, stat, scope, before):
    con = store.connect(path, read_only=True)
    try:
        # Bound the read by this player's stat, never all forecasts / all sports.
        rows = store._latest_rows(con, 'stat', 'player_id=? AND stat=?', [player,stat], before, 2000)
        result = []
        for row in rows:
            obs = store._rec(row)
            if obs.scope != scope or not obs.final or obs.participation != 'played' or obs.value is None:
                continue
            event = store._any_event(con, obs.event_id, before)
            if event and event.start_utc and event.start_utc < before:
                result.append({'event_id': obs.event_id, 'date': event.start_utc, 'value': obs.value,
                               'source': obs.source, 'known_at': obs.known_at})
        return sorted(result, key=lambda r:r['date'], reverse=True)[:20]
    finally:
        con.close()


def history(req):
    status, data = boards.board(req)
    if status != 200:
        return status, data
    offer = next((r for r in data['offers'] if r['offer_id'] == req.query.get('offer_id')
                  and r['platform'] == req.query.get('platform')), None)
    if offer is None:
        return 404, {'error': 'offer_unavailable'}
    directory = boards.root(offer['sport'])
    path = directory / 'current-matches.json'
    matches = []
    try:
        info = path.stat()
        if info.st_size <= 3_000_000:
            matches = _matches(str(path), info.st_mtime_ns, info.st_size)
    except (OSError, ValueError):
        pass
    identity = resolve(offer, matches)
    # Soccer feed's Full is a book scope, not permission to substitute a
    # regulation box score until that platform's house rule is registered.
    response = {'offer_revision': offer['revision'], 'identity': identity, 'games': [],
                'scope': offer['scope'], 'stat': offer['stat'], 'line': offer['line'],
                'projection': None, 'grade': None}
    if not identity:
        return 200, {**response, 'state':'identity_pending', 'message':'This player and event are not yet linked to a verified result source.'}
    scope = 'regulation' if offer['sport'] == 'soccer' and offer['scope'] == 'full_game' else offer['scope']
    path = isolated_database(directory.parent, identity['competition'])
    before = min(now_utc(), offer['start'])
    # Minute boundary limits cached keys; data is still restricted to information
    # known by this boundary. Newly saved facts become visible next minute.
    before = parse_utc(before).replace(second=0, microsecond=0).isoformat().replace('+00:00','Z')
    try:
        games = _history(str(path), _file_signature(path), identity['player_id'], offer['stat'], scope, before)
    except (OSError, ValueError, sqlite3.Error):
        return 503, {'error':'history_temporarily_unavailable'}
    response.update(games=games, scope=scope, state='ready' if games else 'no_exact_stat',
                    message='Historical results at this line; not a predicted chance. Regulation time only.' if scope=='regulation'
                    else 'Historical results at this line; not a predicted chance.')
    response['windows'] = {f'l{n}': {'games':len(games[:n]), 'over':sum(g['value']>offer['line'] for g in games[:n]),
                                   'under':sum(g['value']<offer['line'] for g in games[:n]),
                                   'push':sum(g['value']==offer['line'] for g in games[:n])} for n in (5,10,20)}
    return 200, response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sport', choices=boards.SPORTS)
    args = parser.parse_args()
    if not boards.enabled(args.sport):
        raise SystemExit('sport disabled')
    result = collect(args.sport)
    print(json.dumps(result))
    if not result['matches'] and result['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
