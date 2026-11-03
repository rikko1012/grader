import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace
import pytest
from gradecore import adapters, policy
from gradecore.capture import capture
from gradecore.engine import assess

AT='2026-11-03T12:00:00Z'
START='2026-11-03T20:00:00Z'


def source(sport):
    if sport=='cs2':
        row=dict(match_slug='hltv-1',slug='p',prop='kills',scope='12',line=20.5,book='underdog',
                 line_type='standard',match_start=START,model_version='perf-frozen',logged_at=AT,
                 grade='B',p_pick=.64,team='t',**{'lambda':23})
        return 'cs2',(row,{'probs_raw':json.dumps({'over':.65,'under':.35,'push':0})},7)
    if sport=='lol':
        row=dict(sid='series',pid='p',stat='kills',scope='12',line=20.5,platform='underdog',odds_type='standard',
                 series_start=START,p_over=.65,p_under=.35,p_push=0,model_version='lol-1',fit_sha='frozen',
                 tid='t',grade_label='B',model_side='over',cal_p_over=.64,**{'lambda':23})
        return 'lol',(row,7,AT)
    common=dict(league=sport,event_id='event',player_id='p',stat='points',scope='full',line=20.5)
    fc=SimpleNamespace(**common,model_version='sm-1',artifact_version='frozen',forecast_id='id',forecast_at=AT,
                       p_over=.65,p_under=.35,p_push=0,projection_mean=23,grade=None)
    offer=SimpleNamespace(**common,platform='underdog',variant='standard')
    return 'sports',(fc,offer,START)


@pytest.mark.parametrize('sport',['cs2','lol','nba','nfl'])
def test_real_adapter_shapes_share_policy_and_preserve_raw(sport,monkeypatch):
    kind,args=source(sport)
    a=getattr(adapters,kind)(*args)
    assert a.predict(a.offer).p_over==.65
    assert a.market(a.offer) is None
    assert a.settle(a.offer) is None
    with pytest.raises(ValueError):a.predict(replace(a.offer,event_id='wrong'))
    first=assess(a,a.offer)
    monkeypatch.setattr(policy,'LETTER_CUTS',(('A+',.64),))
    second=assess(a,a.offer)
    assert first['sides']['over']['grade']['band'] != second['sides']['over']['grade']['band']
    assert second['sides']['over']['grade']['letter'] is None
    assert second['sides']['over']['grade']['stake_units']==0
    assert a.prediction.p_over==.65


@pytest.mark.parametrize('sport',['cs2','lol','nba','nfl'])
def test_capture_flag_idempotence_and_immutability(sport,monkeypatch):
    db=sqlite3.connect(':memory:')
    kind,args=source(sport)
    monkeypatch.delenv('SCOUT_GRADECORE_SHADOW',raising=False)
    assert capture(db,kind,*args)=='disabled'
    assert not db.execute("SELECT name FROM sqlite_master WHERE name='gradecore_shadow'").fetchone()
    monkeypatch.setenv('SCOUT_GRADECORE_SHADOW','1')
    assert capture(db,kind,*args)=='captured'
    assert capture(db,kind,*args)=='captured'
    assert db.execute('SELECT count(*) FROM gradecore_shadow').fetchone()[0]==1
    body=json.loads(db.execute('SELECT payload FROM gradecore_shadow').fetchone()[0])
    assert body['candidate']['release_allowed'] is False
    for q in ('DELETE FROM gradecore_shadow',"UPDATE gradecore_shadow SET sport='wrong'"):
        with pytest.raises(sqlite3.IntegrityError):db.execute(q)


def test_invalid_shadow_never_rolls_back_parent_forecast(monkeypatch):
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE original(id INTEGER)')
    db.execute('INSERT INTO original VALUES(1)')
    monkeypatch.setenv('SCOUT_GRADECORE_SHADOW','1')
    kind,args=source('cs2'); args[1]['probs_raw']='{}'
    assert capture(db,kind,*args)=='skipped'
    db.commit()
    assert db.execute('SELECT id FROM original').fetchone()[0]==1


def test_parent_rollback_rolls_back_shadow_too(monkeypatch):
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE original(id INTEGER)')
    db.execute('INSERT INTO original VALUES(1)')
    monkeypatch.setenv('SCOUT_GRADECORE_SHADOW','1')
    kind,args=source('nba'); assert capture(db,kind,*args)=='captured'
    db.rollback()
    assert not db.execute("SELECT name FROM sqlite_master WHERE name='gradecore_shadow'").fetchone()
