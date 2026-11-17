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
    b=summary();b['header']['competitions'][0]['status']['period']=4
    assert not parse_summary(b,'eng.1','1',NOW).records
