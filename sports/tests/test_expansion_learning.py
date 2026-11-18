"""Prospective freezing, corrections and isolation, using private fixture stores."""
from contextlib import closing
from dataclasses import replace
import json
import time

import pytest

from gradecore.projection_review import review
from sports import expansion_learning as learn, store
from sports.contracts import Event, StatObservation, ContextSnapshot
from sports.expansion import isolated_database
from sports.expansion_history import atomic_json
from sports.tests.test_expansion_history import tennis_match, tennis_offer

NOW='2026-11-17T21:00:00Z'
AFTER='2026-11-18T18:00:00Z'


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    m=tennis_match();pid=m['participants'][0]['id']
    offer={**tennis_offer(),'platform':'underdog','offer_id':'one','revision':'rev1',
           'stat':'aces','scope':'full_game','line':7.5,'observed_at':NOW,'sides':{'over':{},'under':{}}}
    root=tmp_path/'tennis';root.mkdir()
    atomic_json(root/'board.json',{'sport':'tennis','offers':[offer]})
    atomic_json(root/'current-matches.json',[m])
    path=isolated_database(tmp_path,'atp')
    with closing(store.connect(path)) as c:
        rows=[]
        for i,v in enumerate([2,4,6,8,10]):
            start=f'2026-11-{i+1:02}T12:00:00Z';known=f'2026-11-{i+1:02}T18:00:00Z'
            eid=f'atp:espn:atp:tour:old{i}'
            rows.extend([Event(eid,'atp',2026,'unknown',None,None,start,'espn',status='final',observed_at=known,known_at=known),
                StatObservation(eid,'atp',pid,'player','full_game','no','aces','count',v,'played','espn',str(i),True,known,known)])
        rows.append(Event(m['event_id'],'atp',2026,'unknown',None,None,m['start'],'espn',status='scheduled',observed_at=NOW,known_at=NOW))
        assert not store.put_many(c,rows).get('quarantined',0)
    return root,path,m,offer


def result(setup,value=10,known=AFTER,**kwargs):
    root,path,m,offer=setup
    obs=StatObservation(m['event_id'],'atp',m['participants'][0]['id'],'player','full_game','no',
                        'aces','count',value,'played','espn','10',True,known,known)
    with closing(store.connect(path)) as c:store.put_many(c,[replace(obs,**kwargs)])


def test_frozen_prediction_replays_and_has_no_publication_path(setup):
    first=learn.cycle('tennis',NOW)
    assert first['forecasts']['saved']==1 and not first['release_allowed']
    with closing(learn.connect('tennis')) as c:
        original=c.execute('SELECT payload FROM predictions').fetchone()[0]
        p=json.loads(original)
        assert p['baseline']==p['candidate']==6 and len(p['history'])==5
        assert p['fit']['player_events']==0 and p['shadow_only']
        assert p['inputs_used']==['same_stat_period_history','prior_baseline_residuals']
        assert p['line_for_review']==7.5 and p['book_scope_verified']
    assert learn.cycle('tennis',NOW)['forecasts']['saved']==0
    result(setup)
    out=learn.cycle('tennis',AFTER)
    assert out['reviews']['added']==1
    with closing(learn.connect('tennis')) as c:
        assert c.execute('SELECT payload FROM predictions').fetchone()[0]==original
        r=json.loads(c.execute('SELECT payload FROM reviews').fetchone()[0])
        assert r['baseline']['error']==-4 and r['baseline']['crossed_correctly'] is False
        assert r['settlement_status'] is None
    assert learn.cycle('tennis',AFTER)['reviews']['added']==0


def test_conflicting_participation_cannot_train_from_the_other_source(setup):
    _,path,m,_=setup
    known='2026-11-16T18:00:00Z'
    with closing(store.connect(path)) as c:
        store.put_many(c,[StatObservation('atp:espn:atp:tour:old0','atp',m['participants'][0]['id'],
            'player','full_game','no','aces','count',None,'did_not_play','thescore','0',True,known,known)])
    report=learn.cycle('tennis',NOW)
    assert report['forecasts']['saved']==0
    assert report['forecasts']['insufficient_history']==1


def test_correction_append_only_training_uses_latest_and_no_same_event(setup):
    learn.cycle('tennis',NOW);result(setup);learn.cycle('tennis',AFTER)
    later='2026-11-18T19:00:00Z';result(setup,3,later);learn.cycle('tennis',later)
    with closing(learn.connect('tennis')) as c:
        assert c.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]==2
        identity={'competition':'atp','event_id':'atp:espn:atp:tour:next'}
        fit=learn.residual_fit(c,identity,'aces','full_game',later,6)
        assert fit['player_events']==1 and fit['shift']==pytest.approx(-3/21)
        prior=learn.residual_fit(c,identity,'aces','full_game',AFTER,6)
        assert prior['shift']==pytest.approx(4/21)
        same=learn.residual_fit(c,dict(identity,event_id=setup[2]['event_id']),'aces','full_game',later,6)
        assert same['player_events']==0
        assert learn.residual_fit(c,dict(identity,competition='wta'),'aces','full_game',later,6)['player_events']==0


def test_many_books_or_lines_count_as_one_training_example(setup):
    root,path,m,offer=setup
    atomic_json(root/'board.json',{'offers':[dict(offer,offer_id=str(i),revision=str(i),line=7.5+i) for i in range(12)]})
    assert learn.cycle('tennis',NOW)['forecasts']['saved']==12
    result(setup);learn.cycle('tennis',AFTER)
    with closing(learn.connect('tennis')) as c:
        fit=learn.residual_fit(c,{'competition':'atp','event_id':'next'},'aces','full_game',AFTER,6)
        assert fit['player_events']==1


def test_dnp_correction_invalidates_prior_error_training(setup):
    learn.cycle('tennis',NOW);result(setup);learn.cycle('tennis',AFTER)
    later='2026-11-18T19:00:00Z';result(setup,None,later,participation='did_not_play')
    learn.cycle('tennis',later)
    with closing(learn.connect('tennis')) as c:
        r=json.loads(c.execute('SELECT payload FROM reviews ORDER BY id DESC').fetchone()[0])
        assert r['actual'] is None and r['reason']=='unresolved_or_nonparticipation'
        assert learn.residual_fit(c,{'competition':'atp','event_id':'next'},'aces','full_game',later,6)['player_events']==0


@pytest.mark.parametrize('change',[{'scope':'set1'},{'stat':'double_faults'},{'participant_id':'atp:player:espn:999'},
                                   {'event_id':'atp:espn:atp:tour:other'}])
def test_wrong_identity_result_cannot_review_prediction(setup,change):
    learn.cycle('tennis',NOW);result(setup,**change)
    assert learn.cycle('tennis',AFTER)['reviews']['added']==0


def test_future_inputs_and_context_are_not_in_saved_prediction(setup):
    root,path,m,offer=setup
    late='2026-11-18T19:00:00Z'
    with closing(store.connect(path)) as c:
        old=store._rec(store._latest_rows(c,'stat','',[],NOW,1)[0])
        store.put_many(c,[replace(old,value=999,observed_at=late,known_at=late),
            ContextSnapshot('atp','weather',m['event_id'],m['event_id'],'rain','yes',source='espn',observed_at=late,known_at=late)])
    learn.cycle('tennis',NOW)
    with closing(learn.connect('tennis')) as c:
        p=json.loads(c.execute('SELECT payload FROM predictions').fetchone()[0])
        assert p['baseline']==6 and not p['context_records']


def test_duplicate_sources_do_not_double_history_and_conflicts_abstain(setup):
    root,path,m,offer=setup;identity={'player_id':m['participants'][0]['id'],'event_id':m['event_id']}
    with closing(store.connect(path)) as c:
        old=store._rec(store._latest_rows(c,'stat','',[],NOW,1)[0])
        store.put_many(c,[replace(old,source='thescore')])
        assert len(learn.history(c,identity,'aces','full_game',NOW))==5
        store.put_many(c,[replace(old,source='thescore',value=999,known_at=NOW,observed_at=NOW)])
        assert len(learn.history(c,identity,'aces','full_game',NOW))==4


@pytest.mark.parametrize('change',[{'start':'2026-11-17T20:00:00Z'},
                                   {'observed_at':'2026-11-17T20:50:00Z'},
                                   {'observed_at':'2026-11-17T22:00:00Z'}])
def test_late_stale_or_future_quote_is_not_frozen(setup,change):
    root,_,_,offer=setup;atomic_json(root/'board.json',{'offers':[dict(offer,**change)]})
    assert learn.cycle('tennis',NOW)['forecasts']['saved']==0


def test_soccer_scope_never_becomes_book_win_or_loss():
    r=review(2.5,3,None)
    assert r['absolute_error']==.5 and r['projected_side'] is None and r['settlement_status'] is None


@pytest.mark.parametrize('status,start',[('in_progress','2026-11-17T20:30:00Z'),
                                       ('scheduled','2026-11-17T20:30:00Z')])
def test_book_start_cannot_override_started_source_event(setup,status,start):
    _,path,m,_=setup
    with closing(store.connect(path)) as c:
        event=store._any_event(c,m['event_id'],NOW)
        store.put_many(c,[replace(event,status=status,start_utc=start,observed_at=NOW,known_at=NOW)])
    # The book still claims tomorrow. The result provider proves it already started.
    assert learn.cycle('tennis',NOW)['forecasts']['saved']==0


def test_sport_isolation_bounded_work_and_disabled_jobs(setup,monkeypatch):
    with closing(learn.connect('tennis')) as c:
        assert learn.capture(c,'tennis',NOW,time.monotonic()-1)['partial']
    assert not (setup[0].parent/'soccer').exists()
    from sports.scheduler import default_jobs
    monkeypatch.delenv('SCOUT_SOCCER_ENABLED',raising=False);monkeypatch.delenv('SCOUT_TENNIS_ENABLED',raising=False)
    assert not any(k.endswith('-learning') for k in default_jobs() if k.startswith(('soccer','tennis')))
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED','1')
    assert default_jobs()['tennis-learning']['cap_mb']>0


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),None,True])
def test_invalid_actual_is_rejected(bad):
    with pytest.raises(ValueError):review(5,bad,4.5)


def test_owner_status_reads_only_small_worker_report(setup,monkeypatch):
    from sports.service import expansion_learning_status
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED','1')
    learn.cycle('tennis',NOW)
    view=expansion_learning_status()
    assert view['tennis']['enabled'] and view['tennis']['report']['totals']['predictions']==1
    assert view['soccer']['report'] is None
    (setup[0]/'learning-status.json').write_text(' '*65537)
    assert expansion_learning_status()['tennis']['report'] is None
    assert 'history' not in expansion_learning_status()['tennis']
    (setup[0]/'history-state.json').write_text(
        '{"finished_at":"t","storage_mb":3400.0,"budget_mb":4096,"offset":8,"errors":[]}')
    assert expansion_learning_status()['tennis']['history']=={
        'finished_at':'t','storage_mb':3400.0,'budget_mb':4096,'degraded':None}
