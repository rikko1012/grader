"""sg-squads (soccer-grades-1120 fair): identities for offered soccer players that ESPN's CURRENT team roster leaves out.

The player resolver links a book player to the ESPN player of the matched fixture by exact name on the fixture's
player list (ESPN team roster + match roster). ESPN's team roster sometimes omits a player who plays (MLS: Matt Turner,
Petar Musa) or lists him under another given name (Liga MX: 'Fernando Gonzalez' = book / FotMob 'Rubén González',
'Ángel Márquez' = 'Jeremy Márquez'). Their lines had no identity, so no research and no letter.

This background step (history job, research phase 'squads', own request cap, never on a member request) writes
squad-links.json: one link per (competition, ESPN team, exact book name), only for offered players the resolver could
not link while their fixture matched exactly. Bases, in order, each deterministic and exact:
  espn_past_roster       the same ESPN team's stored MATCH rosters in that competition hold exactly one player with that
                         exact normalised name, and that player's latest stored appearance in the competition is for
                         this team. (No request.) Full ESPN history, settles like any ESPN player.
  fotmob_squad_shirt_dob FotMob's squad of the same team (the team matched through the same fixture: competition, both
                         team names, kick-off) has exactly one member with that exact normalised name, and ESPN's team
                         roster has exactly one athlete with the SAME shirt number AND the SAME date of birth. Links to
                         that ESPN id (a cross-source identity fact, never a name rule).
  fotmob_squad           FotMob's squad has the exact name but no ESPN athlete has his shirt + birth date: the player is
                         linked to 'soccer:player:fotmob:<id>' - no stored history, so the population prior at 0U
                         (Lean; expansion_forecasts thin-3 cap). Never guessed onto an ESPN id.
Nothing else links: a missing or ambiguous name, a team that does not match, or a failed request writes no link (a
failed request is never cached; a definite 'not in the squad' waits NEG_S before it is asked again).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import parse_utc

PATH = 'squad-links.json'
MAX_BYTES = 400_000
LINK_DAYS = 14           # a link is kept this long after it was last confirmed (rechecked after RECHECK_S)
RECHECK_S = 86400
NEG_S = 6 * 3600


def key(competition, team_id, name):
    from .expansion_history import normalized
    return f'{competition}|{team_id}|{normalized(name)}'


def load(directory):
    """{key: link} from squad-links.json (bounded, never raises)."""
    try:
        path = Path(directory) / PATH
        if path.stat().st_size > MAX_BYTES:
            return {}
        doc = json.loads(path.read_text(encoding='utf-8'))
        links = doc.get('links') or {}
        return {k: v for k, v in links.items() if isinstance(v, dict) and v.get('player_id')}
    except (OSError, ValueError, AttributeError):
        return {}


def lookup(links, competition, team_id, name):
    """The linked player for this exact (competition, ESPN team, book name), as a fixture-player dict, or None."""
    link = links.get(key(competition, team_id, name)) if links else None
    if not link or link.get('team_id') != team_id:
        return None
    return {'id': link['player_id'], 'name': link.get('name') or name, 'team_id': team_id,
            'identity_source': link.get('basis')}


def _board_offers(directory):
    try:
        path = Path(directory) / 'board.json'
        if path.stat().st_size > 3_000_000:
            return []
        return json.loads(path.read_text(encoding='utf-8')).get('offers') or []
    except (OSError, ValueError, AttributeError):
        return []


def unresolved(directory, matches):
    """[(competition, team participant, book name, match)] for offered soccer players whose fixture matches exactly
    (one fixture, one own team) but whom the resolver cannot link. Unique per key, by kick-off."""
    from . import expansion_history as history
    out, seen = [], set()
    for offer in sorted(_board_offers(directory), key=lambda o: o.get('start') or ''):
        if offer.get('sport') != 'soccer' or not offer.get('player'):
            continue
        if history.resolve(offer, matches) is not None:
            continue
        fixtures = [(m, history.soccer_teams(offer, m)) for m in matches if m.get('competition')]
        fixtures = [(m, t) for m, t in fixtures if len(t) == 1]
        if len(fixtures) != 1:
            continue
        match, (team,) = fixtures[0]
        k = key(match['competition'], team['id'], offer['player'])
        if k not in seen:
            seen.add(k)
            out.append((match['competition'], team, offer['player'], match))
    return out


def past_roster(catalog, team_id, name):
    """espn_past_roster: the one player with this exact name in the team's stored match rosters, whose latest stored
    appearance in the competition is for this team. -> player dict or None."""
    from .expansion_history import normalized
    want = normalized(name)
    latest = {}
    for m in (catalog or {}).values():
        start = m.get('start') or ''
        for p in m.get('players') or []:
            if p.get('identity_source') == 'espn_team_roster' or not p.get('id'):
                continue                                          # match rosters only (who was in the squad that day)
            if normalized(p.get('name')) == want:
                if start >= latest.get(p['id'], ('', None))[0]:
                    latest[p['id']] = (start, p)
    if len(latest) != 1:
        return None
    (_, p), = latest.values()
    return p if p.get('team_id') == team_id else None


def _fotmob_team(listing, competition, match, team):
    """FotMob team id of `team` in this exact fixture. A listing row of a league that maps to the competition whose two
    team names and kick-off resolve to this ESPN match (expansion_soccer_detail.resolve_match); else the ONE row of that
    league with the same kick-off minute whose one side's name equals exactly one ESPN participant's name (the other
    side is then the other participant: FotMob 'Chivas' v 'Cruz Azul' = ESPN 'Guadalajara' v 'Cruz Azul'). Never a
    name-only or cross-league join."""
    from .expansion_history import normalized, participant_names
    from .expansion_soccer_detail import league_of_listing, resolve_match
    found, partial = [], []
    parts = match.get('participants') or []
    if len(parts) != 2:
        return None
    for league in listing.get('leagues') or []:
        if league_of_listing(league) != competition:
            continue
        for row in league.get('matches') or []:
            sides = (row.get('home') or {}, row.get('away') or {})
            identity = resolve_match(row, competition, [match])
            if identity:
                _, people = identity
                found += [side['id'] for side, person in zip(sides, people) if person['id'] == team['id'] and side.get('id')]
                continue
            when = str((row.get('status') or {}).get('utcTime') or '')[:16]
            if not when or when != str(match.get('start') or '')[:16]:
                continue
            hits = [(i, j) for i, side in enumerate(sides) for j, p in enumerate(parts)
                    if normalized(side.get('name')) in participant_names(p)]
            if len(hits) == 1:
                i, j = hits[0]
                other_side = sides[1 - i]
                pick = sides[i] if parts[j]['id'] == team['id'] else other_side
                partial.append(pick.get('id'))
    if found:
        return found[0] if len(set(found)) == 1 else None
    return partial[0] if len(partial) == 1 and partial[0] else None


def squad_member(squad_doc, name):
    """The one FotMob squad member with this exact normalised name -> {'id', 'name', 'shirt', 'dob'} or None."""
    from .expansion_history import normalized
    want = normalized(name)
    squad = (squad_doc or {}).get('squad') or {}
    groups = squad.get('squad') if isinstance(squad, dict) else squad
    hits = [p for g in groups or [] if isinstance(g, dict) for p in g.get('members') or []
            if isinstance(p, dict) and p.get('id') and normalized(p.get('name')) == want]
    if len(hits) != 1:
        return None
    p = hits[0]
    return {'id': str(p['id']), 'name': p.get('name'), 'shirt': p.get('shirtNumber'),
            'dob': str(p.get('dateOfBirth') or '')[:10] or None}


def espn_by_shirt_dob(roster_doc, team_id, shirt, dob):
    """The one ESPN roster athlete with the same shirt number AND date of birth -> player dict or None."""
    if shirt in (None, '') or not dob:
        return None
    if str((roster_doc.get('team') or {}).get('id')) != team_id.split(':')[-1]:
        return None
    hits = [a for a in roster_doc.get('athletes') or [] if isinstance(a, dict) and a.get('id') and
            str(a.get('jersey') or '') == str(shirt) and str(a.get('dateOfBirth') or '')[:10] == dob]
    if len(hits) != 1:
        return None
    a = hits[0]
    return {'id': f"soccer:player:espn:{a['id']}", 'name': a.get('displayName'), 'team_id': team_id}


def collect(catalogs, get, directory, matches, now=None, fotmob=True):
    """Write links for unresolved offered players. `get(url, params)` is the phase's capped GET (raises StopIteration
    at its cap). fotmob=False (FotMob switched off): the stored-roster step only, no request. -> {'errors': [...],
    'linked': n, 'checked': n}"""
    from . import net
    directory = Path(directory)
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        doc = json.loads((directory / PATH).read_text(encoding='utf-8'))
        doc = doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        doc = {}
    links, misses = dict(doc.get('links') or {}), dict(doc.get('misses') or {})
    out = {'errors': [], 'linked': 0, 'checked': 0}
    listings, squads, rosters = {}, {}, {}

    def age(stamp_):
        try:
            return (now - parse_utc(stamp_)).total_seconds()
        except (TypeError, ValueError, AttributeError):
            return float('inf')
    try:
        for competition, team, name, match in unresolved(directory, matches):
            k = key(competition, team['id'], name)
            if (k in links and age(links[k].get('checked_at')) < RECHECK_S) or age(misses.get(k)) < NEG_S:
                continue
            out['checked'] += 1
            p = past_roster(catalogs.get(competition), team['id'], name)
            if p:
                links[k] = {'player_id': p['id'], 'name': p.get('name'), 'team_id': team['id'],
                            'competition': competition, 'basis': 'espn_past_roster', 'checked_at': stamp}
                out['linked'] += 1
                misses.pop(k, None)
                continue
            if not fotmob:
                continue
            try:
                # FotMob's day listing follows its own clock: the UTC date first, then the day before
                fm_team = None
                for back in (0, 1):
                    day = (parse_utc(match['start']) - timedelta(days=back)).strftime('%Y%m%d')
                    if day not in listings:
                        listings[day] = get('https://www.fotmob.com/api/data/matches', {'date': day})
                    fm_team = _fotmob_team(listings[day], competition, match, team)
                    if fm_team is not None:
                        break
                if fm_team is None:
                    misses[k] = stamp                              # the fixture is not in FotMob's listing that day
                    continue
                if fm_team not in squads:
                    squads[fm_team] = get('https://www.fotmob.com/api/data/teams', {'id': fm_team})
                member = squad_member(squads[fm_team], name)
                if member is None:
                    misses[k] = stamp                              # no (or no unique) exact name in the squad
                    continue
                tid = team['id'].split(':')[-1]
                if tid not in rosters:
                    rosters[tid] = get(f'https://site.api.espn.com/apis/site/v2/sports/soccer/{competition}/teams/'
                                       f'{tid}/roster', {})
                espn = espn_by_shirt_dob(rosters[tid], team['id'], member['shirt'], member['dob'])
                if espn:
                    links[k] = {'player_id': espn['id'], 'name': espn['name'], 'team_id': team['id'],
                                'competition': competition, 'basis': 'fotmob_squad_shirt_dob', 'checked_at': stamp,
                                'fotmob_id': member['id'], 'shirt': member['shirt'], 'dob': member['dob']}
                else:
                    links[k] = {'player_id': f"soccer:player:fotmob:{member['id']}", 'name': member['name'],
                                'team_id': team['id'], 'competition': competition, 'basis': 'fotmob_squad',
                                'checked_at': stamp, 'fotmob_id': member['id']}
                out['linked'] += 1
                misses.pop(k, None)
            except (net.HttpError, ValueError, OSError, KeyError, TypeError, AttributeError) as e:
                out['errors'].append(type(e).__name__)               # a failed read is never cached
    except StopIteration:
        pass
    finally:
        keep = {k: v for k, v in links.items() if age(v.get('checked_at')) < LINK_DAYS * 86400}
        miss = {k: v for k, v in misses.items() if age(v) < NEG_S}
        from .expansion_history import atomic_json
        atomic_json(directory / PATH, {'at': stamp, 'links': keep, 'misses': miss})
    return out
