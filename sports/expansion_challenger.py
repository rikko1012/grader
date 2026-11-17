"""365Scores Challenger/WTA125 facts in separate level-specific stores.

No main-tour/Challenger pooling and no browser or credential workarounds.
Fraction numerators are explicit counts; comparison percentages are never
mistaken for player probabilities or service opportunities.
"""
from datetime import datetime,timedelta,timezone
import json
import re
from .contracts import Event,Player,SourceId,StatObservation,now_utc,utc,parse_utc
from .expansion import Feed,_number

BASE='https://webws.365scores.com/web/'
PARAMS={'appTypeId':5,'langId':1,'timezoneName':'UTC'}
LEVELS={301:('Challenger','atp_challenger','atp'),302:('WTA 125K','wta125','wta')}
COUNTS={'Aces':'aces','Double Faults':'double_faults'}
FRACTIONS={'Break Points Won':'breakpoints_won','Service Games':'service_games_won',
           'Total Points Won':'points_won','Service Points':'service_points_won',
           '1st Serve Points Won':'first_serve_points_won','2nd Serve Points Won':'second_serve_points_won'}


def parse_games(body,observed):
    observed=utc(observed);out={}
    countries={c.get('id'):c.get('name') for c in body.get('countries') or []}
    competitions={c.get('id'):c for c in body.get('competitions') or []}
    for g in body.get('games') or []:
        comp=competitions.get(g.get('competitionId'),{});level=LEVELS.get(comp.get('countryId'))
        if not level or not g.get('id') or countries.get(comp['countryId'])!=level[0] or g.get('sportId')!=3 or comp.get('sportId')!=3:continue
        _,key,tour=level;players=[g.get('homeCompetitor') or {},g.get('awayCompetitor') or {}]
        if any(p.get('type')!=4 or not p.get('id') or not p.get('name') or p.get('sportId')!=3 for p in players):continue
        if players[0]['id']==players[1]['id']:continue
        try:start=utc(g.get('startTime'))
        except ValueError:continue
        if not start:continue
        status=str(g.get('statusText') or '')
        detail=' '.join(str(g.get(k) or '') for k in ('statusText','shortStatusText','winDescription'))
        if re.search(r'retir|walk|cancel|abandon|default|w/o',detail,re.I):continue
        final=g.get('statusGroup')==4 and status=='Ended'
        scheduled=g.get('statusGroup')==2
        if not final and not scheduled:continue
        eid=f'{tour}:365scores:{key}:{g["competitionId"]}:{g["id"]}'
        participants=[{'id':f'{tour}:player:365scores:{p["id"]}','name':p['name']} for p in players]
        f=out.setdefault(key,Feed())
        f.records.extend(Player(p['id'],tour,p['name'],source_ids=(SourceId('365scores',str(raw['id'])),),
            observed_at=observed,known_at=observed) for p,raw in zip(participants,players))
        f.records.append(Event(eid,tour,parse_utc(start).year,'unknown',None,None,start,'365scores',
            status='final' if final else 'scheduled',source_ids=(SourceId('365scores',str(g['id'])),),
            settlement_source='365scores',observed_at=observed,known_at=observed))
        match={'event_id':eid,'competition':key,'name':comp.get('name'),'start':start,
            'status':'final' if final else 'scheduled','participants':participants,'source_event_id':g['id'],
            'has_stats':g.get('hasStats') is True}
        f.matches.append(match)
        if not final:continue
        stages=sorted((x for x in g.get('stages') or [] if re.fullmatch(r'Set [1-3]',x.get('name','')) and x.get('isEnded') is True),key=lambda x:x['name'])
        values=[[_number(x.get(k)) for x in stages] for k in ('homeCompetitorScore','awayCompetitorScore')]
        valid=len(stages) in (2,3) and all(v is not None and v.is_integer() for row in values for v in row)
        valid=valid and all((max(a,b)==6 and min(a,b)<=4) or (max(a,b)==7 and min(a,b) in (5,6)) for a,b in zip(*values))
        if not valid:continue
        wins=[sum(a>b for a,b in zip(values[i],values[1-i])) for i in range(2)]
        if max(wins)!=2 or any(sum(a>b for a,b in zip(values[i][:-1],values[1-i][:-1]))>=2 for i in range(2)):continue
        if any(_number(p.get('score'))!=wins[i] for i,p in enumerate(players)):continue
        match['set_scores']={p['id']:values[i] for i,p in enumerate(participants)}
        for i,p in enumerate(participants):
            stats={'games_won':sum(values[i]),'games_played':sum(values[0])+sum(values[1]),'sets_won':wins[i],
                   'sets_played':len(stages),'tie_breakers_played':sum(max(a,b)==7 and min(a,b)==6 for a,b in zip(*values))}
            for stat,value in stats.items():
                f.records.append(StatObservation(eid,tour,p['id'],'player','full_game','no',stat,'count',value,'played',
                    '365scores',str(g['id']),True,observed,observed))
            for j,value in enumerate(values[i]):
                for stat,v in [('games_won',value),('games_played',values[0][j]+values[1][j])]:
                    f.records.append(StatObservation(eid,tour,p['id'],'player',f'set{j+1}','no',stat,'count',v,'played',
                        '365scores',str(g['id']),True,observed,observed))
    return out


def parse_stats(body,match,observed):
    out=Feed();games=[g for g in body.get('games') or [] if str(g.get('id'))==str(match.get('source_event_id'))]
    if len(games)!=1 or match.get('status')!='final' or not match.get('set_scores'):return out
    verified=parse_games(body,observed).get(match['competition'],Feed())
    actual=next((m for m in verified.matches if m['event_id']==match['event_id']),None)
    if not actual or actual.get('set_scores')!=match['set_scores']:return out
    tour=match['event_id'].split(':')[0];ids={p['id'].split(':')[-1]:p['id'] for p in match['participants']}
    seen=set()
    for s in body.get('statistics') or []:
        # This endpoint was requested without a period filter: only match rows.
        if s.get('filterId') not in (None,-1):continue
        pid=ids.get(str(s.get('competitorId')));name=s.get('name');value=str(s.get('value',''))
        stat=COUNTS.get(name) or FRACTIONS.get(name)
        if not pid or not stat or (pid,stat) in seen:continue
        if name in COUNTS:
            if not re.fullmatch(r'\d+',value):continue
            n=int(value)
        else:
            parts=re.fullmatch(r'(\d+)/(\d+) \((\d+(?:\.\d+)?)%\)',value)
            if not parts or int(parts[1])>int(parts[2]):continue
            n=int(parts[1])
        seen.add((pid,stat))
        out.records.append(StatObservation(match['event_id'],tour,pid,'player','full_game','no',stat,'count',n,
            'played','365scores',str(match['source_event_id']),True,utc(observed),utc(observed)))
    return out


def collect(catalogs,get,persist,directory,offset):
    from .expansion_history import atomic_json
    from . import net
    errors=[];today=datetime.now(timezone.utc).date();path=directory/'challenger-state.json'
    try:state=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):state={}
    try:
        for a,b in [(today-timedelta(days=1),today+timedelta(days=2)),
                    (today-timedelta(days=min(90,offset+6)),today-timedelta(days=offset))]:
            data=get(BASE+'games/allscores/',{**PARAMS,'sports':3,'startDate':a.strftime('%d/%m/%Y'),'endDate':b.strftime('%d/%m/%Y')})
            for comp,feed in parse_games(data,now_utc()).items():persist(feed,comp)
        pending=[m for comp in ('atp_challenger','wta125') for m in catalogs.get(comp,{}).values()
                 if m.get('set_scores') and m.get('has_stats') and (not state.get(m['event_id']) or
                 (datetime.now(timezone.utc)-parse_utc(state[m['event_id']])).total_seconds()>=86400)]
        pending.sort(key=lambda m:(state.get(m['event_id']) or '',-parse_utc(m['start']).timestamp()))
        # Leave budget for the independent ATP/WTA serve collector.
        for m in pending[:4]:
            try:
                body=get(BASE+'game/stats/',{**PARAMS,'games':m['source_event_id']})
                persist(parse_stats(body,m,now_utc()),m['competition']);state[m['event_id']]=now_utc()
            except (net.HttpError,ValueError,OSError) as exc:
                state[m['event_id']]=now_utc();errors.append(type(exc).__name__)
    except (net.HttpError,ValueError,OSError) as exc:errors.append(type(exc).__name__)
    finally:
        cutoff=utc(datetime.now(timezone.utc)-timedelta(days=100))
        atomic_json(path,{k:v for k,v in state.items() if v>=cutoff})
    return errors
