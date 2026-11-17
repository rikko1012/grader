"""Soccer-only completed player statistics. No NBA/NFL stat mappings or models.

Source field names verified against ESPN eng.1 summary on 2026-11-17.
Only records explicitly reported in that match are emitted. A roster listing
is not evidence of playing; appearances must be reported as one. Extra-time
matches are not treated as regulation-time history.
"""
from .contracts import Player, SourceId, StatObservation, ContextSnapshot, utc
from .expansion import COMPETITIONS, Feed, _number

STATS = {'totalShots':'shots', 'shotsOnTarget':'shots_on_target', 'totalGoals':'goals',
         'goalAssists':'assists', 'saves':'saves', 'foulsCommitted':'fouls',
         'foulsSuffered':'fouls_suffered', 'yellowCards':'yellow_cards', 'redCards':'red_cards'}


def parse_context(body, match, observed_at):
    """Keep source-listed lineups separately from projected playing time.

    A generic team roster is never a confirmed starting lineup. Require a
    match-specific roster with exactly eleven explicit starters on that team.
    Post-match collection stays post-match known_at, not usable in old forecasts.
    """
    out=Feed();header=body.get('header') or {};observed_at=utc(observed_at)
    comp=match['competition'];parts=match['event_id'].split(':')
    if (not observed_at or comp not in COMPETITIONS or COMPETITIONS[comp][0]!='soccer'
            or (header.get('league') or {}).get('slug')!=comp or str(header.get('id'))!=parts[-2]):return out
    teams={p['id'] for p in match['participants']}
    for roster in body.get('rosters') or []:
        tid='soccer:team:espn:'+str((roster.get('team') or {}).get('id'))
        if tid not in teams:continue
        players=roster.get('roster') or []
        starters=[str((p.get('athlete') or {}).get('id')) for p in players if p.get('starter') is True]
        if len(starters)!=11 or len(set(starters))!=11 or 'None' in starters:continue
        for p in players:
            pid='soccer:player:espn:'+str((p.get('athlete') or {}).get('id'))
            if not any(x['id']==pid and x['team_id']==tid for x in match.get('players',[])):continue
            if isinstance(p.get('starter'),bool):
                out.records.append(ContextSnapshot('soccer','starter',pid,match['event_id'],'source_listed',
                    'yes' if p['starter'] else 'no',source='espn',observed_at=observed_at,known_at=observed_at))
            role=(p.get('position') or {}).get('displayName')
            if role:
                out.records.append(ContextSnapshot('soccer','role',pid,match['event_id'],'source_listed','yes',
                    detail=role,source='espn',observed_at=observed_at,known_at=observed_at))
    return out


def parse_summary(body, competition, source_event_id, observed_at):
    if COMPETITIONS.get(competition, (None,))[0] != 'soccer':
        raise ValueError('soccer competition required')
    observed_at = utc(observed_at)
    if not observed_at:
        raise ValueError('observation time required')
    out=Feed()
    header=body.get('header') or {}
    actual_competition = (header.get('league') or {}).get('slug')
    if actual_competition and actual_competition != competition:
        out.rejected.append('summary competition does not match request')
        return out
    if str(header.get('id')) != str(source_event_id):
        out.rejected.append('summary event does not match request')
        return out
    competitions=header.get('competitions') or []
    if len(competitions)!=1:
        out.rejected.append('ambiguous match')
        return out
    match=competitions[0]
    status=(match.get('status') or {})
    typ=status.get('type') or {}
    regulation_final = (typ.get('name')=='STATUS_FULL_TIME' and status.get('period',2)==2) or (
        typ.get('name')=='STATUS_FINAL' and status.get('period')==2)
    if not (typ.get('completed') is True and regulation_final):
        out.rejected.append('not a verified completed regulation match')
        return out
    teams={str(c.get('id')) for c in match.get('competitors') or [] if c.get('type')=='team' or
           (c.get('team') or {}).get('id') == c.get('id') and c.get('id') is not None}
    if len(teams)!=2 or 'None' in teams:
        out.rejected.append('match teams missing')
        return out
    eid=f"soccer:espn:{competition}:{source_event_id}:{match.get('id')}"
    seen=set()
    for roster in body.get('rosters') or []:
        if str((roster.get('team') or {}).get('id')) not in teams:
            out.rejected.append('roster team is not in match')
            continue
        for row in roster.get('roster') or []:
            athlete=row.get('athlete') or {}
            pid=athlete.get('id')
            if not pid or pid in seen:
                continue
            values={s.get('name'):_number(s.get('value')) for s in row.get('stats') or []}
            if values.get('appearances')!=1:
                continue
            seen.add(pid)
            player=Player(f'soccer:player:espn:{pid}','soccer',athlete.get('displayName') or '',
                position=(row.get('position') or {}).get('displayName'),
                source_ids=(SourceId('espn',str(pid)),),observed_at=observed_at,known_at=observed_at)
            if player.validate():
                continue
            out.records.append(player)
            for source_stat, stat in STATS.items():
                value=values.get(source_stat)
                if value is not None:
                    out.records.append(StatObservation(eid,'soccer',player.player_id,'player','regulation','no',stat,
                        'count',value,'played','espn',str(source_event_id),True,observed_at,observed_at))
    return out
