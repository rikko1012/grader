"""Soccer-only detailed stats from FotMob's public /api/data JSON feed.

Ordinary requests only; no signed-header generation or browser collection.
Identity requires exact competition, both teams, time, final score and a unique
player in the ESPN match roster. Missing fields never become zero. Totals in
fraction fields mean attempts, while values mean successful actions.
"""
from datetime import datetime, timedelta, timezone
import json

from .contracts import StatObservation, now_utc, parse_utc, utc
from .expansion import Feed, _number
from . import net

# Source team name verified against the Nov 2 fixture in both feeds.
TEAM_ALIASES = {109705: ('RB Bragantino', 'Red Bull Bragantino', 'Bragantino')}
# FotMob league id (listing primaryId; matchDetails parentLeagueId, = leagueId for a plain league) -> ESPN slug.
# Keyed by id since 2026-11-18: (country, name) broke on group / stage names ("K-League 1 Final Group A") and on
# renames ("Major League Soccer" in the listing, "MLS" in allLeagues). Every id checked against FotMob allLeagues
# (sources_1118/probe/fotmob_allLeagues.json) and a live listing; club competitions only (no national-team ids).
LEAGUES = {
    268: 'bra.1', 47: 'eng.1', 87: 'esp.1', 54: 'ger.1', 55: 'ita.1', 53: 'fra.1',          # 53dad0b's six
    57: 'ned.1', 61: 'por.1', 130: 'usa.1', 230: 'mex.1', 112: 'arg.1', 64: 'sco.1', 40: 'bel.1', 71: 'tur.1',
    42: 'uefa.champions', 73: 'uefa.europa', 10216: 'uefa.europa.conf', 48: 'eng.2', 146: 'ger.2', 86: 'ita.2',
    140: 'esp.2', 110: 'fra.2', 223: 'jpn.1', 113: 'aus.1', 536: 'ksa.1',
}
ORIGINAL = frozenset({'bra.1', 'eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1'})   # always collected (forecast inputs)


def league_of_listing(league):
    try:
        return LEAGUES.get(int(league.get('primaryId')))
    except (TypeError, ValueError):
        return None


def league_of_detail(general):
    try:
        return LEAGUES.get(int(general.get('parentLeagueId') or general.get('leagueId')))
    except (TypeError, ValueError):
        return None
# These do not overlap ESPN's existing exact-stat observations.
FIELDS = {
    'minutes_played':('minutes','value','minutes'),
    'accurate_passes':('passes_attempted','total','count'),
    'matchstats.headers.tackles':('tackles','value','count'),
    'accurate_crosses':('crosses','total','count'),
    'clearances':('clearances','value','count'),
    'dribbles_succeeded':('attempted_dribbles','total','count'),
    'chances_created':('chances_created','value','count'),
    'expected_goals':('xg','value','count'),
    'expected_assists':('xa','value','count'),
    'touches':('touches','value','count'),
    'touches_opp_box':('touches_opposition_box','value','count'),
    'interceptions':('interceptions','value','count'),
    'corners':('corners_taken','value','count'),
}


def resolve_match(row, competition, matches):
    from .expansion_history import normalized
    start = (row.get('status') or {}).get('utcTime')
    if not start:
        return None
    teams = [row.get('home') or {}, row.get('away') or {}]
    found=[]
    for match in matches:
        if match.get('competition') != competition or not match.get('start'):
            continue
        if abs((parse_utc(start)-parse_utc(match['start'])).total_seconds()) > 7200:
            continue
        people=[]
        for team in teams:
            source_names={normalized(team.get('name'))}
            if team.get('name') in TEAM_ALIASES.get(team.get('id'), ()):
                source_names.update(map(normalized, TEAM_ALIASES[team['id']]))
            candidates=[p for p in match['participants'] if source_names &
                        {normalized(p.get(k)) for k in ('name','short_name','abbreviation')} - {''}]
            if len(candidates)!=1:
                break
            people.append(candidates[0])
        if len(people)==2 and people[0]['id']!=people[1]['id']:
            found.append((match, people))
    return found[0] if len(found)==1 else None


def parse_detail(body, competition, matches, observed_at):
    from .expansion_history import normalized
    out=Feed()
    general,header=body.get('general') or {}, body.get('header') or {}
    status=header.get('status') or {}
    if (not general.get('matchId') or not general.get('finished') or not status.get('finished')
            or status.get('cancelled') or status.get('awarded') or status.get('whoLostOnPenalties')
            or (status.get('reason') or {}).get('longKey') != 'finished'
            or any(v for k,v in (status.get('halfs') or {}).items() if 'Extra' in k)):
        return out
    if league_of_detail(general) != competition:
        return out
    teams=header.get('teams') or []
    if len(teams)!=2:
        return out
    identity=resolve_match({'home':teams[0],'away':teams[1],'status':status},competition,matches)
    if not identity:
        return out
    match,canonical=identity
    if match.get('status')!='final' or not match.get('scores'):
        return out
    if any(_number(t.get('score')) != match['scores'].get(p['id']) for t,p in zip(teams,canonical)):
        return out
    team_ids={str(t['id']):p['id'] for t,p in zip(teams,canonical)}
    players=(body.get('content') or {}).get('playerStats') or {}
    observed_at=utc(observed_at)
    seen=set()
    for player in players.values():
        team=team_ids.get(str(player.get('teamId')))
        candidates=[p for p in match.get('players',[]) if p['team_id']==team and normalized(p['name'])==normalized(player.get('name'))]
        if len(candidates)!=1 or candidates[0]['id'] in seen:
            continue
        pid=candidates[0]['id'];seen.add(pid)
        values={v.get('key'):v.get('stat') or {} for group in player.get('stats') or []
                for v in (group.get('stats') or {}).values()}
        minutes=_number((values.get('minutes_played') or {}).get('value'))
        if minutes is None or minutes<=0 or minutes>90:
            continue
        for key,(stat,field,unit) in FIELDS.items():
            value=_number((values.get(key) or {}).get(field))
            if value is None or (stat not in ('xg','xa') and not value.is_integer()):
                continue
            out.records.append(StatObservation(match['event_id'],'soccer',pid,'player','regulation','no',stat,unit,value,
                'played','fotmob',str(general['matchId']),True,observed_at,observed_at))
    return out


def collect_details(catalogs,get,persist,directory,recent=None):
    """recent: competitions with an offer in the last 30 days (beyond the original six); None = all mapped;
    {} = the original six only (53dad0b's collection: the run with every research phase switched off)."""
    from .expansion_history import atomic_json
    path=directory/'detail-history-state.json'
    try:state=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):state={}
    now=datetime.now(timezone.utc);errors=[]
    # A detail requires an ESPN match roster and final score before any fetch.
    available=[m for rows in catalogs.values() for m in rows.values()
               if m.get('competition') in LEAGUES.values() and
               (recent is None or m['competition'] in ORIGINAL or m['competition'] in recent) and
               m.get('status')=='final' and m.get('scores') and m.get('players')]
    due=[m for m in available if not state.get(m['event_id']) or
         (now-parse_utc(state[m['event_id']])).total_seconds()>=86400]
    # The original six (forecast inputs) first, so a newer league's backlog never takes their date slot; with only
    # the six (recent={}) this is the 53dad0b order exactly.
    due.sort(key=lambda m:(m['competition'] not in ORIGINAL,state.get(m['event_id']) or '',-parse_utc(m['start']).timestamp()))
    try:
        # One date per batch prevents broad league discovery from consuming the
        # entire request allowance; pending matches resume on the next run.
        if not due:return errors
        day=due[0]['start'][:10]
        body=get('https://www.fotmob.com/api/data/matches',{'date':day.replace('-','')})
        pending=[]
        for league in body.get('leagues') or []:
            competition=league_of_listing(league)
            if not competition:continue
            for row in league.get('matches') or []:
                identity=resolve_match(row,competition,due)
                if not identity or not str(row.get('id','')).isdigit():continue
                match,_=identity
                pending.append((row,competition,match))
        # Research never takes a graded slot: within the date the original six (forecast inputs) are requested first.
        # Stable sort, so with only the six (recent={}) the order is FotMob's listing order exactly as at 53dad0b.
        pending.sort(key=lambda p:p[1] not in ORIGINAL)
        matched={m['event_id'] for _,_,m in pending}
        # Unmatched identities wait one day; request-budget interruption leaves
        # unrequested mapped details pending rather than falsely checked.
        for match in due:
            if match['start'][:10]==day and match['event_id'] not in matched:
                state[match['event_id']]=now_utc()
        for row,competition,match in pending:
            try:
                detail=get('https://www.fotmob.com/api/data/matchDetails',{'matchId':row['id']})
                if str((detail.get('general') or {}).get('matchId'))!=str(row['id']):
                    raise ValueError('soccer detail identity mismatch')
                feed=parse_detail(detail,competition,[match],now_utc())
                persist(feed,competition)
                state[match['event_id']]=now_utc()
            except (net.HttpError,ValueError,OSError) as e:
                state[match['event_id']]=now_utc()
                errors.append(type(e).__name__)
    except (net.HttpError,ValueError,OSError) as e:
        errors.append(type(e).__name__)
    finally:
        atomic_json(path,{k:v for k,v in state.items() if (now-parse_utc(v)).total_seconds()<100*86400})
    return errors
