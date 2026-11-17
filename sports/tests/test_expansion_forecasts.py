import copy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from sports import expansion_forecasts as model, expansion_board as boards, expansion_research as research
from sports.expansion_history import atomic_json
from sports.tests.test_expansion_learning import setup, result

NOW='2026-11-17T23:00:00Z'


def fixture(sport='soccer'):
    offer={'sport':sport,'platform':'prizepicks','offer_id':'o1','revision':'r1',
           'player_id':'native:p','event_id':'native:e','stat':'shots','scope':'full_game',
           'variant':'standard','line':2.5,'start':'2026-11-18T18:00:00Z',
           'sides':{'over':{},'under':{}}}
    identity={'event_id':sport+':event:1','player_id':sport+':player:1',
              'competition':'eng.1' if sport=='soccer' else 'atp_challenger'}
    facts={'identity':identity,'scope':'regulation','context':{},'games':[],
           'event':{'event_id':identity['event_id'],'status':'scheduled','start':offer['start']}}
    for i,value in enumerate([5,2,4,3,5,2,4,1,3,4]):
        stamp=(datetime(2026,11,16,tzinfo=timezone.utc)-timedelta(days=i)).isoformat()
        facts['games'].append({'event_id':f'{sport}:old:{i}','date':stamp,'known_at':stamp,'value':value,'source':'fixture'})
    if sport=='tennis':
        offer.update(stat='games_won',scope='set1',line=4.5)
        facts['scope']='set1'
    return offer,facts


@pytest.mark.parametrize('sport',['soccer','tennis'])
def test_finite_predictive_distribution_and_shared_letters(sport):
    offer,facts=fixture(sport)
    f,reason=model.make_forecast(offer,facts,NOW)
    assert reason is None and f['grade']
    assert sum(f['p_'+s] for s in ('over','under','push'))==pytest.approx(1)
    assert f['p_side']==pytest.approx(f['p_'+f['side']])
    assert f['sample']==10 and f['units'] is None and f['calibrated'] is False
    assert f['p_over'] != sum(g['value']>offer['line'] for g in facts['games'])/10
    assert f['range80'][0]<=f['projection']<=f['range80'][1]


def test_integer_push_is_separate_and_chance_conditional():
    offer,facts=fixture();offer['line']=3
    f,_=model.make_forecast(offer,facts,NOW)
    assert f['p_push']>0
    assert f['p_side']==pytest.approx(f['p_'+f['side']]/(1-f['p_push']))


def test_only_offered_side_unknown_payout_not_invented():
    offer,facts=fixture();offer.update(sides={'under':{}},variant='goblin')
    f,_=model.make_forecast(offer,facts,NOW)
    assert f['side']=='under' and f['grade'] is None


def test_leakage_duplicates_wrong_period_and_too_few_are_refused():
    offer,facts=fixture();facts['games']=facts['games'][:4]
    assert model.fit(offer,facts,NOW)[1]=='more_exact_history_needed'
    facts['games']*=4
    assert model.fit(offer,facts,NOW)[1]=='more_exact_history_needed'
    offer,facts=fixture();facts['scope']='first_half'
    assert model.fit(offer,facts,NOW)[1]=='history_period_mismatch'
    offer,facts=fixture()
    for game in facts['games']:game['known_at']='2026-11-18T20:00:00Z'
    assert model.fit(offer,facts,NOW)[1]=='more_exact_history_needed'


def test_cup_regulation_does_not_become_full_match():
    offer,facts=fixture();facts['identity']['competition']='fifa.world'
    assert model.fit(offer,facts,NOW)[1]=='extra_time_scope_unverified'


def test_tennis_format_surface_and_period_are_exact():
    offer,facts=fixture('tennis');offer.update(stat='aces',scope='full_game');facts['scope']='full_game'
    assert model.fit(offer,facts,NOW)[1]=='match_format_missing'
    facts['context']={'best_of':3,'surface':'Hard'}
    for game in facts['games']:game.update(best_of=5,surface='Hard')
    assert model.fit(offer,facts,NOW)[1]=='more_exact_history_needed'
    for game in facts['games']:game.update(best_of=3,surface='Clay')
    assert model.fit(offer,facts,NOW)[1]=='more_exact_history_needed'
    for game in facts['games']:game['surface']='Hard'
    assert model.fit(offer,facts,NOW)[0]['sample']==10


def test_zero_results_are_uncertain_not_certain_under():
    offer,facts=fixture()
    for game in facts['games']:game['value']=0
    offer['line']=.5
    f,_=model.make_forecast(offer,facts,NOW)
    assert 0<f['p_over']<1 and 0<f['p_under']<1 and f['projection']>0


def test_worker_freezes_inputs_reuses_issuance_and_rejects_revision(tmp_path,monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    offer,facts=fixture()
    monkeypatch.setattr(boards,'board',lambda req:(200,{'offers':[offer]}))
    monkeypatch.setattr(research,'saved',lambda sport:{'players':{research.key(offer):facts}})
    assert model.publish('soccer',NOW)['published']==1
    with model.connect('soccer') as c:
        original=c.execute('SELECT payload FROM forecasts').fetchone()[0]
    model.publish('soccer','2026-11-17T23:01:00Z')
    with model.connect('soccer') as c:
        assert c.execute('SELECT count(*) FROM forecasts').fetchone()[0]==1
        assert c.execute('SELECT payload FROM forecasts').fetchone()[0]==original
    assert len(model.attach('soccer',{'offers':[offer]})['forecasts'])==1
    assert model.attach('soccer',{'offers':[dict(offer,revision='moved')]})['forecasts']=={}
    facts['games'][0]['value']=8
    model.publish('soccer','2026-11-17T23:02:00Z')
    with model.connect('soccer') as c:
        assert c.execute('SELECT count(*) FROM forecasts').fetchone()[0]==2
        assert c.execute('SELECT payload FROM forecasts ORDER BY rowid').fetchone()[0]==original


def test_reviews_append_corrections_without_rewriting_forecasts(setup,monkeypatch):
    root,path,match,offer=setup
    offer.update(sport='tennis',variant='standard',player_id='native',event_id='native')
    _,facts=fixture('tennis')
    facts.update(identity={'competition':'atp','event_id':match['event_id'],'player_id':match['participants'][0]['id']},
                 scope='full_game',context={'best_of':3},
                 event={'event_id':match['event_id'],'start':offer['start'],'status':'scheduled'})
    for game in facts['games']:game['best_of']=3
    monkeypatch.setattr(boards,'board',lambda req:(200,{'offers':[offer]}))
    monkeypatch.setattr(research,'saved',lambda sport:{'players':{research.key(offer):facts}})
    assert model.publish('tennis',NOW)['published']==1
    with model.connect('tennis') as c:original=c.execute('SELECT payload FROM forecasts').fetchone()[0]
    result(setup,10,'2026-11-18T18:00:00Z')
    assert model.settle('tennis','2026-11-18T18:30:00Z')['added']==1
    assert model.settle('tennis','2026-11-18T18:31:00Z')['added']==0
    result(setup,3,'2026-11-18T19:00:00Z')
    assert model.settle('tennis','2026-11-18T19:30:00Z')['added']==1
    with model.connect('tennis') as c:
        assert c.execute('SELECT payload FROM forecasts').fetchone()[0]==original
        reviews=[json.loads(r[0]) for r in c.execute('SELECT payload FROM reviews ORDER BY rowid')]
    assert [r['actual'] for r in reviews]==[10,3]
    assert all(r['book_settlement'] is False for r in reviews)


def test_started_canonical_event_and_future_context_cannot_publish():
    offer,facts=fixture();facts['event']['status']='in_progress'
    assert model.fit(offer,facts,NOW)[1]=='canonical_event_not_scheduled'
    offer,facts=fixture('tennis');facts['context']={'surface':'Hard','surface_known_at':'2026-11-19T00:00:00Z'}
    assert model.fit(offer,facts,NOW)[1]=='future_context'
