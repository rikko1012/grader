from copy import deepcopy
import json
import pytest
from sports.expansion_soccer_detail import parse_detail, collect_details

NOW='2026-11-17T12:00:00Z'

def fixture():
    match={'event_id':'soccer:espn:bra.1:1:1','competition':'bra.1','start':'2026-11-02T23:00:00Z',
           'status':'final','scores':{'a':2.,'b':1.},
           'participants':[{'id':'a','name':'Santos'},{'id':'b','name':'Example'}],
           'players':[{'id':'player1','name':'Test Player','team_id':'a'}]}
    status={'utcTime':match['start'],'finished':True,'reason':{'longKey':'finished'}}
    values={'minutes_played':{'value':79},'accurate_passes':{'value':41,'total':46},
            'dribbles_succeeded':{'value':2,'total':3},'expected_goals':{'value':.58},
            'matchstats.headers.tackles':{'value':0},'accurate_crosses':{'value':0,'total':1}}
    body={'general':{'matchId':44,'countryCode':'BRA','leagueName':'Série A','finished':True},
          'header':{'status':status,'teams':[{'id':11,'name':'Santos','score':2},{'id':12,'name':'Example','score':1}]},
          'content':{'playerStats':{'1':{'name':'Test Player','teamId':11,'stats':[
              {'stats':{k:{'key':k,'stat':v} for k,v in values.items()}}]}}}}
    return match,body

def test_attempts_not_completions_real_zero_and_provenance():
    m,b=fixture();rows=parse_detail(b,'bra.1',[m],NOW).records
    values={r.stat:r.value for r in rows}
    assert values=={'minutes':79,'passes_attempted':46,'attempted_dribbles':3,'xg':.58,'tackles':0,'crosses':1}
    assert all(r.source=='fotmob' and r.known_at==NOW and r.scope=='regulation' and not r.validate() for r in rows)
    del b['content']['playerStats']['1']['stats'][0]['stats']['accurate_passes']['stat']['total']
    assert 'passes_attempted' not in {r.stat for r in parse_detail(b,'bra.1',[m],NOW).records}

@pytest.mark.parametrize('bad',['score','team','player','extra_time','unplayed','too_many_minutes','competition','duplicate_player'])
def test_unverified_identity_scope_or_participation_is_refused(bad):
    m,b=fixture()
    if bad=='score':m['scores']['a']=0
    if bad=='team':b['header']['teams'][0]['name']='Other'
    if bad=='player':b['content']['playerStats']['1']['name']='Other'
    if bad=='extra_time':b['header']['status']['reason']['longKey']='after_extra_time'
    if bad=='unplayed':b['content']['playerStats']['1']['stats'][0]['stats']['minutes_played']['stat']['value']=0
    if bad=='too_many_minutes':b['content']['playerStats']['1']['stats'][0]['stats']['minutes_played']['stat']['value']=120
    if bad=='competition':b['general']['leagueName']='Other'
    if bad=='duplicate_player':m['players'].append(dict(m['players'][0],id='player2'))
    assert not parse_detail(b,'bra.1',[m],NOW).records

def test_unmatched_date_does_not_starve_older_history(tmp_path):
    m,b=fixture(); m['start']='2026-11-15T23:00:00Z'
    older=dict(m,event_id='older',start='2026-11-14T23:00:00Z')
    unsupported=dict(m,event_id='unsupported',competition='usa.1',start='2026-11-16T23:00:00Z')
    catalogs={'bra.1':{'m':m,'old':older},'usa.1':{'u':unsupported}};dates=[]
    def get(url,params):dates.append(params['date']);return {'leagues':[]}
    for _ in range(2):collect_details(catalogs,get,lambda *a:None,tmp_path)
    assert dates==['20261115','20261114']

def test_request_budget_does_not_mark_unrequested_detail_complete(tmp_path):
    m,b=fixture();listing={'leagues':[{'ccode':'BRA','name':'Série A','matches':[
        {'id':44,'home':b['header']['teams'][0],'away':b['header']['teams'][1],'status':b['header']['status']}]}]}
    def get(url,params):
        if 'matchDetails' in url:raise StopIteration
        return listing
    with pytest.raises(StopIteration):collect_details({'bra.1':{'x':m}},get,lambda *a:None,tmp_path)
    assert json.loads((tmp_path/'detail-history-state.json').read_text())=={}
