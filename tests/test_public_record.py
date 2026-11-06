import json
import sqlite3
import types

import pytest

import public_record
import results_stated
import server
import workspace_store
from tests.test_upgrade_gate import env,get
from tests.test_upgrade_gate import rows as p1_rows


def test_public_sanitizer_never_copies_private_fields_or_nonfinite_values():
    doc={'ADMIN_TOKEN':'secret','member':'user_private','totals':{'wins':2,'losses':1,'printed':float('nan'),'user':'private'},
         'by_letter':[{'name':'B','wins':2,'losses':1,'private':'hidden'}],
         'calendar':{'state':'ready','basis':'UTC','days':[{'date':'2026-11-03','wins':2,'forecast_id':'private'}]}}
    result=public_record.sanitize(doc,'cs2')
    wire=json.dumps(result,allow_nan=False)
    assert 'secret' not in wire and 'private' not in wire and 'hidden' not in wire
    assert result['totals']['printed'] is None
    assert result['bands'][0]['wins']==2


def test_public_record_is_explicitly_enabled_without_unlocking_member_routes(env,monkeypatch):
    monkeypatch.delenv('SCOUT_PUBLIC_RECORD',raising=False)
    assert get(env.base,'/record')[0]==404
    assert get(env.base,'/api/record')[0]==404
    monkeypatch.setenv('SCOUT_PUBLIC_RECORD','1')
    assert get(env.base,'/record')[0]==200
    assert get(env.base,'/record.js')[0]==200
    assert get(env.base,'/api/record?sport=private')[0]==400
    status,body,_=get(env.base,'/api/record?sport=cs2')
    assert status==200 and json.loads(body)['sport']=='cs2'
    assert get(env.base,'/api/upgrade/mine')[0]!=200
    assert get(env.base,'/admin/forecasts_page.json')[0]!=200


# ---------------------------------------------------------------------------------- additions: CI, bins, counts, status, slips
FLAGS=('SCOUT_RECORD_STATUS','SCOUT_PUBLIC_RECORD_SLIPS')


def _p1():
    return {'at':'2026-11-01T00:00:00+00:00','rows':p1_rows(),'pushes':[{'match':'m9','p':0.61,'prop':'kills','scope':'12','units':1,'forecast_id':90}]}


def test_sanitize_passes_ci_bins_and_labelled_counts_and_nothing_else():
    doc={'totals':{'wins':5,'losses':2,'pushes':1,'void':3,'matches':4,'match_word':'series','ci':[0.4,0.9]},
         'groups':{'chance':[{'name':'60-65%','wins':1,'losses':1,'ci':[0.1,float('nan')]},{'name':'65%+','wins':2,'losses':0,'ci':{'lo95':0.5,'hi95':1.0}}]},
         'reliability':[{'lo':0.55,'hi':0.6,'n':3,'stated':0.58,'hit':0.66,'ci':[0.2,0.9],'secret':'x'},{'n':0}]}
    r=public_record.sanitize(doc,'lol',rich=True)
    assert r['totals']['ci']==[0.4,0.9] and r['totals']['void']==3 and r['totals']['match_word']=='series'
    assert r['bands'][0]['ci'] is None and r['bands'][1]['ci']==[0.5,1.0]
    assert r['reliability']==[{'lo':0.55,'hi':0.6,'n':3,'stated':0.58,'hit':0.66,'ci':[0.2,0.9]}]
    assert 'status' not in r and 'slips_tab' not in r
    assert public_record.sanitize({'totals':{'match_word':'<b>'}},'nba',rich=True)['totals']['match_word']=='matches'
    on=public_record.sanitize({},'nba',status={'letters':'held','text':'NBA letters are held.','x':1},slips_tab=True)
    assert on['status']=={'letters':'held','text':'NBA letters are held.'} and on['slips_tab'] is True


def test_cs2_bins_and_tier_ranges_come_from_the_member_results_picks():
    doc=public_record._cs2(types.SimpleNamespace(_p1_export=_p1))
    base=results_stated.stated_observed(_p1())
    assert [{k:v for k,v in b.items() if k!='ci'} for b in doc['by_letter']]==base['by_letter']
    assert {k:v for k,v in doc['totals'].items() if k!='ci'}==base['totals']
    picks,_,_=results_stated.rule_picks(_p1())
    assert sum(b['n'] for b in doc['reliability'])==len(picks)==base['totals']['picks']==7
    assert all(round(b['hi']-b['lo'],6)==0.05 for b in doc['reliability'])
    lo,hi=doc['totals']['ci'];assert 0<=lo<=base['totals']['observed']<=hi<=1
    r=public_record.sanitize(doc,'cs2',rich=True)
    assert r['reliability'] and r['totals']['ci']==[round(lo,4),round(hi,4)]


def test_letter_status_reads_the_sports_containment_snapshot(monkeypatch):
    import workspace_sports
    pub={'leagues':{'nba':{'model_gate':{'contained':True},'grades':{}},'nfl':{'model_gate':{'contained':False},'grades':{'withheld':False}}}}
    monkeypatch.setattr(workspace_sports,'_public',lambda srv:(200,pub))
    assert public_record.letter_status(None,'nba')['letters']=='held'
    assert 'research estimates' in public_record.letter_status(None,'nba')['text']
    assert public_record.letter_status(None,'nfl') is None         # not held: the board decides per row (cf-2), so no league-wide "live"
    assert public_record.letter_status(None,'cs2')['letters']=='live' and public_record.letter_status(None,'lol')['letters']=='live'
    monkeypatch.setattr(workspace_sports,'_public',lambda srv:(503,{'error':'down'}))
    assert public_record.letter_status(None,'nba') is None                       # unknown stays unshown


def _slip_db(path):
    workspace_store.Store(path,start=False)
    con=sqlite3.connect(path)
    def slip(sid,platform,sports,legs,scratch=0):
        con.execute('INSERT INTO slips(slip_id,member,tracked_at,platform,entry_type,sports,legs,scratch) VALUES(?,?,?,?,?,?,?,?)',
                    (sid,'member-private','2026-11-01T00:00:00Z',platform,'power',json.dumps(sports),legs,scratch))
    def leg(sid,n,sport,*results,scratch=0):
        rid=f'{sid}-{n}'
        con.execute('INSERT INTO receipts(receipt_id,member,slip_id,leg_no,tracked_at,sport,platform,row_id,offer_rev,side,line,line_type,pick_id,'
                    'forecast_id,match_slug,match_key,nick,prop,scope,scratch,payload,settle_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (rid,'member-private',sid,n,'2026-11-01T00:00:00Z',sport,'underdog','r','o','over',20.5,'standard',1 if sport=='cs2' else None,
                     'f','m','k','nick-private','kills','12',scratch,'{}',None if sport=='cs2' else 'sk'))
        for rev,res in enumerate(results,1):
            con.execute('INSERT INTO settlements(receipt_id,revision,result,recorded_at) VALUES(?,?,?,?)',(rid,rev,res,'2026-11-02T00:00:00Z'))
    slip('a','underdog',['cs2'],2);leg('a',1,'cs2','hit');leg('a',2,'cs2','hit')                          # every leg hit
    slip('b','underdog',['cs2'],2);leg('b',1,'cs2','hit');leg('b',2,'cs2','miss')                         # a leg missed
    slip('c','prizepicks',['cs2','nba'],2);leg('c',1,'cs2','hit');leg('c',2,'nba','push')                 # push: never a hit
    slip('d','underdog',['cs2'],2);leg('d',1,'cs2','hit');leg('d',2,'cs2','hit','reopened')               # reopened: settling
    slip('e','underdog',['cs2'],2);leg('e',1,'cs2','hit')                                                 # a leg with no result
    slip('f','underdog',['cs2'],2,scratch=1);leg('f',1,'cs2','hit',scratch=1);leg('f',2,'cs2','hit',scratch=1)   # test slip
    con.commit();con.close()


def test_slip_summary_counts_pushes_apart_and_skips_test_slips(tmp_path):
    db=tmp_path/'workspace.db';_slip_db(db)
    s=public_record.read_slips(db)
    c=s['cs2']
    assert (c['tracked'],c['settled'],c['all_hit'],c['missed'],c['push_or_void'],c['pending'],c['mixed'])==(5,3,1,1,1,2,1)
    assert c['all_hit_rate']==0.5 and c['legs']=={'hit':6,'miss':1,'push':0,'void':0,'pending':1}
    assert {g['name']:g['settled'] for g in c['by_platform']}=={'PrizePicks':1,'Underdog':2}
    assert (s['nba']['tracked'],s['nba']['push_or_void'],s['nba']['all_hit_rate'],s['nba']['legs']['push'])==(1,1,None,1)
    assert s['lol']['tracked']==0 and s['lol']['all_hit_rate'] is None
    wire=json.dumps(s);assert 'private' not in wire and '"a"' not in wire
    assert len(public_record.slip_rows(db,limit=1))==1                          # newest non-test slip only ('e', one leg)
    with pytest.raises(FileNotFoundError):public_record.read_slips(tmp_path/'none.db')


def test_record_flags_off_leave_the_record_answer_as_before(env,monkeypatch):
    for k in FLAGS:monkeypatch.delenv(k,raising=False)
    monkeypatch.setenv('SCOUT_PUBLIC_RECORD','1')
    d=json.loads(get(env.base,'/api/record?sport=cs2')[1])
    assert 'status' not in d and 'slips_tab' not in d
    assert get(env.base,'/api/record/slips?sport=cs2')[0]==404
    monkeypatch.setenv('SCOUT_PUBLIC_RECORD_SLIPS','1');monkeypatch.delenv('SCOUT_PUBLIC_RECORD')
    assert get(env.base,'/api/record/slips?sport=cs2')[0]==404                  # the slips flag never opens the record alone
    assert get(env.base,'/record')[0]==404


def test_record_status_flag_on_adds_the_letters_fact(env,monkeypatch):
    monkeypatch.setenv('SCOUT_PUBLIC_RECORD','1');monkeypatch.setenv('SCOUT_RECORD_STATUS','1');monkeypatch.delenv('SCOUT_PUBLIC_RECORD_SLIPS',raising=False)
    d=json.loads(get(env.base,'/api/record?sport=cs2')[1])
    assert d['status']=={'letters':'live','text':'The CS2 board shows letter grades.'} and 'slips_tab' not in d
    assert get(env.base,'/api/record/slips?sport=cs2')[0]==404


def test_record_slips_flag_on_serves_anonymised_counts(env,monkeypatch,tmp_path):
    monkeypatch.setenv('SCOUT_PUBLIC_RECORD','1');monkeypatch.setenv('SCOUT_PUBLIC_RECORD_SLIPS','1');monkeypatch.delenv('SCOUT_RECORD_STATUS',raising=False)
    assert json.loads(get(env.base,'/api/record?sport=cs2')[1])['slips_tab'] is True
    assert get(env.base,'/api/record/slips?sport=private')[0]==400
    db=tmp_path/'workspace.db'
    if db.exists():db.unlink()
    assert get(env.base,'/api/record/slips?sport=cs2')[0]==503                 # no store: unavailable, never zero
    server._RESULTS.clear()
    _slip_db(db)
    status,body,_=get(env.base,'/api/record/slips?sport=cs2')
    d=json.loads(body)
    assert status==200 and d['sport']=='cs2' and d['all_hit']==1 and d['missed']==1 and d['push_or_void']==1
    assert 'private' not in body.decode()



# ---------------------------------------------------------------------------------- review 2026-11-04 fixes
BASE_KEYS={'sport','as_of','population','rule','totals','bands','calendar'}
from pathlib import Path  # noqa: E402
ROOT=Path(public_record.__file__).resolve().parent


def test_reliability_flag_off_keeps_the_record_answer_keys_exactly_as_before(env,monkeypatch):
    """The ranges, bins, voids and match word were added with no flag of their own; off, /api/record has the old keys only."""
    for k in FLAGS+('SCOUT_RECORD_RELIABILITY',):monkeypatch.delenv(k,raising=False)
    monkeypatch.setenv('SCOUT_PUBLIC_RECORD','1')
    server._RESULTS.clear()
    st,body,_=get(env.base,'/api/record?sport=cs2')
    d=json.loads(body)
    assert st==200 and set(d)==BASE_KEYS and set(d['totals'])==set(public_record.BASE_METRICS)
    assert all(set(b)==set(public_record.BASE_METRICS)|{'name'} for b in d['bands'])
    off=public_record.sanitize({'totals':{'wins':1,'void':2,'ci':[0.1,0.9],'match_word':'series'},'reliability':[{'n':3,'lo':.5,'hi':.55}]},'lol')
    assert set(off)==BASE_KEYS and set(off['totals'])==set(public_record.BASE_METRICS)
    monkeypatch.setenv('SCOUT_RECORD_RELIABILITY','1')
    server._RESULTS.clear()
    on=json.loads(get(env.base,'/api/record?sport=cs2')[1])
    assert 'reliability' in on and {'ci','void','match_word'}<=set(on['totals'])


def test_a_range_failure_never_takes_the_cs2_record_down(monkeypatch):
    import gradecore.scorecard as sc
    monkeypatch.setattr(sc,'cluster_ci',lambda *a,**k:1/0)
    doc=public_record._cs2(types.SimpleNamespace(_p1_export=_p1))
    assert doc==results_stated.stated_observed(_p1())                 # the plain record, no ranges
    monkeypatch.setenv('SCOUT_RECORD_RELIABILITY','1')
    r=public_record.read(types.SimpleNamespace(_p1_export=_p1),'cs2')
    assert r['totals']['wins'] is not None and r['reliability']==[] and r['totals']['ci'] is None


def test_record_page_shows_the_labelled_counts_only_with_the_reliability_answer():
    js=(ROOT/'web'/'record.js').read_text(encoding='utf-8')
    assert "rich=Array.isArray(d.reliability)" in js and "${rich?counts(" in js
