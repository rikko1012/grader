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
from .expansion_soccer import parse_summary, parse_context


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


# League IDs verified from each ESPN scoreboard league header on 2026-11-17.
# Future fixtures often omit the league links but retain this source UID.
SOCCER_LEAGUE_IDS = {'700': 'eng.1', '740': 'esp.1', '720': 'ger.1', '730': 'ita.1', '710': 'fra.1', '770': 'usa.1', '630': 'bra.1', '745': 'arg.1', '760': 'mex.1', '725': 'ned.1', '715': 'por.1', '735': 'sco.1', '3901': 'bel.1', '3946': 'tur.1', '775': 'uefa.champions', '3922': 'fifa.friendly', '2395': 'uefa.nations', '786': 'fifa.worldq.uefa', '787': 'fifa.worldq.conmebol', '788': 'fifa.worldq.concacaf', '789': 'fifa.worldq.afc', '790': 'fifa.worldq.caf', '606': 'fifa.world', '19267': 'concacaf.nations.league'}


def soccer_competition(event):
    found = set()
    source_uid = re.search(r'(?:^|~)l:(\d+)(?:~|$)', event.get('uid') or '')
    if source_uid and source_uid[1] in SOCCER_LEAGUE_IDS:
        found.add(SOCCER_LEAGUE_IDS[source_uid[1]])
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
            level = ('atp_challenger' if str(offer.get('competition','')).startswith('ATP Challenger ') else
                     'wta125' if str(offer.get('competition','')).startswith('WTA 125K ') else offer.get('tour'))
            if level != competition or offer.get('tour') != COMPETITIONS[competition][1] or not offer.get('opponent'):
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
            aliases = [{normalized(p.get('name')), normalized(p.get('abbreviation')),
                        normalized(p.get('short_name'))} - {''} for p in participants]
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


def soccer_teams(offer, match):
    """Match the offered fixture before requesting roster or schedule context."""
    if (offer.get('sport') != 'soccer' or not match.get('start') or
            abs((parse_utc(offer['start']) - parse_utc(match['start'])).total_seconds()) > 7200):
        return []
    teams = offer.get('teams') or []
    participants = match.get('participants') or []
    if len(teams) != 2 or not all(teams) or len(participants) != 2:
        return []
    aliases = [{normalized(p.get(k)) for k in ('name','abbreviation','short_name')} - {''} for p in participants]
    a,b = map(normalized, teams)
    if not ((a in aliases[0] and b in aliases[1]) or (a in aliases[1] and b in aliases[0])):
        return []
    return [p for p,names in zip(participants, aliases) if normalized(offer.get('team')) in names]


def collect_soccer_teams(catalogs, get, persist, directory):
    """Current rosters establish identity, never starting status or participation.

    Team schedules discover recent result IDs; only match-specific summaries
    supply stat observations. This is the same cached roster/schedule/box-score
    pattern used by the existing collectors, in separate new-sport storage.
    """
    path = directory / 'team-history-state.json'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    try:
        snapshot = directory / 'board.json'
        offers = json.loads(snapshot.read_text(encoding='utf-8')).get('offers', []) if snapshot.stat().st_size <= 3_000_000 else []
    except (OSError, ValueError):
        offers = []
    now = datetime.now(timezone.utc)
    teams = {}
    for matches in catalogs.values():
        for match in list(matches.values()):
            if match.get('status') not in ('scheduled', 'in_progress'):
                continue
            for offer in offers:
                for team in soccer_teams(offer, match):
                    key = f"{match['competition']}:{team['id']}"
                    teams.setdefault(key, {'competition':match['competition'], 'team':team['id'], 'matches':[]})['matches'].append(match)
    errors = []
    try:
        # Reuse valid rosters every run, then refresh at most two teams so a busy
        # slate cannot consume the entire history request budget indefinitely.
        for key, item in teams.items():
            saved = state.get(key, {})
            if saved.get('checked_at') and (now-parse_utc(saved['checked_at'])).total_seconds() < 21600:
                for match in item['matches']:
                    known = {p['id']:p for p in saved.get('players', [])}
                    known.update({p['id']:p for p in match.get('players', [])})
                    match['players'] = list(known.values())
        due = [(key,item) for key,item in teams.items() if not state.get(key, {}).get('checked_at') or
               (now-parse_utc(state[key]['checked_at'])).total_seconds() >= 21600 or
               not state.get(key, {}).get('schedule_checked_at') or
               (now-parse_utc(state[key]['schedule_checked_at'])).total_seconds() >= 21600]
        due.sort(key=lambda row:state.get(row[0], {}).get('checked_at') or '')
        for key,item in due[:2]:
            comp, tid = item['competition'], item['team'].split(':')[-1]
            try:
                saved = state.setdefault(key, {})
                if not saved.get('checked_at') or (now-parse_utc(saved['checked_at'])).total_seconds() >= 21600:
                    roster = get(f'https://site.api.espn.com/apis/site/v2/sports/soccer/{comp}/teams/{tid}/roster', {})
                    if str((roster.get('team') or {}).get('id')) != tid:
                        raise ValueError('roster team mismatch')
                    players = [{'id':f"soccer:player:espn:{p['id']}", 'name':p['displayName'], 'team_id':item['team'],
                                'identity_source':'espn_team_roster', 'identity_known_at':now_utc()}
                               for p in roster.get('athletes') or [] if p.get('id') and p.get('displayName')]
                    saved.update(players=players, checked_at=now_utc())
                    for match in item['matches']:
                        known = {p['id']:p for p in players}
                        known.update({p['id']:p for p in match.get('players', [])})
                        match['players'] = list(known.values())
                schedule = get(f'https://site.api.espn.com/apis/site/v2/sports/soccer/{comp}/teams/{tid}/schedule', {})
                if str((schedule.get('team') or {}).get('id')) != tid:
                    raise ValueError('schedule team mismatch')
                for event in schedule.get('events') or []:
                    actual = soccer_competition(event)
                    if not actual or not event.get('date') or parse_utc(event['date']) < now-timedelta(days=100):
                        continue
                    feed = parse_scoreboard({'events':[event]}, actual, now_utc())
                    # A team schedule can contain other competitions. Keep their
                    # canonical IDs and databases distinct, never relabel them.
                    if all(any(p['id']==item['team'] for p in m['participants']) for m in feed.matches):
                        persist(feed, actual)
                saved['schedule_checked_at'] = now_utc()
            except (net.HttpError, ValueError, OSError) as e:
                errors.append(type(e).__name__)
    finally:
        atomic_json(path, {k:v for k,v in state.items() if v.get('checked_at') and
                          (now-parse_utc(v['checked_at'])).total_seconds() < 7*86400})
    return errors


DEFAULT_HISTORY_MB = 4096


def storage_budget_bytes(sport):
    """Per-sport cap for the isolated history stores, tunable without a deploy.

    SCOUT_<SPORT>_HISTORY_MAX_MB, then SCOUT_EXPANSION_HISTORY_MAX_MB. The old
    fixed 128 MB was smaller than the 90-day window the collector is built to
    hold (tennis measured 135 MB at day 64). A bad value keeps the default.
    These stores still count toward the global data cap in sports/worker.py.
    """
    for name in (f'SCOUT_{sport.upper()}_HISTORY_MAX_MB', 'SCOUT_EXPANSION_HISTORY_MAX_MB'):
        try:
            value = int(os.environ.get(name) or '')
        except ValueError:
            continue
        if value > 0:
            return value * 1024 * 1024
    return DEFAULT_HISTORY_MB * 1024 * 1024


def storage_bytes(directory):
    total = 0
    for path in Path(directory).glob('*/canonical.db*'):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _unchanged(con, rec):
    """(identity already stored, content equal to its current revision)."""
    try:
        source, key = rec.identity()
        row = con.execute("SELECT content_hash FROM records WHERE kind=? AND source=? AND source_key=? AND late=0 "
                          "ORDER BY revision DESC LIMIT 1", (rec.KIND, source, key)).fetchone()
        return row is not None, row is not None and row[0].split('~', 1)[0] == rec.content_hash()
    except Exception:  # an invalid record is left to store.put_many's quarantine
        return False, False


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
    counts = {'requests': 0, 'records': 0, 'unchanged': 0, 'matches': 0, 'scoreboards_ok': 0,
              'errors': [], 'finished_at': None}
    # Measured once per run. A full store is a recorded, non-failing state:
    # the backfill stops, current matches still settle and identity catalogs
    # and research keep refreshing. Nothing is deleted.
    used, budget = storage_bytes(directory), storage_budget_bytes(sport)
    full = used >= budget
    counts['storage_mb'], counts['budget_mb'] = round(used / 1048576, 1), budget // 1048576
    if full:
        counts['degraded'] = 'storage_budget'
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat().replace('+00:00', 'Z')
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
        starts = {m['event_id']: m.get('start') or '' for m in catalogs.get(competition, {}).values()}
        starts.update({m['event_id']: m.get('start') or '' for m in feed.matches})
        live = any((m.get('start') or '') >= recent for m in feed.matches)
        con = store.connect(path)
        try:
            # Re-reading a scoreboard must not rewrite every stored row: an
            # unchanged fact is skipped here (store sighting columns are for
            # NBA/NFL offers). A changed fact is still a new revision.
            fresh = []
            for rec in feed.records:
                known, same = _unchanged(con, rec)
                if same:
                    counts['unchanged'] += 1
                    continue
                if full and not known:
                    event = getattr(rec, 'event_id', None)
                    if not (starts.get(event, '') >= recent if event else live):
                        continue
                fresh.append(rec)
            if fresh:
                result = store.put_many(con, fresh)
                counts['records'] += result.get('inserted', 0) + result.get('revision', 0)
                counts['unchanged'] += result.get('duplicate', 0)
        finally:
            con.close()
        if competition not in catalogs:
            try:
                catalogs[competition] = json.loads((path.parent / 'matches.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                catalogs[competition] = {}
        for match in feed.matches:
            previous = catalogs[competition].get(match['event_id'], {})
            for field in ('surface', 'best_of'):
                if match.get(field) is None and previous.get(field) is not None:
                    match[field] = previous[field]
            catalogs[competition][match['event_id']] = {**previous, **match}
        counts['matches'] += len(feed.matches)

    # A one-week scoreboard window also returns a tennis tournament's completed
    # rounds; the older cursor advances only after its scoreboard was fetched.
    requests = ([('live', f'{today+timedelta(days=i):%Y%m%d}') for i in range(3)]
                if sport == 'soccer' else [('live', f'{today:%Y%m%d}-{today+timedelta(days=2):%Y%m%d}')])
    # Tennis providers accept bounded date ranges. Seven days per request
    # avoids spending 7.5 hours walking 90 individual dates on a new store.
    width = 7 if sport == 'tennis' else 1
    if not full:
        requests.append(('backfill', f'{today-timedelta(days=min(90,offset+width-1)):%Y%m%d}-{today-timedelta(days=offset):%Y%m%d}'
                         if width>1 else f'{today-timedelta(days=offset):%Y%m%d}'))
    advanced = False
    try:
        for phase, dates in requests:
            phase_ok = True
            for comp in (('all',) if sport == 'soccer' else ('atp', 'wta')):
                try:
                    body = get(f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{comp}/scoreboard',
                               {'dates': dates, 'limit': 200})
                except (net.HttpError, ValueError, OSError) as e:
                    counts['errors'].append(type(e).__name__)
                    phase_ok = False
                    continue
                counts['scoreboards_ok'] += 1
                grouped = {}
                for event in body.get('events') or []:
                    actual = soccer_competition(event) if sport == 'soccer' else comp
                    if actual:
                        grouped.setdefault(actual, []).append(event)
                for actual, events in grouped.items():
                    feed = parse_scoreboard({'events': events}, actual, now_utc())
                    persist(feed, actual)
            if phase == 'backfill' and phase_ok:
                advanced = True
        if sport == 'soccer':
            counts['errors'].extend(collect_soccer_teams(catalogs, get, persist, directory))
            # Scoreboards get their budget first. Pending summaries stay in the
            # catalog across runs, so advancing the date cannot lose a busy day.
            pending = [m for matches in catalogs.values() for m in matches.values()
                       if (not m.get('summary_checked_at') or
                           (datetime.now(timezone.utc)-parse_utc(m['summary_checked_at'])).total_seconds() >=
                           (86400 if m.get('status') == 'final' else 21600))
                       and (not m.get('summary_attempt_at') or
                            (datetime.now(timezone.utc)-parse_utc(m['summary_attempt_at'])).total_seconds() >= 1800)
                       and (not full or (m.get('start') or '') >= recent)]
            # Check imminent offered events first, then the oldest unchecked
            # summary. Once checked, a game waits its interval and frees the queue.
            offered_teams = {p['team_id'] for rows in catalogs.values() for m in rows.values()
                             for p in m.get('players', []) if p.get('identity_source') == 'espn_team_roster'}
            pending.sort(key=lambda m:(bool(m.get('summary_checked_at')),
                                       not any(p['id'] in offered_teams for p in m['participants']),
                                       m.get('status') != 'final', m.get('summary_checked_at') or '',
                                       -parse_utc(m['start']).timestamp() if m.get('start') else 0, m['event_id']))
            for match in pending[:6]:
                actual = match['competition']
                eid = match['event_id'].split(':')[-2]
                try:
                    summary = get(f'https://site.api.espn.com/apis/site/v2/sports/soccer/{actual}/summary', {'event':eid})
                    match['summary_attempt_at'] = now_utc()
                    if (summary.get('header', {}).get('league') or {}).get('slug') != actual:
                        raise ValueError('summary competition mismatch')
                    players = roster_identity(summary, match)
                    if players:
                        match['players'] = players
                    records = parse_summary(summary, actual, eid, now_utc())
                    records.records.extend(parse_context(summary,match,now_utc()).records)
                    persist(records, actual)
                    match['summary_checked_at'] = now_utc()
                except (net.HttpError, ValueError, OSError) as e:
                    match['summary_attempt_at'] = now_utc()
                    counts['errors'].append(type(e).__name__)
            # While full, detail sources see only recent matches, so an older
            # match is not marked checked without its facts being stored.
            current = catalogs if not full else {c: {k: m for k, m in rows.items() if (m.get('start') or '') >= recent}
                                                 for c, rows in catalogs.items()}
            from .expansion_soccer_detail import collect_details
            counts['errors'].extend(collect_details(current, get, persist, directory))
        else:
            current = catalogs if not full else {c: {k: m for k, m in rows.items() if (m.get('start') or '') >= recent}
                                                 for c, rows in catalogs.items()}
            from .expansion_challenger import collect as collect_challenger
            counts['errors'].extend(collect_challenger(current,get,persist,directory,offset,backfill=not full))
            from .expansion_tennis import collect_details
            counts['errors'].extend(collect_details(current, get, persist, directory))
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
    from .expansion_research import publish
    counts['research'] = publish(sport, budget_s=15)
    counts['finished_at'] = now_utc()
    atomic_json(state_path, {**counts, 'offset': (1 if offset+width>90 else offset+width) if advanced else offset})
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
        by_event = {}
        for row in rows:
            obs = store._rec(row)
            if obs.scope != scope:
                continue
            event = store._any_event(con, obs.event_id, before)
            if event and event.start_utc and event.start_utc < before:
                by_event.setdefault(obs.event_id, []).append((obs, event))
        result = []
        for observations in by_event.values():
            # Multiple providers reporting one game are one observation. A
            # disagreement (including participation) must not inflate L5/L10/L20.
            if not all(o.final and o.participation == 'played' and o.value is not None
                       for o, _ in observations) or len({o.value for o, _ in observations}) != 1:
                continue
            obs, event = sorted(observations, key=lambda item: item[0].source)[0]
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
    # A quiet day with one detail-source error did its job; so did a run that
    # degraded for a recorded reason. Fail only when no scoreboard answered.
    if result['errors'] and not result.get('scoreboards_ok') and not result.get('degraded'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
