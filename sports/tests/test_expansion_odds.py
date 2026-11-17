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
