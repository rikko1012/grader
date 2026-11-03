from gradecore.history import cs2, lol


def test_cs2_uses_latest_prelock_raw_forecast_and_its_selected_side():
    pick=dict(id=1,test=0,status='settled',settled_at='2026-11-02T20:00:00Z',
              match_slug='e',slug='p',prop='kills',scope='12',book='underdog',line=20.5,actual=21)
    f=dict(id=1,pick_id=1,model_version='v',logged_at='2026-11-02T10:00:00Z',
           match_start='2026-11-02T12:00:00Z',line=20.5,pick='More',probs_raw={'over':.6,'under':.4,'push':0})
    later=dict(f,id=2,logged_at='2026-11-02T11:00:00Z',pick='Less')
    too_late=dict(f,id=3,logged_at='2026-11-02T11:45:00Z')
    rows,excluded=cs2([f,later,too_late],[pick],'v')
    assert len(rows)==1 and rows[0]['forecast_id']=='2'
    assert rows[0]['status']=='loss' and rows[0]['p']==.4
    assert excluded['invalid_time_or_inside_30_minutes']==1
    push=dict(pick,status='push',actual=20.5)
    assert cs2([f],[push],'v')[0][0]['status']=='push'
    assert cs2([dict(f,probs_raw=None)],[pick],'v')[0]==[]
    assert cs2([f],[dict(pick,test=2)],'v')[0]==[]


def test_lol_latest_correction_and_unplayable_side_are_not_ignored():
    identity=dict(platform='underdog',sid='e',pid='p',stat='kills',scope='12',line=20.5)
    f=dict(identity,id=1,model_version='v',fit_sha='a',side_offered=1,model_side='over',
           forecast_at='2026-11-02T10:00:00Z',series_start='2026-11-02T12:00:00Z',
           p_over=.6,p_under=.4,p_push=0)
    s=dict(identity,version=1,status='graded',over_hit=1,push=0,settled_at='2026-11-02T20:00:00Z')
    correction=dict(s,version=2,over_hit=0)
    rows,_=lol([f],[s,correction],'v','a')
    assert rows[0]['status']=='loss'
    assert lol([dict(f,side_offered=0)],[s],'v','a')[0]==[]
    assert lol([f],[dict(correction,status='rule_unverified')],'v','a')[0]==[]
    assert lol([f],[s],'v','other')[0]==[]
