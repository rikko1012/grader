from copy import deepcopy
from sports.expansion_soccer import parse_summary
from sports.contracts import StatObservation

NOW='2026-11-17T12:00:00Z'

def summary():
    return {'header':{'id':'1','competitions':[{'id':'1','status':{'period':2,'type':{'completed':True,'name':'STATUS_FINAL'}},
        'competitors':[{'id':'a','type':'team'},{'id':'b','type':'team'}]}]},'rosters':[
        {'team':{'id':'a'},'roster':[{'athlete':{'id':'x','displayName':'Player'},
        'stats':[{'name':'appearances','value':1},{'name':'totalShots','value':3}]}]}]}

def test_exact_stat_scope_provenance_and_no_missing_zero():
    result=parse_summary(summary(),'eng.1','1',NOW)
    stats=[r for r in result.records if isinstance(r,StatObservation)]
    assert len(stats)==1
    assert (stats[0].stat,stats[0].scope,stats[0].value)==('shots','regulation',3)
    assert stats[0].known_at==NOW
    assert not stats[0].validate()

def test_wrong_match_wrong_team_unused_player_and_extra_time():
    assert not parse_summary(summary(),'eng.1','2',NOW).records
    b=summary();b['rosters'][0]['team']['id']='other'
    assert not parse_summary(b,'eng.1','1',NOW).records


def test_source_full_time_label_without_period():
    b=summary();b['header']['competitions'][0]['status']={'type':{'completed':True,'name':'STATUS_FULL_TIME'}}
    assert parse_summary(b,'eng.1','1',NOW).records
    b=summary();b['rosters'][0]['roster'][0]['stats'][0]['value']=0
    assert not parse_summary(b,'eng.1','1',NOW).records
    # sg-scope: an extra-time match is kept as full_game (regulation + extra time), never as regulation history
    b=summary();b['header']['competitions'][0]['status']['period']=4
    stats=[r for r in parse_summary(b,'eng.1','1',NOW).records if isinstance(r,StatObservation)]
    assert stats and all(s.scope=='full_game' and s.includes_overtime=='yes' and not s.validate() for s in stats)


def test_extra_time_and_penalty_finals_are_kept_as_full_game():
    for name, overtime in (('STATUS_FINAL_AET','yes'),('STATUS_FINAL_PEN','unknown')):
        b=summary();b['header']['competitions'][0]['status']={'type':{'completed':True,'name':name}}
        stats=[r for r in parse_summary(b,'eng.1','1',NOW).records if isinstance(r,StatObservation)]
        assert [(s.scope,s.includes_overtime,s.value) for s in stats]==[('full_game',overtime,3)]
    b=summary();b['header']['competitions'][0]['status']={'type':{'completed':False,'name':'STATUS_FINAL_PEN'}}
    assert not parse_summary(b,'eng.1','1',NOW).records


def test_match_lineup_facts_are_not_inferred_from_team_roster():
    from sports.expansion_soccer import parse_context
    b=summary();b['header']['league']={'slug':'eng.1'}
    players=[{'athlete':{'id':str(i)},'starter':True,'position':{'displayName':'Midfielder'}} for i in range(11)]
    b['rosters'][0]['roster']=players
    m={'event_id':'soccer:espn:eng.1:1:1','competition':'eng.1',
       'participants':[{'id':'soccer:team:espn:a'},{'id':'soccer:team:espn:b'}],
       'players':[{'id':f'soccer:player:espn:{i}','team_id':'soccer:team:espn:a'} for i in range(11)]}
    rows=parse_context(b,m,NOW).records
    assert len(rows)==22 and all(r.known_at==NOW and not r.validate() for r in rows)
    b['rosters'][0]['roster'][0]['starter']=False
    assert not parse_context(b,m,NOW).records
