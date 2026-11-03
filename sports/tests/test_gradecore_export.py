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


def test_admin_export_reads_only_known_snapshot_name():
    req=lambda league:SimpleNamespace(rest='gradecore-export',query={'league':league})
    assert api_model.admin_model(req('../../secrets'))[0]==400
    assert api_model.admin_model(req('nba'))[0]==503
    folder=config.DATA_DIR/'model/api';folder.mkdir(parents=True)
    doc={'schema':'gradecore-export-1','rows':[]}
    (folder/'gradecore_export_nba.json').write_text(json.dumps(doc))
    assert api_model.admin_model(req('nba'))==(200,doc)
