import json
from pathlib import Path
from copy import deepcopy
import pytest
from sports.expansion_challenger import parse_games,parse_stats
from sports.expansion_history import resolve
from sports.expansion import isolated_database
from sports import store
from sports.contracts import StatObservation

NOW='2026-11-17T21:00:00Z'
def fixture():return json.loads((Path(__file__).parent/'fixtures/expansion_365.json').read_text())

def test_native_challenger_identity_scores_and_stats():
    b=fixture();f=parse_games(b,NOW)['atp_challenger'];m=f.matches[0]
    assert m['event_id']=='atp:365scores:atp_challenger:6968:4894401'
    assert all(not r.validate() and r.known_at==NOW for r in f.records)
    vals={r.stat:r.value for r in f.records if isinstance(r,StatObservation) and r.scope=='full_game' and r.participant_id.endswith(':74804')}
    assert vals=={'games_won':5,'games_played':17,'sets_won':0,'sets_played':2,'tie_breakers_played':0}
    stats=parse_stats(b,m,NOW).records
    assert all(not r.validate() for r in stats) and len(stats)==16
    vals={r.stat:r.value for r in stats if r.participant_id.endswith(':74804')}
    assert vals['aces']==4 and vals['double_faults']==4 and vals['breakpoints_won']==1
    assert vals['service_games_won']==4 # 4/11 is NOT the player's hold probability.

@pytest.mark.parametrize('bad',['doubles','wrong_sport','retired','wrong_country','duplicate_player'])
def test_unsupported_format_rejected(bad):
    b=fixture();g=b['games'][0]
    if bad=='doubles':b['competitions'][0]['countryId']=423
    if bad=='wrong_sport':g['sportId']=1
    if bad=='retired':g['statusText']='Retired'
    if bad=='wrong_country':b['countries'][0]['name']='ATP-D'
    if bad=='duplicate_player':g['awayCompetitor']['id']=g['homeCompetitor']['id']
    assert not parse_games(b,NOW)

def test_invalid_score_cannot_supply_stats():
    b=fixture();m=parse_games(b,NOW)['atp_challenger'].matches[0]
    b['games'][0]['stages'][0]['homeCompetitorScore']=10
    assert not parse_stats(b,m,NOW).records
    assert not [r for r in parse_games(b,NOW)['atp_challenger'].records if isinstance(r,StatObservation)]

def test_exact_level_and_opponent_required_and_separate_store(tmp_path):
    b=fixture();f=parse_games(b,NOW)['atp_challenger'];m=f.matches[0]
    a,o=m['participants'];offer={'sport':'tennis','tour':'atp','competition':'ATP Challenger Champaign',
        'player':a['name'],'opponent':o['name'],'start':m['start']}
    assert resolve(offer,[m])['competition']=='atp_challenger'
    assert resolve(dict(offer,competition='ATP World Tour Finals Men Singles'),[m]) is None
    path=isolated_database(tmp_path,'atp_challenger');c=store.connect(path);store.put_many(c,f.records);c.close()
    assert isolated_database(tmp_path,'atp_challenger')==path
    assert not isolated_database(tmp_path,'atp').exists()

def test_wrong_game_or_non_match_stat_is_not_used():
    b=fixture();m=parse_games(b,NOW)['atp_challenger'].matches[0]
    for s in b['statistics']:s['filterId']=27
    assert not parse_stats(b,m,NOW).records
    b=fixture();b['games'][0]['id']=12
    assert not parse_stats(b,m,NOW).records


def test_collect_without_backfill_requests_only_the_live_window(tmp_path):
    from sports import expansion_challenger as challenger
    calls=[]
    def get(url,params):
        calls.append(params);return {}
    assert challenger.collect({},get,lambda *a:None,tmp_path,8,backfill=False)==[]
    assert len(calls)==1
    assert challenger.collect({},get,lambda *a:None,tmp_path,8)==[] and len(calls)==3
