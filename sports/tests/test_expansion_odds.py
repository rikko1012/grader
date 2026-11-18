from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest
from sports.expansion_odds import kambi_event, draftkings_soccer
from sports import expansion_board as boards
from sports.contracts import utc

NOW='2026-11-17T12:00:00Z'

def fixture(sport='tennis'):
    e={'id':1,'sport':'TENNIS' if sport=='tennis' else 'FOOTBALL','state':'NOT_STARTED',
       'name':'Alpha - Beta','homeName':'Alpha','awayName':'Beta','start':'2026-11-18T12:00:00Z',
       'path':[{'name':sport},{'name':'ATP' if sport=='tennis' else 'Premier League'}]}
    m={'id':2,'eventId':1,'criterion':{'label':'Moneyline' if sport=='tennis' else 'Full Time'},
       'betOfferType':{'name':'Match'},'tags':['OFFERED_PREMATCH','MAIN'],
       'outcomes':[{'id':3,'label':'Alpha','status':'OPEN','odds':2000,'oddsAmerican':'100',
         'changedDate':'2026-11-17T11:00:00Z','type':'OT_ONE'},
         {'id':4,'label':'Beta','status':'OPEN','odds':1900,'oddsAmerican':'-111',
         'changedDate':'2026-11-17T11:00:00Z','type':'OT_TWO'}]}
    return e,m

def test_real_odds_provenance_and_native_scope():
    e,m=fixture();r=kambi_event(e,[m],'tennis',NOW)[0]
    assert r['outcomes'][0]['decimal']==2 and r['outcomes'][1]['american']==-111
    assert r['jurisdiction']=='US-OH' and r['scope'] is None
    assert r['observed_at']==NOW and r['outcomes'][0]['source_updated_at']!=NOW
    assert r['main'] and r['settlement_rules']=='book_native_not_cross_book_verified'
    assert r['revision']==kambi_event(e,[m],'tennis','2026-11-17T12:01:00Z')[0]['revision']

@pytest.mark.parametrize('defect',['live','wrong_sport','started','wrong_event','doubles','promo','suspended','future_price','contradictory_odds'])
def test_invalid_or_incompatible_quotes_refused(defect):
    e,m=fixture()
    if defect=='live':e['state']='STARTED'
    if defect=='wrong_sport':e['sport']='BASKETBALL'
    if defect=='started':e['start']=NOW
    if defect=='wrong_event':m['eventId']=22
    if defect=='doubles':e['path'][1]['name']='ATP Doubles'
    if defect=='promo':m['tags'].append('BOOST')
    for o in m['outcomes']:
        if defect=='suspended':o['status']='SUSPENDED'
        if defect=='future_price':o['changedDate']='2026-11-17T12:01:00Z'
        if defect=='contradictory_odds':o['oddsAmerican']='900'
    assert kambi_event(e,[m],'tennis',NOW)==[]

def test_alternate_line_explicit_and_missing_side_never_filled():
    e,m=fixture();m['tags']=['OFFERED_PREMATCH'];m['criterion']['label']='Total Games - Set 1'
    m['outcomes']=m['outcomes'][:1];m['outcomes'][0].update(label='Over',line=9500)
    r=kambi_event(e,[m],'tennis',NOW)[0]
    assert not r['main'] and len(r['outcomes'])==1 and r['outcomes'][0]['line']==9.5
    assert r['label']=='Total Games - Set 1'
    m['tags'].append('MAIN_LINE')
    assert not kambi_event(e,[m],'tennis',NOW)[0]['main']

def test_soccer_keeps_all_three_outcomes():
    e,m=fixture('soccer');m['outcomes'].append(dict(m['outcomes'][0],id=5,label='Draw',type='OT_CROSS'))
    assert len(kambi_event(e,[m],'soccer',NOW)[0]['outcomes'])==3

def test_draftkings_requires_current_prices_and_draw():
    c={'id':'1','date':'2026-11-18T12:00:00Z','status':{'type':{'state':'pre'}},
       'competitors':[{'homeAway':s,'team':{'displayName':s}} for s in ['home','away']],
       'odds':[{'provider':{'id':'100'},'moneyline':{s:{'close':{'odds':v},'open':{'odds':'999'}}
                    for s,v in [('home','+200'),('draw','+250'),('away','-150')]}}]}
    b={'events':[{'name':'Match','competitions':[c]}]}
    r=draftkings_soccer(b,NOW)[0]
    assert [o['american'] for o in r['outcomes']]==[200,250,-150]
    del c['odds'][0]['moneyline']['draw']['close']
    assert not draftkings_soccer(b,NOW)
    c['odds']=[None]
    assert not draftkings_soccer(b,NOW)

def test_odds_revision_persistence_and_expiry(tmp_path,monkeypatch):
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED','1');monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    now=datetime.now(timezone.utc);stamp=utc(now);e,m=fixture();e['start']=utc(now+timedelta(minutes=1))
    for o in m['outcomes']:o['changedDate']=stamp
    row=kambi_event(e,[m],'tennis',stamp)[0]
    boards.save('tennis',[],{},stamp,game_odds=[row])
    req=SimpleNamespace(query={'sport':'tennis'})
    code,b=boards.board(req);assert code==200 and b['game_odds'][0]['outcomes'][0]['american']==100
    oldversion=b['version'];m['outcomes'][0].update(odds=2100,oddsAmerican='110')
    newer=kambi_event(e,[m],'tennis',stamp)[0];boards.save('tennis',[],{},stamp,game_odds=[newer])
    assert boards.board(req)[1]['version']!=oldversion
    import sqlite3
    with sqlite3.connect(tmp_path/'tennis'/'quotes.db') as con:
        assert con.execute('SELECT count(*) FROM revisions').fetchone()[0]==2
    path=tmp_path/'tennis'/'board.json';st=path.stat()
    b=boards._prepared(path,st,now+timedelta(seconds=61),'tennis')
    assert b['game_odds']==[]


# ---- Kalshi reference prices: their own snapshot key and quotes.db namespace ----------------------------------

def reference(sport='tennis', start=None, observed=None, mid=.665, offer='KXATPMATCH-26NOV17SHEJOD'):
    from sports.expansion_offers import finish
    now=datetime.now(timezone.utc)
    return finish({'sport':sport,'platform':'kalshi','source':'kalshi','offer_id':offer,'event_id':'atp:espn:atp:1:1',
        'match':'Ben Shelton vs Rafael Jodar','teams':['Ben Shelton','Rafael Jodar'],'start':start or utc(now+timedelta(hours=2)),
        'market_type':'Exchange match','main':True,'reference_only':True,'observed_at':observed or utc(now),
        'outcomes':[{'selection_id':'a','label':'Ben Shelton','bid':mid-.005,'ask':mid+.005,'mid':mid},
                    {'selection_id':'b','label':'Rafael Jodar','bid':.33,'ask':.34,'mid':.335}]})


@pytest.fixture
def tennis_store(tmp_path,monkeypatch):
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED','1');monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    return tmp_path/'tennis'


def test_reference_prices_have_their_own_key_namespace_and_version(tennis_store):
    import sqlite3
    stamp=utc(datetime.now(timezone.utc));req=SimpleNamespace(query={'sport':'tennis'})
    row=reference()
    snap=boards.save('tennis',[],{},stamp,reference_prices=[row])
    assert snap['reference_prices']==[row] and snap['game_odds']==[] and snap['offers']==[]
    boards.save('tennis',[],{},stamp,reference_prices=[dict(row,observed_at=stamp)])     # same revision: stored once
    with sqlite3.connect(tennis_store/'quotes.db') as con:
        assert con.execute('SELECT platform,offer_id FROM revisions').fetchall()==[('ref:kalshi',row['offer_id'])]
    code,b=boards.board(req)
    assert code==200 and b['reference_prices'][0]['outcomes'][0]['mid']==.665 and b['game_odds']==[]==b['offers']
    assert boards.board(SimpleNamespace(query={'sport':'tennis','version':b['version']}))[1].get('unchanged')
    boards.save('tennis',[],{},stamp,reference_prices=[reference(mid=.675)])
    moved=boards.board(req)[1]
    assert moved['version']!=b['version']
    with sqlite3.connect(tennis_store/'quotes.db') as con:
        assert con.execute('SELECT count(*) FROM revisions').fetchone()[0]==2
        assert not con.execute("SELECT 1 FROM revisions WHERE platform NOT LIKE 'ref:%'").fetchall()


def test_reference_prices_expire_with_age_and_at_the_start(tennis_store):
    now=datetime.now(timezone.utc);stamp=utc(now)
    soon=reference(start=utc(now+timedelta(seconds=30)),observed=stamp,offer='soon');later=reference(observed=stamp,offer='later')
    foreign=dict(later,offer_id='x')
    boards.save('tennis',[],{},stamp,reference_prices=[soon,later])
    path=tennis_store/'board.json';st=path.stat()
    assert [r['offer_id'] for r in boards._prepared(path,st,now,'tennis')['reference_prices']]==['soon','later']
    assert [r['offer_id'] for r in boards._prepared(path,st,now+timedelta(seconds=31),'tennis')['reference_prices']]==['later']
    assert boards._prepared(path,st,now+timedelta(seconds=299),'tennis')['reference_prices']!=[]
    with pytest.raises(ValueError):                     # the whole board is stale after 300 s
        boards._prepared(path,st,now+timedelta(seconds=301),'tennis')
    # A row seen 5+ minutes before the snapshot was built is dropped even while the board itself is current.
    old=reference(observed=utc(now-timedelta(seconds=301)),offer='old')
    boards.save('tennis',[],{},stamp,reference_prices=[old,later]);st=path.stat()
    assert [r['offer_id'] for r in boards._prepared(path,st,now,'tennis')['reference_prices']]==['later']
    with pytest.raises(ValueError,match='mixed sport'):
        boards.save('tennis',[],{},stamp,reference_prices=[dict(foreign,sport='soccer')])


def test_board_without_reference_rows_is_byte_identical_to_before(tennis_store):
    now=datetime.now(timezone.utc);stamp=utc(now);e,m=fixture();e['start']=utc(now+timedelta(minutes=5))
    for o in m['outcomes']:o['changedDate']=stamp
    odds=kambi_event(e,[m],'tennis',stamp)
    plain=boards.save('tennis',[],{'betrivers':{'ok':True}},stamp,game_odds=odds)
    assert 'reference_prices' not in plain
    path=tennis_store/'board.json';before=path.read_bytes()
    assert list(json.loads(before))==['sport','built_at','sources','offers','mode','grading_ready','game_odds']
    answer=boards.board(SimpleNamespace(query={'sport':'tennis'}))[1]
    assert 'reference_prices' not in answer
    # An empty reference list changes neither the version nor the other keys.
    boards.save('tennis',[],{'betrivers':{'ok':True}},stamp,game_odds=odds,reference_prices=[])
    after=json.loads(path.read_bytes())
    assert after.pop('reference_prices')==[] and json.dumps(after,separators=(',',':')).encode()==before
    assert boards.board(SimpleNamespace(query={'sport':'tennis'}))[1]['version']==answer['version']
    # Reference rows never enter the version through the offers or odds namespaces.
    import hashlib
    expected=hashlib.sha256(json.dumps(sorted([('odds:'+r['platform'],r['offer_id'],r['revision']) for r in odds]),
                                       separators=(',',':')).encode()).hexdigest()
    assert answer['version']==expected


def test_board_stays_under_the_member_limit_with_150_reference_rows(tennis_store):
    from sports import expansion_kalshi
    now=datetime.now(timezone.utc);stamp=utc(now)
    rows=[reference(offer=f'KXATPMATCH-26NOV17{i:06d}') for i in range(expansion_kalshi.MAX_ROWS)]
    start=utc(now+timedelta(hours=2))
    offers=[{'sport':'tennis','platform':'prizepicks','variant':'demon','offer_id':str(i),'revision':str(i),
             'start':start,'observed_at':stamp,'pad':'x'*2000} for i in range(1500)]
    snap=boards.save('tennis',offers,{},stamp,reference_prices=rows)
    size=(tennis_store/'board.json').stat().st_size
    assert size<=3_000_000 and len(snap['reference_prices'])==150 and 0<len(snap['offers'])<1500
    reference_bytes=len(json.dumps(rows,separators=(',',':')))
    assert reference_bytes<150*1500                     # ~0.5-1 KB a row
    assert boards.board(SimpleNamespace(query={'sport':'tennis'}))[0]==200


def test_quotes_job_kill_switch_and_reference_failure_never_cost_the_lines(tennis_store,monkeypatch):
    from sports import expansion_kalshi, expansion_odds
    monkeypatch.setattr('sports.expansion_sources.get_json',lambda sport,url,**kw:{})
    monkeypatch.setattr(expansion_odds,'collect',lambda sport,get,directory:([],{}))
    seen=[]
    def collect(sport,get,directory):
        seen.append(sport);return [reference()],{'ok':True,'reference':True,'markets':1}
    monkeypatch.setattr(expansion_kalshi,'collect',collect)
    snap=boards.collect('tennis')
    assert seen==['tennis'] and len(snap['reference_prices'])==1 and snap['sources']['kalshi']['markets']==1
    monkeypatch.setenv('SCOUT_TENNIS_KALSHI','0')
    snap=boards.collect('tennis')
    assert seen==['tennis'] and 'kalshi' not in snap['sources'] and 'reference_prices' not in snap
    monkeypatch.setenv('SCOUT_TENNIS_KALSHI','1')
    def broken(sport,get,directory):raise RuntimeError('unexpected')
    monkeypatch.setattr(expansion_kalshi,'collect',broken)
    snap=boards.collect('tennis')
    assert snap['reference_prices']==[] and snap['sources']['kalshi']=={**snap['sources']['kalshi'],'ok':False,'reference':True,'error':'RuntimeError'}
    assert snap['sources']['prizepicks']['ok']


def test_quotes_job_fails_when_only_the_reference_source_answered(tennis_store,monkeypatch):
    import sys
    monkeypatch.setattr(sys,'argv',['x','tennis'])
    monkeypatch.setattr(boards,'collect',lambda sport:{'offers':[],'sources':{
        'prizepicks':{'ok':False},'kalshi':{'ok':True,'reference':True}}})
    with pytest.raises(SystemExit) as e:boards.main()
    assert e.value.code==1
