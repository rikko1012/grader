from dataclasses import replace
import json
from types import SimpleNamespace
import pytest

from sports import config, store, api_model
from sports.model import learning, gradecore_export
from sports.tests import helpers as h


def world():
    c=store.connect()
    store.put(c,h.event())
    o=h.offer(line=20)
    oid=store.put(c,o)['record_id']
    l=learning.db()
    for i,at in enumerate([h.T0,'2026-10-23T21:00:00Z','2026-10-23T22:45:00Z']):
        fc=h.forecast(offer_snapshot_ids=(oid,),input_snapshot_ids=('input:'+str(i),),model_version='sm-1',artifact_version='frozen-1',forecast_at=at)
        store.put(c,fc)
        l.execute('INSERT INTO scorecard(forecast_id,settlement_id,model_version,league,stat,event_id,platform,line,side,p_side,p_over,p_under,p_push,status,outcome,value,forecast_at,settled_at,superseded) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)',
                  (fc.forecast_id,'s'+str(i),'sm-1','nba','points',fc.event_id,'prizepicks',20,'over',.5,.5,.45,.05,'settled','over',24,at,'2026-10-24T03:00:00Z'))
    # A future event revision cannot alter the historical decision cutoff.
    store.put(c,replace(h.event(),start_utc='2026-10-24T23:00:00Z',known_at='2026-10-24T04:00:00Z',observed_at='2026-10-24T04:00:00Z'))
    l.commit(); l.close(); c.close()


def test_export_is_deduplicated_point_in_time_and_read_only():
    world()
    before={p:p.read_bytes() for p in (config.DATA_DIR/'canonical.db',config.DATA_DIR/'model/learning.db')}
    r=gradecore_export.export(config.DATA_DIR,'nba')
    assert r['scanned']==3 and len(r['rows'])==1
    row=r['rows'][0]
    assert row['forecast_at']=='2026-10-23T21:00:00Z'
    assert row['start_at']==h.START
    assert row['status']=='win' and row['p']==pytest.approx(.5/.95)
    assert r['excluded']['less_than_30_minutes_before_start']==1
    assert all(p.read_bytes()==v for p,v in before.items())


def test_export_refuses_truncation(monkeypatch):
    world();monkeypatch.setattr(gradecore_export,'MAX_ROWS',2)
    with pytest.raises(ValueError,match='limit exceeded'):gradecore_export.export(config.DATA_DIR,'nba')


def test_export_uses_saved_display_chance_not_raw_or_current_calibration():
    from sports.model import shown
    world()
    row=gradecore_export.export(config.DATA_DIR,'nba')['rows'][0]
    assert row['published_p'] is None
    shown.record('nba',[(row['forecast_id'],'points',20,{'po':.61,'pu':.34,'pp':.05,'v':'frozen-cal'})],row['forecast_at'])
    result=gradecore_export.export(config.DATA_DIR,'nba')['rows'][0]
    assert result['published_p']==.61
    assert result['p']==pytest.approx(.5/.95)


def test_admin_export_reads_only_known_snapshot_name():
    req=lambda league:SimpleNamespace(rest='gradecore-export',query={'league':league})
    assert api_model.admin_model(req('../../secrets'))[0]==400
    assert api_model.admin_model(req('nba'))[0]==503
    folder=config.DATA_DIR/'model/api';folder.mkdir(parents=True)
    doc={'schema':'gradecore-export-1','rows':[]}
    (folder/'gradecore_export_nba.json').write_text(json.dumps(doc))
    assert api_model.admin_model(req('nba'))==(200,doc)


def test_export_marks_offer_forced_side_without_changing_saved_side():
    """audit_1121 nfl r2: an Over-only offer with p_over < 0.5 keeps side='over' (the saved chosen side) but is marked
    side_offer_forced with the model's own max-p side, so max-p scorecards stop counting it as the model's pick."""
    c=store.connect()
    store.put(c,h.event())
    o=h.offer(line=20,sides=(h.SideQuote("over",True),h.SideQuote("under",False)),variant="demon")
    oid=store.put(c,o)['record_id']
    l=learning.db()
    at='2026-10-23T21:00:00Z'
    fc=h.forecast(offer_snapshot_ids=(oid,),input_snapshot_ids=('input:f',),model_version='sm-1',artifact_version='frozen-1',
                  forecast_at=at,p_over=.38,p_under=.57,p_push=.05,p_over_given_no_push=.38/.95,p_under_given_no_push=.57/.95,
                  sides_available=('over',),chosen_side='over')
    store.put(c,fc)
    l.execute('INSERT INTO scorecard(forecast_id,settlement_id,model_version,league,stat,event_id,platform,line,side,p_side,p_over,p_under,p_push,status,outcome,value,forecast_at,settled_at,superseded) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)',
              (fc.forecast_id,'sf','sm-1','nba','points',fc.event_id,'prizepicks',20,'over',.4,.38,.57,.05,'settled','under',14,at,'2026-10-24T03:00:00Z'))
    l.commit(); l.close(); c.close()
    row=gradecore_export.export(config.DATA_DIR,'nba')['rows'][0]
    assert row['side']=='over' and row['p']==pytest.approx(.38/.95) and row['status']=='loss'
    assert row['side_offer_forced'] is True and row['max_p_side']=='under'
    assert row['p_max_side']==pytest.approx(.57/.95) and row['sides_available']==['over']


def test_export_two_sided_offer_is_not_forced():
    world()
    row=gradecore_export.export(config.DATA_DIR,'nba')['rows'][0]
    assert row['side_offer_forced'] is False and row['max_p_side']==row['side']=='over'
    assert row['p_max_side']==pytest.approx(row['p'])
